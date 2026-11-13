"""Interfaz de línea de comandos de ``foto-export`` (ADR 0007).

Solo lectura del catálogo: no toma ``index.lock`` y convive con
``foto-index`` y la app (WAL). Genera ``tags.json`` y ``places.json`` en el
directorio de destino con escritura atómica.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from foto.core.errors import (
    CatalogError,
    LibraryError,
    TransferError,
)
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import ConfigError
from foto.storage.resolver import resolve_library_path, resolve_metadata_dir
from foto.transfer import exporter
from foto.transfer.exporter import export_library

logger = logging.getLogger("foto.transfer")

EPILOG = """\
precedencia de la biblioteca:
  1. --library PATH
  2. FOTO_LIBRARY
  3. [library].path de config.toml
precedencia de la metainformación (.foto, ADR 0008):
  1. --metadata PATH
  2. FOTO_METADATA
  3. [metadata].path de config.toml
  4. <fototeca>/.foto (por defecto)

códigos de salida:
  0  éxito, sin avisos
  1  error no fatal (avisos de exportación incluidos)
  2  configuración inválida, biblioteca o catálogo inexistente o inválido
  130  interrumpido (SIGINT)

ejemplos:
  uv run foto-export --library ~/Fotos
  uv run foto-export --library ~/Fotos --metadata ~/Fotos.foto
  uv run foto-export --library ~/Fotos --tags --out ~/backups
  uv run foto-export --library ~/Fotos --dry-run --json
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foto-export",
        description=(
            "Exporta los metadatos de usuario de la biblioteca a dos ficheros "
            "JSON (tags.json y places.json). Solo lectura del catálogo."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--library", metavar="PATH", type=Path, help="raíz de la biblioteca")
    parser.add_argument(
        "--metadata",
        metavar="PATH",
        type=Path,
        help="directorio de metainformación; por defecto: <fototeca>/.foto",
    )
    parser.add_argument("--config", metavar="PATH", type=Path, help="configuración global")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--tags", action="store_true", help="exporta solo tags.json (por defecto: ambos)"
    )
    selection.add_argument(
        "--places",
        action="store_true",
        help="exporta solo places.json (por defecto: ambos)",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        type=Path,
        help="directorio de destino (por defecto: <fototeca>/.foto/exports/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="calcula y valida todo sin escribir ficheros"
    )
    parser.add_argument(
        "--json", action="store_true", help="escribe el resumen en JSON por stdout"
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--quiet", action="store_true", help="solo errores")
    verbosity.add_argument("--verbose", action="store_true", help="más detalle")
    return parser


def _configure_logging(args: argparse.Namespace) -> None:
    if args.quiet:
        level = logging.ERROR
    elif args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _emit_summary(summary: exporter.ExportSummary, args: argparse.Namespace) -> None:
    if args.quiet:
        return
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
        return

    prefix = "Simulación de exportación" if summary.dry_run else "Exportación"
    print(f"{prefix}: etiquetas={summary.tags} asignaciones={summary.assignments} "
          f"huérfanas={summary.orphan_tags}")
    print(
        "  "
        f"lugares={summary.places} avisos_dirección={summary.address_warnings}"
    )
    for kind, path in summary.destinations.items():
        size = summary.sizes.get(kind)
        action = "escribiría" if summary.dry_run else "escrito"
        detail = f" ({size} bytes)" if size is not None else ""
        print(f"  {action} {path}{detail}")
    if summary.address_warnings:
        print(f"  aviso: {summary.address_warnings} direcciones no parseadas")


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``foto-export``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)

    try:
        root = resolve_library_path(args.library, config_path=args.config).library_path
    except ConfigError as exc:
        logger.error("Configuración inválida: %s", exc)
        return 2

    if root is None:
        logger.error(
            "No hay biblioteca configurada: usa --library, FOTO_LIBRARY o "
            "[library].path en config.toml"
        )
        return 2

    # ADR 0008: la metainformación se resuelve solo por el resolvedor
    # compartido, nunca leyendo FOTO_METADATA ni config.toml por su cuenta.
    try:
        metadata_dir = resolve_metadata_dir(
            root, args.metadata, config_path=args.config
        ).metadata_dir
    except (ConfigError, LibraryError) as exc:
        logger.error("Metainformación inválida: %s", exc)
        return 2
    assert metadata_dir is not None

    # Solo lectura: no se crea nada. Un directorio de metainformación
    # ausente es un error explícito con la acción sugerida.
    if not metadata_dir.is_dir():
        logger.error(
            "No hay metainformación en %s: ejecuta 'foto-index' en la "
            "biblioteca o corrige --metadata/FOTO_METADATA/[metadata].path",
            metadata_dir,
        )
        return 2

    catalog_path = metadata_dir / CATALOG_FILE_NAME
    if not catalog_path.exists():
        logger.error(
            "No hay catálogo en %s: ejecuta 'foto-index' antes de exportar", catalog_path
        )
        return 2

    out_dir = args.out if args.out is not None else metadata_dir / "exports"

    export_tags = not args.places
    export_places = not args.tags

    catalog: Catalog | None = None
    try:
        catalog = Catalog.open_read_only(catalog_path)
        summary = export_library(
            catalog.connection,
            out_dir,
            export_tags=export_tags,
            export_places=export_places,
            generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            dry_run=args.dry_run,
        )
        _emit_summary(summary, args)
        # Los avisos de direcciones no parseadas son no fatales pero se
        # señalizan: mismo criterio que foto-index (avisos incluidos → 1).
        return 0 if summary.address_warnings == 0 else 1
    except KeyboardInterrupt:
        logger.error("Exportación interrumpida")
        return 130
    except (CatalogError, LibraryError, TransferError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:
        if args.verbose:
            logger.exception("Error inesperado durante la exportación")
        else:
            logger.error("Error inesperado durante la exportación: %s", exc)
        return 1
    finally:
        if catalog is not None:
            catalog.close()
