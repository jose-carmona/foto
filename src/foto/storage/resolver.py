"""Resolución de ubicaciones y metainformación compartida por los binarios.

Con el ADR 0010 la fototeca se declara **solo en el fichero de
configuración** — ``[[library.locations]]`` en v2; ``[library].path`` en v1
equivale a una única ubicación ``default`` — y ``resolve_locations()`` es su
única lectura. Desaparecen ``--library`` y ``FOTO_LIBRARY``: la presencia de
esta variable es un error explícito, jamás se ignora en silencio.

La cadena de metainformación del ADR 0008 no cambia en niveles
(``--metadata`` → ``FOTO_METADATA`` → ``[metadata].path``) y gana la regla
del default: con una sola ubicación declarada, ``<ubicación>/.foto``; con
varias y sin override, error explícito (ambigüedad prohibida). Reglas duras
aplicadas por ubicación: la metainformación no puede caer dentro de una
ubicación ``archive`` ni de un paquete de Apple Photos (ADR 0009).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from foto.core.errors import LibraryError
from foto.core.scanner import is_apple_photos_library
from foto.storage.config import (
    DEFAULT_LOCATION_NAME,
    DEFAULT_LOCATION_TIER,
    ConfigError,
    Location,
    load_config,
)
from foto.storage.library import FOTO_DIR_NAME

#: Fuente retirada (ADR 0010): definirla es un error explícito.
ENVIRONMENT_LIBRARY = "FOTO_LIBRARY"
ENVIRONMENT_METADATA = "FOTO_METADATA"

ORIGIN_OPTION = "option"
ORIGIN_ENVIRONMENT = "environment"
ORIGIN_CONFIG = "config"
ORIGIN_DEFAULT = "default"

__all__ = [
    "DEFAULT_LOCATION_NAME",
    "DEFAULT_LOCATION_TIER",
    "ENVIRONMENT_LIBRARY",
    "ENVIRONMENT_METADATA",
    "ORIGIN_CONFIG",
    "ORIGIN_DEFAULT",
    "ORIGIN_ENVIRONMENT",
    "ORIGIN_OPTION",
    "Location",
    "MetadataResolution",
    "resolve_locations",
    "resolve_metadata_dir",
]


@dataclass(frozen=True)
class MetadataResolution:
    """Directorio de metainformación y nivel de precedencia del que procede."""

    metadata_dir: Path | None
    origin: str | None


def _validated_metadata_dir(path: Path, origin: str) -> Path:
    """Comprueba que la ruta de metainformación, si existe, sea un directorio.

    Si no existe no se crea aquí: lo inicializa el bootstrap de
    ``storage/library.py``. Existe y no es un directorio es un error
    explícito, con la ruta y el origen de la cadena.
    """
    if path.exists() and not path.is_dir():
        raise LibraryError(
            f"La ruta de metainformación (origen {origin}) existe y no es un "
            f"directorio: {path}"
        )
    return path


def resolve_locations(
    config_path: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[Location, ...]:
    """Lee la declaración de ubicaciones desde la config (ADR 0010).

    La configuración es la única fuente: no existe parámetro de biblioteca ni
    entorno inyectable de rutas. ``FOTO_LIBRARY`` definida es un error
    explícito que enseña la vía nueva (nunca se ignora en silencio). Sin
    declaración devuelve una tupla vacía; una config v1 con ``[library].path``
    equivale a una única ubicación ``default``.
    """
    sources = os.environ if environment is None else environment
    if sources.get(ENVIRONMENT_LIBRARY):
        raise ConfigError(
            "FOTO_LIBRARY ya no declara la fototeca: declara las ubicaciones "
            "en '[[library.locations]]' de config.toml ('[library].path' en "
            "un fichero 'version = 1')"
        )
    return load_config(config_path).locations


def resolve_metadata_dir(
    locations: Sequence[Location],
    metadata: Path | None = None,
    *,
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> MetadataResolution:
    """Aplica la precedencia del ADR 0008 para la metainformación.

    ``--metadata`` → ``FOTO_METADATA`` → ``[metadata].path`` de ``config.toml``
    → ``<única ubicación declarada>/.foto``. Con varias ubicaciones y sin
    override es ``ConfigError``: la ambigüedad está prohibida (ADR 0010).
    Vive en este módulo junto a la declaración: es el único sitio con la
    precedencia. El directorio por defecto es el único derivado de las
    ubicaciones.

    Reglas duras (por ubicación): el resultado —sea cual sea su origen— no
    puede caer dentro de una ubicación ``tier = "archive"`` ni de un paquete
    de Apple Photos (ADR 0009); el fallo es ``LibraryError`` con la acción
    sugerida, sin fallback automático.
    """
    frozen_locations = tuple(locations)
    resolution = _resolve_metadata_origin(
        frozen_locations, metadata, config_path=config_path, environment=environment
    )
    return _enforce_location_rules(frozen_locations, resolution)


def _resolve_metadata_origin(
    locations: tuple[Location, ...],
    metadata: Path | None,
    *,
    config_path: Path | None,
    environment: Mapping[str, str] | None,
) -> MetadataResolution:
    """La cadena de precedencia del ADR 0008, sin reglas de ubicaciones."""
    if metadata is not None:
        resolved = _validated_metadata_dir(
            Path(metadata).expanduser().resolve(), ORIGIN_OPTION
        )
        return MetadataResolution(resolved, ORIGIN_OPTION)

    sources = os.environ if environment is None else environment
    override = sources.get(ENVIRONMENT_METADATA)
    if override:
        resolved = _validated_metadata_dir(
            Path(override).expanduser().resolve(), ORIGIN_ENVIRONMENT
        )
        return MetadataResolution(resolved, ORIGIN_ENVIRONMENT)

    config = load_config(config_path)
    if config.metadata_path is not None:
        resolved = _validated_metadata_dir(
            config.metadata_path, ORIGIN_CONFIG
        )
        return MetadataResolution(resolved, ORIGIN_CONFIG)

    if len(locations) == 1:
        return MetadataResolution(
            locations[0].path / FOTO_DIR_NAME, ORIGIN_DEFAULT
        )
    if not locations:
        raise ConfigError(
            "No hay ubicaciones declaradas: decláralas en "
            "'[[library.locations]]' de config.toml ('[library].path' en un "
            "fichero 'version = 1')"
        )
    raise ConfigError(
        "Con varias ubicaciones declaradas la metainformación debe "
        "concretarse: usa --metadata PATH, FOTO_METADATA o [metadata].path "
        "en config.toml"
    )


def _enforce_location_rules(
    locations: tuple[Location, ...], resolution: MetadataResolution
) -> MetadataResolution:
    """Reglas duras por ubicación (ADR 0009 y ADR 0010).

    Compara el directorio resultante (expandido y resuelto, sea cual sea su
    origen) contra cada ubicación declarada. No hay fallback automático: si
    no hay ruta externa explícita, el error exige proporcionarla.
    """
    directory = resolution.metadata_dir
    if directory is None:
        return resolution
    resolved_dir = Path(directory).expanduser().resolve()
    for location in locations:
        root = location.path
        inside = resolved_dir == root or resolved_dir.is_relative_to(root)
        if not inside:
            continue
        if location.tier == "archive":
            raise LibraryError(
                f"La metainformación (origen {resolution.origin}) no puede "
                f"vivir en la ubicación de archivo '{location.name}' ({root}): "
                "reubícala con --metadata PATH, FOTO_METADATA o [metadata].path"
            )
        if is_apple_photos_library(root):
            raise LibraryError(
                f"La ubicación '{location.name}' es un paquete de Apple "
                "Photos y la metainformación (origen "
                f"{resolution.origin}) no puede vivir dentro de él: usa "
                "--metadata PATH, FOTO_METADATA o [metadata].path apuntando "
                "a una ruta fuera del paquete"
            )
    return resolution
