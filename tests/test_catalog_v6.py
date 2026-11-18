"""Esquema v6 del catálogo: migración, clave compuesta y ubicaciones (ADR 0010).

Cubre T3.1 (migración v5 → v6 con la ubicación declarada única y su nombre
real; ``default`` solo con config v1) y T3.9 (clave compuesta
``(location_id, rel_path)``: mismo ``rel_path`` en dos ubicaciones con la
deduplicación por hash intacta).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from foto.core.errors import CatalogError
from foto.storage.catalog import (
    SCHEMA_V1,
    SCHEMA_VERSION,
    Catalog,
)
from foto.storage.config import Location

NOW = "2026-10-30T12:00:00+00:00"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def _build_v5_catalog(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """Fabrica un catálogo v5 real: fotos y apariciones de raíz única.

    ``rows`` son tuplas ``(rel_path, photo_hash, status)`` con la semántica
    de la tabla ``locations`` del esquema v5.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_V1)
        # Columnas de lugar del esquema v2/v3 (un catálogo v5 real las lleva).
        from foto.storage.catalog import PLACE_COLUMNS_V2, PLACE_COLUMNS_V3

        existing = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(photos)")
        }
        for name, definition in PLACE_COLUMNS_V2 + PLACE_COLUMNS_V3:
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE photos ADD COLUMN {name} {definition}"
                )
        connection.execute("PRAGMA user_version = 5")
        hashes = sorted({photo_hash for _, photo_hash, _ in rows})
        for photo_hash in hashes:
            connection.execute(
                "INSERT INTO photos (hash, extension, metadata_status, "
                "created_at, updated_at) VALUES (?, '.jpg', 'pending', ?, ?)",
                (photo_hash, NOW, NOW),
            )
        for rel_path, photo_hash, status in rows:
            connection.execute(
                "INSERT INTO locations (rel_path, photo_hash, size, mtime_ns, "
                "status, first_seen_at, last_indexed_at) "
                "VALUES (?, ?, 5, 1, ?, ?, ?)",
                (rel_path, photo_hash, status, NOW, NOW),
            )
        connection.commit()
    finally:
        connection.close()


def _named_rows(catalog: Catalog) -> dict[str, object]:
    rows = catalog._conn.execute(
        "SELECT name, path, tier, state FROM locations"
    ).fetchall()
    return {str(row["name"]): dict(row) for row in rows}


def test_fresh_catalog_creates_the_v6_schema():
    catalog = Catalog.open_in_memory()
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        tables = {
            str(row[0])
            for row in catalog._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"photos", "files", "locations"} <= tables
        # Clave compuesta de ``files`` (T3.9).
        columns = [
            (row["name"], row["pk"])
            for row in catalog._conn.execute("PRAGMA table_info(files)")
        ]
        pk = {name: position for name, position in columns if position}
        assert pk == {"location_id": 1, "rel_path": 2}
    finally:
        catalog.close()


def test_v5_with_rows_migrates_to_the_declared_location_real_name(tmp_path):
    """Las filas heredadas van a la ubicación declarada única, con su nombre."""
    path = tmp_path / "catalog.sqlite"
    _build_v5_catalog(
        path,
        [("2024/una.jpg", HASH_A, "present"), ("2025/dos.jpg", HASH_B, "missing")],
    )
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)

    catalog = Catalog.open(path, declared_locations=declared)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        named = _named_rows(catalog)
        assert set(named) == {"ssd"}
        assert named["ssd"]["state"] == "active"
        assert named["ssd"]["path"] == str(tmp_path / "ssd")
        rows = catalog._conn.execute(
            "SELECT rel_path, location_id, status FROM files ORDER BY rel_path"
        ).fetchall()
        ssd_id = catalog.get_named_location("ssd").id
        assert [(r["rel_path"], r["location_id"], r["status"]) for r in rows] == [
            ("2024/una.jpg", ssd_id, "present"),
            ("2025/dos.jpg", ssd_id, "missing"),
        ]
    finally:
        catalog.close()


def test_v5_migration_with_a_v1_config_uses_default(tmp_path):
    """Solo la config v1 produce la ubicación ``default`` (T1.6)."""
    path = tmp_path / "catalog.sqlite"
    _build_v5_catalog(path, [("una.jpg", HASH_A, "present")])
    declared = (Location("default", tmp_path / "fototeca", "fast"),)

    catalog = Catalog.open(path, declared_locations=declared)
    try:
        assert set(_named_rows(catalog)) == {"default"}
    finally:
        catalog.close()


def test_v5_with_rows_and_several_declared_errors_before_migrating(tmp_path):
    """Filas heredadas + varias declaradas: error explícito, nada migrado."""
    path = tmp_path / "catalog.sqlite"
    _build_v5_catalog(path, [("una.jpg", HASH_A, "present")])
    declared = (
        Location("ssd", tmp_path / "ssd", "fast"),
        Location("usb", tmp_path / "usb", "archive"),
    )

    with pytest.raises(CatalogError, match="con solo la ubicación original declarada"):
        Catalog.open(path, declared_locations=declared)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        # La tabla legacy sigue intacta: la migración no empezó.
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "locations" in tables and "files" not in tables
    finally:
        connection.close()


