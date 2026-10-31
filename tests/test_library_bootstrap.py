"""Tests del descriptor ``.foto/library.toml``."""

from __future__ import annotations

import uuid

import pytest

from foto.core.errors import LibraryError
from foto.storage.library import LIBRARY_VERSION, ensure_library, load_library


def test_bootstrap_creates_uuid_and_version_and_is_idempotent(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()

    first = ensure_library(root)
    second = ensure_library(root)

    assert first.created is True
    assert first.would_create is False
    assert second.created is False
    assert (root / ".foto" / "library.toml").is_file()

    config = load_library(root)
    assert config.version == LIBRARY_VERSION
    assert str(uuid.UUID(config.id)) == config.id
    assert config.id == first.config.id == second.config.id


def test_bootstrap_dry_run_does_not_create_anything(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()

    result = ensure_library(root, dry_run=True)

    assert result.would_create is True
    assert result.created is False
    assert not (root / ".foto").exists()


def test_bootstrap_rejects_missing_root(tmp_path):
    with pytest.raises(LibraryError):
        ensure_library(tmp_path / "no-existe")


def test_library_rejects_newer_version(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    (root / ".foto" / "library.toml").write_text(
        'version = 99\nid = "3f9c1b62-8a44-4f0e-9d1a-2c7b5e0f4a11"\n',
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="más nueva"):
        ensure_library(root)


def test_library_rejects_invalid_uuid(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    (root / ".foto" / "library.toml").write_text(
        'version = 1\nid = "no-es-un-uuid"\n',
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="UUID"):
        ensure_library(root)


def test_library_rejects_broken_toml(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    (root / ".foto" / "library.toml").write_text("version = [", encoding="utf-8")

    with pytest.raises(LibraryError, match="TOML"):
        ensure_library(root)


def test_bootstrap_falls_back_when_hardlinks_are_unsupported(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    root.mkdir()

    def unsupported_link(source, target, *args, **kwargs):
        raise OSError(45, "Operation not supported")

    monkeypatch.setattr("foto.storage.library.os.link", unsupported_link)
    result = ensure_library(root)

    assert result.created is True
    assert load_library(root).id == result.config.id
    assert not list((root / ".foto").glob(".library.*.tmp"))
