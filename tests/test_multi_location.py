"""Indexado y prune multi-ubicación (T3.3, T3.4, T3.6, T3.10; ADR 0010).

Dos raíces de prueba (SSD ``fast`` + USB ``archive``): recorrido por
ubicación con filas ``(location_id, rel_path)``, tolerancia a una ubicación
ausente, reglas de ``--prune`` (solo filas, nunca ficheros; solo ubicaciones
escaneadas o ``deleted``), desempate canónico por ``tier`` y la función
pura de disponibilidad.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from foto.core.indexing import Indexer, IndexingOptions
from foto.core.library import load_from_catalog
from foto.storage.availability import (
    AVAILABLE,
    UNAVAILABLE,
    availability_state,
)
from foto.storage.catalog import Catalog
from foto.storage.config import Location
from tests.conftest import FakeExtractor, write_image

NOW = datetime.now(UTC).replace(microsecond=0)


def _declared(tmp_path, *, with_usb: bool = True) -> tuple[Location, ...]:
    locations = [
        Location("ssd", tmp_path / "ssd", "fast"),
    ]
    if with_usb:
        locations.append(Location("usb", tmp_path / "usb", "archive"))
    return tuple(locations)


@pytest.fixture()
def library(tmp_path):
    """Dos raíces con contenido y su catálogo de metainformación externa."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "2024" / "una.jpg", b"ssd-una")
    write_image(ssd / "2025" / "dos.jpg", b"ssd-dos")
    write_image(usb / "archivos" / "tres.jpg", b"usb-tres")
    declared = _declared(tmp_path)
    catalog = Catalog.open_in_memory()
    try:
        yield catalog, declared, ssd, usb
    finally:
        catalog.close()


def _index(catalog, declared, **kwargs) -> object:
    return Indexer(
        catalog,
        declared,
        options=kwargs.pop(
            "options", IndexingOptions(skip=frozenset({"geocode"}))
        ),
        **kwargs,
    ).run()


# ----------------------------------------------------------------------
# T3.3 — Scanner e Indexer por ubicación
# ----------------------------------------------------------------------
def test_each_location_is_scanned_and_rows_carry_location_id(library):
    catalog, declared, _ssd, _usb = library
    summary = _index(catalog, declared)

    assert summary.status == "ok"
    assert summary.locations_scanned == ("ssd", "usb")
    assert summary.added == 3
    named = {row.name: row.id for row in catalog.list_named_locations()}
    rows = catalog._conn.execute(
        "SELECT location_id, rel_path, status FROM files ORDER BY rel_path"
    ).fetchall()
    assert [(r["location_id"], r["rel_path"], r["status"]) for r in rows] == [
        (named["ssd"], "2024/una.jpg", "present"),
        (named["ssd"], "2025/dos.jpg", "present"),
        (named["usb"], "archivos/tres.jpg", "present"),
    ]


def test_same_content_in_two_locations_is_one_photo_with_two_rows(tmp_path):
    """Deduplicación por hash intacta con la clave compuesta (T3.9)."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "copia.jpg", b"mismos-bytes")
    write_image(usb / "copia.jpg", b"mismos-bytes")
    catalog = Catalog.open_in_memory()
    try:
        summary = _index(catalog, _declared(tmp_path))
        assert summary.photos_total == 1
        assert summary.locations_total == 2
        assert summary.duplicate_photos == 1
    finally:
        catalog.close()


def test_metadata_is_extracted_from_the_canonical_fast_copy(tmp_path):
    """Si una foto está en los dos discos, se lee del SSD (Decisión 4)."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "z.jpg", b"mismos-bytes")  # rel_path mayor en fast
    write_image(usb / "a.jpg", b"mismos-bytes")  # rel_path menor en archive
    catalog = Catalog.open_in_memory()
    extractor = FakeExtractor()
    try:
        summary = _index(catalog, _declared(tmp_path), extractor=extractor)
        assert summary.metadata_extracted == 1
        assert len(extractor.calls) == 1
        # El lote apunta a la copia canónica (fast), no a la de menor rel_path.
        assert extractor.calls[0] == [ssd / "z.jpg"]
    finally:
        catalog.close()


def test_unavailable_location_warns_and_leaves_its_rows_untouched(
    library, tmp_path
):
    """USB «desmontado»: aviso, salida correcta, nada marcado ni borrado."""
    catalog, declared, _ssd, usb = library
    _index(catalog, declared)

    usb.rename(tmp_path / "desmontado")  # simula el desmontado
    summary = _index(catalog, declared)

    assert summary.status == "ok"
    assert summary.locations_scanned == ("ssd",)
    assert summary.locations_unavailable == ("usb",)
    assert summary.missing == 0
    rows = {
        str(row["rel_path"]): str(row["status"])
        for row in catalog._conn.execute("SELECT rel_path, status FROM files")
    }
    assert rows["archivos/tres.jpg"] == "present"
    assert not usb.exists()


def test_location_path_change_reconciles_its_rows(tmp_path):
    """Cambiar la ruta de una ubicación: el escaneo de la ruta nueva reconcilia."""
    ssd = tmp_path / "ssd"
    write_image(ssd / "una.jpg", b"contenido")
    usb = tmp_path / "usb"
    write_image(usb / "vieja.jpg", b"usb")
    declared = (
        Location("ssd", ssd, "fast"),
        Location("usb", usb, "archive"),
    )
    catalog = Catalog.open_in_memory()
    try:
        _index(catalog, declared)
        new_usb = tmp_path / "usb-nueva"
        write_image(new_usb / "nueva.jpg", b"usb")
        summary = _index(
            catalog,
            (Location("ssd", ssd, "fast"), Location("usb", new_usb, "archive")),
        )
        assert "usb" in summary.locations_sync.path_changed
        rows = {
            str(row["rel_path"]): str(row["status"])
            for row in catalog._conn.execute(
                "SELECT location_id, rel_path, status FROM files"
            )
            if row["location_id"] == catalog.get_named_location("usb").id
        }
        assert rows == {"vieja.jpg": "missing", "nueva.jpg": "present"}
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# T3.4 — Reglas de prune multi-ubicación
# ----------------------------------------------------------------------
def test_prune_never_touches_a_declared_but_unscanned_location(
    library, tmp_path
):
    """USB «desmontada» durante el prune: sus filas no se limpian jamás."""
    catalog, declared, ssd, usb = library
    _index(catalog, declared)
    # El SSD pierde un fichero (fila missing en la siguiente pasada)...
    (ssd / "2025" / "dos.jpg").unlink()
    _index(catalog, declared)
    rows = {
        str(row["rel_path"]): str(row["status"])
        for row in catalog._conn.execute("SELECT rel_path, status FROM files")
    }
    assert rows["2025/dos.jpg"] == "missing"

    # ...y el USB se desmonta: el prune con ventana 0 solo toca lo escaneado.
    usb.rename(tmp_path / "desmontado")
    summary = _index(
        catalog,
        declared,
        options=IndexingOptions(
            skip=frozenset({"geocode"}), prune=True, prune_after_days=0
        ),
    )

    assert summary.locations_unavailable == ("usb",)
    assert summary.pruned_locations == 1  # solo la fila del SSD
    rows = {
        str(row["rel_path"]): str(row["status"])
        for row in catalog._conn.execute("SELECT rel_path, status FROM files")
    }
    assert "2025/dos.jpg" not in rows
    assert rows["archivos/tres.jpg"] == "present"


def test_prune_cleans_deleted_location_rows_after_the_window(tmp_path):
    """Las filas ``deleted`` usan la ventana ``--prune-after`` desde ``deleted_at``."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "una.jpg", b"ssd")
    write_image(usb / "vieja.jpg", b"usb")
    catalog = Catalog.open_in_memory()
    try:
        _index(catalog, _declared(tmp_path))
        # Quitar el USB de la config: baja lógica, nada borrado del disco.
        summary = _index(catalog, _declared(tmp_path, with_usb=False))
        assert summary.locations_sync.removed == ("usb",)
        rows = {
            str(row["rel_path"]): str(row["status"])
            for row in catalog._conn.execute("SELECT rel_path, status FROM files")
        }
        assert rows["vieja.jpg"] == "deleted"
        # Regla 1 del AGENT.md: quitar una ubicación jamás borra ficheros.
        assert (usb / "vieja.jpg").is_file()

        # El prune con ventana 0 limpia las filas deleted y borra en cascada
        # la foto huérfana con sus etiquetas (Decisión 6).
        summary = _index(
            catalog,
            _declared(tmp_path, with_usb=False),
            options=IndexingOptions(
                skip=frozenset({"geocode"}), prune=True, prune_after_days=0
            ),
        )
        assert summary.pruned_locations >= 1
        assert summary.pruned_photos == 1
        assert (usb / "vieja.jpg").is_file()  # el fichero sigue intacto
    finally:
        catalog.close()


def test_prune_respects_a_wide_window_for_deleted_rows(tmp_path):
    """Con ventana amplia, las filas ``deleted`` siguen recuperables sin rescan."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "una.jpg", b"ssd")
    write_image(usb / "vieja.jpg", b"usb")
    catalog = Catalog.open_in_memory()
    try:
        _index(catalog, _declared(tmp_path))
        _index(
            catalog,
            _declared(tmp_path, with_usb=False),
            options=IndexingOptions(
                skip=frozenset({"geocode"}), prune=True, prune_after_days=30
            ),
        )
        rows = {
            str(row["rel_path"]): str(row["status"])
            for row in catalog._conn.execute("SELECT rel_path, status FROM files")
        }
        assert rows["vieja.jpg"] == "deleted"
        # Reactivar y escanear las restaura: el catálogo es derivable.
        summary = _index(catalog, _declared(tmp_path))
        rows = {
            str(row["rel_path"]): str(row["status"])
            for row in catalog._conn.execute("SELECT rel_path, status FROM files")
        }
        assert rows["vieja.jpg"] == "present"
        assert summary.locations_sync.reactivated == ("usb",)
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# T3.6 — Canónico por tier y rutas absolutas desde la BD
# ----------------------------------------------------------------------
def test_load_from_catalog_prefers_fast_tier_over_smaller_rel_path(tmp_path):
    """Foto en ambos discos: gana ``fast`` aunque su ``rel_path`` sea mayor."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "z" / "original.jpg", b"mismos-bytes")
    write_image(usb / "a" / "original.jpg", b"mismos-bytes")
    catalog = Catalog.open_in_memory()
    try:
        _index(catalog, _declared(tmp_path))

        photos = load_from_catalog(catalog)

        assert len(photos) == 1
        assert photos[0].path == (ssd / "z" / "original.jpg").resolve()
        assert photos[0].canonical_rel_path == "z/original.jpg"
        assert photos[0].other_locations == ("usb:a/original.jpg",)
    finally:
        catalog.close()


def test_load_from_catalog_skips_deleted_locations(tmp_path):
    """Las filas de ubicaciones ``deleted`` no se muestran (pendientes de prune)."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "una.jpg", b"ssd")
    write_image(usb / "vieja.jpg", b"usb")
    catalog = Catalog.open_in_memory()
    try:
        _index(catalog, _declared(tmp_path))
        _index(catalog, _declared(tmp_path, with_usb=False))

        photos = load_from_catalog(catalog)

        assert [photo.canonical_rel_path for photo in photos] == ["una.jpg"]
    finally:
        catalog.close()