def test_v5_with_rows_and_no_declaration_errors(tmp_path):
    """Filas heredadas sin ninguna ubicación declarada: error explícito."""
    path = tmp_path / "catalog.sqlite"
    _build_v5_catalog(path, [("una.jpg", HASH_A, "present")])

    with pytest.raises(CatalogError, match="no declara ninguna ubicación"):
        Catalog.open(path, declared_locations=())

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        connection.close()


def test_v5_without_rows_migrates_without_declaration(tmp_path):
    """Un catálogo v5 vacío no exige declaración: no hay filas que asignar."""
    path = tmp_path / "catalog.sqlite"
    _build_v5_catalog(path, [])

    catalog = Catalog.open(path, declared_locations=())
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        assert _named_rows(catalog) == {}
        assert catalog.counts().locations == 0
    finally:
        catalog.close()


def test_v6_migration_preserves_the_file_data(tmp_path):
    """Firma, estado y fechas de las filas heredadas sobreviven al renombre."""
    path = tmp_path / "catalog.sqlite"
    _build_v5_catalog(path, [("2024/una.jpg", HASH_A, "missing")])
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE locations SET missing_since = '2026-10-01T00:00:00+00:00', "
        "size = 77, mtime_ns = 42 WHERE rel_path = '2024/una.jpg'"
    )
    connection.commit()
    connection.close()

    catalog = Catalog.open(path, declared_locations=(Location("ssd", tmp_path, "fast"),))
    try:
        row = catalog.get_file(catalog.get_named_location("ssd").id, "2024/una.jpg")
        assert row is not None
        assert row.status == "missing"
        assert row.size == 77
        assert row.mtime_ns == 42
        cursor = catalog._conn.execute(
            "SELECT missing_since FROM files WHERE rel_path = '2024/una.jpg'"
        )
        assert cursor.fetchone()["missing_since"] == "2026-10-01T00:00:00+00:00"
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Clave compuesta ``(location_id, rel_path)`` (T3.9)
# ----------------------------------------------------------------------
def test_same_rel_path_in_two_locations_coexists(tmp_path):
    """Un mismo ``rel_path`` vive en dos ubicaciones sin colisión."""
    catalog = Catalog.open_in_memory()
    try:
        ssd = catalog.insert_named_location(
            name="ssd", path=tmp_path / "ssd", tier="fast", now=NOW
        )
        usb = catalog.insert_named_location(
            name="usb", path=tmp_path / "usb", tier="archive", now=NOW
        )
        catalog.insert_photo(HASH_A, ".jpg", None, NOW)
        catalog.insert_file(
            location_id=ssd, rel_path="2024/x.jpg", photo_hash=HASH_A,
            size=5, mtime_ns=1, inode=1, device=1, now=NOW,
        )
        catalog.insert_file(
            location_id=usb, rel_path="2024/x.jpg", photo_hash=HASH_A,
            size=5, mtime_ns=1, inode=1, device=1, now=NOW,
        )

        assert catalog.get_file(ssd, "2024/x.jpg").location_id == ssd
        assert catalog.get_file(usb, "2024/x.jpg").location_id == usb
        # Deduplicación por hash intacta: una sola foto, dos apariciones.
        assert catalog.counts().photos == 1
        assert catalog.counts().locations == 2
        assert catalog.counts().duplicate_photos == 1

        with pytest.raises(sqlite3.IntegrityError):
            catalog.insert_file(
                location_id=ssd, rel_path="2024/x.jpg", photo_hash=HASH_A,
                size=5, mtime_ns=1, inode=1, device=1, now=NOW,
            )
    finally:
        catalog.close()


def test_files_status_accepts_deleted_and_rejects_unknown(tmp_path):
    """El estado de ``files`` gana ``deleted``; lo demás lo rechaza el CHECK."""
    catalog = Catalog.open_in_memory()
    try:
        location = catalog.insert_named_location(
            name="ssd", path=tmp_path / "ssd", tier="fast", now=NOW
        )
        catalog.insert_photo(HASH_A, ".jpg", None, NOW)
        catalog.insert_file(
            location_id=location, rel_path="a.jpg", photo_hash=HASH_A,
            size=5, mtime_ns=1, inode=1, device=1, now=NOW,
        )
        catalog._conn.execute(
            "UPDATE files SET status = 'deleted' WHERE rel_path = 'a.jpg'"
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            catalog._conn.execute(
                "UPDATE files SET status = 'gone' WHERE rel_path = 'a.jpg'"
            )
    finally:
        catalog.close()


def test_named_location_state_is_closed(tmp_path):
    """``state`` solo admite ``active``/``deleted``; ``tier`` fast/archive."""
    catalog = Catalog.open_in_memory()
    try:
        location = catalog.insert_named_location(
            name="ssd", path=tmp_path / "ssd", tier="fast", now=NOW
        )
        catalog.deactivate_named_location(location_id=location, now=NOW)
        row = catalog.get_named_location("ssd")
        assert row.state == "deleted" and row.deleted_at is not None

        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            catalog._conn.execute(
                "UPDATE locations SET state = 'gone' WHERE id = ?", (location,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            catalog._conn.execute(
                "UPDATE locations SET tier = 'ssd' WHERE id = ?", (location,)
            )
        # El nombre es único y estable: identidad de la ubicación.
        with pytest.raises(sqlite3.IntegrityError):
            catalog.insert_named_location(
                name="ssd", path=tmp_path / "otro", tier="fast", now=NOW
            )
    finally:
        catalog.close()
