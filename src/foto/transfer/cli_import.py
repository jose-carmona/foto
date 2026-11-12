"""Interfaz de línea de comandos de ``foto-import`` (ADR 0007).

Escritor único: toma ``index.lock`` como ``foto-index``, porque escribe en
``photos`` (columnas ``place_*``) además de en ``tags``/``photo_tags``. Con
``--dry-run`` no toma el lock ni escribe: valida y aplica sobre una copia en
memoria del catálogo (mismo patrón que ``foto-index``).
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
    LockBusyError,
    LockError,
    TransferError,
)
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import ConfigError
from foto.storage.library import ensure_library, foto_dir
from foto.storage.lock import LOCK_FILE_NAME, IndexerLock
from foto.storage.resolver import resolve_library_path
from foto.transfer.importer import (
    AppliedFile,
    ImportCounters,
    ImportSummary,
    apply_places_file,
    apply_tags_file,
    read_places_file,
    read_tags_file,
)

logger = logging.getLogger("foto.transfer")

EPILOG = """\
precedencia de la biblioteca:
  1. --library PATH
  2. FOTO_LIBRARY
  3. [library].path de config.toml

códigos de salida:
  0  éxito
  1  error no fatal (avisos de importación incluidos)
  2  configuración, biblioteca, catálogo o fichero inválido
  3  otro proceso tiene el lock de la biblioteca
  130  interrumpido (SIGINT)

