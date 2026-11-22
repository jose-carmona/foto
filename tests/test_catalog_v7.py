"""Esquema v7 del catálogo: columna de vínculo con Apple (ADR 0009).

Cubre T3.2 del paso 3: la migración a ``photos.apple_uuid`` (desde v6, con
ruta completa desde el catálogo recién creado), el índice único parcial y
las operaciones de etiquetas importadas que consume la fase
``apple-import`` (upsert, revocación import-only).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from foto.core.errors import TagError
from foto.storage.catalog import SCHEMA_VERSION, Catalog
from foto.storage.tags import find_tag_by_name, toggle_favorite

NOW = "2026-11-22T10:00:00+00:00"
LATER = "2026-11-22T11:00:00+00:00"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
UUID = "1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D"

# Esquema v6 mínimo (ADR 0010): lo que exige la migración a v7.
SCHEMA_V6_MINIMAL = """
CREATE TABLE photos (
    hash                TEXT PRIMARY KEY,
    file_type           TEXT,
    extension           TEXT NOT NULL,
    metadata_status     TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE locations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    path            TEXT NOT NULL,
    tier            TEXT NOT NULL CHECK (tier IN ('fast', 'archive')),
    state           TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    deleted_at      TEXT,
    last_scanned_at TEXT
);

CREATE TABLE files (
    location_id     INTEGER NOT NULL REFERENCES locations(id),
    rel_path        TEXT NOT NULL,
    photo_hash      TEXT NOT NULL REFERENCES photos(hash) ON DELETE RESTRICT,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    inode           INTEGER,
    device          INTEGER,
    status          TEXT NOT NULL DEFAULT 'present',
    first_seen_at   TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL,
    missing_since   TEXT,
    PRIMARY KEY (location_id, rel_path)
);

CREATE TABLE tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    name_normalized TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL DEFAULT 'free',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX tags_system_singleton_idx ON tags(kind) WHERE kind = 'system';

CREATE TABLE photo_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_hash  TEXT NOT NULL REFERENCES photos(hash) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'manual',
    status      TEXT NOT NULL DEFAULT 'confirmed',
    confidence  REAL,
    region_x    REAL,
    region_y    REAL,
    region_w    REAL,
    region_h    REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

INSERT INTO tags (name, name_normalized, kind, created_at, updated_at)
VALUES ('fav', 'fav', 'system', '2026-01-01T00:00:00+00:00',
        '2026-01-01T00:00:00+00:00');
"""


def _build_v6_catalog(path: Path, photo_hashes: Sequence[str]) -> None:
    """Fabrica un catálogo v6 real con fotos y una ubicación."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_V6_MINIMAL)
        connection.execute(
            "INSERT INTO locations (name, path, tier, state, created_at) "
            "VALUES ('default', '/fotos', 'fast', 'active', ?)",
            (NOW,),
        )
        for photo_hash in photo_hashes:
            connection.execute(
                "INSERT INTO photos (hash, extension, created_at, updated_at) "
                "VALUES (?, '.jpg', ?, ?)",
                (photo_hash, NOW, NOW),
            )
        connection.execute("PRAGMA user_version = 6")
        connection.commit()
    finally:
        connection.close()


def _assignment_rows(catalog: Catalog) -> list[sqlite3.Row]:
    return catalog._conn.execute(
        "SELECT photo_hash, tag_id, source, status, region_x FROM photo_tags "
        "ORDER BY id"
    ).fetchall()


# ----------------------------------------------------------------------
# Migración v7
# ----------------------------------------------------------------------
def test_fresh_catalog_creates_the_v7_schema():
    catalog = Catalog.open_in_memory()
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        columns = [
            row["name"]
            for row in catalog._conn.execute("PRAGMA table_info(photos)")
        ]
        assert "apple_uuid" in columns
        indexes = [
            row["name"]
            for row in catalog._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        ]
        assert "photos_apple_uuid_idx" in indexes
    finally:
        catalog.close()


def test_v6_catalog_migrates_to_v7_preserving_rows(tmp_path):
    path = tmp_path / "catalog.sqlite"
    _build_v6_catalog(path, [HASH_A, HASH_B])

    catalog = Catalog.open(path)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        rows = catalog._conn.execute(
            "SELECT hash, apple_uuid FROM photos ORDER BY hash"
        ).fetchall()
        assert [(str(row["hash"]), row["apple_uuid"]) for row in rows] == [
            (HASH_A, None),
            (HASH_B, None),
        ]
    finally:
        catalog.close()


