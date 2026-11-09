"""Tests de la CLI y de los códigos de salida."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from foto.indexer.cli import main
from foto.storage.library import ensure_library
from foto.storage.lock import IndexerLock
from tests.conftest import read_photos, write_image


def _write_config(path: Path, library: Path) -> Path:
    path.write_text(
        f'version = 1\n\n[library]\npath = "{library.as_posix()}"\n',
        encoding="utf-8",
    )
    return path


def test_help_documents_precedence_and_exit_codes(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "FOTO_LIBRARY" in output
    assert "códigos de salida" in output
    assert "--prune-after" in output


def test_cli_indexes_and_emits_valid_json(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "foto.jpg", b"cli")

    code = main(
        [
            "--library",
            str(root),
            "--config",
            str(tmp_path / "config-inexistente.toml"),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert code == 0
    assert summary["status"] == "ok"
    assert summary["phases"]["scan"]["added"] == 1
    assert summary["totals"]["photos"] == 1
    assert summary["totals"]["metadata_pending"] == 1
    assert summary["phases"]["scan"]["hashed_files"] == 1
    assert summary["phases"]["scan"]["metadata_extracted"] == 0
    assert summary["phases"]["geocode"]["status"] == "ok"
    assert summary["phases"]["geocode"]["candidates"] == 0
    assert (root / ".foto" / "library.toml").is_file()
    assert (root / ".foto" / "catalog.sqlite").is_file()
    assert next(iter(read_photos(root).values()))["metadata_status"] == "pending"


def test_cli_text_summary_shows_work_done(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "foto.jpg", b"resumen")

    code = main(["--library", str(root)])

    assert code == 0
    output = capsys.readouterr().out
    assert "ficheros_hasheados=1" in output
    assert "metadatos_extraidos=0" in output


def test_cli_missing_root_exits_2_and_creates_nothing(tmp_path, capsys):
    root = tmp_path / "no-existe"

    code = main(["--library", str(root), "--json"])

    assert code == 2
    assert not root.exists()
    assert "no existe" in capsys.readouterr().err


def test_cli_without_library_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    monkeypatch.delenv("FOTO_CONFIG", raising=False)

    code = main(["--config", str(tmp_path / "config.toml"), "--json"])

    assert code == 2
    assert "No hay biblioteca" in capsys.readouterr().err


def test_cli_uses_library_from_config(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    root = tmp_path / "biblioteca-config"
    write_image(root / "foto.jpg", b"config")
    config = _write_config(tmp_path / "config.toml", root)

    code = main(["--config", str(config), "--json"])

    assert code == 0
    assert (root / ".foto" / "catalog.sqlite").is_file()


def test_cli_uses_library_from_environment_and_foto_config(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    root = tmp_path / "biblioteca-entorno"
    write_image(root / "foto.jpg", b"entorno")
    config = _write_config(tmp_path / "config.toml", tmp_path / "otra")
    monkeypatch.setenv("FOTO_CONFIG", str(config))
    monkeypatch.setenv("FOTO_LIBRARY", str(root))

    code = main(["--json"])

    assert code == 0
    assert (root / ".foto" / "catalog.sqlite").is_file()


def test_cli_library_option_beats_environment(tmp_path, monkeypatch):
    from_cli = tmp_path / "biblioteca-cli"
    from_env = tmp_path / "biblioteca-env"
    write_image(from_cli / "foto.jpg", b"cli")
    write_image(from_env / "foto.jpg", b"env")
    monkeypatch.setenv("FOTO_LIBRARY", str(from_env))

    code = main(["--library", str(from_cli), "--json"])

    assert code == 0
    assert (from_cli / ".foto" / "catalog.sqlite").is_file()
    assert not (from_env / ".foto").exists()


def test_cli_lock_busy_exits_3(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    root.mkdir()
    ensure_library(root)
    lock = IndexerLock(root / ".foto" / "index.lock")
    lock.acquire()
    try:
        code = main(["--library", str(root), "--json"])
    finally:
        lock.release()

    assert code == 3
    assert "Otro proceso" in capsys.readouterr().err


def test_cli_lock_busy_leaves_pending_schema_untouched(tmp_path, capsys):
    """Un lock ocupado no debe configurar ni migrar el catálogo (I02)."""
    root = tmp_path / "biblioteca"
    root.mkdir()
    ensure_library(root)
    catalog_path = root / ".foto" / "catalog.sqlite"
    connection = sqlite3.connect(catalog_path)
    connection.execute("PRAGMA user_version = 0")
    connection.close()

    lock = IndexerLock(root / ".foto" / "index.lock")
    lock.acquire()
    try:
        code = main(["--library", str(root), "--json"])
    finally:
        lock.release()

    connection = sqlite3.connect(catalog_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert code == 3
    assert "Otro proceso" in capsys.readouterr().err
    assert version == 0
    assert journal.lower() != "wal"
    assert tables == 0


def test_cli_lock_busy_does_not_create_the_catalog(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    root.mkdir()
    ensure_library(root)
    catalog_path = root / ".foto" / "catalog.sqlite"

    lock = IndexerLock(root / ".foto" / "index.lock")
    lock.acquire()
    try:
        code = main(["--library", str(root), "--json"])
    finally:
        lock.release()

    assert code == 3
    assert not catalog_path.exists()


def test_cli_partial_scan_exits_1(tmp_path, monkeypatch, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "visible.jpg", b"visible")
    blocked = root / "bloqueado"
    write_image(blocked / "oculta.jpg", b"oculta")
    ensure_library(root)

    scanner_module = importlib.import_module("foto.core.scanner")
    real_scandir = os.scandir

    def failing_scandir(path):
        if os.fspath(path) == os.fspath(blocked):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", failing_scandir)
    code = main(["--library", str(root), "--json"])
    summary = json.loads(capsys.readouterr().out)

    assert code == 1
    assert summary["status"] == "partial"
    assert summary["phases"]["scan"]["missing"] == 0


def test_cli_dry_run_does_not_create_anything(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    root.mkdir()
    write_image(root / "foto.jpg", b"dry")

    code = main(["--library", str(root), "--dry-run", "--json"])
    summary = json.loads(capsys.readouterr().out)

    assert code == 0
    assert summary["dry_run"] is True
    assert summary["phases"]["scan"]["added"] == 1
    assert not (root / ".foto").exists()


def test_cli_quiet_suppresses_summary(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "foto.jpg", b"quiet")

    code = main(["--library", str(root), "--quiet"])

    assert code == 0
    assert capsys.readouterr().out == ""


def test_cli_newer_schema_exits_2(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    root.mkdir()
    ensure_library(root)
    catalog_path = root / ".foto" / "catalog.sqlite"
    connection = sqlite3.connect(catalog_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    code = main(["--library", str(root), "--json"])

    assert code == 2
    assert "más nuevo" in capsys.readouterr().err


def test_importing_indexer_does_not_import_qt():
    source = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import foto.indexer; print('PySide6' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


# ----------------------------------------------------------------------
# Pipeline de fases: --skip, geocodificación y --dry-run
# ----------------------------------------------------------------------
def test_skip_rejects_unknown_phase_with_exit_code_2(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--library", "/no-importa", "--skip", "minimizar"])

    assert raised.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_skip_geocode_leaves_text_summary_noting_it(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")

    code = main(["--library", str(root), "--skip", "geocode"])

    assert code == 0
    output = capsys.readouterr().out
    assert "geocode: omitida" in output
    assert "--skip geocode" in output


def test_skip_scan_and_geocode_run_no_phases(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")

    code = main(
        ["--library", str(root), "--skip", "scan", "--skip", "geocode", "--json"]
    )
    summary = json.loads(capsys.readouterr().out)

    assert code == 0
    assert summary["phases"]["scan"]["status"] == "skipped"
    assert summary["phases"]["geocode"]["status"] == "skipped"
    assert summary["phases"]["scan"]["scanned"] == 0


def test_geocoding_disabled_by_config_is_skipped(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n\n[library]\npath = "{root.as_posix()}"\n'
        f"\n[geocoding]\nenabled = false\n",
        encoding="utf-8",
    )

    code = main(["--config", str(config)])
    output = capsys.readouterr()

    assert code == 0
    assert "geocode: omitida" in output.out
    assert "enabled = false" in output.err


def test_invalid_geocoding_config_exits_2(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        "[geocoding]\nrequest_interval_seconds = -1\n", encoding="utf-8"
    )

    code = main(["--library", str(tmp_path / "no-importa"), "--config", str(config)])

    assert code == 2
    assert "request_interval_seconds" in capsys.readouterr().err


def test_dry_run_reports_geocode_candidates_without_network(tmp_path, capsys, monkeypatch):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    monkeypatch.setattr(
        "foto.indexer.cli.ExifToolExtractor.from_environment",
        classmethod(lambda cls: _StaticExtractor()),
    )
    monkeypatch.setattr(
        "foto.indexer.cli.NominatimGeocoder", lambda *a, **kw: _FailingGeocoder()
    )

    code = main(["--library", str(root), "--dry-run"])

    assert code == 0
    output = capsys.readouterr().out
    assert "geocode: candidatas=1" in output
    assert "celdas_a_pedir=1" in output
    assert "sin red" in output


def test_cli_run_geocodes_with_injected_service(tmp_path, capsys, monkeypatch):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    monkeypatch.setattr(
        "foto.indexer.cli.ExifToolExtractor.from_environment",
        classmethod(lambda cls: _StaticExtractor()),
    )
    geocoder = _StubGeocoder()
    monkeypatch.setattr("foto.indexer.cli.NominatimGeocoder", lambda *a, **kw: geocoder)

    code = main(["--library", str(root), "--json"])
    summary = json.loads(capsys.readouterr().out)
    photos = read_photos(root)

    assert code == 0
    assert summary["phases"]["geocode"]["geocoded_ok"] == 1
    assert summary["phases"]["geocode"]["http_requests"] == 1
    assert next(iter(photos.values()))["place_city"] == "Madrid"


class _StaticExtractor:
    """Extractor sin exiftool que devuelve metadatos fijos con GPS."""

    def extract_batch(self, paths):
        from foto.core.metadata import ExtractionOutcome, PhotoMetadata

        metadata = PhotoMetadata(
            file_type="JPEG",
            gps_latitude=40.4168,
            gps_longitude=-3.7038,
        )
        return {path: ExtractionOutcome(metadata=metadata) for path in paths}


class _FailingGeocoder:
    """Geocodificador de la CLI de dry-run: no debe llamarse nunca."""

    def reverse(self, latitude, longitude):
        raise AssertionError("La simulación no debe tocar la red")


class _StubGeocoder:
    """Geocodificador fijo inyectado en la CLI."""

    from foto.core.geocoding import Place

    def __init__(self):
        from tests.test_geocoding import make_place

        self._place = make_place()

    def reverse(self, latitude, longitude):
        return self._place
