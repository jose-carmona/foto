"""Tests del sistema de etiquetas (ADR 0006): migración v4 y ``storage/tags``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from foto.core.errors import CatalogError, TagError
from foto.core.hashing import hash_bytes
from foto.core.indexing import IndexingOptions
from foto.storage.catalog import (
    PLACE_COLUMNS_V2,
    PLACE_COLUMNS_V3,
    SCHEMA_V1,
    SCHEMA_V2_TABLES,
    SCHEMA_VERSION,
    Catalog,
)
from foto.storage.tags import (
    Tag,
    assign_tag,
    assignments_by_hash,
    favorite_tag,
    find_tag_by_name,
    list_tags,
    normalize_tag_name,
    toggle_favorite,
    unassign_tag,
)
from tests.conftest import run_index, write_image

NOW = "2026-11-11T12:00:00+00:00"
NOW_2 = "2026-11-11T13:00:00+00:00"


def _photo(suffix: str) -> str:
    return "sha256:" + suffix * 64


def _seed_photo(catalog: Catalog, suffix: str) -> str:
    photo_hash = _photo(suffix)
    catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
    catalog.commit()
    return photo_hash


# ----------------------------------------------------------------------
# Normalización de nombres (ADR 0006, Decisión 5)
# ----------------------------------------------------------------------
def test_normalize_ignores_case_but_keeps_accents():
    assert normalize_tag_name("AÑO 2024") == normalize_tag_name("año 2024")
    assert normalize_tag_name("AÑO 2024") != normalize_tag_name("anio 2024")
    assert normalize_tag_name("  AÑO   2024 ") == "año 2024"


def test_normalize_collapses_whitespace_and_strips():
    assert normalize_tag_name("  Ana\tMaría \n López  ") == "ana maría lópez"


def test_normalize_rejects_empty_and_whitespace_only_names():
    with pytest.raises(TagError, match="vacío"):
        normalize_tag_name("")
    with pytest.raises(TagError, match="vacío"):
        normalize_tag_name("   \n\t  ")


# ----------------------------------------------------------------------
# Migración v4 (ADR 0006, Decisión 9)
# ----------------------------------------------------------------------
def _create_v3_catalog(path: Path) -> None:
    """Catálogo real de la v3: sin tablas de etiquetas.

    El ``SCHEMA_V2_TABLES`` del módulo ya crea ``geocode_cells`` con el
    detalle completo (municipio, ``display_name``, ``address_json`` y
    ``zoom``), así que basta con añadir a ``photos`` las columnas v2 y v3.
    """
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_V1)
    for name, definition in PLACE_COLUMNS_V2:
        connection.execute(f"ALTER TABLE photos ADD COLUMN {name} {definition}")
    connection.executescript(SCHEMA_V2_TABLES)
    for name, definition in PLACE_COLUMNS_V3:
        connection.execute(f"ALTER TABLE photos ADD COLUMN {name} {definition}")
    connection.execute(
        "INSERT INTO photos (hash, extension, metadata_status, created_at, "
        "updated_at) VALUES (?, '.jpg', 'ok', '2026-01-01', '2026-01-01')",
        (_photo("3"),),
    )
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()


def test_migration_from_v3_creates_tags_and_fav(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _create_v3_catalog(path)

    catalog = Catalog.open(path)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        tables = {
            str(row[0])
            for row in catalog._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"tags", "photo_tags"} <= tables
        favs = catalog._conn.execute(
            "SELECT name, kind FROM tags WHERE kind = 'system'"
        ).fetchall()
        assert len(favs) == 1
        assert favs[0]["name"] == "fav"
        # Los datos previos siguen ahí.
        assert catalog.counts().photos == 1
    finally:
        catalog.close()


def test_migration_from_v3_is_idempotent(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _create_v3_catalog(path)

    first = Catalog.open(path)
    try:
        fav = favorite_tag(first._conn)
        first._conn.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (_photo("3"), fav.id, NOW, NOW),
        )
        first.commit()
    finally:
        first.close()

    second = Catalog.open(path)
    try:
        assert second.user_version() == SCHEMA_VERSION
        assert second._conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
        assert second._conn.execute(
            "SELECT COUNT(*) FROM photo_tags"
        ).fetchone()[0] == 1
    finally:
        second.close()


def test_partial_index_blocks_a_second_system_tag(tmp_path):
    _root, catalog = _open(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            catalog._conn.execute(
                "INSERT INTO tags (name, name_normalized, kind, created_at, "
                "updated_at) VALUES ('otra', 'otra', 'system', ?, ?)",
                (NOW, NOW),
            )
    finally:
        catalog.close()


def test_read_only_open_of_v3_requests_foto_index(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _create_v3_catalog(path)

    with pytest.raises(CatalogError, match="foto-index"):
        Catalog.open_read_only(path)


def test_dry_run_migrates_the_in_memory_copy_without_writing(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _create_v3_catalog(path)
    before = path.stat()

    simulated = Catalog.open_dry_run(path)
    try:
        assert simulated.user_version() == SCHEMA_VERSION
        assert simulated._conn.execute(
            "SELECT COUNT(*) FROM tags WHERE kind = 'system'"
        ).fetchone()[0] == 1
    finally:
        simulated.close()

    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_size == before.st_size
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        connection.close()


# ----------------------------------------------------------------------
# Catálogo nuevo: ``fav`` y asignaciones
# ----------------------------------------------------------------------
def _open(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    return root, Catalog.open(root / ".foto" / "catalog.sqlite")


def _seed_region_row(
    catalog: Catalog,
    photo_hash: str,
    tag_id: int,
    *,
    region: tuple[float, float, float, float],
    source: str = "manual",
    status: str = "confirmed",
    created_at: str = NOW,
) -> None:
    x, y, w, h = region
    catalog._conn.execute(
        "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
        "region_x, region_y, region_w, region_h, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_hash, tag_id, source, status, x, y, w, h, created_at, created_at),
    )
    catalog.commit()


def test_new_catalog_has_fav_as_the_only_system_tag(tmp_path):
    _root, catalog = _open(tmp_path)
    try:
        fav = favorite_tag(catalog._conn)
        assert fav.name == "fav"
        assert fav.kind == "system"
        assert len(list_tags(catalog._conn)) == 1
    finally:
        catalog.close()


def test_favorite_toggle_creates_and_removes_a_manual_confirmed_row(tmp_path):
    _root, catalog = _open(tmp_path)
    try:
        photo_hash = _seed_photo(catalog, "a")
        assert toggle_favorite(catalog._conn, photo_hash, now=NOW) is True
        rows = catalog._conn.execute(
            "SELECT source, status, region_x FROM photo_tags"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source"] == "manual"
        assert rows[0]["status"] == "confirmed"
        assert rows[0]["region_x"] is None

        assert toggle_favorite(catalog._conn, photo_hash, now=NOW_2) is False
        assert catalog._conn.execute(
            "SELECT COUNT(*) FROM photo_tags"
        ).fetchone()[0] == 0
        # La etiqueta ``fav`` sobrevive al conmutado.
        assert favorite_tag(catalog._conn).name == "fav"
    finally:
        catalog.close()


def test_full_photo_assignment_is_unique_even_with_null_region(tmp_path):
    """Trampa real del COALESCE: la región NULL también es única."""
    _root, catalog = _open(tmp_path)
    try:
        photo_hash = _seed_photo(catalog, "a")
        fav = favorite_tag(catalog._conn)
        assign_tag(catalog._conn, photo_hash, fav, now=NOW)
        assign_tag(catalog._conn, photo_hash, fav, now=NOW_2)
        assert catalog._conn.execute(
            "SELECT COUNT(*) FROM photo_tags"
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            catalog._conn.execute(
                "INSERT INTO photo_tags (photo_hash, tag_id, created_at, "
                "updated_at) VALUES (?, ?, ?, ?)",
                (photo_hash, fav.id, NOW, NOW),
            )
    finally:
        catalog.close()


def test_same_person_with_distinct_regions_coexists(tmp_path):
    """Dos caras de la misma persona en la misma foto: tres filas."""
    _root, catalog = _open(tmp_path)
    try:
        photo_hash = _seed_photo(catalog, "a")
        person = _seed_tag(catalog, "Ana", kind="person")
        _seed_region_row(catalog, photo_hash, person.id, region=(0.1, 0.1, 0.2, 0.2))
        _seed_region_row(catalog, photo_hash, person.id, region=(0.5, 0.5, 0.3, 0.3))
        # Grupo y retrato conviven: la persona también a foto completa.
        assign_tag(catalog._conn, photo_hash, person, now=NOW)
        rows = catalog._conn.execute(
            "SELECT region_x, region_y, region_w, region_h FROM photo_tags "
            "ORDER BY region_x IS NULL, region_x"
        ).fetchall()
        assert len(rows) == 3
        assert rows[0]["region_x"] == 0.1
        assert rows[1]["region_x"] == 0.5
        assert rows[2]["region_x"] is None
    finally:
        catalog.close()


def test_assign_confirms_a_suggestion_without_stomping_source_or_created_at():
    catalog = Catalog.open_in_memory()
    try:
        photo_hash = _seed_photo(catalog, "a")
        person = _seed_tag(catalog, "Ana", kind="person")
        # Sugerencia futura del ML sobre la foto completa, sembrada por SQL.
        catalog._conn.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "confidence, created_at, updated_at) "
            "VALUES (?, ?, 'ml', 'suggested', 0.9, ?, ?)",
            (photo_hash, person.id, NOW, NOW),
        )
        catalog.commit()

        assign_tag(catalog._conn, photo_hash, person, now=NOW_2)
        row = catalog._conn.execute("SELECT * FROM photo_tags").fetchone()
        assert row["status"] == "confirmed"
        assert row["source"] == "ml"
        assert row["confidence"] == 0.9
        assert row["created_at"] == NOW
        assert row["updated_at"] == NOW_2
    finally:
        catalog.close()


def test_assign_on_a_rejected_row_is_refused():
    catalog = Catalog.open_in_memory()
    try:
        photo_hash = _seed_photo(catalog, "a")
        person = _seed_tag(catalog, "Ana", kind="person")
        # Una fila 'rejected' de foto completa bloquea la reasignación:
        # reactivarla es borrarla, decisión explícita del usuario.
        catalog._conn.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "created_at, updated_at) VALUES (?, ?, 'ml', 'rejected', ?, ?)",
            (photo_hash, person.id, NOW, NOW),
        )
        catalog.commit()
        with pytest.raises(TagError, match="rechazada"):
            assign_tag(catalog._conn, photo_hash, person, now=NOW_2)
    finally:
        catalog.close()


def test_unassign_removes_only_the_full_photo_row():
    catalog = Catalog.open_in_memory()
    try:
        photo_hash = _seed_photo(catalog, "a")
        person = _seed_tag(catalog, "Ana", kind="person")
        _seed_region_row(catalog, photo_hash, person.id, region=(0.0, 0.0, 0.4, 0.4))
        assign_tag(catalog._conn, photo_hash, person, now=NOW)

        assert unassign_tag(catalog._conn, photo_hash, person) is True
        assert unassign_tag(catalog._conn, photo_hash, person) is False
        rows = catalog._conn.execute("SELECT * FROM photo_tags").fetchall()
        assert len(rows) == 1
        assert rows[0]["region_x"] == 0.0
    finally:
        catalog.close()


def test_assign_requires_an_existing_tag():
    catalog = Catalog.open_in_memory()
    try:
        photo_hash = _seed_photo(catalog, "a")
        ghost = Tag(id=999, name="fantasma", kind="free", created_at=NOW, updated_at=NOW)
        with pytest.raises(CatalogError, match="asignación"):
            assign_tag(catalog._conn, photo_hash, ghost, now=NOW)
    finally:
        catalog.close()


def test_reading_functions_load_assignments_by_hash():
    catalog = Catalog.open_in_memory()
    try:
        photo_hash = _seed_photo(catalog, "a")
        other = _photo("b")
        _seed_photo(catalog, "b")
        person = _seed_tag(catalog, "Ana", kind="person")
        place = _seed_tag(catalog, "Lisboa", kind="place")
        _seed_region_row(catalog, photo_hash, person.id, region=(0.0, 0.0, 0.4, 0.4))
        assign_tag(catalog._conn, photo_hash, place, now=NOW)

        assignments = assignments_by_hash(catalog._conn)
        assert set(assignments) == {photo_hash}  # el hash sin filas no aparece
        pair = {(item.tag_name, item.kind, item.status, item.source)
                for item in assignments[photo_hash]}
        assert pair == {
            ("Ana", "person", "confirmed", "manual"),
            ("Lisboa", "place", "confirmed", "manual"),
        }
        assert find_tag_by_name(catalog._conn, "  ANA ").name == "Ana"
        assert find_tag_by_name(catalog._conn, "no-existe") is None
        assert other not in assignments_by_hash(catalog._conn)
    finally:
        catalog.close()


def _seed_tag(
    catalog: Catalog, name: str, *, kind: str = "free"
) -> Tag:
    catalog._conn.execute(
        "INSERT INTO tags (name, name_normalized, kind, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, normalize_tag_name(name), kind, NOW, NOW),
    )
    catalog.commit()
    found = find_tag_by_name(catalog._conn, name)
    assert found is not None
    return found


# ----------------------------------------------------------------------
# Reconciliación del indexador (ADR 0006, Decisión 12)
# ----------------------------------------------------------------------
def test_prune_cascades_photo_tags_and_keeps_orphan_tags(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "una.jpg", b"version-1")
    run_index(root)
    photo_hash = hash_bytes(path.read_bytes())

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        toggle_favorite(catalog._conn, photo_hash, now=NOW)
    finally:
        catalog.close()
    path.unlink()
    run_index(root, options=IndexingOptions(prune=True, prune_after_days=0))

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        assert catalog.counts().photos == 0
        # El CASCADE se lleva la asignación; la etiqueta huérfana se
        # conserva: es taxonomía del usuario, no basura.
        assert catalog._conn.execute(
            "SELECT COUNT(*) FROM photo_tags"
        ).fetchone()[0] == 0
        assert favorite_tag(catalog._conn).name == "fav"
    finally:
        catalog.close()


def test_content_change_never_moves_assignments_between_hashes(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "una.jpg", b"version-1")
    run_index(root)
    old_hash = hash_bytes(path.read_bytes())

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        toggle_favorite(catalog._conn, old_hash, now=NOW)
    finally:
        catalog.close()

    path.write_bytes(b"version-2")
    run_index(root)
    new_hash = hash_bytes(b"version-2")

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        # El contenido nuevo es una foto nueva, sin etiquetas; la vieja
        # conserva la suya hasta que la poda la borre.
        assert set(assignments_by_hash(catalog._conn)) == {old_hash}
        assert new_hash not in assignments_by_hash(catalog._conn)
    finally:
        catalog.close()

    run_index(root, options=IndexingOptions(prune=True, prune_after_days=0))
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        assert catalog.counts().photos == 1
        assert assignments_by_hash(catalog._conn) == {}
    finally:
        catalog.close()
