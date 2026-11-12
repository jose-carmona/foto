"""Tests de ``foto-import``: merge aditivo idempotente (ADR 0007).

Sin Qt ni pantalla, como el resto de ``core/``, ``storage/`` y ``transfer/``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from foto.core.errors import TransferError
from foto.storage.catalog import Catalog
from foto.storage.lock import IndexerLock
from foto.transfer import model
from foto.transfer.cli_import import main
from foto.transfer.importer import (
    apply_places_file,
    apply_tags_file,
    read_tags_file,
)

NOW_FILE = "2026-11-01T08:00:00+00:00"
NOW_OLD = "2026-10-01T08:00:00+00:00"
IMPORT_NOW = "2026-11-12T12:00:00+00:00"

PHOTO_A = "sha256:" + "a" * 64
PHOTO_B = "sha256:" + "b" * 64
PHOTO_C = "sha256:" + "c" * 64
PHOTO_D = "sha256:" + "d" * 64
PHOTO_MISSING = "sha256:" + "e" * 64


def _fresh_catalog() -> Catalog:
    return Catalog.open_in_memory()


def _seed_photos(catalog: Catalog) -> None:
    for photo_hash in (PHOTO_A, PHOTO_B, PHOTO_C, PHOTO_D):
        catalog.connection.execute(
            "INSERT INTO photos (hash, extension, metadata_status, created_at, "
            "updated_at) VALUES (?, '.jpg', 'ok', ?, ?)",
            (photo_hash, NOW_OLD, NOW_OLD),
        )


def _seed_tag(catalog: Catalog, name: str, normalized: str, kind: str) -> int:
    cursor = catalog.connection.execute(
        "INSERT INTO tags (name, name_normalized, kind, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, normalized, kind, NOW_OLD, NOW_OLD),
    )
    return int(cursor.lastrowid)


def _seed_assignment(
    catalog: Catalog,
    photo_hash: str,
    tag_id: int,
    *,
    source: str = "manual",
    status: str = "confirmed",
    confidence: float | None = None,
    region: tuple[float, float, float, float] | None = None,
    created_at: str = NOW_OLD,
    updated_at: str = NOW_OLD,
) -> None:
    region_values = (
        region if region is not None else (None, None, None, None)
    )
    catalog.connection.execute(
        "INSERT INTO photo_tags (photo_hash, tag_id, source, status, confidence, "
        "region_x, region_y, region_w, region_h, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_hash, tag_id, source, status, confidence, *region_values,
         created_at, updated_at),
    )


def _set_place(
    catalog: Catalog, photo_hash: str, *, status: str, updated_at: str = NOW_OLD
) -> None:
    catalog.connection.execute(
        "UPDATE photos SET place_status = ?, place_country = 'Viejo', "
        "place_updated_at = ? WHERE hash = ?",
        (status, updated_at, photo_hash),
    )


def _tags_file() -> model.TagsFile:
    return model.TagsFile(
        generated_at=NOW_FILE,
        tags=(
            model.TagEntry("Lisboa", "lisboa", "place", NOW_FILE, NOW_FILE),
            model.TagEntry("Marta", "marta", "person", NOW_FILE, NOW_FILE),
        ),
        assignments=(
            model.AssignmentEntry(
                photo=PHOTO_A, tag="lisboa", source="manual", status="confirmed",
                confidence=None, region=None,
                created_at=NOW_FILE, updated_at=NOW_FILE,
            ),
            model.AssignmentEntry(
                photo=PHOTO_B, tag="marta", source="ml", status="suggested",
                confidence=0.9, region=(0.1, 0.2, 0.3, 0.4),
                created_at=NOW_FILE, updated_at=NOW_FILE,
            ),
        ),
    )


def _places_file() -> model.PlacesFile:
    return model.PlacesFile(
        generated_at=NOW_FILE,
        places=(
            model.PlaceEntry(
                photo=PHOTO_A, country="Portugal", country_code="PT",
                state="Lisboa", city="Lisboa", municipality="Lisboa",
                display_name="Rua Nova, Lisboa, Portugal",
                address={"city": "Lisboa", "road": "Rua Nova"},
                resolved_at=NOW_FILE,
            ),
            model.PlaceEntry(
                photo=PHOTO_B, country="Portugal", country_code="PT",
                state=None, city=None, municipality=None,
                display_name=None, address=None, resolved_at=NOW_FILE,
            ),
            model.PlaceEntry(
                photo=PHOTO_C, country="Portugal", country_code="PT",
                state="Lisboa", city="Lisboa", municipality="Lisboa",
                display_name="Rua Augusta, Lisboa, Portugal",
                address={"road": "Rua Augusta"}, resolved_at=NOW_FILE,
            ),
            model.PlaceEntry(
                photo=PHOTO_D, country="Portugal", country_code="PT",
                state="Lisboa", city="Lisboa", municipality="Lisboa",
                display_name="Cascais, Portugal",
                address={"road": "Rua Direita"}, resolved_at=NOW_FILE,
            ),
            model.PlaceEntry(
                photo=PHOTO_MISSING, country="Portugal", country_code="PT",
                state=None, city=None, municipality=None,
                display_name=None, address=None, resolved_at=NOW_FILE,
            ),
        ),
    )


def _photo_row(catalog: Catalog, photo_hash: str) -> sqlite3.Row:
    return catalog.connection.execute(
        "SELECT place_status, place_country, place_city, place_display_name, "
        "place_address_json, place_error, place_updated_at "
        "FROM photos WHERE hash = ?",
        (photo_hash,),
    ).fetchone()


def _assignment_rows(catalog: Catalog) -> list[sqlite3.Row]:
    return catalog.connection.execute(
        "SELECT pt.source, pt.status, pt.confidence, pt.region_x, pt.region_y, "
        "pt.region_w, pt.region_h, pt.created_at, pt.updated_at, "
        "t.name_normalized FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id "
        "ORDER BY t.name_normalized"
    ).fetchall()


# ----------------------------------------------------------------------
# Etiquetas y asignaciones
# ----------------------------------------------------------------------
def test_import_creates_tags_and_assignments_with_import_source():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        counters = apply_tags_file(catalog, _tags_file(), now=IMPORT_NOW)
        catalog.commit()

        assert counters.tags_created == 2
        assert counters.assignments_applied == 2
        assert counters.warnings == 0
        rows = {row["name_normalized"]: row for row in _assignment_rows(catalog)}
        lisboa = rows["lisboa"]
        assert lisboa["source"] == "import"  # el fichero traía 'manual'
        assert lisboa["status"] == "confirmed"
        assert lisboa["confidence"] is None
        assert lisboa["region_x"] is None
        assert lisboa["created_at"] == NOW_FILE
        marta = rows["marta"]
        assert marta["source"] == "import"  # el fichero traía 'ml'
        assert marta["status"] == "suggested"
        assert marta["confidence"] == 0.9  # el fichero lo trae, se conserva
        assert (marta["region_x"], marta["region_y"],
                marta["region_w"], marta["region_h"]) == (0.1, 0.2, 0.3, 0.4)
    finally:
        catalog.close()


def _logical_dump(catalog: Catalog) -> list[tuple]:
    """Contenido lógico del catálogo (sin AUTOINCREMENT ni fechas de sistema)."""
    rows: list[tuple] = []
    for table, columns in (
        ("tags", ("name_normalized", "kind")),
        ("photo_tags", ("photo_hash", "tag_id", "source", "status", "confidence",
                        "region_x", "region_y", "region_w", "region_h")),
        ("photos", ("hash", "place_status", "place_country", "place_updated_at")),
    ):
        query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1"
        rows.extend(
            (table, *tuple(row)) for row in catalog.connection.execute(query).fetchall()
        )
    return rows


def test_import_is_idempotent_and_leaves_the_catalog_identical():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        apply_tags_file(catalog, _tags_file(), now=IMPORT_NOW)
        catalog.commit()
        first = _logical_dump(catalog)

        counters = apply_tags_file(catalog, _tags_file(), now=IMPORT_NOW)
        catalog.commit()
        second = _logical_dump(catalog)

        assert first == second
        assert counters.tags_created == 0
        assert counters.assignments_applied == 0
        assert counters.tags_existing == 2
        assert counters.assignments_existing == 2
    finally:
        catalog.close()


def test_import_never_touches_existing_rows():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        lisboa_id = _seed_tag(catalog, "Lisboa", "lisboa", "place")
        _seed_assignment(catalog, PHOTO_A, lisboa_id)  # manual/confirmed, sin confianza
        before = _assignment_rows(catalog)[0]

        data = model.TagsFile(
            generated_at=NOW_FILE,
            tags=(model.TagEntry("Lisboa", "lisboa", "place", NOW_FILE, NOW_FILE),),
            assignments=(
                model.AssignmentEntry(
                    photo=PHOTO_A, tag="lisboa", source="ml", status="suggested",
                    confidence=0.5, region=None,
                    created_at=NOW_FILE, updated_at=NOW_FILE,
                ),
            ),
        )
        counters = apply_tags_file(catalog, data, now=IMPORT_NOW)
        catalog.commit()

        after = _assignment_rows(catalog)[0]
        assert tuple(before) == tuple(after)  # fila existente intocable
        assert counters.assignments_applied == 0
        assert counters.assignments_existing == 1
        assert counters.kind_warnings == 0
    finally:
        catalog.close()


def test_kind_mismatch_warns_and_keeps_existing_kind():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        _seed_tag(catalog, "Lisboa", "lisboa", "place")

        data = model.TagsFile(
            generated_at=NOW_FILE,
            tags=(model.TagEntry("Lisboa", "lisboa", "event", NOW_FILE, NOW_FILE),),
            assignments=(
                model.AssignmentEntry(
                    photo=PHOTO_A, tag="lisboa", source="manual", status="confirmed",
                    confidence=None, region=None,
                    created_at=NOW_FILE, updated_at=NOW_FILE,
                ),
            ),
        )
        counters = apply_tags_file(catalog, data, now=IMPORT_NOW)
        catalog.commit()

        assert counters.tags_existing == 1
        assert counters.kind_warnings == 1
        assert counters.assignments_applied == 1  # se aplican igual
        row = catalog.connection.execute(
            "SELECT kind FROM tags WHERE name_normalized = 'lisboa'"
        ).fetchone()
        assert row["kind"] == "place"  # se conserva la existente
    finally:
        catalog.close()


def test_rejected_rows_are_imported_and_respected():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        _seed_tag(catalog, "Marta", "marta", "person")
        data = model.TagsFile(
            generated_at=NOW_FILE,
            tags=(model.TagEntry("Marta", "marta", "person", NOW_FILE, NOW_FILE),),
            assignments=(
                model.AssignmentEntry(
                    photo=PHOTO_A, tag="marta", source="ml", status="rejected",
                    confidence=0.3, region=None,
                    created_at=NOW_FILE, updated_at=NOW_FILE,
                ),
            ),
        )
        counters = apply_tags_file(catalog, data, now=IMPORT_NOW)
        catalog.commit()

        assert counters.assignments_applied == 1
        row = _assignment_rows(catalog)[0]
        assert row["status"] == "rejected"  # bloquea re-sugerencias en destino
    finally:
        catalog.close()


def test_system_tag_other_than_fav_is_ignored_with_its_assignments():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        data = model.TagsFile(
            generated_at=NOW_FILE,
            tags=(
                model.TagEntry("Destacadas", "destacadas", "system", NOW_FILE, NOW_FILE),
            ),
            assignments=(
                model.AssignmentEntry(
                    photo=PHOTO_A, tag="destacadas", source="manual",
                    status="confirmed", confidence=None, region=None,
                    created_at=NOW_FILE, updated_at=NOW_FILE,
                ),
            ),
        )
        counters = apply_tags_file(catalog, data, now=IMPORT_NOW)
        catalog.commit()

        assert counters.ignored_tags == 1
        assert counters.assignments_ignored == 1
        assert counters.tags_created == 0
        assert counters.warnings == 2
        count = catalog.connection.execute(
            "SELECT COUNT(*) FROM tags WHERE name_normalized = 'destacadas'"
        ).fetchone()[0]
        assert count == 0
    finally:
        catalog.close()


def test_duplicate_assignment_in_file_is_not_duplicated():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        assignment = model.AssignmentEntry(
            photo=PHOTO_A, tag="lisboa", source="manual", status="confirmed",
            confidence=None, region=None, created_at=NOW_FILE, updated_at=NOW_FILE,
        )
        data = model.TagsFile(
            generated_at=NOW_FILE,
            tags=(model.TagEntry("Lisboa", "lisboa", "place", NOW_FILE, NOW_FILE),),
            assignments=(assignment, assignment),
        )
        counters = apply_tags_file(catalog, data, now=IMPORT_NOW)
        catalog.commit()

        assert counters.assignments_applied == 1
        assert counters.assignments_existing == 1
        count = catalog.connection.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0]
        assert count == 1
    finally:
        catalog.close()


def test_missing_hashes_are_counted_without_aborting():
    catalog = _fresh_catalog()
    try:
        _seed_photos(catalog)
        data = model.TagsFile(
            generated_at=NOW_FILE,
            tags=(model.TagEntry("Lisboa", "lisboa", "place", NOW_FILE, NOW_FILE),),
            assignments=(
                model.AssignmentEntry(
                    photo=PHOTO_MISSING, tag="lisboa", source="manual",
                    status="confirmed", confidence=None, region=None,
                    created_at=NOW_FILE, updated_at=NOW_FILE,
                ),
            ),
        )
        counters = apply_tags_file(catalog, data, now=IMPORT_NOW)
        catalog.commit()

        assert counters.assignments_without_photo == 1
        assert counters.missing_hashes == (PHOTO_MISSING,)
        assert counters.warnings == 0  # caso normal, no un aviso (ADR 0007 § 7)
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Lugares
# ----------------------------------------------------------------------
def _seeded_places_catalog() -> Catalog:
    catalog = _fresh_catalog()
    _seed_photos(catalog)
    _set_place(catalog, PHOTO_A, status="pending")
    _set_place(catalog, PHOTO_B, status="none")
    _set_place(catalog, PHOTO_C, status="ok")
    _set_place(catalog, PHOTO_D, status="error")
    catalog.commit()
    return catalog


def test_places_fill_pending_and_none_only():
    catalog = _seeded_places_catalog()
    try:
        counters = apply_places_file(
            catalog, _places_file(), now=IMPORT_NOW, force=False
        )
        catalog.commit()

        assert counters.places_filled == 2
        assert counters.places_filled_force == 0
        assert counters.places_skipped_resolved == 1
        assert counters.places_skipped_error == 1
        assert PHOTO_MISSING in counters.missing_hashes

        filled = _photo_row(catalog, PHOTO_A)
        assert filled["place_status"] == "ok"
        assert filled["place_country"] == "Portugal"
        assert filled["place_city"] == "Lisboa"
        assert filled["place_display_name"] == "Rua Nova, Lisboa, Portugal"
        assert filled["place_error"] is None
        assert filled["place_updated_at"] == IMPORT_NOW  # hora del import
        none_row = _photo_row(catalog, PHOTO_B)
        assert none_row["place_status"] == "ok"

        resolved = _photo_row(catalog, PHOTO_C)
        assert resolved["place_status"] == "ok"
        assert resolved["place_country"] == "Viejo"  # intocado
        assert resolved["place_updated_at"] == NOW_OLD
        errored = _photo_row(catalog, PHOTO_D)
        assert errored["place_status"] == "error"
    finally:
        catalog.close()


def test_places_with_force_fill_error_but_never_ok():
    catalog = _seeded_places_catalog()
    try:
        counters = apply_places_file(catalog, _places_file(), now=IMPORT_NOW, force=True)
        catalog.commit()

        assert counters.places_filled == 3
        assert counters.places_filled_force == 1
        assert counters.places_skipped_resolved == 1
        errored = _photo_row(catalog, PHOTO_D)
        assert errored["place_status"] == "ok"
        assert errored["place_city"] == "Lisboa"
        resolved = _photo_row(catalog, PHOTO_C)
        assert resolved["place_country"] == "Viejo"  # un 'ok' jamás se pisa
    finally:
        catalog.close()


def test_place_address_json_is_canonical():
    catalog = _seeded_places_catalog()
    try:
        apply_places_file(catalog, _places_file(), now=IMPORT_NOW, force=False)
        catalog.commit()

        stored = _photo_row(catalog, PHOTO_A)["place_address_json"]
        assert stored == json.dumps(
            {"city": "Lisboa", "road": "Rua Nova"}, ensure_ascii=False, sort_keys=True
        )
    finally:
        catalog.close()


def test_places_transaction_rolls_back_on_failure(monkeypatch):
    catalog = _seeded_places_catalog()
    try:
        original_update_place = catalog.update_place
        calls = 0

        def failing_update_place(photo_hash, record, now):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fallo simulado a mitad del fichero")
            return original_update_place(photo_hash, record, now)

        monkeypatch.setattr(catalog, "update_place", failing_update_place)
        with pytest.raises(RuntimeError, match="fallo simulado"):
            apply_places_file(catalog, _places_file(), now=IMPORT_NOW, force=False)

        # O se aplica el fichero entero o no aplica nada: la primera foto no
        # quedó rellenada (conserva el estado y el contenido de la semilla).
        assert _photo_row(catalog, PHOTO_A)["place_status"] == "pending"
        assert _photo_row(catalog, PHOTO_A)["place_country"] == "Viejo"
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Lectura y validación de ficheros
# ----------------------------------------------------------------------
def test_from_dict_round_trip():
    data = _tags_file()
    parsed = model.tags_file_from_dict(model.tags_file_to_dict(data))
    assert parsed == data

    places = model.PlacesFile(
        generated_at=NOW_FILE,
        places=(
            model.PlaceEntry(
                photo=PHOTO_A, country="Portugal", country_code="PT", state=None,
                city="Lisboa", municipality=None, display_name="X",
                address={"road": "Rua"}, resolved_at=NOW_FILE,
            ),
        ),
    )
    assert model.places_file_from_dict(model.places_file_to_dict(places)) == places


def test_from_dict_rejects_unknown_format_and_version():
    with pytest.raises(TransferError, match="Formato desconocido"):
        model.tags_file_from_dict({"format": "foto/otro", "version": 1})
    with pytest.raises(TransferError, match="más nueva"):
        model.tags_file_from_dict(
            {"format": "foto/tags", "version": 2, "generated_at": NOW_FILE,
             "tags": [], "assignments": []}
        )
    with pytest.raises(TransferError, match="desconocida"):
        model.tags_file_from_dict(
            {"format": "foto/tags", "version": 0, "generated_at": NOW_FILE,
             "tags": [], "assignments": []}
        )


def test_from_dict_rejects_missing_tag_reference():
    payload = model.tags_file_to_dict(_tags_file())
    payload["assignments"] = [
        {"photo": PHOTO_A, "tag": "ausente", "source": "manual",
         "status": "confirmed", "confidence": None, "region": None,
         "created_at": NOW_FILE, "updated_at": NOW_FILE}
    ]
    with pytest.raises(TransferError, match="ausente de la sección"):
        model.tags_file_from_dict(payload)


def test_from_dict_rejects_bad_values():
    payload = model.tags_file_to_dict(_tags_file())
    payload["assignments"][0]["photo"] = "sha256:corto"
    with pytest.raises(TransferError, match="hash de foto mal formado"):
        model.tags_file_from_dict(payload)

    payload = model.tags_file_to_dict(_tags_file())
    payload["assignments"][0]["region"] = [0.0, 0.0, 2.0, 0.5]  # w > 1
    with pytest.raises(TransferError, match="fuera de rango"):
        model.tags_file_from_dict(payload)

    payload = model.tags_file_to_dict(_tags_file())
    payload["tags"][0]["kind"] = "album"
    with pytest.raises(TransferError, match="kind"):
        model.tags_file_from_dict(payload)


def test_read_tags_file_rejects_invalid_json(tmp_path):
    path = tmp_path / "tags.json"
    path.write_text("{no soy json", encoding="utf-8")
    with pytest.raises(TransferError, match="no es un JSON válido"):
        read_tags_file(path)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _seeded_library(root: Path) -> Path:
    (root / ".foto").mkdir(parents=True)
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        _seed_photos(catalog)
        _set_place(catalog, PHOTO_A, status="pending")
        _set_place(catalog, PHOTO_C, status="ok")
        _set_place(catalog, PHOTO_D, status="error")
        catalog.commit()
    finally:
        catalog.close()
    return root / ".foto" / "catalog.sqlite"


def _write_files(tmp_path: Path) -> tuple[Path, Path]:
    tags_path = tmp_path / "tags.json"
    places_path = tmp_path / "places.json"
    tags_path.write_text(
        model.serialize_json(model.tags_file_to_dict(_tags_file())), encoding="utf-8"
    )
    places_path.write_text(
        model.serialize_json(model.places_file_to_dict(_places_file())), encoding="utf-8"
    )
    return tags_path, places_path


def test_cli_imports_both_files(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    tags_path, places_path = _write_files(tmp_path)

    code = main(
        ["--library", str(root), "--tags", str(tags_path),
         "--places", str(places_path), "--json"]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert code == 1  # aviso: un 'error' se omite sin --force
    assert summary["totals"]["tags_created"] == 2
    assert summary["totals"]["assignments_applied"] == 2
    assert summary["totals"]["places_filled"] == 2
    assert summary["totals"]["places_skipped_resolved"] == 1
    assert summary["totals"]["places_skipped_error"] == 1
    assert summary["totals"]["missing_hashes"] == 1
    assert summary["totals"]["warnings"] == 1

    catalog = Catalog.open_read_only(root / ".foto" / "catalog.sqlite")
    try:
        rows = _assignment_rows(catalog)
        assert all(row["source"] == "import" for row in rows)
        assert _photo_row(catalog, PHOTO_A)["place_updated_at"] is not None
    finally:
        catalog.close()


def test_cli_import_is_idempotent_through_the_cli(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    tags_path, _ = _write_files(tmp_path)
    main(["--library", str(root), "--tags", str(tags_path), "--json"])
    capsys.readouterr()

    code = main(["--library", str(root), "--tags", str(tags_path), "--json"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert code == 0
    assert summary["totals"]["tags_created"] == 0
    assert summary["totals"]["assignments_applied"] == 0
    assert summary["totals"]["tags_existing"] == 2
    assert summary["totals"]["assignments_existing"] == 2


def test_cli_dry_run_does_not_write(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    tags_path, _ = _write_files(tmp_path)

    code = main(
        ["--library", str(root), "--tags", str(tags_path), "--dry-run", "--json"]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert code == 0
    assert summary["dry_run"] is True
    assert summary["totals"]["assignments_applied"] == 2

    catalog = Catalog.open_read_only(root / ".foto" / "catalog.sqlite")
    try:
        assert _assignment_rows(catalog) == []  # nada escrito en disco
    finally:
        catalog.close()


def test_cli_without_lock_available_returns_3(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    tags_path, _ = _write_files(tmp_path)

    lock = IndexerLock(root / ".foto" / "index.lock")
    lock.acquire()
    try:
        code = main(["--library", str(root), "--tags", str(tags_path)])
        assert code == 3
        # Con --dry-run no toma el lock: funciona igual.
        assert main(
            ["--library", str(root), "--tags", str(tags_path), "--dry-run", "--quiet"]
        ) == 0
    finally:
        lock.release()


def test_cli_invalid_file_returns_2_without_writes(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    tags_path, _ = _write_files(tmp_path)
    payload = json.loads(tags_path.read_text(encoding="utf-8"))
    payload["version"] = 2
    tags_path.write_text(json.dumps(payload), encoding="utf-8")

    code = main(["--library", str(root), "--tags", str(tags_path)])
    captured = capsys.readouterr()

    assert code == 2
    assert "más nueva" in captured.err
    catalog = Catalog.open_read_only(root / ".foto" / "catalog.sqlite")
    try:
        assert _assignment_rows(catalog) == []  # cero escrituras
    finally:
        catalog.close()


def test_cli_requires_at_least_one_file(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    with pytest.raises(SystemExit) as raised:
        main(["--library", str(root)])
    assert raised.value.code == 2


def test_cli_force_fills_error_places(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    _, places_path = _write_files(tmp_path)

    code = main(
        ["--library", str(root), "--places", str(places_path), "--force", "--quiet"]
    )

    assert code == 0  # con --force no hay avisos: el 'error' se rellena
    catalog = Catalog.open_read_only(root / ".foto" / "catalog.sqlite")
    try:
        assert _photo_row(catalog, PHOTO_D)["place_status"] == "ok"
        assert _photo_row(catalog, PHOTO_C)["place_country"] == "Viejo"
    finally:
        catalog.close()


def test_cli_summary_has_no_names_or_addresses(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    tags_path, places_path = _write_files(tmp_path)

    main(
        ["--library", str(root), "--tags", str(tags_path),
         "--places", str(places_path), "--json"]
    )
    dumped = capsys.readouterr().out

    for private in ("Lisboa", "Marta", "Rua", "Portugal"):
        assert private not in dumped
