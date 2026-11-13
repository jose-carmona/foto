"""Tests de la cadena de resolución del fichero de configuración (ADR 0001).

Estado del paso 1 del ADR 0008: ``resolve_config_path`` aplica
``--config`` → ``FOTO_CONFIG`` → ``platformdirs`` y fija el primer eslabón
que el paso 2 comparte con los demás.
"""

from __future__ import annotations

from pathlib import Path

from foto.storage.config import get_default_config_path, resolve_config_path


def test_option_beats_environment(tmp_path, monkeypatch):
    from_option = tmp_path / "config-cli.toml"
    from_env = tmp_path / "config-env.toml"
    monkeypatch.setenv("FOTO_CONFIG", str(from_env))

    resolved = resolve_config_path(from_option)

    assert resolved == from_option.resolve()


def test_environment_beats_platformdirs_default(tmp_path, monkeypatch):
    from_env = tmp_path / "config-env.toml"
    monkeypatch.setenv("FOTO_CONFIG", str(from_env))

    resolved = resolve_config_path()

    assert resolved == from_env.resolve()
    assert resolved != get_default_config_path()


def test_platformdirs_default_without_sources(monkeypatch):
    monkeypatch.delenv("FOTO_CONFIG", raising=False)

    resolved = resolve_config_path()

    assert resolved == get_default_config_path().expanduser().resolve()
    assert resolved.is_absolute()


def test_environment_supports_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOTO_CONFIG", "~/config-de-env.toml")

    resolved = resolve_config_path()

    assert resolved == (tmp_path / "config-de-env.toml").resolve()


def test_option_supports_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = resolve_config_path(Path("~/config-de-opcion.toml"))

    assert resolved == (tmp_path / "config-de-opcion.toml").resolve()


def test_relative_environment_path_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("FOTO_CONFIG", "config-relativa.toml")
    monkeypatch.chdir(tmp_path)

    resolved = resolve_config_path()

    assert resolved == (tmp_path / "config-relativa.toml").resolve()
