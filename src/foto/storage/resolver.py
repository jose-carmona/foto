"""Resolución de la biblioteca compartida por ``foto`` y ``foto-index``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from foto.storage.config import load_config

ENVIRONMENT_LIBRARY = "FOTO_LIBRARY"

ORIGIN_OPTION = "option"
ORIGIN_ENVIRONMENT = "environment"
ORIGIN_CONFIG = "config"


@dataclass(frozen=True)
class LibraryResolution:
    """Biblioteca resultante y nivel de precedencia del que procede."""

    library_path: Path | None
    origin: str | None


def resolve_library_path(
    library: Path | None = None,
    *,
    config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> LibraryResolution:
    """Aplica la precedencia aceptada: ``--library`` → ``FOTO_LIBRARY`` → config.

    Es el único sitio donde se implementa la precedencia (ADR 0001). Un
    override por CLI no llega a leer el fichero de configuración, como en la
    CLI original. Devuelve ``None`` solo si no hay ninguna de las tres fuentes;
    el diálogo de primera ejecución queda como último nivel interactivo del
    llamador, jamás como paso del resolvedor.
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
