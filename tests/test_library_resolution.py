"""Tests del resolvedor compartido de biblioteca (C03)."""

from __future__ import annotations

from pathlib import Path

import pytest

from foto.storage.config import ConfigError
from foto.storage.resolver import (
    ORIGIN_CONFIG,
    ORIGIN_ENVIRONMENT,
    ORIGIN_OPTION,
    resolve_library_path,
)


def test_option_takes_precedence_without_reading_config(tmp_path, monkeypatch):
    from_option = tmp_path / "biblioteca-cli"
    from_env = tmp_path / "biblioteca-env"
    config = tmp_path / "config.toml"
    config.write_text("version = \"roto\"")  # inválida: no debe leerse
    monkeypatch.setenv("FOTO_LIBRARY", str(from_env))

    resolution = resolve_library_path(from_option, config_path=config)

    assert resolution.library_path == from_option.resolve()
    assert resolution.origin == ORIGIN_OPTION


def test_environment_beats_config(tmp_path, monkeypatch):
    from_env = tmp_path / "biblioteca-env"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[library]\npath = "{tmp_path / "otra"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FOTO_LIBRARY", str(from_env))

    resolution = resolve_library_path(config_path=config)

    assert resolution.library_path == from_env.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_config_path_used_when_no_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    from_config = tmp_path / "biblioteca-config"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[library]\npath = "{from_config}"\n',
        encoding="utf-8",
    )

    resolution = resolve_library_path(config_path=config)

    assert resolution.library_path == from_config.resolve()
    assert resolution.origin == ORIGIN_CONFIG


def test_relative_environment_path_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("FOTO_LIBRARY", "biblioteca-relativa")
    monkeypatch.chdir(tmp_path)

    resolution = resolve_library_path()

    assert resolution.library_path == (tmp_path / "biblioteca-relativa").resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_option_supports_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    resolution = resolve_library_path(Path("~/biblioteca-anonima"))

    assert resolution.library_path == (tmp_path / "biblioteca-anonima").resolve()
    assert resolution.origin == ORIGIN_OPTION


def test_no_sources_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    nonexistent = tmp_path / "config.toml"

    resolution = resolve_library_path(config_path=nonexistent)

    assert resolution.library_path is None
    assert resolution.origin == ORIGIN_CONFIG


def test_invalid_config_propagates_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    config = tmp_path / "config.toml"
    config.write_text("version = 99\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="más nueva"):
        resolve_library_path(config_path=config)


def test_environment_injected_mapping_respected(tmp_path):
    from_env = tmp_path / "biblioteca-map"
    resolution = resolve_library_path(
        environment={"FOTO_LIBRARY": str(from_env)},
        config_path=tmp_path / "no-leído.toml",
    )

    assert resolution.library_path == from_env.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT
