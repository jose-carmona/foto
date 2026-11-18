"""Indexado de una fototeca de Apple Photos (ADR 0009, paso 2).

Cubre la inyección del scanner en el ``Indexer``, el flujo incremental sobre
``originals/`` y la CLI con una biblioteca Apple sintética en
``tests/fixtures/apple-photoslibrary/``. La metainformación vive siempre
fuera del paquete, como exige el ADR 0009.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from foto.core.errors import LibraryError
from foto.core.hashing import hash_bytes
from foto.core.indexing import Indexer, IndexingOptions
from foto.core.scanner import LibraryScanner, apple_photos_scan_policy
from foto.indexer.cli import main
from foto.storage.catalog import Catalog
from foto.storage.config import Location
from tests.conftest import write_image

FIXTURE_PACKAGE = (
    Path(__file__).parent
    / "fixtures"
    / "apple-photoslibrary"
    / "Fotos.photoslibrary"
)

ORIGINAL_JPEG = "originals/1/1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D.jpeg"
ORIGINAL_PNG = "originals/A/ABCDEF01-2345-4678-9ABC-DEF012345678.png"
VARIANT_JPEG = "originals/1/1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D_1.jpeg"


def _apple_scanner_factory(root: Path) -> LibraryScanner:
    return LibraryScanner(root, policy=apple_photos_scan_policy())


def _run_index(
    root: Path,
    *,
    options: IndexingOptions | None = None,
    hasher=None,
    scanner: Callable[[Path], LibraryScanner] | None = None,
):
    """Indexa con metainformación externa al paquete, como manda el ADR 0009."""
    metadata = root.parent / "metainfo"
    metadata.mkdir(parents=True, exist_ok=True)
    declared = (Location("default", root, "fast"),)
    catalog = Catalog.open(metadata / "catalog.sqlite", declared_locations=declared)
    try:
        return Indexer(
            catalog,
            declared,
            options=options or IndexingOptions(),
            hasher=hasher,
            scanner=scanner,
        ).run()
    finally:
        catalog.close()


def _location_rel_paths(metadata: Path) -> set[str]:
    catalog = Catalog.open(metadata / "catalog.sqlite")
    try:
        rows = catalog._conn.execute("SELECT rel_path FROM files").fetchall()
        return {str(row["rel_path"]) for row in rows}
    finally:
        catalog.close()


def _build_synthetic_package(tmp_path: Path) -> Path:
    root = tmp_path / "Fotos.photoslibrary"
    write_image(root / ORIGINAL_JPEG, b"asset-base-jpeg")
    write_image(root / ORIGINAL_PNG, b"asset-base-png")
    write_image(root / VARIANT_JPEG, b"asset-editado")
    write_image(root / "resources" / "renders" / "1" / "ASSET_1_201_a.jpeg", b"render")
    write_image(root / "database" / "Photos.sqlite", b"base-datos")
    return root


class CountingHasher:
    """Hasher real que registra cada fichero leído."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def hash_file(self, path: Path) -> str:
        self.calls.append(Path(path))
        return hash_bytes(Path(path).read_bytes())


# ----------------------------------------------------------------------
# T2.2 — Inyección del scanner en el Indexer
# ----------------------------------------------------------------------
def test_indexer_indexes_only_originals_with_the_per_location_policy(tmp_path):
    """Sin scanner inyectado, la política Apple se aplica mirando la raíz."""
    root = _build_synthetic_package(tmp_path)
    hasher = CountingHasher()

    summary = _run_index(root, hasher=hasher)

    assert summary.status == "ok"
    assert summary.photos_total == 2
    assert summary.locations_total == 2
    assert summary.duplicate_photos == 0
    assert len(hasher.calls) == 2
    assert _location_rel_paths(root.parent / "metainfo") == {
        ORIGINAL_JPEG,
        ORIGINAL_PNG,
    }


def test_indexer_uses_the_injected_scanner_factory(tmp_path):
    """La factoría inyectada se llama con la ruta de la ubicación declarada."""
    root = _build_synthetic_package(tmp_path)
    calls: list[Path] = []

    def factory(path: Path) -> LibraryScanner:
        calls.append(Path(path))
        return LibraryScanner(path, policy=apple_photos_scan_policy())

    first = _run_index(root, scanner=factory)
    second = _run_index(root, scanner=factory)

    assert first.status == "ok"
    assert second.status == "ok"
    assert second.unchanged == 2
    assert second.hashed_files == 0
    assert calls == [root, root]


