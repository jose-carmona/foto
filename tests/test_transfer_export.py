"""Tests de ``foto-export`` y del modelo de los ficheros JSON (ADR 0007).

Sin Qt ni pantalla, como el resto de ``core/`` y ``storage/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foto.core.errors import TransferError
from foto.storage.catalog import Catalog
from foto.transfer import exporter, model
from foto.transfer.cli_export import main
from foto.transfer.exporter import export_library

GENERATED = "2026-11-12T10:00:00+00:00"
NOW = "2026-11-10T09:00:00+00:00"

PHOTO_A = "sha256:" + "a" * 64
PHOTO_B = "sha256:" + "b" * 64
PHOTO_C = "sha256:" + "c" * 64
PHOTO_D = "sha256:" + "d" * 64

VALID_ADDRESS = {"road": "Rua Augusta", "city": "Lisboa"}


def _seed_photos(connection) -> None:
    for photo_hash in (PHOTO_A, PHOTO_B, PHOTO_C, PHOTO_D):
        connection.execute(
            "INSERT INTO photos (hash, extension, metadata_status, created_at, "
            "updated_at) VALUES (?, '.jpg', 'ok', ?, ?)",
            (photo_hash, NOW, NOW),
        )


def _seed_tags(connection) -> None:
    # 'fav' (kind='system') ya la siembra la migración v4 con id 1.
    for name, normalized, kind in (
        ("Lisboa", "lisboa", "place"),
        ("Marta", "marta", "person"),
    ):
        connection.execute(
            "INSERT INTO tags (name, name_normalized, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, normalized, kind, NOW, NOW),
        )


def _seed_assignments(connection) -> None:
    # (photo, tag_id, source, status, confidence, region) — cubre las tres
    # 'source', los tres 'status', confianza nula y numérica, y región nula
    # y en rango.
    rows = (
        (PHOTO_A, 1, "manual", "confirmed", None, None),
        (PHOTO_A, 2, "ml", "suggested", 0.85, (0.1, 0.2, 0.3, 0.4)),
        (PHOTO_B, 2, "import", "rejected", None, None),
    )
    for photo_hash, tag_id, source, status, confidence, region in rows:
        region_values = (
            (region[0], region[1], region[2], region[3])
            if region is not None
            else (None, None, None, None)
        )
        connection.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "confidence, region_x, region_y, region_w, region_h, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (photo_hash, tag_id, source, status, confidence, *region_values, NOW, NOW),
        )


def _seed_places(connection) -> None:
    updates = (
        (PHOTO_A, "ok", json.dumps(VALID_ADDRESS), "Rua Augusta, Lisboa, Portugal"),
        (PHOTO_B, "ok", json.dumps({"road": "Rua Nova"}), "Rua Nova, Lisboa, Portugal"),
        (PHOTO_C, "pending", None, None),
        (PHOTO_D, "error", None, None),
    )
    for photo_hash, status, address_json, display_name in updates:
        connection.execute(
            "UPDATE photos SET place_status = ?, place_address_json = ?, "
            "place_display_name = ?, place_country = 'Portugal', "
            "place_country_code = 'PT', place_state = 'Lisboa', "
            "place_city = 'Lisboa', place_municipality = 'Lisboa', "
            "place_updated_at = ? WHERE hash = ?",
            (status, address_json, display_name, NOW, photo_hash),
        )


def _seeded_catalog() -> Catalog:
    catalog = Catalog.open_in_memory()
    connection = catalog.connection
    _seed_photos(connection)
    _seed_tags(connection)
    _seed_assignments(connection)
    _seed_places(connection)
    catalog.commit()
    return catalog


def _export(connection, tmp_path: Path, *, dry_run: bool = False, out_name="destino"):
    return export_library(
        connection,
        tmp_path / out_name,
        export_tags=True,
        export_places=True,
        generated_at=GENERATED,
        dry_run=dry_run,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Formato
# ----------------------------------------------------------------------
def test_tags_file_header_and_counts(tmp_path):
    catalog = _seeded_catalog()
    try:
        _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    data = _read_json(tmp_path / "destino" / "tags.json")
    assert data["format"] == "foto/tags"
    assert data["version"] == 1
    assert data["generated_at"] == GENERATED
    assert data["counts"] == {"tags": 3, "assignments": 3}

    assert [tag["normalized"] for tag in data["tags"]] == ["fav", "lisboa", "marta"]
    lisboa = next(tag for tag in data["tags"] if tag["normalized"] == "lisboa")
    assert lisboa == {
        "name": "Lisboa",
        "normalized": "lisboa",
        "kind": "place",
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_export_is_deterministic_byte_to_byte(tmp_path):
    first = _seeded_catalog()
    second = _seeded_catalog()
    try:
        summary_a = _export(first.connection, tmp_path, out_name="a")
        summary_b = _export(second.connection, tmp_path, out_name="b")
    finally:
        first.close()
        second.close()

    for kind in ("tags", "places"):
        bytes_a = (tmp_path / "a" / f"{kind}.json").read_bytes()
        bytes_b = (tmp_path / "b" / f"{kind}.json").read_bytes()
        assert bytes_a == bytes_b
    assert summary_a.tags == summary_b.tags
    assert summary_a.assignments == summary_b.assignments
    assert summary_a.places == summary_b.places
    assert summary_a.sizes == summary_b.sizes


def test_region_null_and_array(tmp_path):
    catalog = _seeded_catalog()
    try:
        _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    assignments = {
        (assignment["photo"], assignment["tag"]): assignment
        for assignment in _read_json(tmp_path / "destino" / "tags.json")["assignments"]
    }
    assert assignments[(PHOTO_A, "fav")]["region"] is None
    assert assignments[(PHOTO_A, "lisboa")]["region"] == [0.1, 0.2, 0.3, 0.4]
    assert assignments[(PHOTO_B, "lisboa")]["region"] is None


def test_assignments_preserve_source_status_and_confidence(tmp_path):
    catalog = _seeded_catalog()
    try:
        _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    assignments = {
        (assignment["photo"], assignment["tag"]): assignment
        for assignment in _read_json(tmp_path / "destino" / "tags.json")["assignments"]
    }
    assert assignments[(PHOTO_A, "fav")]["source"] == "manual"
    assert assignments[(PHOTO_A, "fav")]["status"] == "confirmed"
    assert assignments[(PHOTO_A, "fav")]["confidence"] is None
    assert assignments[(PHOTO_A, "lisboa")]["source"] == "ml"
    assert assignments[(PHOTO_A, "lisboa")]["status"] == "suggested"
    assert assignments[(PHOTO_A, "lisboa")]["confidence"] == 0.85
    assert assignments[(PHOTO_B, "lisboa")]["source"] == "import"
    assert assignments[(PHOTO_B, "lisboa")]["status"] == "rejected"
    assert assignments[(PHOTO_B, "lisboa")]["confidence"] is None


def test_orphan_tags_and_fav_are_included(tmp_path):
    catalog = _seeded_catalog()
    try:
        summary = _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    data = _read_json(tmp_path / "destino" / "tags.json")
    normalized = {tag["normalized"] for tag in data["tags"]}
    # 'marta' no tiene asignaciones: es taxonomía del usuario. 'fav' es
    # kind='system' y también viaja.
    assert "marta" in normalized
    fav = next(tag for tag in data["tags"] if tag["normalized"] == "fav")
    assert fav["kind"] == "system"
    assert summary.orphan_tags == 1


# ----------------------------------------------------------------------
# places.json
# ----------------------------------------------------------------------
def test_places_include_only_ok_rows(tmp_path):
    catalog = _seeded_catalog()
    try:
        summary = _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    data = _read_json(tmp_path / "destino" / "places.json")
    assert data["format"] == "foto/places"
    assert data["version"] == 1
    assert data["counts"] == {"photos": 2}
    photos = [place["photo"] for place in data["places"]]
    assert photos == [PHOTO_A, PHOTO_B]  # orden por hash
    assert summary.places == 2


def test_place_entry_fields(tmp_path):
    catalog = _seeded_catalog()
    try:
        _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    place = _read_json(tmp_path / "destino" / "places.json")["places"][0]
    assert place["country"] == "Portugal"
    assert place["country_code"] == "PT"
    assert place["state"] == "Lisboa"
    assert place["city"] == "Lisboa"
    assert place["municipality"] == "Lisboa"
    assert place["display_name"] == "Rua Augusta, Lisboa, Portugal"
    assert place["address"] == VALID_ADDRESS
    assert place["resolved_at"] == NOW


def test_unparseable_address_exports_null_with_warning(tmp_path):
    catalog = _seeded_catalog()
    try:
        catalog.connection.execute(
            "UPDATE photos SET place_address_json = 'no-soy-json', "
            "place_display_name = 'Calle de los terceros' WHERE hash = ?",
            (PHOTO_B,),
        )
        catalog.commit()
        summary = _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    places = _read_json(tmp_path / "destino" / "places.json")["places"]
    broken = next(place for place in places if place["photo"] == PHOTO_B)
    assert broken["address"] is None
    assert broken["display_name"] == "Calle de los terceros"
    assert summary.address_warnings == 1


# ----------------------------------------------------------------------
# Escritura atómica y dry-run
# ----------------------------------------------------------------------
def test_destination_keeps_previous_content_if_writing_fails(
    tmp_path, monkeypatch
):
    catalog = _seeded_catalog()
    try:
        out_dir = tmp_path / "destino"
        out_dir.mkdir()
        destination = out_dir / "tags.json"
        destination.write_text("contenido anterior\n", encoding="utf-8")

        def broken_replace(source, target):  # pragma: no cover - controlado abajo
            raise OSError("simulado")

        monkeypatch.setattr(exporter.os, "replace", broken_replace)
        with pytest.raises(TransferError, match="No se pudo escribir"):
            _export(catalog.connection, tmp_path)

        # El destino no quedó truncado ni hay temporales a medias.
        assert destination.read_text(encoding="utf-8") == "contenido anterior\n"
        assert [item.name for item in out_dir.iterdir()] == ["tags.json"]

        monkeypatch.undo()
        summary = _export(catalog.connection, tmp_path)
        assert summary.dry_run is False
        assert destination.read_text(encoding="utf-8") != "contenido anterior\n"
    finally:
        catalog.close()


def test_dry_run_does_not_write(tmp_path):
    catalog = _seeded_catalog()
    try:
        summary = _export(catalog.connection, tmp_path, dry_run=True)
    finally:
        catalog.close()

    assert summary.dry_run is True
    assert summary.tags == 3
    assert summary.assignments == 3
    assert summary.places == 2
    assert summary.sizes["tags"] > 0
    assert summary.sizes["places"] > 0
    assert not (tmp_path / "destino").exists()


# ----------------------------------------------------------------------
# Privacidad del resumen
# ----------------------------------------------------------------------
def test_summary_contains_no_names_or_addresses(tmp_path):
    catalog = _seeded_catalog()
    try:
        summary = _export(catalog.connection, tmp_path)
    finally:
        catalog.close()

    dumped = json.dumps(summary.to_dict(), ensure_ascii=False)
    for private in ("Marta", "marta", "Lisboa", "Rua Augusta", "Calle de los"):
        assert private not in dumped


# ----------------------------------------------------------------------
# Validación pura del modelo
# ----------------------------------------------------------------------
def test_validation_rejects_bad_values():
    assert model.validate_photo_hash("sha256:abc") is not None
    assert model.validate_photo_hash(PHOTO_A) is None
    assert model.validate_tag_kind("album") is not None
    assert model.validate_assignment_source("xmp_import") is not None
    assert model.validate_assignment_status("archived") is not None
    assert model.validate_confidence(1.5) is not None
    assert model.validate_confidence(None) is None
    assert model.validate_region((0.0, 0.0, 0.0, 0.5)) is not None  # w = 0
    assert model.validate_region((1.0, 1.0, 0.5, 0.5)) is None
    assert model.validate_region(None) is None


def test_require_valid_tags_file_rejects_out_of_range_region():
    data = model.TagsFile(
        generated_at=GENERATED,
        tags=(model.TagEntry("A", "a", "free", NOW, NOW),),
        assignments=(
            model.AssignmentEntry(
                photo=PHOTO_A,
                tag="a",
                source="manual",
                status="confirmed",
                confidence=None,
                region=(0.1, 0.2, 1.5, 0.4),  # w > 1
                created_at=NOW,
                updated_at=NOW,
            ),
        ),
    )
    with pytest.raises(TransferError, match="región"):
        model.require_valid_tags_file(data)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _seeded_library(root: Path) -> Path:
    (root / ".foto").mkdir(parents=True)
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        _seed_photos(catalog.connection)
        _seed_tags(catalog.connection)
        _seed_assignments(catalog.connection)
        _seed_places(catalog.connection)
        catalog.commit()
    finally:
        catalog.close()
    return root / ".foto" / "catalog.sqlite"


def test_cli_exports_both_files(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)
    out_dir = tmp_path / "salida"

    code = main(["--library", str(root), "--out", str(out_dir)])

    assert code == 0
    tags_data = _read_json(out_dir / "tags.json")
    places_data = _read_json(out_dir / "places.json")
    assert tags_data["format"] == "foto/tags"
    assert tags_data["counts"] == {"tags": 3, "assignments": 3}
    assert places_data["format"] == "foto/places"
    assert places_data["counts"] == {"photos": 2}


def test_cli_selection_and_json_summary(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    _seeded_library(root)

    code = main(
        ["--library", str(root), "--tags", "--out", str(tmp_path / "solo-tags"), "--json"]
    )
    captured = capsys.readouterr()

    assert code == 0  # sin places.json no hay avisos de direcciones
    summary = json.loads(captured.out)
    assert summary["tags"] == 3
    assert summary["assignments"] == 3
    assert summary["places"] == 0
    assert summary["address_warnings"] == 0
    assert set(summary["destinations"]) == {"tags"}
    assert (tmp_path / "solo-tags" / "places.json").exists() is False


def test_cli_places_only(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)

    code = main(
        ["--library", str(root), "--places", "--out", str(tmp_path / "solo-lugares")]
    )

    assert code == 0
    assert (tmp_path / "solo-lugares" / "tags.json").exists() is False
    assert _read_json(tmp_path / "solo-lugares" / "places.json")["counts"] == {"photos": 2}


def test_cli_default_out_is_exports_dir(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)

    code = main(["--library", str(root)])

    assert code == 0
    assert (root / ".foto" / "exports" / "tags.json").exists()
    assert (root / ".foto" / "exports" / "places.json").exists()


def test_cli_address_warning_returns_1(tmp_path):
    root = tmp_path / "biblioteca"
    catalog_path = _seeded_library(root)

    import sqlite3

    conn = sqlite3.connect(catalog_path)
    try:
        conn.execute(
            "UPDATE photos SET place_address_json = 'no-soy-json' WHERE hash = ?",
            (PHOTO_B,),
        )
        conn.commit()
    finally:
        conn.close()

    code = main(["--library", str(root), "--out", str(tmp_path / "salida")])

    assert code == 1
    places = _read_json(tmp_path / "salida" / "places.json")["places"]
    broken = next(place for place in places if place["photo"] == PHOTO_B)
    assert broken["address"] is None


def test_cli_without_catalog_returns_2(tmp_path, capsys):
    root = tmp_path / "vacia"
    root.mkdir()

    code = main(["--library", str(root), "--out", str(tmp_path / "nada")])
    captured = capsys.readouterr()

    assert code == 2
    assert "foto-index" in captured.err


def test_cli_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "biblioteca"
    _seeded_library(root)

    code = main(["--library", str(root), "--out", str(tmp_path / "nada"), "--dry-run"])

    assert code == 0
    assert not (tmp_path / "nada").exists()


# ----------------------------------------------------------------------
# --metadata (ADR 0008, § 3.4): foto-export es solo lectura
# ----------------------------------------------------------------------
def test_cli_metadata_reads_the_catalog_in_the_external_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = tmp_path / "biblioteca"
    root.mkdir()
    external = tmp_path / "metainfo-externa"
    external.mkdir()
    catalog = Catalog.open(external / "catalog.sqlite")
    try:
        _seed_photos(catalog.connection)
        _seed_tags(catalog.connection)
        _seed_assignments(catalog.connection)
        _seed_places(catalog.connection)
        catalog.commit()
    finally:
        catalog.close()

    code = main(
        ["--library", str(root), "--metadata", str(external),
         "--out", str(tmp_path / "salida")]
    )

    assert code == 0
    assert _read_json(tmp_path / "salida" / "tags.json")["counts"]["tags"] == 3


def test_cli_metadata_default_out_is_the_metadata_exports_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = tmp_path / "biblioteca"
    root.mkdir()
    external = tmp_path / "metainfo-externa"
    external.mkdir()
    catalog = Catalog.open(external / "catalog.sqlite")
    try:
        _seed_photos(catalog.connection)
        catalog.commit()
    finally:
        catalog.close()

    code = main(["--library", str(root), "--metadata", str(external)])

    assert code == 0
    assert (external / "exports" / "tags.json").exists()
    assert not (root / ".foto").exists()


def test_cli_metadata_missing_dir_returns_2_and_creates_nothing(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    root.mkdir()
    external = tmp_path / "no-existe"

    code = main(["--library", str(root), "--metadata", str(external)])
    captured = capsys.readouterr()

    assert code == 2
    assert "No hay metainformación" in captured.err
    assert not external.exists()


def test_cli_metadata_environment_is_respected(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    root.mkdir()
    external = tmp_path / "metainfo-entorno"
    external.mkdir()
    catalog = Catalog.open(external / "catalog.sqlite")
    try:
        _seed_photos(catalog.connection)
        catalog.commit()
    finally:
        catalog.close()
    monkeypatch.setenv("FOTO_METADATA", str(external))

    code = main(
        ["--library", str(root), "--out", str(tmp_path / "salida"), "--quiet"]
    )

    assert code == 0
    assert (tmp_path / "salida" / "places.json").exists()


def test_cli_export_help_documents_the_metadata_chain(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--metadata" in output
    assert "FOTO_METADATA" in output
    assert "[metadata].path" in output