def test_apple_uuid_partial_unique_index(tmp_path):
    """Dos fotos no pueden compartir asset; los NULL no colisionan."""
    path = tmp_path / "catalog.sqlite"
    _build_v6_catalog(path, [HASH_A, HASH_B])
    catalog = Catalog.open(path)
    try:
        catalog.set_apple_uuid(HASH_A, UUID, NOW)
        catalog.commit()
        with pytest.raises(sqlite3.IntegrityError):
            catalog._conn.execute(
                "UPDATE photos SET apple_uuid = ? WHERE hash = ?", (UUID, HASH_B)
            )
    finally:
        catalog.close()

    # Los NULL nunca colisionan en el índice parcial.
    catalog = Catalog.open(path)
    try:
        catalog._conn.execute(
            "INSERT INTO photos (hash, extension, created_at, updated_at) "
            "VALUES (?, '.jpg', ?, ?)",
            ("sha256:" + "c" * 64, NOW, NOW),
        )
        catalog.commit()
    finally:
        catalog.close()


def test_set_apple_uuid_follows_the_current_content(tmp_path):
    """El rehash libera el vínculo del hash antiguo (contenido modificado)."""
    path = tmp_path / "catalog.sqlite"
    _build_v6_catalog(path, [HASH_A, HASH_B])
    catalog = Catalog.open(path)
    try:
        catalog.set_apple_uuid(HASH_A, UUID, NOW)
        # El contenido cambió: el mismo asset ahora vive en otro hash.
        catalog.set_apple_uuid(HASH_B, UUID, LATER)
        catalog.commit()
        rows = catalog._conn.execute(
            "SELECT hash, apple_uuid FROM photos WHERE apple_uuid IS NOT NULL"
        ).fetchall()
        assert [(str(row["hash"]), str(row["apple_uuid"])) for row in rows] == [
            (HASH_B, UUID)
        ]
    finally:
        catalog.close()


def test_set_apple_uuid_only_touches_updated_at_when_it_changes(tmp_path):
    path = tmp_path / "catalog.sqlite"
    _build_v6_catalog(path, [HASH_A])
    catalog = Catalog.open(path)
    try:
        catalog.set_apple_uuid(HASH_A, UUID, NOW)
        catalog.commit()
        catalog.set_apple_uuid(HASH_A, UUID, LATER)
        catalog.commit()
        row = catalog._conn.execute(
            "SELECT apple_uuid, updated_at FROM photos WHERE hash = ?", (HASH_A,)
        ).fetchone()
        assert str(row["updated_at"]) == NOW
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Operaciones de etiquetas importadas
# ----------------------------------------------------------------------
def test_upsert_imported_favorite_creates_once_and_respects_manual(tmp_path):
    catalog = Catalog.open_in_memory()
    try:
        catalog._conn.execute(
            "INSERT INTO photos (hash, extension, created_at, updated_at) "
            "VALUES (?, '.jpg', ?, ?)",
            (HASH_A, NOW, NOW),
        )
        assert catalog.upsert_imported_favorite(HASH_A, NOW) is True
        # Segunda pasada: la asignación ya está, no crea nada.
        assert catalog.upsert_imported_favorite(HASH_A, LATER) is False

        # El dueño puso el favorito a mano (source manual): la importación
        # no lo sobrescribe ni crea una fila importada duplicada.
        toggle_favorite(catalog._conn, HASH_A, now=LATER)  # quita el import
        toggle_favorite(catalog._conn, HASH_A, now=LATER)  # pone el manual
        assert catalog.upsert_imported_favorite(HASH_A, LATER) is False
        rows = _assignment_rows(catalog)
        assert [(str(r["source"]), str(r["status"])) for r in rows] == [
            ("manual", "confirmed")
        ]
        # Y la revocación import-only tampoco toca la fila manual.
        assert catalog.revoke_imported_favorite(HASH_A) is False
        assert len(_assignment_rows(catalog)) == 1
    finally:
        catalog.close()


def test_revoke_imported_favorite_only_touches_import_rows(tmp_path):
    catalog = Catalog.open_in_memory()
    try:
        catalog._conn.execute(
            "INSERT INTO photos (hash, extension, created_at, updated_at) "
            "VALUES (?, '.jpg', ?, ?)",
            (HASH_A, NOW, NOW),
        )
        toggle_favorite(catalog._conn, HASH_A, now=NOW)  # manual
        assert catalog.revoke_imported_favorite(HASH_A) is False
        assert len(_assignment_rows(catalog)) == 1

        catalog.upsert_imported_favorite(HASH_A, NOW)  # ignored (manual)
        assert catalog.revoke_imported_favorite(HASH_A) is False
        assert len(_assignment_rows(catalog)) == 1
    finally:
        catalog.close()


