"""Tests del catálogo SQLite."""

from __future__ import annotations

import sqlite3

import pytest

from foto.core.errors import CatalogError, CatalogVersionError
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


# ----------------------------------------------------------------------
# Apertura en modo solo lectura (la UI)
# ----------------------------------------------------------------------
def test_open_read_only_reads_data_without_changing_the_file(tmp_path):
    root, catalog = _open_catalog(tmp_path)
    photo_hash = "sha256:" + "d" * 64
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

    path = root / ".foto" / "catalog.sqlite"
    before = path.stat()
    read_only = Catalog.open_read_only(path)
    try:
        assert read_only.counts().photos == 1
        assert read_only.user_version() == SCHEMA_VERSION
    finally:
        read_only.close()
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_size == before.st_size


def test_open_read_only_does_not_create_a_missing_catalog(tmp_path):
    path = tmp_path / "biblioteca" / ".foto" / "catalog.sqlite"

    with pytest.raises(CatalogError, match="No se pudo abrir"):
        Catalog.open_read_only(path)

    assert not path.exists()


def test_open_read_only_rejects_writes(tmp_path):
    root, catalog = _open_catalog(tmp_path)
    photo_hash = "sha256:" + "e" * 64
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
    path = root / ".foto" / "catalog.sqlite"

    read_only = Catalog.open_read_only(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_only.mark_location_present("2024/una.jpg", NOW)
    finally:
        read_only.close()


def test_open_read_only_rejects_newer_schema_without_migrating(tmp_path):
    root, catalog = _open_catalog(tmp_path)
    catalog.close()
    path = root / ".foto" / "catalog.sqlite"

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(CatalogVersionError, match="más nuevo"):
        Catalog.open_read_only(path)

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
    connection.close()


def test_open_read_only_rejects_unmigrated_schema(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 0")
    connection.close()

    with pytest.raises(CatalogError, match="foto-index"):
        Catalog.open_read_only(path)

    tables = sqlite3.connect(path).execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0]
    assert tables == 0


# ----------------------------------------------------------------------
# Migración v5: ``photo_tags.source`` admite ``'import'`` (ADR 0007)
# ----------------------------------------------------------------------
V4_PHOTO_HASH = "sha256:" + "f" * 64


def _build_v4_catalog(path):
    """Fabrica un catálogo v4 real con el esquema de la v4 y dos asignaciones."""
    from foto.storage.catalog import (
        CELL_COLUMNS_V3,
        PLACE_COLUMNS_V2,
        PLACE_COLUMNS_V3,
        SCHEMA_V1,
        SCHEMA_V2_TABLES,
        SCHEMA_V4_TAGS,
    )

    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_V1)
        connection.executescript(SCHEMA_V2_TABLES)
        for table, columns in (
            ("photos", PLACE_COLUMNS_V2 + PLACE_COLUMNS_V3),
            ("geocode_cells", CELL_COLUMNS_V3),
        ):
            existing = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in columns:
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
        connection.executescript(SCHEMA_V4_TAGS)
        connection.execute(
            "INSERT OR IGNORE INTO tags (name, name_normalized, kind, "
            "created_at, updated_at) VALUES ('fav', 'fav', 'system', ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO photos (hash, extension, metadata_status, "
            "created_at, updated_at) VALUES (?, '.jpg', 'ok', ?, ?)",
            (V4_PHOTO_HASH, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "confidence, region_x, region_y, region_w, region_h, "
            "created_at, updated_at) "
            "VALUES (?, 1, 'manual', 'confirmed', NULL, "
            "NULL, NULL, NULL, NULL, ?, ?)",
            (V4_PHOTO_HASH, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "confidence, region_x, region_y, region_w, region_h, "
            "created_at, updated_at) "
            "VALUES (?, 1, 'ml', 'suggested', 0.9, 0.1, 0.2, 0.3, 0.4, ?, ?)",
            (V4_PHOTO_HASH, NOW, NOW),
        )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    finally:
        connection.close()


def _tags_schema(connection):
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE name IN "
        "('photo_tags', 'photo_tags_assign_unique_idx', "
        "'photo_tags_photo_idx', 'photo_tags_tag_idx')"
    ).fetchall()
    return {str(row["name"]): str(row["sql"]) for row in rows}


def test_v5_migration_preserves_rows_and_accepts_import(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _build_v4_catalog(path)

    catalog = Catalog.open(path)
    try:
        assert catalog.user_version() == 5
        rows = catalog._conn.execute(
            "SELECT source, status, confidence, region_x, region_y, "
            "region_w, region_h, created_at, updated_at "
            "FROM photo_tags ORDER BY id"
        ).fetchall()
        assert [
            (
                row["source"],
                row["status"],
                row["confidence"],
                row["region_x"],
                row["region_y"],
                row["region_w"],
                row["region_h"],
                row["created_at"],
                row["updated_at"],
            )
            for row in rows
        ] == [
            ("manual", "confirmed", None, None, None, None, None, NOW, NOW),
            ("ml", "suggested", 0.9, 0.1, 0.2, 0.3, 0.4, NOW, NOW),
        ]

        # El nuevo valor del CHECK se acepta (ADR 0007).
        catalog._conn.execute(
            "INSERT INTO tags (name, name_normalized, kind, created_at, "
            "updated_at) VALUES ('Viaje', 'viaje', 'free', ?, ?)",
            (NOW, NOW),
        )
        catalog._conn.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "created_at, updated_at) VALUES (?, 2, 'import', 'confirmed', ?, ?)",
            (V4_PHOTO_HASH, NOW, NOW),
        )

        # Un valor fuera del vocabulario sigue rechazado.
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            catalog._conn.execute(
                "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
                "created_at, updated_at) VALUES (?, 1, 'xmp_import', "
                "'confirmed', ?, ?)",
                (V4_PHOTO_HASH, NOW, NOW),
            )
    finally:
        catalog.close()


def test_v5_schema_is_byte_identical_to_a_fresh_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _build_v4_catalog(path)

    migrated = Catalog.open(path)
    try:
        fresh = Catalog.open_in_memory()
        try:
            assert _tags_schema(migrated._conn) == _tags_schema(fresh._conn)
        finally:
            fresh.close()

        # Los índices quedan byte a byte iguales a los del ADR 0006 (v4):
        # se extrae su SQL del propio texto de la v4 aplicado a un catálogo
        # de rascar.
        scratch = sqlite3.connect(":memory:")
        try:
            scratch.execute(
                "CREATE TABLE photos (hash TEXT PRIMARY KEY, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            from foto.storage.catalog import SCHEMA_V4_TAGS

            scratch.executescript(SCHEMA_V4_TAGS)
            expected = {
                str(row[0]): str(row[1])
                for row in scratch.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE name LIKE 'photo_tags_%_idx'"
                ).fetchall()
            }
        finally:
            scratch.close()
        actual = {
            name: sql
            for name, sql in _tags_schema(migrated._conn).items()
            if name.endswith("_idx")
        }
        assert actual == expected
    finally:
        migrated.close()


def test_v5_migration_is_idempotent(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _build_v4_catalog(path)

    first = Catalog.open(path)
    try:
        schema_first = _tags_schema(first._conn)
    finally:
        first.close()

    second = Catalog.open(path)
    try:
        assert second.user_version() == 5
        assert _tags_schema(second._conn) == schema_first
        assert second._conn.execute(
            "SELECT COUNT(*) FROM photo_tags"
        ).fetchone()[0] == 2
    finally:
        second.close()


def test_v5_viewer_requires_migrated_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _build_v4_catalog(path)

    with pytest.raises(CatalogError, match="foto-index"):
        Catalog.open_read_only(path)
