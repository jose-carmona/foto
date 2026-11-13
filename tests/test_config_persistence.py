"""Tests de la persistencia global conservadora y validada (C01, C02, C06)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from foto.storage.config import (
    AppConfig,
    ConfigError,
    load_config,
    save_config,
)


def _config(tmp_path, name: str = "config.toml") -> AppConfig:
    return AppConfig(config_path=tmp_path / name)


def _with_library(config: AppConfig, library_path) -> AppConfig:
    return replace(config, library_path=library_path)


def test_save_config_creates_canonical_file(tmp_path):
    config = _config(tmp_path, "nuevo.toml")

    save_config(config)

    text = config.config_path.read_text(encoding="utf-8")
    assert "version = 1" in text
    assert "[library]" in text
    loaded = load_config(config.config_path)
    assert loaded.library_path is None


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "-inexistente-.toml")

    assert config.version == 1
    assert config.library_path is None


def test_save_preserves_comments_unknown_keys_and_sections(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        "# Mi configuración personal\n"
        "version = 1\n"
        "\n"
        "[library]\n"
        'path = "/vieja/fototeca"  # pista del disco externo\n'
        'display = "grande"       # clave desconocida dentro de la sección\n'
        "\n"
        "# Sección desconocida para la app\n"
        "[misc]\n"
        'color = "azul"\n',
        encoding="utf-8",
    )

    new_path = tmp_path / "nueva-fototeca"
    save_config(_with_library(config, new_path))

    text = config.config_path.read_text(encoding="utf-8")
    assert "# Mi configuración personal" in text
    assert "version = 1" in text
    assert "[misc]" in text
    assert 'color = "azul"' in text
    assert 'display = "grande"' in text
    assert "# pista del disco externo" in text
    assert f'path = "{new_path}"' in text
    assert "/vieja/fototeca" not in text

    loaded = load_config(config.config_path)
    assert loaded.library_path == new_path.resolve()


def test_save_is_idempotent(tmp_path):
    config = _with_library(_config(tmp_path), tmp_path / "fototeca")

    save_config(config)
    first = config.config_path.read_text(encoding="utf-8")

    save_config(config)
    second = config.config_path.read_text(encoding="utf-8")

    assert first == second
    library = load_config(config.config_path).library_path
    assert library == (tmp_path / "fototeca").resolve()


def test_save_rewrites_file_without_duplicating_paths(tmp_path):
    config = _with_library(_config(tmp_path), tmp_path / "fototeca")

    save_config(config)
    save_config(config)

    text = config.config_path.read_text(encoding="utf-8")
    assert text.count("[library]") == 1
    assert text.count("path =") == 1


def test_save_rejects_future_version_instead_of_overwriting(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        "version = 99\n\n[library]\npath = \"/fototeca\"\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="más nueva"):
        save_config(_with_library(config, tmp_path / "otra"))

    assert "version = 99" in config.config_path.read_text(encoding="utf-8")


def test_load_rejects_boolean_version(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text("version = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="entero"):
        load_config(config.config_path)


def test_load_rejects_non_positive_version(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text("version = 0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="no es válida"):
        load_config(config.config_path)


def test_load_rejects_library_not_a_table(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text("version = 1\nlibrary = 5\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="tabla TOML"):
        load_config(config.config_path)


def test_load_rejects_path_not_a_string(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        "version = 1\n\n[library]\npath = 7\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="debe ser texto"):
        load_config(config.config_path)


def test_load_rejects_broken_toml(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text("version = [", encoding="utf-8")

    with pytest.raises(ConfigError, match="No se pudo leer"):
        load_config(config.config_path)


def test_save_rejects_broken_toml_instead_of_rewriting(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text("version = [", encoding="utf-8")

    with pytest.raises(ConfigError, match="No se pudo leer"):
        save_config(_with_library(config, tmp_path / "fototeca"))

    assert config.config_path.read_text(encoding="utf-8") == "version = ["


def test_write_path_round_trip(tmp_path):
    cases = [
        tmp_path / 'con comillas " y espacios',
        tmp_path / "con backslash \\ raro",
        tmp_path / "unicode – áéíø 你好",
        tmp_path / "control\x01carácter",
    ]

    for library in cases:
        config = _with_library(_config(tmp_path, "roundtrip.toml"), library)
        save_config(config)
        loaded = load_config(config.config_path)
        assert loaded.library_path == library.resolve()


def test_control_characters_are_escaped_in_output(tmp_path):
    library = tmp_path / "con\tsalto"
    config = _with_library(_config(tmp_path, "escapes.toml"), library)

    save_config(config)

    text = config.config_path.read_text(encoding="utf-8")
    assert "\\t" in text
    assert 'path = "' in text


def test_save_inserts_path_in_existing_section_without_path(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        "# solo versión\nversion = 1\n\n[library]\n# sin ruta\n",
        encoding="utf-8",
    )

    library = tmp_path / "fototeca"
    save_config(_with_library(config, library))

    text = config.config_path.read_text(encoding="utf-8")
    assert "# solo versión" in text
    assert "# sin ruta" in text
    assert f'path = "{library}"' in text
    loaded = load_config(config.config_path)
    assert loaded.library_path == library.resolve()


def test_save_adds_library_section_when_missing(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        "version = 1\n\n[preferencias]\ntema = 'oscuro'\n",
        encoding="utf-8",
    )

    library = tmp_path / "fototeca"
    save_config(_with_library(config, library))

    text = config.config_path.read_text(encoding="utf-8")
    assert text.count("[library]") == 1
    assert "tema = 'oscuro'" in text
    assert f'path = "{library}"' in text
    loaded = load_config(config.config_path)
    assert loaded.library_path == library.resolve()


# ----------------------------------------------------------------------
# [metadata] (ADR 0008): sección opt-in que save_config nunca toca
# ----------------------------------------------------------------------
def test_load_config_reads_metadata_section_absolute(tmp_path):
    metadata = tmp_path / "metainfo"
    config = _config(tmp_path)
    config.config_path.write_text(
        f'version = 1\n\n[metadata]\npath = "{metadata}"\n',
        encoding="utf-8",
    )

    loaded = load_config(config.config_path)

    assert loaded.metadata_path == metadata.resolve()
    assert loaded.library_path is None


def test_metadata_relative_path_resolves_against_config_dir(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        'version = 1\n\n[metadata]\npath = "metainfo/externa"\n',
        encoding="utf-8",
    )

    loaded = load_config(config.config_path)

    assert loaded.metadata_path == (tmp_path / "metainfo" / "externa").resolve()


def test_metadata_path_supports_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = _config(tmp_path)
    config.config_path.write_text(
        'version = 1\n\n[metadata]\npath = "~/Fotos/externa.foto"\n',
        encoding="utf-8",
    )

    loaded = load_config(config.config_path)

    assert loaded.metadata_path == tmp_path / "Fotos" / "externa.foto"


def test_load_rejects_metadata_not_a_string(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        "version = 1\n\n[metadata]\npath = 7\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="debe ser texto"):
        load_config(config.config_path)


def test_load_rejects_blank_metadata_path(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        'version = 1\n\n[metadata]\npath = "   "\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="no puede estar vacío"):
        load_config(config.config_path)


def test_load_rejects_metadata_not_a_table(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text("version = 1\nmetadata = 5\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="tabla TOML"):
        load_config(config.config_path)


def test_save_preserves_metadata_section_byte_for_byte(tmp_path):
    metadata_block = (
        "[metadata]\n"
        "# Solo hace falta si la metainformación no va dentro de la fototeca.\n"
        'path = "~/Fotos/externa.foto"  # disco externo\n'
    )
    config = _config(tmp_path)
    config.config_path.write_text(
        "# Mi configuración personal\n"
        "version = 1\n"
        "\n"
        "[library]\n"
        'path = "/vieja/fototeca"\n'
        "\n"
        + metadata_block,
        encoding="utf-8",
    )

    save_config(_with_library(config, tmp_path / "nueva-fototeca"))

    text = config.config_path.read_text(encoding="utf-8")
    assert metadata_block in text
    assert f'path = "{tmp_path / "nueva-fototeca"}"' in text
    loaded = load_config(config.config_path)
    assert loaded.metadata_path == Path("~/Fotos/externa.foto").expanduser()


def test_canonical_file_has_no_metadata_section(tmp_path):
    config = _config(tmp_path, "nuevo.toml")

    save_config(_with_library(config, tmp_path / "fototeca"))

    text = config.config_path.read_text(encoding="utf-8")
    assert "[metadata]" not in text
