"""Resolución de la biblioteca compartida por ``foto`` y ``foto-index``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from foto.core.errors import LibraryError
from foto.storage.config import load_config
from foto.storage.library import FOTO_DIR_NAME

ENVIRONMENT_LIBRARY = "FOTO_LIBRARY"
ENVIRONMENT_METADATA = "FOTO_METADATA"

ORIGIN_OPTION = "option"
ORIGIN_ENVIRONMENT = "environment"
ORIGIN_CONFIG = "config"
ORIGIN_DEFAULT = "default"


@dataclass(frozen=True)
class LibraryResolution:
    """Biblioteca resultante y nivel de precedencia del que procede."""

    library_path: Path | None
    origin: str | None


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


def resolve_metadata_dir(
    root: Path,
    metadata: Path | None = None,
    *,
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> MetadataResolution:
    """Aplica la precedencia del ADR 0008 para la metainformación.

    ``--metadata`` → ``FOTO_METADATA`` → ``[metadata].path`` de ``config.toml``
    → ``<fototeca>/.foto``. Vive en este módulo junto a la cadena de la
    fototeca: es el único sitio con la precedencia. Un override por CLI no
    llega a leer el fichero de configuración, como en ``resolve_library_path``.
    El directorio por defecto (nivel 4) es el único derivado de la raíz.
    """
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

    return MetadataResolution(Path(root) / FOTO_DIR_NAME, ORIGIN_DEFAULT)


def resolve_library_path(
    library: Path | None = None,
    *,
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> LibraryResolution:
    """Aplica la precedencia aceptada: ``--library`` → ``FOTO_LIBRARY`` → config.

    Es el único sitio donde se implementa la precedencia de la fototeca
    (ADR 0001; ampliado con la cadena de metainformación en el ADR 0008).
    Un override por CLI no llega a leer el fichero de configuración, como en
    la CLI original. Devuelve ``None`` solo si no hay ninguna de las tres
    fuentes; el diálogo de primera ejecución queda como último nivel
    interactivo del llamador, jamás como paso del resolvedor.
    """
    if library is not None:
        return LibraryResolution(library.expanduser().resolve(), ORIGIN_OPTION)

    sources = os.environ if environment is None else environment
    override = sources.get(ENVIRONMENT_LIBRARY)
    if override:
        return LibraryResolution(
            Path(override).expanduser().resolve(),
            ORIGIN_ENVIRONMENT,
        )

    config = load_config(config_path)
    return LibraryResolution(config.library_path, ORIGIN_CONFIG)
