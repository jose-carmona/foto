"""Comprobaciones de la carga del catálogo desde la UI (V01).

Solo se ejercita ``load_photos``, que no necesita una instancia de Qt: la
importación del módulo funciona incluso en un entorno sin pantalla.
"""

from __future__ import annotations

import sqlite3

import pytest

from foto.core.geocoding import Place, PlaceRecord
from foto.storage.catalog import Catalog
from foto.storage.config import Location
from foto.ui.app import load_photos
from tests.conftest import write_image

NOW = "2026-10-30T12:00:00+00:00"


def _seed_catalog(root, photo_hash: str) -> None:
    (root / ".foto").mkdir(parents=True, exist_ok=True)
    declared = (Location("default", root, "fast"),)
    catalog = Catalog.open(
        root / ".foto" / "catalog.sqlite", declared_locations=declared
    )
    try:
        location_id = catalog.insert_named_location(
            name="default", path=root, tier="fast", now=NOW
        )
        catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
        catalog.insert_file(
            location_id=location_id,
            rel_path="2024/una.jpg",
            photo_hash=photo_hash,
            size=5,
            mtime_ns=1,
            inode=1,
            device=1,
            now=NOW,
        )
        catalog.commit()
    finally:
        catalog.close()


def test_ui_loads_the_resolved_place_fields(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    (root / ".foto").mkdir()
    photo_hash = "sha256:" + "d" * 64
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        location_id = catalog.insert_named_location(
            name="default", path=root, tier="fast", now=NOW
        )
        catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
        catalog.insert_file(
            location_id=location_id,
            rel_path="2024/una.jpg",
            photo_hash=photo_hash,
            size=5,
            mtime_ns=1,
            inode=1,
            device=1,
            now=NOW,
        )
        catalog.update_place(
            photo_hash,
            PlaceRecord(
                place=Place(
                    country="España",
                    country_code="ES",
                    state="Cataluña",
                    city="Barcelona",
                    municipality="Barcelona",
                ),
                display_name="Passeig de Colom, Barcelona, Cataluña, España",
                address_json='{"city": "Barcelona", "country": "España"}',
            ),
            NOW,
        )
        catalog.commit()
    finally:
        catalog.close()

    photos = load_photos(root)

    assert len(photos) == 1
    assert photos[0].place_country == "España"
    assert photos[0].place_country_code == "ES"
    assert photos[0].place_state == "Cataluña"
    assert photos[0].place_city == "Barcelona"
    assert photos[0].place_municipality == "Barcelona"
    assert photos[0].place_display_name == "Passeig de Colom, Barcelona, Cataluña, España"
    assert photos[0].place_address_json == '{"city": "Barcelona", "country": "España"}'


def test_ui_loads_photos_from_an_existing_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    _seed_catalog(root, "sha256:" + "d" * 64)

    photos = load_photos(root)

    assert len(photos) == 1
    assert photos[0].name == "una.jpg"
    assert photos[0].photo_hash == "sha256:" + "d" * 64


# ----------------------------------------------------------------------
# T1.3 — el arranque abre el catálogo solo-lectura una sola vez
# ----------------------------------------------------------------------
def test_load_library_opens_the_catalog_read_only_once(tmp_path, monkeypatch):
    """Fotos, tags y ubicaciones salen de una única conexión ``mode=ro``.

    Antes el arranque abría y cerraba tres conexiones solo-lectura
    secuenciales (load_photos, load_tags, load_locations) más la RW de
    favoritos. La conexión RW sigue aparte (escritor único del visor,
    ADR 0006); aquí solo cuentan las aperturas de lectura.
    """
    from foto.storage.catalog import Catalog as catalog_cls
    from foto.ui.app import load_library

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    _seed_catalog(root, "sha256:" + "d" * 64)

    original = catalog_cls.open_read_only
    aperturas = []

    def _contando(path):
        aperturas.append(path)
        return original(path)

    monkeypatch.setattr(catalog_cls, "open_read_only", _contando)

    photos, tags, locations = load_library(root)

    assert len(aperturas) == 1
    assert len(photos) == 1
    assert len(locations) == 1
    assert [tag.kind for tag in tags] == ["system"]  # ``fav`` sembrada
    assert photos[0].available is True


def test_ui_missing_catalog_raises_without_creating_a_file(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    catalog_path = root / ".foto" / "catalog.sqlite"

    with pytest.raises(FileNotFoundError, match="foto-index"):
        load_photos(root)

    assert not catalog_path.exists()


def test_ui_does_not_migrate_newer_schemas(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    catalog_path = root / ".foto" / "catalog.sqlite"
    connection = sqlite3.connect(catalog_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(RuntimeError, match="más nuevo"):
        load_photos(root)

    connection = sqlite3.connect(catalog_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
    connection.close()


# ----------------------------------------------------------------------
# Guard del catálogo al abrir desde la UI (T3.7, ADR 0010 Decisión C)
# ----------------------------------------------------------------------
def test_ui_requires_a_migrated_catalog_before_opening_it(tmp_path):
    """Un catálogo sin migrar no se toca desde la UI: error pidiendo foto-index."""
    root = tmp_path / "biblioteca"
    root.mkdir()
    catalog_path = root / ".foto" / "catalog.sqlite"
    catalog_path.parent.mkdir()
    connection = sqlite3.connect(catalog_path)
    connection.execute("PRAGMA user_version = 0")
    connection.close()

    with pytest.raises(RuntimeError, match="foto-index"):
        load_photos(root)

    # El catálogo no se creó ni se migró.
    connection = sqlite3.connect(catalog_path)
    tables = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0]
    connection.close()
    assert tables == 0


def test_ui_ignores_a_legacy_descriptor_orphan(tmp_path):
    """Un ``library.toml`` huérfano se ignora: no se lee ni se borra."""
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    legacy_text = 'version = 1\nid = "3f9c1b62-8a44-4f0e-9d1a-2c7b5e0f4a11"\n'
    (root / ".foto" / "library.toml").write_text(legacy_text, encoding="utf-8")
    _seed_catalog(root, "sha256:" + "d" * 64)

    photos = load_photos(root)

    assert len(photos) == 1
    assert (root / ".foto" / "library.toml").read_text(
        encoding="utf-8"
    ) == legacy_text