def test_incremental_flow_works_over_originals(tmp_path):
    root = _build_synthetic_package(tmp_path)
    _run_index(root, scanner=_apple_scanner_factory)

    # Sin cambios: nada se rehashea.
    hasher = CountingHasher()
    summary = _run_index(root, hasher=hasher, scanner=_apple_scanner_factory)
    assert summary.unchanged == 2
    assert hasher.calls == []

    # Contenido modificado: la ubicación se reasigna al nuevo hash.
    (root / ORIGINAL_JPEG).write_bytes(b"asset-base-jpeg-v2")
    summary = _run_index(
        root, hasher=CountingHasher(), scanner=_apple_scanner_factory
    )
    assert summary.modified == 1
    assert summary.hashed_files == 1

    # Borrado: la segunda pasada marca la ausencia (recorrido completo).
    (root / ORIGINAL_JPEG).unlink()
    summary = _run_index(root, scanner=_apple_scanner_factory)
    assert summary.missing == 1
    assert summary.status == "ok"
    locations = Catalog.open(root.parent / "metainfo" / "catalog.sqlite")
    try:
        rows = {
            str(row["rel_path"]): str(row["status"])
            for row in locations._conn.execute(
                "SELECT rel_path, status FROM files"
            )
        }
    finally:
        locations.close()
    assert rows[ORIGINAL_JPEG] == "missing"


def test_missing_originals_root_aborts_the_run_with_a_library_error(tmp_path):
    root = tmp_path / "Anomala.photoslibrary"
    root.mkdir()

    with pytest.raises(LibraryError, match="originals"):
        _run_index(root, scanner=_apple_scanner_factory)


# ----------------------------------------------------------------------
# T2.5 — Errores de TCC con acción sugerida
# ----------------------------------------------------------------------
def test_tcc_denial_reports_partial_run_without_marking_missing(
    tmp_path, monkeypatch, caplog
):
    root = _build_synthetic_package(tmp_path)
    _run_index(root, scanner=_apple_scanner_factory)

    def denied(path):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "scandir", denied)
    with caplog.at_level(logging.WARNING, logger="foto.core.scanner"):
        summary = _run_index(root, scanner=_apple_scanner_factory)

    assert summary.status == "partial"
    assert summary.errors == 1
    assert summary.missing == 0
    assert "Full Disk Access" in caplog.text


# ----------------------------------------------------------------------
# T2.3 — Detección y política de Apple en la CLI
# ----------------------------------------------------------------------
def _cli_args(tmp_path: Path, *extra: str) -> list[str]:
    config = tmp_path / "config.toml"
    config.write_text(
        "version = 1\n\n[library]\n"
        f"path = \"{FIXTURE_PACKAGE.as_posix()}\"\n",
        encoding="utf-8",
    )
    return ["--config", str(config), *extra]


def test_cli_rejects_the_retired_library_flag(tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--library", str(FIXTURE_PACKAGE)])

    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_indexes_the_synthetic_apple_library_without_duplicates(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    metadata = tmp_path / "metainfo"

    code = main(_cli_args(tmp_path, "--metadata", str(metadata), "--json"))
    summary = json.loads(capsys.readouterr().out)

    assert code == 0
    assert summary["status"] == "ok"
    scan = summary["phases"]["scan"]
    assert scan["scanned"] == 2
    assert scan["added"] == 2
    assert summary["totals"]["photos"] == 2
    assert summary["totals"]["locations"] == 2
    assert summary["totals"]["duplicate_photos"] == 0
    assert (metadata / "catalog.sqlite").is_file()
    # Ni resources/ ni database/ ni la variante editada entran al catálogo;
    # y no aparece ningún .foto/ dentro del paquete.
    assert _location_rel_paths(metadata) == {ORIGINAL_JPEG, ORIGINAL_PNG}
    assert not (FIXTURE_PACKAGE / ".foto").exists()


def test_cli_logs_the_applied_apple_policy(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    monkeypatch.delenv("FOTO_METADATA", raising=False)

    code = main(_cli_args(tmp_path, "--metadata", str(tmp_path / "metainfo")))

    assert code == 0
    assert "política de escaneo de Apple Photos aplicada" in capsys.readouterr().err


def test_cli_without_external_metadata_fails_for_an_apple_package(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    monkeypatch.delenv("FOTO_METADATA", raising=False)

    code = main(_cli_args(tmp_path))

    assert code == 2
    assert "no puede vivir dentro" in capsys.readouterr().err
    assert not (FIXTURE_PACKAGE / ".foto").exists()


def test_cli_metadata_inside_the_package_fails(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    monkeypatch.delenv("FOTO_METADATA", raising=False)

    code = main(
        _cli_args(
            tmp_path, "--metadata", str(FIXTURE_PACKAGE / "metainfo-dentro")
        )
    )

    assert code == 2
    assert "no puede vivir dentro" in capsys.readouterr().err
    assert not (FIXTURE_PACKAGE / "metainfo-dentro").exists()


def test_cli_anomalous_apple_package_without_originals_fails(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    package = tmp_path / "Rota.photoslibrary"
    package.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n\n[library]\npath = "{package.as_posix()}"\n',
        encoding="utf-8",
    )

    code = main(
        [
            "--config",
            str(config),
            "--metadata",
            str(tmp_path / "metainfo"),
        ]
    )

    assert code == 2
    assert "originals" in capsys.readouterr().err