ejemplos:
  uv run foto-import --library ~/Fotos --tags ~/backups/tags.json
  uv run foto-import --library ~/Fotos --tags a.json --places b.json
  uv run foto-import --library ~/Fotos --tags a.json --dry-run --verbose
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foto-import",
        description=(
            "Importa los ficheros JSON de foto-export sobre el catálogo con "
            "merge aditivo idempotente: añade lo que falta y no toca lo existente."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--library", metavar="PATH", type=Path, help="raíz de la biblioteca")
    parser.add_argument("--config", metavar="PATH", type=Path, help="configuración global")
    parser.add_argument(
        "--tags", metavar="FILE", type=Path, help="fichero tags.json a importar"
    )
    parser.add_argument(
        "--places", metavar="FILE", type=Path, help="fichero places.json a importar"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rellena también lugares de fotos con place_status='error' (nunca 'ok')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="valida y calcula lo que cambiaría, sin escribir (no toma el lock)",
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


def _emit_counters(counters: ImportCounters) -> None:
    print(
        "  "
        f"etiquetas_creadas={counters.tags_created} "
        f"ya_existian={counters.tags_existing} "
        f"avisos_kind={counters.kind_warnings} "
        f"sistema_ignoradas={counters.ignored_tags}"
    )
    print(
        "  "
        f"asignaciones_aplicadas={counters.assignments_applied} "
        f"ya_existian={counters.assignments_existing} "
        f"sin_foto={counters.assignments_without_photo} "
        f"ignoradas={counters.assignments_ignored}"
    )
    print(
        "  "
        f"lugares_rellenados={counters.places_filled} "
        f"(forzados={counters.places_filled_force}) "
        f"resueltos_omitidos={counters.places_skipped_resolved} "
        f"con_error_omitidos={counters.places_skipped_error}"
    )
    print(f"  hashes_ausentes={len(counters.missing_hashes)}")
    if counters.missing_hashes:
        print(f"  avisos: {counters.warnings}")


def _emit_summary(summary: ImportSummary, args: argparse.Namespace) -> None:
    if args.quiet:
        return
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
        return

    prefix = "Simulación de importación" if summary.dry_run else "Importación"
    if summary.force:
        prefix += " (forzada)"
    for applied in summary.files:
        print(f"{prefix} [{applied.kind}] {applied.path.name}:")
        _emit_counters(applied.counters)
        # Detalle de los hashes ausentes solo con --verbose: son hashes de
        # contenido, no rutas personales, pero pueden ser largos.
        if args.verbose and applied.counters.missing_hashes:
            for missing in applied.counters.missing_hashes:
                print(f"    ausente: {missing}")
    print(f"{prefix} total:")
    _emit_counters(summary.totals)


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``foto-import``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)

    if args.tags is None and args.places is None:
        parser.error("indica al menos un fichero: --tags FILE o --places FILE")

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

    # Los ficheros se validan completos antes de escribir nada: ni siquiera
    # se toma el lock si el fichero es inválido (ADR 0007, Decisión 5).
    tags_data = None
    places_data = None
    try:
        if args.tags is not None:
            tags_data = read_tags_file(args.tags)
        if args.places is not None:
            places_data = read_places_file(args.places)
    except TransferError as exc:
        logger.error("%s", exc)
        return 2

    try:
        ensure_library(root, dry_run=args.dry_run)
    except LibraryError as exc:
        logger.error("%s", exc)
        return 2

    lock: IndexerLock | None = None
    catalog: Catalog | None = None
    try:
        # El lock se adquiere antes de abrir el catálogo: la apertura puede
        # activar WAL y crear o migrar el esquema, y son escrituras que deben
        # quedarse dentro de la garantía de escritor único.
        if not args.dry_run:
            lock = IndexerLock(foto_dir(root) / LOCK_FILE_NAME)
            try:
                lock.acquire()
            except LockBusyError as exc:
                logger.error("%s", exc)
                return 3
            except LockError as exc:
                logger.error("%s", exc)
                return 2

        catalog_path = foto_dir(root) / CATALOG_FILE_NAME
        try:
            if args.dry_run:
                catalog = Catalog.open_dry_run(catalog_path)
            else:
                catalog = Catalog.open(catalog_path)
        except CatalogError as exc:
            logger.error("No se pudo abrir el catálogo: %s", exc)
            return 2

        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        applied_files: list[AppliedFile] = []
        if tags_data is not None:
            assert args.tags is not None
            counters = apply_tags_file(catalog, tags_data, now=now)
            applied_files.append(AppliedFile("tags", args.tags, counters))
        if places_data is not None:
            assert args.places is not None
            counters = apply_places_file(
                catalog, places_data, now=now, force=args.force
            )
            applied_files.append(AppliedFile("places", args.places, counters))

        totals = ImportCounters(
            tags_created=sum(applied.counters.tags_created for applied in applied_files),
            tags_existing=sum(applied.counters.tags_existing for applied in applied_files),
            kind_warnings=sum(applied.counters.kind_warnings for applied in applied_files),
            ignored_tags=sum(applied.counters.ignored_tags for applied in applied_files),
            assignments_applied=sum(
                applied.counters.assignments_applied for applied in applied_files
            ),
            assignments_existing=sum(
                applied.counters.assignments_existing for applied in applied_files
            ),
            assignments_without_photo=sum(
                applied.counters.assignments_without_photo for applied in applied_files
            ),
            assignments_ignored=sum(
                applied.counters.assignments_ignored for applied in applied_files
            ),
            places_filled=sum(applied.counters.places_filled for applied in applied_files),
            places_filled_force=sum(
                applied.counters.places_filled_force for applied in applied_files
            ),
            places_skipped_resolved=sum(
                applied.counters.places_skipped_resolved for applied in applied_files
            ),
            places_skipped_error=sum(
                applied.counters.places_skipped_error for applied in applied_files
            ),
            missing_hashes=tuple(
                sorted(
                    missing
                    for applied in applied_files
                    for missing in applied.counters.missing_hashes
                )
            ),
        )
        summary = ImportSummary(
            dry_run=args.dry_run, force=args.force, files=tuple(applied_files), totals=totals
        )
        _emit_summary(summary, args)
        return 0 if totals.warnings == 0 else 1
    except KeyboardInterrupt:
        logger.error("Importación interrumpida")
        return 130
    except (CatalogError, LibraryError, TransferError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:
        if args.verbose:
            logger.exception("Error inesperado durante la importación")
        else:
            logger.error("Error inesperado durante la importación: %s", exc)
        return 1
    finally:
        if catalog is not None:
            catalog.close()
        if lock is not None:
            lock.release()