def test_load_from_catalog_resolves_paths_per_row(tmp_path):
    """Dos fotos en ubicaciones distintas: cada ruta sale de su ``locations.path``."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "una.jpg", b"ssd-una")
    write_image(usb / "dos.jpg", b"usb-dos")
    catalog = Catalog.open_in_memory()
    try:
        _index(catalog, _declared(tmp_path))

        photos = {photo.name: photo for photo in load_from_catalog(catalog)}

        assert photos["una.jpg"].path == (ssd / "una.jpg").resolve()
        assert photos["dos.jpg"].path == (usb / "dos.jpg").resolve()
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# T3.10 — Evaluación de estados disponible/inexistente
# ----------------------------------------------------------------------
def test_availability_state_is_a_pure_injectable_function(tmp_path):
    """Dos estados: ``available`` (existe y es directorio) y ``unavailable``."""
    directory = tmp_path / "ssd"
    directory.mkdir()
    file_path = tmp_path / "no-es-directorio"
    file_path.write_text("")
    missing = tmp_path / "desmontado"

    assert availability_state(directory) == AVAILABLE
    assert availability_state(missing) == UNAVAILABLE
    # «Existe» no basta: debe ser directorio.
    assert availability_state(file_path) == UNAVAILABLE

    # Inyectable: el futuro backend remoto sustituye la comprobación.
    assert availability_state(missing, checker=lambda _path: True) == AVAILABLE
    assert availability_state(directory, checker=lambda _path: False) == UNAVAILABLE


# ----------------------------------------------------------------------
# T3.8 — Smoke end-to-end multi-ubicación
# ----------------------------------------------------------------------
def test_smoke_two_roots_lifecycle_from_baja_to_reactivation(tmp_path):
    """Ciclo completo: alta, indexado, baja por config, prune seguro, reactivación."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "una.jpg", b"favorita")
    write_image(usb / "dos.jpg", b"archivo")
    catalog = Catalog.open_in_memory()
    try:
        first = _index(catalog, _declared(tmp_path))
        assert first.status == "ok" and first.added == 2

        # Baja del USB: filas marcadas, ficheros intactos.
        second = _index(
            catalog,
            _declared(tmp_path, with_usb=False),
            options=IndexingOptions(
                skip=frozenset({"geocode"}), prune=True, prune_after_days=30
            ),
        )
        assert second.locations_sync.removed == ("usb",)
        assert (usb / "dos.jpg").is_file()

        # Reactivación: el escaneo restaura la fila (el catálogo es derivable).
        third = _index(catalog, _declared(tmp_path))
        assert third.locations_sync.reactivated == ("usb",)
        rows = {
            str(row["rel_path"]): str(row["status"])
            for row in catalog._conn.execute("SELECT rel_path, status FROM files")
        }
        assert rows == {"dos.jpg": "present", "una.jpg": "present"}
        assert catalog.counts().photos == 2
    finally:
        catalog.close()
