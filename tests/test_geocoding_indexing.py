"""Tests del pipeline de fases: geocodificación, caché por celda y reanudación."""

from __future__ import annotations

import sqlite3

import pytest

from foto.core.errors import (
    CatalogError,
    GeocodingRejectedError,
    GeocodingResponseError,
    IndexingError,
)
from foto.core.geocoding import REVERSE_ZOOM, Place, PlaceRecord
from foto.core.indexing import INDEXING_PHASES, Indexer, IndexingOptions
from foto.core.metadata import PhotoMetadata
from foto.storage.catalog import (
    PLACE_COLUMNS_V2,
    SCHEMA_VERSION,
    Catalog,
)
from tests.conftest import FakeExtractor, FakeGeocoder, read_photos, run_index, write_image
from tests.test_geocoding import make_place

MADRID = make_place()
MADRID_RECORD = PlaceRecord(place=MADRID)


def _photos_with_place(root):
    return read_photos(root)


def _place_fields(photo: dict[str, object]) -> dict[str, object]:
    return {
        key: photo[key]
        for key in (
            "place_status",
            "place_error",
            "place_country",
            "place_country_code",
            "place_state",
            "place_city",
            "place_municipality",
            "place_display_name",
            "place_address_json",
        )
    }


def _place_status(root, rel_path=None):
    rows = read_photos(root)
    if rel_path is None:
        return {hash_: row["place_status"] for hash_, row in rows.items()}
    return next(iter(rows.values()))["place_status"]


# ----------------------------------------------------------------------
# Esquema y migración
# ----------------------------------------------------------------------
SCHEMA_V1_DDL = """
CREATE TABLE photos (
    hash                TEXT PRIMARY KEY,
    file_type           TEXT,
    extension           TEXT NOT NULL,
    width               INTEGER,
    height              INTEGER,
    captured_at         TEXT,
    camera_make         TEXT,
    camera_model        TEXT,
    lens_model          TEXT,
    iso                 INTEGER,
    f_number            REAL,
    exposure_time       REAL,
    focal_length        REAL,
    gps_latitude        REAL,
    gps_longitude       REAL,
    orientation         INTEGER,
    metadata_status     TEXT NOT NULL DEFAULT 'pending'
                        CHECK (metadata_status IN ('pending', 'ok', 'error')),
    metadata_error      TEXT,
    metadata_updated_at TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE locations (
    rel_path        TEXT PRIMARY KEY,
    photo_hash      TEXT NOT NULL REFERENCES photos(hash) ON DELETE RESTRICT,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    inode           INTEGER,
    device          INTEGER,
    status          TEXT NOT NULL DEFAULT 'present'
                    CHECK (status IN ('present', 'missing')),
    first_seen_at   TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL,
    missing_since   TEXT
);

CREATE INDEX locations_photo_hash_idx ON locations(photo_hash);
CREATE INDEX locations_status_idx ON locations(status);

CREATE TABLE scan_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    mode           TEXT NOT NULL,
    status         TEXT NOT NULL,
    scanned        INTEGER NOT NULL DEFAULT 0,
    unchanged      INTEGER NOT NULL DEFAULT 0,
    added          INTEGER NOT NULL DEFAULT 0,
    modified       INTEGER NOT NULL DEFAULT 0,
    missing        INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0,
    hashed_bytes   INTEGER NOT NULL DEFAULT 0
);
"""


def _create_v1_catalog(path):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_V1_DDL)
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO photos (hash, extension, gps_latitude, gps_longitude, "
        "metadata_status, created_at, updated_at) VALUES (?, '.jpg', 40.4168, "
        "-3.7038, 'ok', '2026-01-01', '2026-01-01')",
        ("sha256:" + "1" * 64,),
    )
    connection.commit()
    connection.close()


