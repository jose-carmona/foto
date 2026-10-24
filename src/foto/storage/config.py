"""Carga y escritura de configuración."""

from __future__ import annotations

import os
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import platformdirs


@dataclass(frozen=True)
class AppConfig:
    """Configuración global de la aplicación."""

    config_path: Path
    library_path: Optional[Path] = None
    version: int = 1


class ConfigError(Exception):
    """Error de configuración."""


def get_default_config_path() -> Path:
    """Ruta por defecto del fichero de configuración."""
    config_dir = Path(platformdirs.user_config_dir("foto"))
    return config_dir / "config.toml"


def resolve_config_path(config_path: Optional[Path] = None) -> Path:
    """Resuelve la ruta del fichero de configuración con precedencia."""
    if config_path is not None:
        return Path(config_path).expanduser().resolve()

    env_config = os.environ.get("FOTO_CONFIG")
    if env_config:
        return Path(env_config).expanduser().resolve()

    return get_default_config_path().expanduser().resolve()


def _expand_path(p: str, base_dir: Path) -> Path:
    """Expande ruta relativa respecto al directorio base."""
    path = Path(p).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Carga la configuración global."""
    resolved = resolve_config_path(config_path)
    if not resolved.exists():
        return AppConfig(config_path=resolved, library_path=None, version=1)

    try:
        if tomllib is None:
            raise ConfigError("No hay soporte para leer TOML")
        with resolved.open("rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise ConfigError(f"No se pudo leer la configuración: {e}") from e

    version = data.get("version", 1)
    if not isinstance(version, int):
        raise ConfigError("El campo 'version' debe ser un entero")

    library_path = None
    library_section = data.get("library")
    if isinstance(library_section, dict):
        path_str = library_section.get("path")
        if isinstance(path_str, str):
            library_path = _expand_path(path_str, resolved.parent)

    return AppConfig(config_path=resolved, library_path=library_path, version=version)


def save_config(config: AppConfig) -> None:
    """Guarda la configuración global de forma atómica."""
    config.config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append(f"version = {config.version}")
    lines.append("")
    lines.append("[library]")
    if config.library_path is not None:
        # Guardar ruta absoluta
        lines.append(f'path = "{config.library_path}"')
    else:
        lines.append('# path = "/ruta/a/fototeca"')

    content = "\n".join(lines) + "\n"
    tmp_path = config.config_path.with_suffix(config.config_path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, config.config_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
