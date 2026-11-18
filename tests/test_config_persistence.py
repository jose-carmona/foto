"""Tests de la persistencia global conservadora y validada (C01, C02, C06)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from foto.storage.config import (
    AppConfig,
    ConfigError,
    Location,
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


# ----------------------------------------------------------------------
# version = 2 y [[library.locations]] (ADR 0010, T2.1)
# ----------------------------------------------------------------------
def _write_v2(path: Path, body: str) -> Path:
    path.write_text(f"version = 2\n\n{body}", encoding="utf-8")
    return path


def test_load_v2_reads_the_locations_list(tmp_path):
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        f'[[library.locations]]\nname = "ssd"\npath = "{ssd}"\ntier = "fast"\n'
        f'\n[[library.locations]]\nname = "usb"\npath = "{usb}"\ntier = "archive"\n',
    )

    loaded = load_config(config.config_path)

    assert loaded.version == 2
    assert loaded.library_path is None
    assert loaded.locations == (
        Location(name="ssd", path=ssd.resolve(), tier="fast"),
        Location(name="usb", path=usb.resolve(), tier="archive"),
    )


def test_load_v2_without_locations_or_path_is_valid_and_empty(tmp_path):
    config = _config(tmp_path)
    _write_v2(config.config_path, '[geocoding]\nlanguage = "es"\n')

    loaded = load_config(config.config_path)

    assert loaded.version == 2
    assert loaded.locations == ()
    assert loaded.library_path is None


def test_load_v2_rejects_bare_library_path(tmp_path):
    config = _config(tmp_path)
    _write_v2(config.config_path, '[library]\npath = "/fototeca"\n')

    with pytest.raises(ConfigError, match="suelto no es válido"):
        load_config(config.config_path)


def test_load_v2_rejects_bare_library_path_even_with_the_list(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[library]\npath = "/fototeca"\n\n[[library.locations]]\n'
        'name = "ssd"\npath = "/otra"\ntier = "fast"\n',
    )

    with pytest.raises(ConfigError, match="no puede convivir"):
        load_config(config.config_path)


def test_load_v1_rejects_the_locations_list_and_suggests_v2(tmp_path):
    config = _config(tmp_path)
    config.config_path.write_text(
        'version = 1\n\n[[library.locations]]\nname = "ssd"\n'
        'path = "/ssd"\ntier = "fast"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="version = 2"):
        load_config(config.config_path)


def test_load_v2_rejects_duplicated_names(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[[library.locations]]\nname = "ssd"\npath = "/uno"\ntier = "fast"\n'
        '\n[[library.locations]]\nname = "ssd"\npath = "/dos"\ntier = "fast"\n',
    )

    with pytest.raises(ConfigError, match="duplicado"):
        load_config(config.config_path)


def test_load_v2_rejects_duplicated_expanded_paths(tmp_path):
    config = _config(tmp_path)
    shared = tmp_path / "mismo"
    _write_v2(
        config.config_path,
        f'[[library.locations]]\nname = "ssd"\npath = "{shared}"\ntier = "fast"\n'
        f'\n[[library.locations]]\nname = "otro"\npath = "{shared}"\ntier = "archive"\n',
    )

    with pytest.raises(ConfigError, match="rutas deben ser únicas"):
        load_config(config.config_path)


def test_load_v2_rejects_unknown_tier(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[[library.locations]]\nname = "ssd"\npath = "/ssd"\ntier = "turbo"\n',
    )

    with pytest.raises(ConfigError, match="tier"):
        load_config(config.config_path)


def test_load_v2_rejects_blank_name(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[[library.locations]]\nname = "  "\npath = "/ssd"\ntier = "fast"\n',
    )

    with pytest.raises(ConfigError, match="no vacío"):
        load_config(config.config_path)


def test_load_v2_rejects_uri_scheme_paths(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[[library.locations]]\nname = "nube"\npath = "s3://cubeta/fotos"\n'
        'tier = "archive"\n',
    )

    with pytest.raises(ConfigError, match="volúmenes remotos"):
        load_config(config.config_path)


def test_v2_relative_paths_expand_against_the_config_dir(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[[library.locations]]\nname = "ssd"\npath = "fotos/principal"\n'
        'tier = "fast"\n',
    )

    loaded = load_config(config.config_path)

    assert loaded.locations[0].path == (tmp_path / "fotos" / "principal").resolve()


def test_v2_paths_support_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '[[library.locations]]\nname = "ssd"\npath = "~/Fotos/Principal"\n'
        'tier = "fast"\n',
    )

    loaded = load_config(config.config_path)

    assert loaded.locations[0].path == tmp_path / "Fotos" / "Principal"


def test_save_config_refuses_library_path_on_a_v2_file(tmp_path):
    config = _config(tmp_path)
    original = _write_v2(
        config.config_path,
        '# Config v2 editada a mano\n[[library.locations]]\nname = "ssd"\n'
        'path = "/ssd"\ntier = "fast"\n',
    )
    before = original.read_text(encoding="utf-8")

    with pytest.raises(ConfigError, match="version = 2"):
        save_config(_with_library(config, tmp_path / "fototeca"))

    assert original.read_text(encoding="utf-8") == before


def test_save_config_refuses_library_path_when_creating_a_v2_file(tmp_path):
    config = replace(
        _config(tmp_path, "nuevo.toml"), version=2, library_path=tmp_path / "fototeca"
    )

    with pytest.raises(ConfigError, match="version = 2"):
        save_config(config)

    assert not config.config_path.exists()


def test_save_config_accepts_v2_without_writing_a_path(tmp_path):
    config = _config(tmp_path)
    _write_v2(
        config.config_path,
        '# Config v2 editada a mano\n[[library.locations]]\nname = "ssd"\n'
        'path = "/ssd"\ntier = "fast"\n',
    )
    before = config.config_path.read_text(encoding="utf-8")

    save_config(replace(config, version=2))

    assert config.config_path.read_text(encoding="utf-8") == before
