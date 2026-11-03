"""Comprobaciones de la carga del catálogo desde la UI (V01).

Solo se ejercita ``load_photos``, que no necesita una instancia de Qt: la
importación del módulo funciona incluso en un entorno sin pantalla.
"""

from __future__ import annotations

import sqlite3

import pytest

from foto.storage.catalog import Catalog
from foto.storage.library import ensure_library
from foto.ui.app import load_photos
from tests.conftest import write_image

NOW = "2026-10-30T12:00:00+00:00"


def _seed_catalog(root, photo_hash: str) -> None:
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
        catalog.insert_location(
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


def test_ui_loads_photos_from_an_existing_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    ensure_library(root)
    _seed_catalog(root, "sha256:" + "d" * 64)

    photos = load_photos(root)

    assert len(photos) == 1
    assert photos[0].name == "una.jpg"
    assert photos[0].photo_hash == "sha256:" + "d" * 64


def test_ui_missing_catalog_raises_without_creating_a_file(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    ensure_library(root)
    catalog_path = root / ".foto" / "catalog.sqlite"

    with pytest.raises(FileNotFoundError, match="foto-index"):
        load_photos(root)

    assert not catalog_path.exists()


def test_ui_without_descriptor_asks_to_run_the_indexer(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()

    with pytest.raises(RuntimeError, match="foto-index"):
        load_photos(root)

    assert not (root / ".foto").exists()


def test_ui_does_not_migrate_newer_schemas(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    ensure_library(root)
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
# Validación del descriptor al abrir desde la UI (C04)
# ----------------------------------------------------------------------
def test_ui_requires_descriptor_before_opening_the_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    catalog_path = root / ".foto" / "catalog.sqlite"
    catalog_path.parent.mkdir()
    connection = sqlite3.connect(catalog_path)
    connection.execute("PRAGMA user_version = 1")
    connection.close()

    with pytest.raises(RuntimeError, match="foto-index"):
        load_photos(root)

    # No se creó el descriptor y el catálogo no se tocó.
    assert not (root / ".foto" / "library.toml").exists()
    connection = sqlite3.connect(catalog_path)
    tables = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0]
    connection.close()
    assert tables == 0


def test_ui_rejects_corrupt_descriptor(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    (root / ".foto" / "library.toml").write_text("version = [", encoding="utf-8")

    with pytest.raises(RuntimeError, match="TOML"):
        load_photos(root)


def test_ui_rejects_future_descriptor_version(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    (root / ".foto" / "library.toml").write_text(
        'version = 99\nid = "3f9c1b62-8a44-4f0e-9d1a-2c7b5e0f4a11"\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="más nueva"):
        load_photos(root)


def test_ui_rejects_descriptor_with_invalid_uuid(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    (root / ".foto" / "library.toml").write_text(
        'version = 1\nid = "no-es-un-uuid"\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="UUID"):
        load_photos(root)


def test_ui_rejects_descriptor_with_future_version_unmigrated_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    (root / ".foto" / "library.toml").write_text(
        'version = 99\nid = "3f9c1b62-8a44-4f0e-9d1a-2c7b5e0f4a11"\n',
        encoding="utf-8",
    )
    catalog_path = root / ".foto" / "catalog.sqlite"
    connection = sqlite3.connect(catalog_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(RuntimeError, match="más nueva"):
        load_photos(root)
