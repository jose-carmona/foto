"""Tolerancia de la UI ante ubicaciones ausentes (T4.1–T4.5 y T4.7).

Nivel de carga, sin Qt: la disponibilidad se evalúa **al cargar** con la
función pura de ``storage/`` (por ubicación, no por fichero); la foto
fantasma conserva fecha, tamaño y etiquetas del catálogo; el error duro
queda reservado a la metainformación. Las interacciones de ventana
(banner, panel de ubicaciones, visor, favoritos offline) están en
``tests/test_ui_locations.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foto.core.hashing import hash_bytes
from foto.core.library import load_from_catalog
from foto.storage.catalog import Catalog
from foto.storage.config import Location
from foto.storage.sync import sync_locations
from foto.storage.tags import connect_read_write, toggle_favorite
from foto.ui.app import load_locations, load_photos
from foto.ui.presentation import location_rows
from tests.conftest import write_image

NOW = "2026-11-18T09:00:00+00:00"


@pytest.fixture()
def two_disks(tmp_path) -> tuple[Path, Path, Path]:
    """SSD ``fast`` con una foto y USB ``archive`` con otra; metainformación externa."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "2024" / "una.jpg", b"ssd-una")
    write_image(usb / "archivos" / "tres.jpg", b"usb-tres")
    metadata = tmp_path / "foto-meta"
    metadata.mkdir()
    declared = (
        Location("ssd", ssd, "fast"),
        Location("usb", usb, "archive"),
    )
    catalog = Catalog.open(
        metadata / "catalog.sqlite", declared_locations=declared
    )
    try:
        ids = {}
        for location in declared:
            ids[location.name] = catalog.insert_named_location(
                name=location.name,
                path=location.path,
                tier=location.tier,
                now=NOW,
            )
            catalog.mark_location_scanned(ids[location.name], NOW)
        for location_name, rel_path, data in (
            ("ssd", "2024/una.jpg", b"ssd-una"),
            ("usb", "archivos/tres.jpg", b"usb-tres"),
        ):
            photo_hash = f"sha256:{hash_bytes(data)}"
            catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
            catalog.insert_file(
                location_id=ids[location_name],
                rel_path=rel_path,
                photo_hash=photo_hash,
                size=len(data),
                mtime_ns=1,
                inode=1,
                device=1,
                now=NOW,
            )
        catalog.commit()
    finally:
        catalog.close()
    # La foto del USB es favorita: etiqueta de sistema (ADR 0006), escrita
    # con la conexión RW del visor una vez cerrado el catálogo del montaje.
    connection = connect_read_write(metadata / "catalog.sqlite")
    try:
        toggle_favorite(connection, f"sha256:{hash_bytes(b'usb-tres')}", now=NOW)
    finally:
        connection.close()
    return ssd, usb, metadata


# ----------------------------------------------------------------------
# T4.1 / T4.2 — el estado de las ubicaciones sale del catálogo
# ----------------------------------------------------------------------
def test_load_locations_reads_the_catalog_and_evaluates_availability(two_disks):
    _ssd, usb, metadata = two_disks
    usb.rename(usb.with_name("desmontado"))

    locations = load_locations(metadata)

    assert [(row.name, row.available, row.photo_count) for row in locations] == [
        ("ssd", True, 1),
        ("usb", False, 1),
    ]
    assert all(row.state == "active" for row in locations)
    # El recuento sale de la BD, no del disco.
    assert not usb.exists()


def test_load_locations_lists_deleted_locations_last(two_disks):
    ssd, _usb, metadata = two_disks
    declared = (Location("ssd", ssd, "fast"),)
    from datetime import datetime

    catalog = Catalog.open(metadata / "catalog.sqlite")
    try:
        sync_locations(catalog, declared, now=datetime.now())
    finally:
        catalog.close()

    locations = load_locations(metadata)

    assert [(row.name, row.state) for row in locations] == [
        ("ssd", "active"),
        ("usb", "deleted"),
    ]
    assert location_rows(locations)[-1] == (
        "usb",
        "archive",
        str(_usb),
        "dada de baja",
        0,
    )


# ----------------------------------------------------------------------
# T4.3 — placeholders fantasma con los datos del catálogo
# ----------------------------------------------------------------------
def test_load_photos_keeps_the_ghost_with_its_catalog_data(two_disks):
    _ssd, usb, metadata = two_disks
    usb.rename(usb.with_name("desmontado"))

    photos = load_photos(_ssd, metadata)
    by_name = {photo.name: photo for photo in photos}

    assert len(photos) == 2  # nada desaparece: se degrada, no se oculta
    ghost = by_name["tres.jpg"]
    assert ghost.available is False
    assert ghost.canonical_location == "usb"
    assert ghost.path is not None and not ghost.path.exists()
    assert ghost.favorite is True  # la etiqueta vive en el catálogo (ADR 0006)
    assert ghost.size  # el tamaño cae al valor guardado en el catálogo
    served = by_name["una.jpg"]
    assert served.available is True and served.canonical_location == "ssd"


