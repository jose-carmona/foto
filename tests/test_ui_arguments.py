"""Argumentos y resolución de ubicaciones de la UI ``foto`` (ADR 0008, § 3.5).

El parseo y la resolución no necesitan una instancia de Qt: se ejercitan los
mismos resolutores que los CLIs. ``load_photos`` con metainformación externa
tampoco (ver ``tests/test_ui_load_photos.py``).
"""

from __future__ import annotations

import pytest

from foto.core.errors import LibraryError
from foto.storage.catalog import Catalog
from foto.storage.library import ensure_library
from foto.ui.app import (
    _build_ui_parser,
    _resolve_before_dialog,
    _resolve_metadata_dir,
    load_photos,
)
from tests.conftest import write_image


def _parse(*argv: str):
    return _build_ui_parser().parse_args(list(argv))


def test_ui_parser_accepts_the_three_flags(tmp_path):
    args = _parse(
        "--config", str(tmp_path / "config.toml"),
        "--library", str(tmp_path / "biblioteca"),
        "--metadata", str(tmp_path / "metainfo"),
    )

    assert args.config == tmp_path / "config.toml"
    assert args.library == tmp_path / "biblioteca"
    assert args.metadata == tmp_path / "metainfo"


def test_ui_parser_defaults_to_none():
    args = _parse()

    assert args.config is None
    assert args.library is None
    assert args.metadata is None


def test_ui_help_documents_the_three_location_chains(capsys):
    with pytest.raises(SystemExit) as raised:
        _parse("--help")

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--library" in output
    assert "FOTO_LIBRARY" in output
    assert "[library].path" in output
    assert "--metadata" in output
    assert "FOTO_METADATA" in output
    assert "[metadata].path" in output
    assert "--config" in output


def test_ui_resolves_the_library_like_the_clis(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    from_config = tmp_path / "biblioteca-config"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n\n[library]\npath = "{from_config}"\n',
        encoding="utf-8",
    )
    args = _parse("--config", str(config))

    config_loaded, library_path = _resolve_before_dialog(args)

    assert library_path == from_config.resolve()
    assert config_loaded.config_path == config.resolve()


def test_ui_library_option_beats_the_environment(tmp_path, monkeypatch):
    from_option = tmp_path / "biblioteca-opcion"
    from_env = tmp_path / "biblioteca-entorno"
    monkeypatch.setenv("FOTO_LIBRARY", str(from_env))
    args = _parse("--library", str(from_option))

    _, library_path = _resolve_before_dialog(args)

    assert library_path == from_option.resolve()


def test_ui_metadata_follows_the_same_chain_as_the_clis(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = tmp_path / "biblioteca"
    root.mkdir()
    from_config = tmp_path / "metainfo-config"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n\n[metadata]\npath = "{from_config}"\n',
        encoding="utf-8",
    )
    args = _parse("--config", str(config))

    metadata_dir = _resolve_metadata_dir(root, args)

    assert metadata_dir == from_config.resolve()


def test_ui_metadata_option_beats_the_environment(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    root.mkdir()
    from_option = tmp_path / "metainfo-opcion"
    from_env = tmp_path / "metainfo-entorno"
    monkeypatch.setenv("FOTO_METADATA", str(from_env))
    args = _parse("--metadata", str(from_option))

    metadata_dir = _resolve_metadata_dir(root, args)

    assert metadata_dir == from_option.resolve()


def test_ui_metadata_default_is_derived_from_the_library(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = tmp_path / "biblioteca"
    root.mkdir()
    args = _parse()

    metadata_dir = _resolve_metadata_dir(root, args)

    assert metadata_dir == root / ".foto"


def test_ui_metadata_invalid_path_raises_explicitly(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    not_a_dir = tmp_path / "fichero"
    not_a_dir.write_text("", encoding="utf-8")
    args = _parse("--metadata", str(not_a_dir))

    try:
        _resolve_metadata_dir(root, args)
    except LibraryError as exc:
        assert "no es un directorio" in str(exc)
    else:
        raise AssertionError("se esperaba LibraryError")


def test_ui_load_photos_reads_the_external_metadata_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = tmp_path / "biblioteca"
    write_image(root / "2026" / "una.jpg", b"externa")
    external = tmp_path / "metainfo-externa"

    assert ensure_library(root, external).created is True
    catalog = Catalog.open(external / "catalog.sqlite")
    try:
        catalog.insert_photo("sha256:" + "a" * 64, ".jpg", "JPEG", "2026-11-13T00:00:00")
        catalog.insert_location(
            rel_path="2026/una.jpg",
            photo_hash="sha256:" + "a" * 64,
            size=6,
            mtime_ns=1,
            inode=1,
            device=1,
            now="2026-11-13T00:00:00",
        )
        catalog.commit()
    finally:
        catalog.close()

    photos = load_photos(root, external)

    assert len(photos) == 1
    assert photos[0].name == "una.jpg"
    assert not (root / ".foto").exists()
