"""Tests del resolvedor de metainformación (ADR 0008, § 3.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from foto.core.errors import LibraryError
from foto.storage.config import ConfigError
from foto.storage.resolver import (
    ORIGIN_CONFIG,
    ORIGIN_DEFAULT,
    ORIGIN_ENVIRONMENT,
    ORIGIN_OPTION,
    resolve_metadata_dir,
)


def test_option_takes_precedence_without_reading_config(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    from_option = tmp_path / "metainfo-cli"
    from_env = tmp_path / "metainfo-env"
    config = tmp_path / "config.toml"
    config.write_text("version = \"roto\"")  # inválida: no debe leerse
    monkeypatch.setenv("FOTO_METADATA", str(from_env))

    resolution = resolve_metadata_dir(root, from_option, config_path=config)

    assert resolution.metadata_dir == from_option.resolve()
    assert resolution.origin == ORIGIN_OPTION


def test_environment_beats_config(tmp_path):
    root = tmp_path / "biblioteca"
    from_env = tmp_path / "metainfo-env"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{tmp_path / "otra"}"\n',
        encoding="utf-8",
    )

    resolution = resolve_metadata_dir(
        root, environment={"FOTO_METADATA": str(from_env)}, config_path=config
    )

    assert resolution.metadata_dir == from_env.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_config_path_used_when_no_overrides(tmp_path):
    root = tmp_path / "biblioteca"
    from_config = tmp_path / "metainfo-config"
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{from_config}"\n',
        encoding="utf-8",
    )

    resolution = resolve_metadata_dir(root, config_path=config)

    assert resolution.metadata_dir == from_config.resolve()
    assert resolution.origin == ORIGIN_CONFIG


def test_default_is_derived_from_the_library_root(tmp_path):
    root = tmp_path / "biblioteca"
    config = tmp_path / "config.toml"  # no existe: sin [metadata]

    resolution = resolve_metadata_dir(root, config_path=config)

    assert resolution.metadata_dir == root / ".foto"
    assert resolution.origin == ORIGIN_DEFAULT


def test_environment_injected_mapping_respected(tmp_path):
    root = tmp_path / "biblioteca"
    from_env = tmp_path / "metainfo-map"

    resolution = resolve_metadata_dir(
        root,
        environment={"FOTO_METADATA": str(from_env)},
        config_path=tmp_path / "no-leído.toml",
    )

    assert resolution.metadata_dir == from_env.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_option_supports_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    resolution = resolve_metadata_dir(tmp_path, Path("~/metainfo-anonima"))

    assert resolution.metadata_dir == tmp_path / "metainfo-anonima"
    assert resolution.origin == ORIGIN_OPTION


def test_invalid_config_propagates_config_error(tmp_path):
    root = tmp_path / "biblioteca"
    config = tmp_path / "config.toml"
    config.write_text("version = 99\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="más nueva"):
        resolve_metadata_dir(root, config_path=config)


def test_existing_file_as_metadata_dir_raises(tmp_path):
    root = tmp_path / "biblioteca"
    file_path = tmp_path / "no-es-directorio"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(LibraryError, match="no es un directorio"):
        resolve_metadata_dir(root, file_path)


def test_existing_file_from_environment_raises(tmp_path):
    root = tmp_path / "biblioteca"
    file_path = tmp_path / "fichero-env"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(LibraryError, match="origen environment"):
        resolve_metadata_dir(
            root, environment={"FOTO_METADATA": str(file_path)}
        )


def test_existing_file_from_config_raises(tmp_path):
    root = tmp_path / "biblioteca"
    file_path = tmp_path / "fichero-config"
    file_path.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{file_path}"\n',
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="origen config"):
        resolve_metadata_dir(root, config_path=config)


def test_missing_metadata_dir_is_not_created_by_the_resolver(tmp_path):
    root = tmp_path / "biblioteca"
    external = tmp_path / "metainfo-externa"

    resolution = resolve_metadata_dir(root, external)

    assert resolution.metadata_dir == external.resolve()
    assert not external.exists()


# ----------------------------------------------------------------------
# ADR 0009 (§ 3.8): metainformación obligatoriamente externa
# ----------------------------------------------------------------------
def _apple_root(tmp_path: Path) -> Path:
    root = tmp_path / "Fotos.photoslibrary"
    root.mkdir()
    return root


def test_apple_default_metadata_inside_the_package_raises(tmp_path):
    root = _apple_root(tmp_path)

    with pytest.raises(LibraryError, match="no puede vivir dentro"):
        resolve_metadata_dir(root, config_path=tmp_path / "config.toml")


def test_apple_option_inside_the_package_raises(tmp_path):
    root = _apple_root(tmp_path)

    with pytest.raises(LibraryError, match="origen option"):
        resolve_metadata_dir(root, root / ".foto")


def test_apple_option_equal_to_the_package_root_raises(tmp_path):
    root = _apple_root(tmp_path)

    with pytest.raises(LibraryError, match="origen option"):
        resolve_metadata_dir(root, root)


def test_apple_environment_inside_the_package_raises(tmp_path):
    root = _apple_root(tmp_path)

    with pytest.raises(LibraryError, match="origen environment"):
        resolve_metadata_dir(
            root,
            environment={"FOTO_METADATA": str(root / "metainfo")},
        )


def test_apple_config_inside_the_package_raises(tmp_path):
    root = _apple_root(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        f'version = 1\n[metadata]\npath = "{root / "metainfo"}"\n',
        encoding="utf-8",
    )

    with pytest.raises(LibraryError, match="origen config"):
        resolve_metadata_dir(root, config_path=config)


def test_apple_error_suggests_the_three_external_sources(tmp_path):
    root = _apple_root(tmp_path)

    with pytest.raises(LibraryError) as raised:
        resolve_metadata_dir(root, config_path=tmp_path / "config.toml")

    message = str(raised.value)
    assert "--metadata" in message
    assert "FOTO_METADATA" in message
    assert "[metadata].path" in message


def test_apple_option_outside_the_package_resolves(tmp_path):
    root = _apple_root(tmp_path)
    external = tmp_path / "metainfo-externa"

    resolution = resolve_metadata_dir(root, external)

    assert resolution.metadata_dir == external.resolve()
    assert resolution.origin == ORIGIN_OPTION
    assert not external.exists()


def test_apple_environment_outside_the_package_resolves(tmp_path):
    root = _apple_root(tmp_path)
    external = tmp_path / "metainfo-entorno"

    resolution = resolve_metadata_dir(
        root,
        environment={"FOTO_METADATA": str(external)},
        config_path=tmp_path / "no-leído.toml",
    )

    assert resolution.metadata_dir == external.resolve()
    assert resolution.origin == ORIGIN_ENVIRONMENT


def test_sibling_directory_of_the_package_is_outside(tmp_path):
    root = _apple_root(tmp_path)
    sibling = tmp_path / "ApplePhotos.foto"

    resolution = resolve_metadata_dir(root, sibling)

    assert resolution.metadata_dir == sibling.resolve()


def test_ui_inherits_the_rule_through_the_shared_resolver(tmp_path):
    """Coherencia con la UI: ``ui/app.py`` llama a este resolvedor con el
    entorno del proceso; con ``environment`` inyectable la regla es la misma."""
    root = _apple_root(tmp_path)

    with pytest.raises(LibraryError, match="origen environment"):
        resolve_metadata_dir(
            root,
            environment={"FOTO_METADATA": str(root / ".foto")},
        )
