"""Tests del resolvedor de ubicaciones compartido (ADR 0010, T2.2).

La config es la única fuente de ubicaciones: ``resolve_locations()`` la lee
(v1 = única ``default``; v2 = lista) y ``FOTO_LIBRARY`` definida es un error
explícito. Los tests ya no inyectan la fototeca por entorno: usan configs de
fixture que los binarios reciben con ``--config``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foto.storage.config import ConfigError, Location
from foto.storage.resolver import (
    DEFAULT_LOCATION_NAME,
    DEFAULT_LOCATION_TIER,
    resolve_locations,
)


def _single(name: str, path: Path, tier: str = "fast") -> tuple[Location, ...]:
    return (Location(name=name, path=path, tier=tier),)


def test_v1_config_declares_a_single_default_location(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    from_config = tmp_path / "biblioteca-config"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[library]\npath = "{from_config}"\n',
        encoding="utf-8",
    )

    locations = resolve_locations(config_path=config)

    assert locations == _single(
        DEFAULT_LOCATION_NAME, from_config.resolve(), DEFAULT_LOCATION_TIER
    )


def test_v2_config_declares_the_list_in_order(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    config = tmp_path / "config.toml"
    config.write_text(
        "version = 2\n"
        "\n"
        "[[library.locations]]\n"
        'name = "ssd"\n'
        f'path = "{ssd}"\n'
        'tier = "fast"\n'
        "\n"
        "[[library.locations]]\n"
        'name = "usb"\n'
        f'path = "{usb}"\n'
        'tier = "archive"\n',
        encoding="utf-8",
    )

    locations = resolve_locations(config_path=config)

    assert locations == (
        Location(name="ssd", path=ssd.resolve(), tier="fast"),
        Location(name="usb", path=usb.resolve(), tier="archive"),
    )


def test_missing_config_declares_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)

    locations = resolve_locations(config_path=tmp_path / "ausente.toml")

    assert locations == ()


def test_v2_without_locations_or_path_is_valid_and_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    config = tmp_path / "config.toml"
    config.write_text("version = 2\n", encoding="utf-8")

    locations = resolve_locations(config_path=config)

    assert locations == ()


def test_relative_v1_path_resolves_against_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    config = tmp_path / "configs"
    config.mkdir()
    fichero = config / "config.toml"
    fichero.write_text(
        'version = 1\n[library]\npath = "biblioteca-relativa"\n',
        encoding="utf-8",
    )

    locations = resolve_locations(config_path=fichero)

    assert locations == _single(
        DEFAULT_LOCATION_NAME, tmp_path / "configs" / "biblioteca-relativa"
    )


def test_foto_library_environment_is_an_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FOTO_LIBRARY", str(tmp_path / "entorno"))

    with pytest.raises(ConfigError, match="FOTO_LIBRARY"):
        resolve_locations(config_path=tmp_path / "config.toml")


def test_foto_library_in_injected_mapping_is_an_explicit_error(tmp_path):
    with pytest.raises(ConfigError, match="FOTO_LIBRARY"):
        resolve_locations(
            environment={"FOTO_LIBRARY": str(tmp_path / "entorno")},
            config_path=tmp_path / "no-leído.toml",
        )


def test_invalid_config_propagates_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("FOTO_LIBRARY", raising=False)
    config = tmp_path / "config.toml"
    config.write_text("version = 99\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="más nueva"):
        resolve_locations(config_path=config)
