"""Tests de identidad por hash de contenido."""

from __future__ import annotations

import os

from foto.core.hashing import HASH_PREFIX, Sha256Hasher, hash_bytes
from tests.conftest import write_image


def test_hash_format_and_prefix(tmp_path):
    path = write_image(tmp_path / "foto.jpg", b"contenido")
    digest = Sha256Hasher().hash_file(path)

    assert digest.startswith(HASH_PREFIX)
    prefix, value = digest.split(":", 1)
    assert prefix == "sha256"
    assert len(value) == 64
    assert value == value.lower()


def test_same_bytes_same_hash_regardless_of_path_name_and_mtime(tmp_path):
    first = write_image(tmp_path / "a" / "una.jpg", b"mismos-bytes")
    second = write_image(tmp_path / "b" / "otra.jpeg", b"mismos-bytes")
    os.utime(second, ns=(1_000_000_000, 1_000_000_000))

    hasher = Sha256Hasher()
    assert hasher.hash_file(first) == hasher.hash_file(second)


def test_different_bytes_different_hash(tmp_path):
    first = write_image(tmp_path / "una.jpg", b"contenido-a")
    second = write_image(tmp_path / "otra.jpg", b"contenido-b")

    assert Sha256Hasher().hash_file(first) != Sha256Hasher().hash_file(second)


def test_hash_bytes_matches_file_hash(tmp_path):
    data = b"bytes-pequenos"
    path = write_image(tmp_path / "foto.jpg", data)

    assert hash_bytes(data) == Sha256Hasher().hash_file(path)