def test_ensure_person_tag_creates_and_normalizes():
    catalog = Catalog.open_in_memory()
    try:
        first = catalog.ensure_person_tag("  María   García  ", NOW)
        assert first.kind == "person"
        assert first.name == "María García"
        # Se resuelve por nombre normalizado, como el resto de etiquetas.
        resolved = find_tag_by_name(catalog._conn, "MARÍA GARCÍA")
        assert resolved is not None and resolved.id == first.id
        # Idempotente: la segunda llamada devuelve la misma etiqueta.
        again = catalog.ensure_person_tag("maría garcía", LATER)
        assert again.id == first.id
        assert again.created_at == first.created_at
    finally:
        catalog.close()


def test_ensure_person_tag_rejects_empty_names():
    catalog = Catalog.open_in_memory()
    try:
        with pytest.raises(TagError):
            catalog.ensure_person_tag("   ", NOW)
    finally:
        catalog.close()


def test_imported_person_assignment_unique_per_region():
    catalog = Catalog.open_in_memory()
    try:
        catalog._conn.execute(
            "INSERT INTO photos (hash, extension, created_at, updated_at) "
            "VALUES (?, '.jpg', ?, ?)",
            (HASH_A, NOW, NOW),
        )
        tag = catalog.ensure_person_tag("María García", NOW)
        region = (0.4, 0.4, 0.2, 0.2)
        assert (
            catalog.insert_imported_person_assignment(
                HASH_A, tag, region=region, now=NOW
            )
            is True
        )
        # Misma cara en la segunda pasada: INSERT OR IGNORE no duplica.
        assert (
            catalog.insert_imported_person_assignment(
                HASH_A, tag, region=region, now=LATER
            )
            is False
        )
        # Otra cara de la misma persona sí es otra fila (unicidad por región).
        assert (
            catalog.insert_imported_person_assignment(
                HASH_A, tag, region=(0.1, 0.1, 0.2, 0.2), now=LATER
            )
            is True
        )
        rows = catalog.imported_person_assignments(HASH_A)
        assert len(rows) == 2
        assert {row.region_x for row in rows} == {0.4, 0.1}
    finally:
        catalog.close()


def test_delete_imported_assignment_removes_the_row():
    catalog = Catalog.open_in_memory()
    try:
        catalog._conn.execute(
            "INSERT INTO photos (hash, extension, created_at, updated_at) "
            "VALUES (?, '.jpg', ?, ?)",
            (HASH_A, NOW, NOW),
        )
        tag = catalog.ensure_person_tag("María García", NOW)
        catalog.insert_imported_person_assignment(
            HASH_A, tag, region=(0.4, 0.4, 0.2, 0.2), now=NOW
        )
        (row,) = catalog.imported_person_assignments(HASH_A)
        catalog.delete_imported_assignment(row.id)
        catalog.commit()
        assert catalog.imported_person_assignments(HASH_A) == []
    finally:
        catalog.close()


def test_present_rel_paths_filters_by_location_and_status():
    catalog = Catalog.open_in_memory()
    try:
        first = catalog.insert_named_location(
            name="default", path=Path("/fotos"), tier="fast", now=NOW
        )
        second = catalog.insert_named_location(
            name="otra", path=Path("/otras"), tier="fast", now=NOW
        )
        catalog._conn.execute(
            "INSERT INTO photos (hash, extension, created_at, updated_at) "
            "VALUES (?, '.jpg', ?, ?)",
            (HASH_A, NOW, NOW),
        )
        catalog.insert_file(
            location_id=first,
            rel_path="originals/1/1A.jpeg",
            photo_hash=HASH_A,
            size=1,
            mtime_ns=1,
            inode=None,
            device=None,
            now=NOW,
        )
        catalog.insert_file(
            location_id=second,
            rel_path="originals/A/2A.jpeg",
            photo_hash=HASH_A,
            size=1,
            mtime_ns=1,
            inode=None,
            device=None,
            now=NOW,
        )
        catalog.commit()
        pairs = catalog.present_rel_paths(first)
        assert pairs == [("originals/1/1A.jpeg", HASH_A)]
    finally:
        catalog.close()
