"""Caracterización del estado actual: la cadena v1 de raíz única.

Antes de la fototeca distribuida (ADR 0010), fija el comportamiento
observable de la cadena v1 —resolución, bootstrap, escaneo incremental,
prune y carga en la UI—. La resolución se actualizó en el paso 2 (T2.2: la
config es la única fuente y ``FOTO_LIBRARY`` es error); el resto queda como
caracterización: si alguno de estos tests falla al implementar los pasos 3–4,
hay una rotura no prevista.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from foto.core.errors import LibraryError
from foto.core.indexing import Indexer, IndexingOptions
from foto.storage.catalog import Catalog
from foto.storage.config import (
    DEFAULT_LOCATION_NAME,
    DEFAULT_LOCATION_TIER,
    ConfigError,
    Location,
)
from foto.storage.library import ensure_library, library_file
from foto.storage.resolver import resolve_locations
from foto.ui.app import load_photos

from .conftest import write_image

# ----------------------------------------------------------------------
# Resolución v1: la config es la única fuente (ADR 0010, T2.2)
# ----------------------------------------------------------------------


def test_v1_config_declares_the_default_location(tmp_path, monkeypatch):
    """``[library].path`` equivale a una única ubicación ``default`` fast."""
    from_config = tmp_path / "cfg"
    config = tmp_path / "config.toml"
    config.write_text(f'version = 1\n[library]\npath = "{from_config}"\n')
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)

    locations = resolve_locations(config_path=config)

    assert locations == (
        Location(
            name=DEFAULT_LOCATION_NAME,
            path=from_config.resolve(),
            tier=DEFAULT_LOCATION_TIER,
        ),
    )


def test_resolution_without_declaration_returns_empty(tmp_path, monkeypatch):
    """Sin ninguna fuente, el resolvedor no declara ubicaciones (diálogo aparte)."""
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    locations = resolve_locations(config_path=tmp_path / "ausente.toml")
    assert locations == ()


def test_foto_library_environment_is_rejected(tmp_path, monkeypatch):
    """``FOTO_LIBRARY`` definida es un error explícito, nunca silencio."""
    monkeypatch.setenv("FOTO_LIBRARY", str(tmp_path / "env"))
    config = tmp_path / "config.toml"
    config.write_text(f'version = 1\n[library]\npath = "{tmp_path / "cfg"}"\n')

    with pytest.raises(ConfigError, match="FOTO_LIBRARY"):
        resolve_locations(config_path=config)


# ----------------------------------------------------------------------
# Bootstrap v1: library.toml con root, idempotente y coherente
# ----------------------------------------------------------------------


def test_bootstrap_creates_descriptor_with_root_once(tmp_path):
    """``ensure_library`` crea el descriptor una vez y lo reutiliza."""
    root = tmp_path / "fototeca"
    root.mkdir()

    first = ensure_library(root)
    descriptor = library_file(root / ".foto")
    assert first.created is True
    assert descriptor.exists()
    content = descriptor.read_text(encoding="utf-8")
    assert f'root = "{root.resolve()}"' in content
    assert 'version = 1' in content
    assert 'id = "' in content

    second = ensure_library(root)
    assert second.created is False
    assert second.config.id == first.config.id
    assert second.config.root == root.resolve()


def test_bootstrap_rejects_foreign_descriptor(tmp_path):
    """Un descriptor con ``root`` distinto es un error explícito."""
    library_a = tmp_path / "a"
    library_b = tmp_path / "b"
    library_a.mkdir()
    library_b.mkdir()
    ensure_library(library_a)

    with pytest.raises(LibraryError, match="pertenece a la fototeca"):
        ensure_library(library_b, library_a / ".foto")


def test_bootstrap_rejects_missing_root(tmp_path):
    """La raíz inexistente sigue siendo un error duro del bootstrap."""
    with pytest.raises(LibraryError, match="no existe"):
        ensure_library(tmp_path / "falta")


# ----------------------------------------------------------------------
# Escaneo y prune v1: una raíz, filas rel_path + reconciliación
# ----------------------------------------------------------------------


@pytest.fixture()
def indexed_library(tmp_path):
    """Biblioteca v1 con dos imágenes indexadas y su catálogo abierto."""
    root = tmp_path / "fototeca"
    root.mkdir()
    write_image(root / "2024" / "una.jpg", b"contenido-uno")
    write_image(root / "2025" / "dos.jpg", b"contenido-dos")

    bootstrap = ensure_library(root)
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    indexer = Indexer(
        catalog,
        root,
        options=IndexingOptions(skip=frozenset({"geocode"})),
    )
    summary = indexer.run()
    return root, bootstrap, catalog, summary


def test_single_root_scan_records_relative_locations(indexed_library):
    """La primera pasada da de alta una fila por fichero con ``rel_path``."""
    _root, _, catalog, summary = indexed_library
    try:
        assert summary.status == "ok"
        assert summary.added == 2
        counts = catalog.counts()
        assert counts.photos == 2
        assert counts.locations == 2

        present = catalog.list_present_locations()
        rel_paths = sorted(row["rel_path"] for row in present)
        assert rel_paths == ["2024/una.jpg", "2025/dos.jpg"]
        # Las rutas en el catálogo son relativas POSIX a la única raíz.
        for row in present:
            assert not Path(row["rel_path"]).is_absolute()
    finally:
        catalog.close()


def test_single_root_rescan_marks_missing_without_touching_others(
    indexed_library,
):
    """Quitar un fichero marca su fila ``missing``; la otra queda intacta."""
    root, _, catalog, _ = indexed_library
    (root / "2024" / "una.jpg").unlink()

    indexer = Indexer(
        catalog,
        root,
        options=IndexingOptions(skip=frozenset({"geocode"})),
    )
    summary = indexer.run()
    try:
        assert summary.missing == 1
        assert summary.added == 0
        counts = catalog.counts()
        assert counts.photos == 2  # la foto no se borra: queda sin ubicación
        assert counts.locations == 2
    finally:
        catalog.close()


def test_single_root_prune_removes_old_missing_rows_and_orphans(
    indexed_library,
):
    """``--prune-after 0`` limpia filas ausentes y la foto huérfana."""
    root, _, catalog, _ = indexed_library
    (root / "2024" / "una.jpg").unlink()

    indexer = Indexer(
        catalog,
        root,
        options=IndexingOptions(
            skip=frozenset({"geocode"}), prune=True, prune_after_days=0
        ),
    )
    indexer.run()
    try:
        counts = catalog.counts()
        assert counts.locations == 1
        assert counts.photos == 1
    finally:
        catalog.close()


def test_prune_never_touches_present_rows(indexed_library):
    """``--prune`` no borra filas ni fotos con ubicaciones presentes."""
    root, _, catalog, _ = indexed_library

    indexer = Indexer(
        catalog,
        root,
        options=IndexingOptions(
            skip=frozenset({"geocode"}), prune=True, prune_after_days=0
        ),
    )
    indexer.run()
    try:
        counts = catalog.counts()
        assert counts.locations == 2
        assert counts.photos == 2
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# UI v1: rutas absolutas derivadas de la raíz única
# ----------------------------------------------------------------------


def test_ui_loads_from_catalog_with_absolute_paths_from_single_root(
    indexed_library,
):
    """``load_photos`` deriva las rutas absolutas de la raíz única."""
    root, _bootstrap, catalog, _ = indexed_library
    catalog.close()

    photos = load_photos(root)

    assert len(photos) == 2
    by_rel = {photo.canonical_rel_path: photo for photo in photos}
    assert by_rel["2024/una.jpg"].path == root / "2024" / "una.jpg"
    assert by_rel["2025/dos.jpg"].path == root / "2025" / "dos.jpg"
    # La foto sin metadatos (sin exiftool) queda pendiente, no ausente.
    assert all(photo.photo_hash.startswith("sha256:") for photo in photos)


def test_ui_requires_prepared_library(tmp_path):
    """Sin descriptor ni catálogo, la UI falla con mensaje claro.

    Caracterización: hoy el guard del descriptor (library.toml) dispara
    antes que el del catálogo; con el ADR 0010 el guard pasará al catálogo
    y este test se actualizará entonces (T3.6).
    """
    root = tmp_path / "vacia"
    root.mkdir()

    with pytest.raises(
        RuntimeError, match="no tiene descriptor|catalogo|índice|foto-index"
    ):
        load_photos(root)


def test_run_summary_counts_survive_json_round_trip(indexed_library):
    """El resumen de la pasada v1 es serializable (``--json``)."""
    _, _, _, summary = indexed_library
    payload = summary.to_dict()
    assert payload["totals"]["photos"] == 2
    assert payload["totals"]["locations"] == 2
    assert payload["phases"]["scan"]["added"] == 2
    assert payload["phases"]["scan"]["status"] == "ok"
    now = datetime.now(UTC).isoformat()
    assert summary.mode == "incremental" and now  # modo por defecto intacto
