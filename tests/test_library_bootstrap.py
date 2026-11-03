"""Tests del descriptor ``.foto/library.toml``."""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from foto.core.errors import LibraryError
from foto.storage.library import (
    CREATION_LOCK_NAME,
    LIBRARY_VERSION,
    ensure_library,
    library_file,
    load_library,
    thumbs_dir,
)


def test_bootstrap_fresh_creation_lock_blocks_without_publishing(tmp_path):
    root = tmp_path / "biblioteca"
    (root / ".foto").mkdir(parents=True)
    lock = root / ".foto" / CREATION_LOCK_NAME
    lock.write_bytes(b"")
    # Lock reciente: el par está "en medio" de la creación.

    with pytest.raises(LibraryError, match="está creando"):
        ensure_library(root)

    assert not library_file(root).exists()
    assert lock.exists()
    assert not list((root / ".foto").glob(".library.*.tmp"))


def test_bootstrap_takes_over_a_stale_creation_lock(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    (root / ".foto").mkdir()
    lock = root / ".foto" / CREATION_LOCK_NAME
    lock.write_bytes(b"")
    old = time.time() - 120
    os.utime(lock, (old, old))

    result = ensure_library(root)

    assert result.created is True
    assert load_library(root).id == result.config.id
    assert not lock.exists()


def test_bootstrap_failure_mid_publish_leaves_no_partial_descriptor(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    root.mkdir()

    def failing_publish(temporary_path, path):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("foto.storage.library._publish", failing_publish)

    with pytest.raises(LibraryError, match="No se pudo crear library.toml"):
        ensure_library(root)

    assert not library_file(root).exists()
    assert not list((root / ".foto").glob(".library.*.tmp"))
    assert not (root / ".foto" / CREATION_LOCK_NAME).exists()


def test_bootstrap_concurrent_creators_produce_one_id(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()

    results: list[object] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(ensure_library(root))
        except (LibraryError, RuntimeError) as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    created_flags = [result.created for result in results]
    assert created_flags.count(True) == 1
    identifiers = {result.config.id for result in results}
    assert len(identifiers) == 1
    assert str(uuid.UUID(next(iter(identifiers)))) == next(iter(identifiers))
    descriptor = load_library(root)
    assert descriptor.id == next(iter(identifiers))
    assert descriptor.version == LIBRARY_VERSION


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


def test_thumbs_dir_lives_inside_foto_dir(tmp_path):
    root = tmp_path / "biblioteca"

    assert thumbs_dir(root) == root / ".foto" / "thumbs"


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