def test_ghost_is_served_from_the_other_disk_when_it_exists(tmp_path):
    """Foto en los dos discos con el SSD desmontado: se sirve desde el USB."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "z" / "doble.jpg", b"mismos-bytes")
    write_image(usb / "a" / "doble.jpg", b"mismos-bytes")
    metadata = tmp_path / "foto-meta"
    metadata.mkdir()
    declared = (
        Location("ssd", ssd, "fast"),
        Location("usb", usb, "archive"),
    )
    catalog = Catalog.open(metadata / "catalog.sqlite", declared_locations=declared)
    try:
        ids = {
            location.name: catalog.insert_named_location(
                name=location.name,
                path=location.path,
                tier=location.tier,
                now=NOW,
            )
            for location in declared
        }
        photo_hash = f"sha256:{hash_bytes(b'mismos-bytes')}"
        catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
        for location_name, rel_path in (
            ("ssd", "z/doble.jpg"),
            ("usb", "a/doble.jpg"),
        ):
            catalog.insert_file(
                location_id=ids[location_name],
                rel_path=rel_path,
                photo_hash=photo_hash,
                size=12,
                mtime_ns=1,
                inode=1,
                device=1,
                now=NOW,
            )
        catalog.commit()
    finally:
        catalog.close()

    ssd.rename(ssd.with_name("desmontado"))
    catalog = Catalog.open_read_only(metadata / "catalog.sqlite")
    try:
        photos = load_from_catalog(catalog)
    finally:
        catalog.close()

    assert len(photos) == 1
    assert photos[0].canonical_location == "usb"
    assert photos[0].path == (usb / "a" / "doble.jpg").resolve()
    assert photos[0].other_locations == ("ssd:z/doble.jpg",)


def test_cached_thumbnail_survives_the_unmounted_volume(two_disks):
    """La miniatura fantasma sale de la caché sin tocar la ruta ausente."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    from foto.ui.thumbnails import ThumbnailCache, load_thumbnail

    _ssd, usb, metadata = two_disks
    usb.rename(usb.with_name("desmontado"))
    target = QSize(4, 4)
    cache = ThumbnailCache(metadata / "thumbs", target)
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0)
    cache.write(f"sha256:{hash_bytes(b'usb-tres')}", image)

    missing = usb / "archivos" / "tres.jpg"
    assert not missing.exists()

    loaded = load_thumbnail(
        missing, target, photo_hash=f"sha256:{hash_bytes(b'usb-tres')}", cache=cache
    )

    assert not loaded.isNull()


# ----------------------------------------------------------------------
# T4.5 — el error duro es solo la metainformación
# ----------------------------------------------------------------------
def test_missing_metadata_is_the_only_hard_error(tmp_path):
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    empty = tmp_path / "sin-catalogo"
    empty.mkdir()

    with pytest.raises(FileNotFoundError, match="foto-index"):
        load_photos(ssd, empty)
    with pytest.raises(FileNotFoundError, match="foto-index"):
        load_locations(empty)


def test_an_unavailable_location_is_never_a_hard_error(two_disks):
    """Ubicación ausente: carga normal con degradación; jamás bloquea."""
    _ssd, usb, metadata = two_disks
    usb.rename(usb.with_name("desmontado"))

    photos = load_photos(_ssd, metadata)
    locations = load_locations(metadata)

    assert len(photos) == 2
    assert any(not photo.available for photo in photos)
    assert any(not location.available for location in locations)


# ----------------------------------------------------------------------
# T4.7 — ubicación reactivada cuyo escaneo aún no ha corrido
# ----------------------------------------------------------------------
def test_reactivated_location_shows_empty_until_its_scan_runs(tmp_path):
    """Filas ``deleted`` de una reactivada: sin fotos; «pendiente de escaneo»."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "una.jpg", b"ssd-una")
    write_image(usb / "tres.jpg", b"usb-tres")
    metadata = tmp_path / "foto-meta"
    metadata.mkdir()
    both = (
        Location("ssd", ssd, "fast"),
        Location("usb", usb, "archive"),
    )
    only_ssd = (Location("ssd", ssd, "fast"),)
    from datetime import datetime

    catalog = Catalog.open(metadata / "catalog.sqlite", declared_locations=both)
    try:
        # Alta sin escaneo: sin ``last_scanned_at`` (nunca ha corrido).
        ids = {
            location.name: catalog.insert_named_location(
                name=location.name,
                path=location.path,
                tier=location.tier,
                now=NOW,
            )
            for location in both
        }
        for location_name, rel_path, data in (
            ("ssd", "una.jpg", b"ssd-una"),
            ("usb", "tres.jpg", b"usb-tres"),
        ):
            photo_hash = f"sha256:{hash_bytes(data)}"
            catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
            catalog.insert_file(
                location_id=ids[location_name],
                rel_path=rel_path,
                photo_hash=photo_hash,
                size=len(data),
                mtime_ns=1,
                inode=1,
                device=1,
                now=NOW,
            )
        catalog.commit()
        # Baja por config: filas del USB marcadas ``deleted``.
        sync_locations(catalog, only_ssd, now=datetime.now())
        # Reactivación por config sin escaneo posterior.
        sync_locations(catalog, both, now=datetime.now())
    finally:
        catalog.close()

    photos = load_photos(ssd, metadata)
    locations = load_locations(metadata)

    # La ubicación reactivada se muestra vacía (ADR 0010, Decisión 7):
    # sus filas siguen ``deleted`` hasta que el escaneo las repuebla.
    assert [photo.name for photo in photos] == ["una.jpg"]
    assert [(row.name, row.state, row.available, row.photo_count) for row in locations] == [
        ("ssd", "active", True, 1),
        ("usb", "active", True, 0),
    ]
    assert location_rows(locations)[1][-2] == "pendiente de escaneo"
