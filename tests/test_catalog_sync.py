"""Sincronización config ↔ catálogo (T3.2, ADR 0010 Decisión 3).

Alta, cambio, baja (marca ubicación **y** filas de fotos, no borra nada),
reactivación, renombrado (baja + alta), idempotencia y orden determinista.
Una ubicación ausente jamás se da de baja aquí: la baja solo la provoca la
config.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from foto.storage.catalog import Catalog
from foto.storage.config import Location
from foto.storage.sync import sync_locations

NOW = datetime.now(UTC).replace(microsecond=0)
NOW_TEXT = NOW.isoformat()
HASH_A = "sha256:" + "a" * 64


def _catalog_with_declared(tmp_path, declared):
    catalog = Catalog.open_in_memory()
    sync_locations(catalog, declared, NOW)
    return catalog


def _file_rows(catalog: Catalog) -> dict[str, str]:
    rows = catalog._conn.execute(
        "SELECT rel_path, status FROM files ORDER BY rel_path"
    ).fetchall()
    return {str(row["rel_path"]): str(row["status"]) for row in rows}


def test_first_sync_registers_declared_locations_in_config_order():
    catalog = Catalog.open_in_memory()
    report = sync_locations(
        catalog,
        (
            Location("ssd", Path("/tmp/ssd"), "fast"),
            Location("usb", Path("/tmp/usb"), "archive"),
        ),
        NOW,
    )
    assert report.added == ("ssd", "usb")
    rows = {row.name: row for row in catalog.list_named_locations()}
    assert rows["ssd"].state == "active"
    assert rows["usb"].tier == "archive"


def test_sync_is_idempotent_with_the_same_config(tmp_path):
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)

    second = sync_locations(catalog, declared, NOW)

    assert second.changed is False
    rows = catalog.list_named_locations()
    assert len(rows) == 1 and rows[0].state == "active"


def test_path_or_tier_change_is_reported_and_updated(tmp_path):
    declared = (Location("ssd", tmp_path / "vieja", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)
    location_id = catalog.get_named_location("ssd").id
    catalog.insert_photo(HASH_A, ".jpg", None, NOW_TEXT)
    catalog.insert_file(
        location_id=location_id, rel_path="a.jpg", photo_hash=HASH_A,
        size=5, mtime_ns=1, inode=1, device=1, now=NOW_TEXT,
    )
    catalog.commit()

    report = sync_locations(
        catalog, (Location("ssd", tmp_path / "nueva", "archive"),), NOW
    )

    assert report.path_changed == ("ssd",)
    row = catalog.get_named_location("ssd")
    assert row.path == tmp_path / "nueva"
    assert row.tier == "archive"
    # Las filas siguen siendo válidas: las reconciliará el escaneo.
    assert _file_rows(catalog) == {"a.jpg": "present"}


def test_removal_deactivates_location_and_marks_its_rows_deleted(tmp_path):
    """Baja por config: la ubicación y sus filas pasan a ``deleted``, nada se borra."""
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)
    location_id = catalog.get_named_location("ssd").id
    catalog.insert_photo(HASH_A, ".jpg", None, NOW_TEXT)
    catalog.insert_file(
        location_id=location_id, rel_path="a.jpg", photo_hash=HASH_A,
        size=5, mtime_ns=1, inode=1, device=1, now=NOW_TEXT,
    )
    catalog.commit()

    report = sync_locations(catalog, (), NOW)

    assert report.removed == ("ssd",)
    row = catalog.get_named_location("ssd")
    assert row.state == "deleted" and row.deleted_at == NOW_TEXT
    assert _file_rows(catalog) == {"a.jpg": "deleted"}


def test_already_deleted_location_not_in_config_is_not_reported_again(tmp_path):
    """La baja es un evento del diff: idempotente, sin re-informes."""
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)
    sync_locations(catalog, (), NOW)

    second = sync_locations(catalog, (), NOW)

    assert second.changed is False


def test_reactivation_returns_to_active_without_restoring_rows(tmp_path):
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)
    location_id = catalog.get_named_location("ssd").id
    catalog.insert_photo(HASH_A, ".jpg", None, NOW_TEXT)
    catalog.insert_file(
        location_id=location_id, rel_path="a.jpg", photo_hash=HASH_A,
        size=5, mtime_ns=1, inode=1, device=1, now=NOW_TEXT,
    )
    catalog.commit()
    sync_locations(catalog, (), NOW)

    report = sync_locations(catalog, declared, NOW)

    assert report.reactivated == ("ssd",)
    assert catalog.get_named_location("ssd").state == "active"
    assert catalog.get_named_location("ssd").deleted_at is None
    # No restaura filas por sí sola: las restaura el escaneo de la ubicación.
    assert _file_rows(catalog) == {"a.jpg": "deleted"}


def test_rename_is_a_removal_and_an_addition(tmp_path):
    """Renombrar es baja + alta del mismo diff, sin caso especial."""
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)

    report = sync_locations(
        catalog, (Location("principal", tmp_path / "ssd", "fast"),), NOW
    )

    assert report.removed == ("ssd",)
    assert report.added == ("principal",)
    rows = {row.name: row.state for row in catalog.list_named_locations()}
    assert rows == {"ssd": "deleted", "principal": "active"}
    # La ubicación reactivada/creada comparte path: el escaneo repuebla por
    # hash (deduplicación natural).


def test_declared_after_reactivation_with_a_new_path(tmp_path):
    declared = (Location("ssd", tmp_path / "vieja", "fast"),)
    catalog = _catalog_with_declared(tmp_path, declared)
    sync_locations(catalog, (), NOW)

    report = sync_locations(
        catalog, (Location("ssd", tmp_path / "nueva", "fast"),), NOW
    )

    assert report.reactivated == ("ssd",)
    assert report.path_changed == ("ssd",)
    row = catalog.get_named_location("ssd")
    assert row.state == "active" and row.path == tmp_path / "nueva"


def test_sync_never_touches_the_filesystem(tmp_path):
    """La sincronización vive en la BD: no crea, ni borra, ni mira discos."""
    declared = (Location("ssd", tmp_path / "ssd", "fast"),)
    catalog = Catalog.open_in_memory()
    sync_locations(catalog, declared, NOW)
    sync_locations(catalog, (), NOW)

    assert not (tmp_path / "ssd").exists()