def test_new_catalog_has_geocode_cells_and_run_phases(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        tables = {
            str(row[0])
            for row in catalog._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"geocode_cells", "run_phases"} <= tables
        columns = {
            str(row[1]) for row in catalog._conn.execute("PRAGMA table_info(photos)")
        }
        for name in (
            "place_status",
            "place_error",
            "place_country",
            "place_country_code",
            "place_state",
            "place_city",
            "place_updated_at",
        ):
            assert name in columns
    finally:
        catalog.close()


def test_migration_from_v1_keeps_data_and_bumps_version(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    path = root / ".foto" / "catalog.sqlite"
    root.joinpath(".foto").mkdir()
    _create_v1_catalog(path)

    catalog = Catalog.open(path)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        row = catalog._conn.execute("SELECT * FROM photos").fetchone()
        assert row["gps_latitude"] == 40.4168
        assert row["place_status"] == "pending"
        candidates = catalog.count_geocode_candidates(full=False)
        assert candidates == 1
    finally:
        catalog.close()


def test_migration_is_idempotent(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"

    first = Catalog.open(path)
    first.put_place_cell(
        40.417, -3.704, MADRID_RECORD, zoom=REVERSE_ZOOM, now="2026-01-01T00:00:00+00:00"
    )
    first.commit()
    first.close()

    second = Catalog.open(path)
    try:
        record = second.get_place_cell(40.417, -3.704, min_zoom=REVERSE_ZOOM)
        assert record == MADRID_RECORD
    finally:
        second.close()


def test_newer_schema_still_aborts(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(CatalogError, match="más nuevo"):
        Catalog.open(path)


# Esquema v2 real: ``geocode_cells`` sin el detalle de la revisión.
SCHEMA_V2_CELLS_DDL = """
CREATE TABLE geocode_cells (
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    country       TEXT,
    country_code  TEXT,
    state         TEXT,
    city          TEXT,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (latitude, longitude)
);

CREATE TABLE run_phases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES scan_runs(id),
    phase         TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT,
    candidates    INTEGER,
    processed     INTEGER,
    errors        INTEGER,
    cache_hits    INTEGER,
    http_requests INTEGER,
    recorded_at   TEXT NOT NULL
);
"""


def _create_v2_catalog(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_V1_DDL)
    # Columnas de lugar de la v2 (la revisión v3 añade las suyas encima).
    for name, definition in PLACE_COLUMNS_V2:
        connection.execute(f"ALTER TABLE photos ADD COLUMN {name} {definition}")
    connection.executescript(SCHEMA_V2_CELLS_DDL)
    connection.execute(
        "INSERT INTO photos (hash, extension, gps_latitude, gps_longitude, "
        "metadata_status, created_at, updated_at) VALUES (?, '.jpg', 44.652, "
        "-1.198, 'ok', '2026-01-01', '2026-01-01')",
        ("sha256:" + "9" * 64,),
    )
    connection.execute(
        "INSERT INTO photos (hash, extension, metadata_status, created_at, "
        "updated_at) VALUES (?, '.jpg', 'ok', '2026-01-01', '2026-01-01')",
        ("sha256:" + "8" * 64,),
    )
    # La celda pobre que motivó la revisión: solo país, resuelta a zoom 10.
    connection.execute(
        "INSERT INTO geocode_cells (latitude, longitude, country, country_code, "
        "state, city, fetched_at) VALUES (44.652, -1.198, 'Francia', 'fr', "
        "NULL, NULL, '2026-01-01')"
    )
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()


def test_migration_from_v2_adds_detail_and_marks_old_cells(tmp_path):
    """La migración v3 añade columnas y marca las celdas con su zoom real.

    Las celdas guardadas bajo el esquema v2 se resolvieron con zoom 10:
    quedan marcadas así para que la siguiente pasada las vuelva a pedir
    con el zoom actual y refresque el lugar (caso de Arcachón).
    """
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _create_v2_catalog(path)

    catalog = Catalog.open(path)
    try:
        assert catalog.user_version() == SCHEMA_VERSION
        cell_columns = {
            str(row[1])
            for row in catalog._conn.execute("PRAGMA table_info(geocode_cells)")
        }
        assert {"municipality", "display_name", "address_json", "zoom"} <= cell_columns
        photo_columns = {
            str(row[1]) for row in catalog._conn.execute("PRAGMA table_info(photos)")
        }
        assert {
            "place_municipality",
            "place_display_name",
            "place_address_json",
        } <= photo_columns
        # La celda vieja conserva sus datos pero queda con el zoom 10.
        row = catalog._conn.execute(
            "SELECT * FROM geocode_cells"
        ).fetchone()
        assert row["country"] == "Francia"
        assert row["zoom"] == 10
        assert catalog.get_place_cell(44.652, -1.198, min_zoom=REVERSE_ZOOM) is None
        # Y los datos de fotos siguen ahí: quedan pendientes de geocodificar
        # hasta que la siguiente pasada las repone.
        rows = {
            r["hash"]: r["place_status"]
            for r in catalog._conn.execute("SELECT hash, place_status FROM photos")
        }
        assert rows["sha256:" + "9" * 64] == "pending"
        assert rows["sha256:" + "8" * 64] == "pending"
    finally:
        catalog.close()


def test_next_run_refreshes_a_poor_cell_without_full(tmp_path):
    """Fin del caso real: tras la migración, la pasada repone la jerarquía."""
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    path = root / ".foto" / "catalog.sqlite"
    _create_v2_catalog(path)

    geocoder = FakeGeocoder()
    summary = run_index(root, extractor=FakeExtractor(), geocoder=geocoder)

    # La celda pobre se volvió a pedir con el zoom actual y la foto quedó
    # con el lugar completo, sin necesidad de --full.
    assert geocoder.calls == [(44.652, -1.198)]
    assert summary.geocode.geocoded_ok == 1
    assert summary.geocode.cache_hits == 0
    catalog = Catalog.open(path)
    try:
        row = catalog._conn.execute("SELECT * FROM photos WHERE place_status = 'ok'").fetchone()
        assert row["place_country"] == "España"
        assert row["place_city"] == "Madrid"
        cell = catalog._conn.execute("SELECT zoom FROM geocode_cells").fetchone()
        assert cell["zoom"] == REVERSE_ZOOM
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Operaciones del catálogo: caché por celda y estados
# ----------------------------------------------------------------------
def test_cell_cache_roundtrip(tmp_path):
    _root, catalog = _open(tmp_path)
    try:
        assert catalog.get_place_cell(40.417, -3.704, min_zoom=REVERSE_ZOOM) is None
        catalog.put_place_cell(
            40.417, -3.704, MADRID_RECORD, zoom=REVERSE_ZOOM, now="2026-02-02"
        )
        catalog.commit()
        assert catalog.get_place_cell(
            40.417, -3.704, min_zoom=REVERSE_ZOOM
        ) == MADRID_RECORD
        assert catalog.get_place_cell(40.418, -3.704, min_zoom=REVERSE_ZOOM) is None
    finally:
        catalog.close()


def test_cells_cached_with_an_older_zoom_are_requested_again(tmp_path):
    """Revisión del ADR 0005: celdas resueltas con zoom 10 se re-piden.

    Un catálogo cacheado con el zoom antiguo no sirve: la celda vuelve a
    pedirse y el lugar se refresca con la jerarquía completa, sin `--full`.
    """
    _root, catalog = _open(tmp_path)
    try:
        catalog.put_place_cell(
            40.417, -3.704, PlaceRecord(place=Place(country="Francia")),
            zoom=10, now="2026-02-02",
        )
        catalog.commit()
        # Con el zoom antiguo servía la caché; con el actual, no.
        assert catalog.get_place_cell(40.417, -3.704, min_zoom=10) is not None
        assert catalog.get_place_cell(40.417, -3.704, min_zoom=14) is None
    finally:
        catalog.close()


def test_update_place_guards_ok_state(tmp_path):
    _root, catalog = _open(tmp_path)
    photo = "sha256:" + "2" * 64
    try:
        catalog.insert_photo(photo, ".jpg", "JPEG", "2026-02-02T00:00:00")
        catalog.update_place(photo, MADRID_RECORD, "2026-02-02T00:00:00")
        row = catalog._conn.execute("SELECT * FROM photos").fetchone()
        assert row["place_status"] == "ok"
        assert row["place_city"] == "Madrid"
        assert row["place_error"] is None
    finally:
        catalog.close()


def test_update_place_stores_the_full_service_detail(tmp_path):
    """«Toda la información del servicio» queda junto a la jerarquía."""
    _root, catalog = _open(tmp_path)
    photo = "sha256:" + "7" * 64
    record = PlaceRecord(
        place=make_place(municipality="Arcachón"),
        display_name="Boulevard de la Mer, Arcachón, Gironda, Francia",
        address_json='{"country": "Francia", "town": "Arcachón"}',
    )
    try:
        catalog.insert_photo(photo, ".jpg", "JPEG", "2026-02-02T00:00:00")
        catalog.update_place(photo, record, "2026-02-02T00:00:00")
        row = catalog._conn.execute("SELECT * FROM photos").fetchone()
        assert row["place_municipality"] == "Arcachón"
        assert row["place_display_name"] == record.display_name
        assert row["place_address_json"] == record.address_json
        assert row["place_status"] == "ok"
    finally:
        catalog.close()


def test_fetch_geocode_targets_orders_and_filters(tmp_path):
    _root, catalog = _open(tmp_path)
    hashes = ["sha256:" + f"{index:064x}" for index in range(1, 4)]
    try:
        for hash_ in hashes:
            catalog.insert_photo(hash_, ".jpg", "JPEG", "2026-02-02")
        catalog.update_metadata(
            hashes[0],
            PhotoMetadata(gps_latitude=40.4168, gps_longitude=-3.7038),
            "2026-02-02",
        )
        catalog.update_metadata(
            hashes[2],
            PhotoMetadata(gps_latitude=45.8, gps_longitude=6.8),
            "2026-02-02",
        )
        # hash[1] sin GPS; place_status none para su estado definitivo.
        targets = catalog.fetch_geocode_targets(
            full=False, after_hash=None, limit=10
        )
        assert [target.hash for target in targets] == [hashes[0], hashes[2]]

        catalog.update_place_error(hashes[2], "respuesta sin lugar", "2026-02-02")
        targets = catalog.fetch_geocode_targets(full=False, after_hash=None, limit=10)
        assert [target.hash for target in targets] == [hashes[0]]
        targets = catalog.fetch_geocode_targets(full=True, after_hash=None, limit=10)
        assert [target.hash for target in targets] == [hashes[0], hashes[2]]
        assert catalog.count_geocode_candidates(full=False) == 1
        assert catalog.count_geocode_candidates(full=True) == 2
    finally:
        catalog.close()


def test_update_metadata_assigns_place_status_from_gps(tmp_path):
    _root, catalog = _open(tmp_path)
    photo = "sha256:" + "3" * 64
    photo_no_gps = "sha256:" + "4" * 64
    try:
        catalog.insert_photo(photo, ".jpg", "JPEG", "2026-02-02")
        catalog.insert_photo(photo_no_gps, ".jpg", "JPEG", "2026-02-02")
        catalog.update_metadata(
            photo,
            PhotoMetadata(gps_latitude=40.4168, gps_longitude=-3.7038),
            "2026-02-02",
        )
        catalog.update_metadata(photo_no_gps, PhotoMetadata(width=10), "2026-02-02")
        rows = catalog._conn.execute("SELECT hash, place_status FROM photos").fetchall()
        status = {row["hash"]: row["place_status"] for row in rows}
        assert status[photo] == "pending"
        assert status[photo_no_gps] == "none"
    finally:
        catalog.close()


def test_update_metadata_never_downgrades_ok_place(tmp_path):
    _root, catalog = _open(tmp_path)
    photo = "sha256:" + "5" * 64
    try:
        catalog.insert_photo(photo, ".jpg", "JPEG", "2026-02-02")
        catalog.update_place(photo, MADRID_RECORD, "2026-02-02")
        catalog.update_metadata(
            photo,
            PhotoMetadata(gps_latitude=40.4168, gps_longitude=-3.7038),
            "2026-02-02",
        )
        row = catalog._conn.execute(
            "SELECT place_status FROM photos"
        ).fetchone()
        assert row["place_status"] == "ok"
    finally:
        catalog.close()


def test_run_phases_records_each_phase(tmp_path):
    _root, catalog = _open(tmp_path)
    try:
        catalog.mark_interrupted_runs("2026-02-02")
        run_id = catalog.start_run("incremental", "2026-02-02")
        catalog.record_phase(run_id, "scan", status="ok", now="2026-02-02")
        catalog.record_phase(
            run_id,
            "geocode",
            status="partial",
            reason="La fase se detuvo: rechazo del servicio",
            candidates=3,
            processed=2,
            errors=0,
            cache_hits=0,
            http_requests=2,
            now="2026-02-02",
        )
        rows = catalog._conn.execute(
            "SELECT phase, status, reason, processed FROM run_phases "
            "WHERE run_id = ? ORDER BY phase",
            (run_id,),
        ).fetchall()
        assert [(row["phase"], row["status"]) for row in rows] == [
            ("geocode", "partial"),
            ("scan", "ok"),
        ]
        assert rows[0]["processed"] == 2
        assert rows[0]["reason"].startswith("La fase se detuvo")
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Integración del indexador con la fase geocode
# ----------------------------------------------------------------------
def test_geocode_phase_geocodes_new_photos(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "madrid.jpg", b"madrid-contenido")
    extractor = FakeExtractor()
    geocoder = FakeGeocoder()

    summary = run_index(root, extractor=extractor, geocoder=geocoder)

    photos = read_photos(root)
    photo = next(iter(photos.values()))
    assert photo["place_status"] == "ok"
    assert photo["place_city"] == "Madrid"
    assert photo["place_country"] == "España"
    assert geocoder.calls == [(40.417, -3.704)]
    assert summary.geocode.candidates == 1
    assert summary.geocode.geocoded_ok == 1
    assert summary.geocode.http_requests == 1
    assert summary.geocode.cache_hits == 0
    assert summary.status == "ok"


def test_geocode_phase_is_recorded_in_run_phases(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "f.jpg", b"f")
    run_index(root, extractor=FakeExtractor(), geocoder=FakeGeocoder())

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        rows = catalog._conn.execute(
            "SELECT run_id, phase, status FROM run_phases ORDER BY id"
        ).fetchall()
        assert [(row["phase"], row["status"]) for row in rows] == [
            ("scan", "ok"),
            ("geocode", "ok"),
        ]
        scan_runs = catalog._conn.execute(
            "SELECT status FROM scan_runs ORDER BY id"
        ).fetchall()
        assert scan_runs[-1]["status"] == "ok"
    finally:
        catalog.close()


def test_two_photos_in_the_same_cell_share_one_request(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    write_image(root / "b.jpg", b"b")
    geocoder = FakeGeocoder()

    summary = run_index(root, extractor=FakeExtractor(), geocoder=geocoder)

    assert geocoder.calls == [(40.417, -3.704)]
    assert summary.geocode.geocoded_ok == 2
    assert summary.geocode.cache_hits == 1
    assert summary.geocode.http_requests == 1


def test_second_run_does_not_repeat_requests(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    run_index(root, extractor=FakeExtractor(), geocoder=FakeGeocoder())

    geocoder = FakeGeocoder()
    summary = run_index(root, extractor=FakeExtractor(), geocoder=geocoder)

    assert geocoder.calls == []
    assert summary.geocode.candidates == 0
    assert summary.geocode.geocoded_ok == 0


def test_response_error_marks_the_photo_and_continues(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"madrid")
    write_image(root / "b.jpg", b"otro-sitio")
    geocoder = FakeGeocoder(
        errors=[GeocodingResponseError("respuesta sin lugar interpretable")]
    )
    summary = run_index(root, extractor=FakeExtractor(), geocoder=geocoder)

    photos = list(read_photos(root).values())
    statuses = {row["place_status"] for row in photos}
    assert statuses == {"ok", "error"}
    errored = [row for row in photos if row["place_status"] == "error"]
    assert errored[0]["place_error"].endswith("interpretable")
    assert summary.geocode.geocoded_error == 1
    assert summary.geocode.geocoded_ok == 1
    assert summary.status == "ok"


def test_place_error_is_only_retried_with_full(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    run_index(root, extractor=FakeExtractor(), geocoder=FakeGeocoder(errors=[GeocodingResponseError("x")]))

    retry = FakeGeocoder()
    summary = run_index(root, extractor=FakeExtractor(), geocoder=retry)
    assert retry.calls == []
    assert summary.geocode.geocoded_ok == 0

    summary = run_index(
        root,
        options=IndexingOptions(full=True),
        extractor=FakeExtractor(),
        geocoder=retry,
    )
    assert retry.calls == [(40.417, -3.704)]
    assert summary.geocode.geocoded_ok == 1
    assert set(_place_status(root).values()) == {"ok"}


def test_rejection_stops_the_phase_and_keeps_pending(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    write_image(root / "b.jpg", b"b")
    geocoder = FakeGeocoder(errors=[GeocodingRejectedError("El servicio rechazó la petición (HTTP 429)")])
    summary = run_index(root, extractor=FakeExtractor(), geocoder=geocoder)

    assert geocoder.calls == []
    assert summary.status == "partial"
    assert summary.geocode.status == "partial"
    assert summary.geocode.reason is not None
    assert "se detuvo" in summary.geocode.reason
    statuses = _place_status(root)
    assert set(statuses.values()) == {"pending"}
    assert summary.geocode.geocoded_ok == 0
    assert summary.partial_reason is not None


def test_next_run_resumes_after_rejection_without_repeating(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    write_image(root / "b.jpg", b"b")
    run_index(
        root,
        extractor=FakeExtractor(),
        geocoder=FakeGeocoder(errors=[GeocodingRejectedError("HTTP 429")]),
    )
    statuses = _place_status(root)
    assert any(state == "pending" for state in statuses.values())

    geocoder = FakeGeocoder()
    summary = run_index(root, extractor=FakeExtractor(), geocoder=geocoder)

    assert summary.status == "ok"
    assert summary.geocode.geocoded_ok == 2
    statuses = _place_status(root)
    assert set(statuses.values()) == {"ok"}


def test_photos_without_gps_are_never_geocoded(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    without_gps = PhotoMetadata(
        file_type="JPEG", width=10, height=10, orientation=1
    )
    geocoder = FakeGeocoder()
    summary = run_index(
        root, extractor=FakeExtractor(metadata=without_gps), geocoder=geocoder
    )

    assert geocoder.calls == []
    assert summary.geocode.candidates == 0
    assert set(_place_status(root).values()) == {"none"}


def test_partial_hierarchy_is_saved_ok_with_nulls(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "montana.jpg", b"montana")
    geocoder = FakeGeocoder(
        results={(40.417, -3.704): Place(country="Francia")}
    )
    summary = run_index(
        root, extractor=FakeExtractor(), geocoder=geocoder
    )

    photos = list(read_photos(root).values())
    assert photos[0]["place_status"] == "ok"
    assert photos[0]["place_country"] == "Francia"
    assert photos[0]["place_city"] is None
    assert summary.geocode.geocoded_ok == 1


def test_skip_geocode_leaves_pending_and_records_skipped(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "f.jpg", b"f")
    summary = run_index(
        root,
        extractor=FakeExtractor(),
        geocoder=FakeGeocoder(),
        options=IndexingOptions(skip=frozenset(["geocode"])),
    )

    assert summary.geocode.status == "skipped"
    assert set(_place_status(root).values()) == {"pending"}
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        rows = catalog._conn.execute(
            "SELECT phase, status, reason FROM run_phases ORDER BY id"
        ).fetchall()
        assert rows[-1]["phase"] == "geocode"
        assert rows[-1]["status"] == "skipped"
        assert "--skip geocode" in rows[-1]["reason"]
    finally:
        catalog.close()


def test_no_geocoder_configured_records_skipped(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "f.jpg", b"f")
    summary = run_index(root, extractor=FakeExtractor())

    assert summary.geocode.status == "skipped"
    assert "sin geocodificador" in summary.geocode.reason


def test_unknown_phase_name_is_rejected_at_construction(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        with pytest.raises(IndexingError, match="Se desconoce"):
            Indexer(
                catalog,
                root,
                options=IndexingOptions(skip=frozenset(["poda"])),
            )
    finally:
        catalog.close()


def test_fatal_scan_error_records_geocode_as_skipped(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")

    def broken_scan(self, summary):
        raise CatalogError("El disco desapareció")

    monkeypatch.setattr(Indexer, "_scan", broken_scan)
    try:
        with pytest.raises(CatalogError):
            Indexer(catalog, root, options=IndexingOptions()).run()
        rows = catalog._conn.execute(
            "SELECT run_id, phase, status, reason FROM run_phases ORDER BY id"
        ).fetchall()
        statuses = {(row["phase"], row["status"]) for row in rows}
        assert ("scan", "error") in statuses
        assert ("geocode", "skipped") in statuses
        run_status = catalog._conn.execute(
            "SELECT status FROM scan_runs ORDER BY id"
        ).fetchone()
        assert run_status["status"] == "error"
    finally:
        catalog.close()


def test_phases_run_in_order(tmp_path):
    root = tmp_path / "biblioteca"
    assert INDEXING_PHASES == ("scan", "geocode")
    write_image(root / "f.jpg", b"f")
    run_index(root, extractor=FakeExtractor(), geocoder=FakeGeocoder())
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        phases = [
            row["phase"]
            for row in catalog._conn.execute(
                "SELECT phase FROM run_phases ORDER BY id"
            )
        ]
        assert phases == list(INDEXING_PHASES)
    finally:
        catalog.close()


def test_dry_run_counts_candidates_without_network(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    write_image(root / "b.jpg", b"b")
    run_index(root, extractor=FakeExtractor())
    count_before = _cell_rows(root)

    geocoder = FakeGeocoder()
    summary = run_index(
        root,
        options=IndexingOptions(dry_run=True),
        geocoder=geocoder,
    )

    assert geocoder.calls == []
    count_after = _cell_rows(root)
    assert count_after == count_before
    assert summary.geocode.dry_run is True
    assert summary.geocode.candidates == 2
    assert summary.geocode.unique_cells == 1
    assert summary.geocode.http_requests == 1
    assert summary.geocode.cache_hits == 1
    assert summary.status == "ok"


def _open(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    return root, catalog


def _cell_rows(root):
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        return catalog._conn.execute("SELECT COUNT(*) FROM geocode_cells").fetchone()[0]
    finally:
        catalog.close()
