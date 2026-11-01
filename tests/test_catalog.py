"""Tests del catálogo SQLite."""

from __future__ import annotations

import sqlite3

import pytest

from foto.core.errors import CatalogVersionError
from foto.core.library import load_from_catalog
from foto.storage.catalog import SCHEMA_VERSION, Catalog

NOW = "2026-10-30T12:00:00+00:00"


def _open_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    return root, Catalog.open(root / ".foto" / "catalog.sqlite")


def test_schema_version_and_wal(tmp_path):
    _root, catalog = _open_catalog(tmp_path)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        assert catalog.journal_mode().lower() == "wal"
        tables = {
            row[0]
            for row in catalog._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"photos", "locations", "scan_runs"} <= tables
    finally:
        catalog.close()


def test_newer_schema_raises(tmp_path):
    root, catalog = _open_catalog(tmp_path)
    catalog.close()
    path = root / ".foto" / "catalog.sqlite"

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(CatalogVersionError, match="más nuevo"):
        Catalog.open(path)


def test_dry_run_copies_existing_catalog_without_writing(tmp_path):
    root, catalog = _open_catalog(tmp_path)
    try:
        catalog.insert_photo("sha256:" + "a" * 64, ".jpg", "JPEG", NOW)
        catalog.insert_location(
            rel_path="2024/una.jpg",
            photo_hash="sha256:" + "a" * 64,
            size=10,
            mtime_ns=1,
            inode=1,
            device=1,
            now=NOW,
        )
        catalog.commit()
    finally:
        catalog.close()

    simulated = Catalog.open_dry_run(root / ".foto" / "catalog.sqlite")
    try:
        assert simulated.counts().photos == 1
        simulated.insert_photo("sha256:" + "b" * 64, ".jpg", "JPEG", NOW)
        simulated.commit()
        assert simulated.counts().photos == 2
    finally:
        simulated.close()

    real = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        assert real.counts().photos == 1
    finally:
        real.close()


def test_dry_run_without_catalog_uses_memory_schema(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    path = root / ".foto" / "catalog.sqlite"

    catalog = Catalog.open_dry_run(path)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        assert catalog.counts().photos == 0
    finally:
        catalog.close()

    assert not path.exists()


def test_load_from_catalog_exposes_content_hash(tmp_path):
    root, catalog = _open_catalog(tmp_path)
    photo_hash = "sha256:" + "c" * 64
    (root / "2024").mkdir()
    (root / "2024" / "una.jpg").write_bytes(b"datos")
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
        photos = load_from_catalog(catalog, root)
    finally:
        catalog.close()

    assert len(photos) == 1
    assert photos[0].photo_hash == photo_hash
