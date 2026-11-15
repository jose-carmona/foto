"""Tests del scanner y de su política declarativa (ADR 0009, paso 2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from foto.core.errors import LibraryError
from foto.core.scanner import (
    APPLE_VARIANT_PATTERN,
    LibraryScanner,
    ScanIssue,
    ScanPolicy,
    apple_photos_scan_policy,
    is_apple_photos_library,
)
from tests.conftest import write_image


def _scan(root: Path, policy: ScanPolicy | None = None):
    """Recorre una raíz y separa ficheros de incidencias."""
    scanner = LibraryScanner(root, policy=policy)
    files: list[str] = []
    issues: list[ScanIssue] = []
    for event in scanner.scan():
        if isinstance(event, ScanIssue):
            issues.append(event)
        else:
            files.append(event.rel_path)
    return files, issues, scanner.complete


def test_is_apple_photos_library_detects_the_suffix_without_case(tmp_path):
    assert is_apple_photos_library(tmp_path / "Fotos.photoslibrary")
    assert is_apple_photos_library(tmp_path / "BIBLIOTECA.PHOTOSLIBRARY")
    assert not is_apple_photos_library(tmp_path / "Fotos")
    assert not is_apple_photos_library(tmp_path / "fotos.photoslibraryx")


def test_variant_pattern_matches_uuid_with_numeric_suffix_only():
    uuid = "1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D"
    assert APPLE_VARIANT_PATTERN.match(f"{uuid}_1")
    assert APPLE_VARIANT_PATTERN.match(f"{uuid}_12")
    assert not APPLE_VARIANT_PATTERN.match(uuid)
    assert not APPLE_VARIANT_PATTERN.match(f"{uuid}_1_201_a")
    assert not APPLE_VARIANT_PATTERN.match("Fotito_1")


def test_default_scan_is_unchanged(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a" / "una.jpg", b"a")
    write_image(root / "b" / "c" / "dos.jpeg", b"b")
    write_image(root / "notas.txt", b"texto")
    write_image(root / ".foto" / "catalog.sqlite", b"catalogo")
    (root / "enlace.jpg").symlink_to(root / "a" / "una.jpg")

    files, issues, complete = _scan(root)

    assert set(files) == {"a/una.jpg", "b/c/dos.jpeg"}
    assert issues == []
    assert complete


def test_policy_without_rules_reproduces_the_default_scan(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a" / "una.jpg", b"a")
    write_image(root / "otra.png", b"b")

    default, _, default_complete = _scan(root)
    with_empty_policy, _, policy_complete = _scan(root, policy=ScanPolicy())

    assert with_empty_policy == default
    assert policy_complete == default_complete


def test_apple_policy_restricts_the_scan_to_originals(tmp_path):
    root = tmp_path / "Fotos.photoslibrary"
    write_image(root / "originals" / "1" / "ASSET.jpeg", b"base")
    write_image(root / "originals" / "A" / "OTRO.png", b"otro")
    write_image(root / "resources" / "renders" / "ASSET_1_201_a.jpeg", b"render")
    write_image(root / "database" / "Photos.sqlite", b"bd")

    files, issues, complete = _scan(root, policy=apple_photos_scan_policy())

    assert set(files) == {"originals/1/ASSET.jpeg", "originals/A/OTRO.png"}
    assert issues == []
    assert complete


def test_apple_policy_excludes_variant_files_in_originals(tmp_path):
    root = tmp_path / "Fotos.photoslibrary"
    write_image(
        root / "originals" / "1" / "1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D.jpeg", b"base"
    )
    write_image(
        root / "originals" / "1" / "1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D_1.jpeg",
        b"variante",
    )

    files, _, _ = _scan(root, policy=apple_photos_scan_policy())

    assert files == ["originals/1/1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D.jpeg"]


def test_policy_can_exclude_directories_by_name(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "quedan" / "una.jpg", b"a")
    write_image(root / "fuera" / "dos.jpg", b"b")
    policy = ScanPolicy(excluded_dir_names=frozenset({"fuera"}))

    files, _, _ = _scan(root, policy=policy)

    assert files == ["quedan/una.jpg"]


def test_policy_extensions_override_the_scanner_defaults(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "una.jpg", b"a")
    write_image(root / "dos.png", b"b")
    policy = ScanPolicy(extensions=frozenset({".png"}))

    files, _, _ = _scan(root, policy=policy)

    assert files == ["dos.png"]


def test_policy_symlinks_and_dot_foto_are_ignored_as_today(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "originals" / "una.jpg", b"a")
    write_image(root / "originals" / ".foto" / "x.jpg", b"catalogo")
    (root / "originals" / "enlace.jpg").symlink_to(root / "originals" / "una.jpg")

    files, _, complete = _scan(root, policy=apple_photos_scan_policy())

    assert files == ["originals/una.jpg"]
    assert complete


def test_policy_missing_include_root_is_an_explicit_error(tmp_path):
    root = tmp_path / "Fotos.photoslibrary"
    root.mkdir()

    with pytest.raises(LibraryError, match="originals"):
        _scan(root, policy=apple_photos_scan_policy())


def test_permission_error_on_apple_package_names_full_disk_access(
    tmp_path, monkeypatch
):
    root = tmp_path / "Fotos.photoslibrary"
    (root / "originals").mkdir(parents=True)

    def denied(path):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "scandir", denied)
    files, issues, complete = _scan(root, policy=apple_photos_scan_policy())

    assert files == []
    assert not complete
    assert len(issues) == 1
    assert issues[0].rel_path == "originals"
    assert "Full Disk Access" in issues[0].message
    assert "TCC" in issues[0].message


def test_permission_error_on_a_normal_library_keeps_the_plain_reason(
    tmp_path, monkeypatch
):
    root = tmp_path / "biblioteca"
    root.mkdir()

    def denied(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "scandir", denied)
    _, issues, complete = _scan(root)

    assert not complete
    assert issues[0].message == "Permission denied"
