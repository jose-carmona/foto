"""Tests del resolvedor de metainformación (ADR 0008, § 3.2; ADR 0010, T2.2).

La cadena no cambia en niveles (``--metadata`` → ``FOTO_METADATA`` →
``[metadata].path``) y el nivel 4 gana la regla del default: con una sola
ubicación declarada, ``<ubicación>/.foto``; con varias y sin override,
``ConfigError``. Reglas duras por ubicación: fuera de ubicaciones ``archive``
y de paquetes Apple (ADR 0009 extendido).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foto.core.errors import LibraryError
from foto.storage.config import ConfigError, Location
from foto.storage.resolver import (
    ORIGIN_CONFIG,
    ORIGIN_DEFAULT,
    ORIGIN_ENVIRONMENT,
    ORIGIN_OPTION,
    resolve_metadata_dir,
)


def _single(root: Path, *, name: str = "default", tier: str = "fast") -> tuple[Location, ...]:
    return (Location(name=name, path=root, tier=tier),)


def _two(root: Path) -> tuple[Location, ...]:
    return (
        Location(name="ssd", path=root / "ssd", tier="fast"),
        Location(name="usb", path=root / "usb", tier="archive"),
    )


def test_option_takes_precedence_without_reading_config(tmp_path, monkeypatch):
    from_option = tmp_path / "metainfo-cli"
    from_env = tmp_path / "metainfo-env"
    config = tmp_path / "config.toml"
    config.write_text("version = \"roto\"")  # inválida: no debe leerse
    monkeypatch.setenv("FOTO_METADATA", str(from_env))

    resolution = resolve_metadata_dir(_single(tmp_path), from_option, config_path=config)

    assert resolution.metadata_dir == from_option.resolve()
    assert resolution.origin == ORIGIN_OPTION


def test_environment_beats_config(tmp_path):
    from_env = tmp_path / "metainfo-env"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{tmp_path / "otra"}"\n',
        encoding="utf-8",
    )

    resolution = resolve_metadata_dir(
        _single(tmp_path),
        environment={"FOTO_METADATA": str(from_env)},
        config_path=config,
    )

    assert resolution.metadata_dir == from_env.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_config_path_used_when_no_overrides(tmp_path):
    from_config = tmp_path / "metainfo-config"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{from_config}"\n',
        encoding="utf-8",
    )

    resolution = resolve_metadata_dir(_single(tmp_path), config_path=config)

    assert resolution.metadata_dir == from_config.resolve()
    assert resolution.origin == ORIGIN_CONFIG


def test_default_is_derived_from_the_single_location(tmp_path):
    root = tmp_path / "biblioteca"
    config = tmp_path / "config.toml"  # no existe: sin [metadata]

    resolution = resolve_metadata_dir(_single(root), config_path=config)

    assert resolution.metadata_dir == root / ".foto"
    assert resolution.origin == ORIGIN_DEFAULT


def test_default_with_several_locations_is_an_explicit_error(tmp_path):
    config = tmp_path / "config.toml"  # no existe: sin [metadata]

    with pytest.raises(ConfigError, match="concretarse"):
        resolve_metadata_dir(_two(tmp_path), config_path=config)


def test_default_without_locations_is_an_explicit_error(tmp_path):
    config = tmp_path / "config.toml"  # no existe

    with pytest.raises(ConfigError, match="No hay ubicaciones declaradas"):
        resolve_metadata_dir((), config_path=config)


def test_override_wins_over_the_multi_location_ambiguity(tmp_path):
    external = tmp_path / "metainfo-externa"

    resolution = resolve_metadata_dir(_two(tmp_path), external)

    assert resolution.metadata_dir == external.resolve()
    assert resolution.origin == ORIGIN_OPTION


def test_environment_injected_mapping_respected(tmp_path):
    from_env = tmp_path / "metainfo-map"

    resolution = resolve_metadata_dir(
        _single(tmp_path),
        environment={"FOTO_METADATA": str(from_env)},
        config_path=tmp_path / "no-leído.toml",
    )

    assert resolution.metadata_dir == from_env.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_option_supports_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    resolution = resolve_metadata_dir(
        _single(tmp_path), Path("~/metainfo-anonima")
    )

    assert resolution.metadata_dir == tmp_path / "metainfo-anonima"
    assert resolution.origin == ORIGIN_OPTION


def test_invalid_config_propagates_config_error(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("version = 99\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="más nueva"):
        resolve_metadata_dir(_single(tmp_path), config_path=config)


def test_existing_file_as_metadata_dir_raises(tmp_path):
    file_path = tmp_path / "no-es-directorio"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(LibraryError, match="no es un directorio"):
        resolve_metadata_dir(_single(tmp_path), file_path)


def test_existing_file_from_environment_raises(tmp_path):
    file_path = tmp_path / "fichero-env"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(LibraryError, match="origen environment"):
        resolve_metadata_dir(
            _single(tmp_path), environment={"FOTO_METADATA": str(file_path)}
        )


def test_existing_file_from_config_raises(tmp_path):
    file_path = tmp_path / "fichero-config"
    file_path.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{file_path}"\n',
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="origen config"):
        resolve_metadata_dir(_single(tmp_path), config_path=config)


def test_missing_metadata_dir_is_not_created_by_the_resolver(tmp_path):
    external = tmp_path / "metainfo-externa"

    resolution = resolve_metadata_dir(_single(tmp_path), external)

    assert resolution.metadata_dir == external.resolve()
    assert not external.exists()


# ----------------------------------------------------------------------
# Regla dura por ubicación: fuera de una ubicación archive (ADR 0010, T1.14)
# ----------------------------------------------------------------------
def test_archive_location_default_is_a_hard_error(tmp_path):
    root = tmp_path / "usb"

    with pytest.raises(LibraryError, match="ubicación de archivo"):
        resolve_metadata_dir(_single(root, name="usb", tier="archive"))


def test_archive_location_override_inside_is_a_hard_error(tmp_path):
    root = tmp_path / "usb"
    locations = (
        Location(name="ssd", path=tmp_path / "ssd", tier="fast"),
        Location(name="usb", path=root, tier="archive"),
    )

    with pytest.raises(LibraryError, match="origen option"):
        resolve_metadata_dir(locations, root / ".foto")


def test_archive_location_override_outside_resolves(tmp_path):
    external = tmp_path / "metainfo-externa"
    locations = (
        Location(name="ssd", path=tmp_path / "ssd", tier="fast"),
        Location(name="usb", path=tmp_path / "usb", tier="archive"),
    )

    resolution = resolve_metadata_dir(locations, external)

    assert resolution.metadata_dir == external.resolve()
    assert resolution.origin == ORIGIN_OPTION


def test_fast_location_default_is_allowed(tmp_path):
    root = tmp_path / "ssd"

    resolution = resolve_metadata_dir(_single(root, name="ssd", tier="fast"))

    assert resolution.metadata_dir == root / ".foto"


# ----------------------------------------------------------------------
# ADR 0009 (§ 3.8) extendido a cada ubicación (ADR 0010, T1.15)
# ----------------------------------------------------------------------
def _apple_location(tmp_path: Path, *, name: str = "apple") -> tuple[Location, ...]:
    root = tmp_path / "Fotos.photoslibrary"
    root.mkdir()
    return (Location(name=name, path=root, tier="fast"),)


def test_apple_default_metadata_inside_the_package_raises(tmp_path):
    locations = _apple_location(tmp_path)

    with pytest.raises(LibraryError, match="no puede vivir dentro"):
        resolve_metadata_dir(locations, config_path=tmp_path / "config.toml")


def test_apple_option_inside_the_package_raises(tmp_path):
    locations = _apple_location(tmp_path)

    with pytest.raises(LibraryError, match="origen option"):
        resolve_metadata_dir(locations, locations[0].path / ".foto")


def test_apple_option_equal_to_the_package_root_raises(tmp_path):
    locations = _apple_location(tmp_path)

    with pytest.raises(LibraryError, match="origen option"):
        resolve_metadata_dir(locations, locations[0].path)


def test_apple_environment_inside_the_package_raises(tmp_path):
    locations = _apple_location(tmp_path)

    with pytest.raises(LibraryError, match="origen environment"):
        resolve_metadata_dir(
            locations,
            environment={"FOTO_METADATA": str(locations[0].path / "metainfo")},
        )


def test_apple_config_inside_the_package_raises(tmp_path):
    locations = _apple_location(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{locations[0].path / "metainfo"}"\n',
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="origen config"):
        resolve_metadata_dir(locations, config_path=config)


def test_apple_rule_applies_among_several_locations(tmp_path):
    locations = _apple_location(tmp_path, name="paquete")
    locations = (
        *locations,
        Location(name="ssd", path=tmp_path / "ssd", tier="fast"),
    )

    with pytest.raises(LibraryError, match="paquete"):
        resolve_metadata_dir(
            locations,
            environment={
                "FOTO_METADATA": str(locations[0].path / "metainfo")
            },
        )


def test_apple_error_suggests_the_three_external_sources(tmp_path):
    locations = _apple_location(tmp_path)

    with pytest.raises(LibraryError) as raised:
        resolve_metadata_dir(locations, config_path=tmp_path / "config.toml")

    message = str(raised.value)
    assert "--metadata" in message
    assert "FOTO_METADATA" in message
    assert "[metadata].path" in message


def test_apple_option_outside_the_package_resolves(tmp_path):
    locations = _apple_location(tmp_path)
    external = tmp_path / "metainfo-externa"

    resolution = resolve_metadata_dir(locations, external)

    assert resolution.metadata_dir == external.resolve()
    assert resolution.origin == ORIGIN_OPTION
    assert not external.exists()


def test_apple_environment_outside_the_package_resolves(tmp_path):
    locations = _apple_location(tmp_path)
    external = tmp_path / "metainfo-entorno"

    resolution = resolve_metadata_dir(
        locations,
        environment={"FOTO_METADATA": str(external)},
        config_path=tmp_path / "no-leído.toml",
    )

    assert resolution.metadata_dir == external.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_sibling_directory_of_the_package_is_outside(tmp_path):
    locations = _apple_location(tmp_path)
    sibling = tmp_path / "ApplePhotos.foto"

    resolution = resolve_metadata_dir(locations, sibling)

    assert resolution.metadata_dir == sibling.resolve()


def test_ui_inherits_the_rule_through_the_shared_resolver(tmp_path):
    """Coherencia con la UI: ``ui/app.py`` llama a este resolvedor con el
    entorno del proceso; con ``environment`` inyectable la regla es la misma."""
    locations = _apple_location(tmp_path)

    with pytest.raises(LibraryError, match="origen environment"):
        resolve_metadata_dir(
            locations,
            environment={"FOTO_METADATA": str(locations[0].path / ".foto")},
        )
