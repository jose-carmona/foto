"""Interfaz de línea de comandos del indexador."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from foto.core.errors import (
    CatalogError,
    CatalogVersionError,
    IndexingError,
    LibraryError,
    LockBusyError,
    LockError,
)
from foto.core.indexing import INDEXING_PHASES, Indexer, IndexingOptions, IndexingSummary
from foto.indexer.geonames import NominatimGeocoder, ReverseGeocoderConfig
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import ConfigError, load_config
from foto.storage.exiftool import ExifToolExtractor
from foto.storage.library import ensure_library
from foto.storage.lock import LOCK_FILE_NAME, IndexerLock
from foto.storage.resolver import resolve_library_path, resolve_metadata_dir

logger = logging.getLogger("foto.indexer")

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
Para el fichero de configuración: --config PATH, FOTO_CONFIG o platformdirs.

fases del pipeline (todas por defecto); --skip FASE las excluye:
  scan    recorrido del filesystem, hashing, metadatos y ausencias
  geocode geocodificación inversa (OpenStreetMap/Nominatim) de las fotos con GPS

códigos de salida:
  0  éxito, con o sin cambios
  1  error no fatal, recorrido parcial, errores de ficheros o fase detenida
  2  configuración inválida, biblioteca inexistente o esquema incompatible
  3  otro indexador tiene el lock

ejemplos:
  uv run foto-index --library ~/Fotos
  uv run foto-index --library ~/Fotos --metadata ~/Fotos.foto
  uv run foto-index --library ~/Fotos --json
  uv run foto-index --library ~/Fotos --skip geocode
  uv run foto-index --library ~/Fotos --prune --prune-after 30
"""

ALL_PHASES = tuple(INDEXING_PHASES)


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("debe ser un entero") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("no puede ser negativo")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foto-index",
        description=(
            "Indexa una fototeca local: calcula el hash de contenido, guarda "
            "ubicaciones relativas y extrae metadatos con exiftool."
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
    parser.add_argument("--full", action="store_true", help="rehashea y reextrae todo")
    parser.add_argument(
        "--prune", action="store_true", help="borra ubicaciones ausentes antiguas y fotos huérfanas"
    )
    parser.add_argument(
        "--prune-after",
        metavar="DAYS",
        type=_non_negative_int,
        default=30,
        help="antigüedad mínima para --prune (por defecto: 30 días)",
    )
    parser.add_argument("--dry-run", action="store_true", help="no escribe nada en disco")
    parser.add_argument(
        "--skip",
        metavar="FASE",
        action="append",
        choices=list(ALL_PHASES),
        default=[],
        help=f"excluye una fase; repetible: {', '.join(ALL_PHASES)}",
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


def _resolve_library(args: argparse.Namespace) -> Path | None:
    return resolve_library_path(args.library, config_path=args.config).library_path


def _emit_summary(summary: IndexingSummary, args: argparse.Namespace) -> None:
    if args.quiet:
        return
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
        return

    prefix = "Simulación" if summary.dry_run else "Indexado"
    print(f"{prefix} {summary.mode}: {summary.status}")
    print(
        "  "
        f"escaneados={summary.scanned} sin_cambios={summary.unchanged} "
        f"nuevas={summary.added} modificadas={summary.modified} "
        f"ausentes={summary.missing}"
    )
    print(
        "  "
        f"fotos={summary.photos_total} ubicaciones={summary.locations_total} "
        f"duplicadas={summary.duplicate_photos} "
        f"metadatos_extraidos={summary.metadata_extracted} pendientes_metadatos="
        f"{summary.metadata_pending}"
    )
    print(
        "  "
        f"ficheros_hasheados={summary.hashed_files} bytes_hasheados={summary.hashed_bytes} "
        f"errores={summary.errors} duración={summary.duration_seconds:.3f}s"
    )
    if summary.pruned_locations or summary.pruned_photos:
        print(
            "  "
            f"limpiadas={summary.pruned_locations} ubicaciones y "
            f"{summary.pruned_photos} fotos"
        )
    _emit_geocode_phase(summary)
    if summary.partial_reason:
        print(f"  aviso: {summary.partial_reason}")


def _emit_geocode_phase(summary: IndexingSummary) -> None:
    """Resumen de la fase geocode en una línea, según cómo terminó."""
    geocode = summary.geocode
    if geocode.dry_run:
        print(
            "  "
            f"geocode: candidatas={geocode.candidates} "
            f"celdas_a_pedir={geocode.http_requests} (sin red)"
        )
        return
    if summary.geocode.status == "skipped":
        reason = summary.geocode.reason or "excluida con --skip geocode"
        print(f"  geocode: omitida ({reason})")
        return
    print(
        "  "
        f"geocode: candidatas={geocode.candidates} geocodificadas={geocode.geocoded_ok} "
        f"con_error={geocode.geocoded_error} cacheadas={geocode.cache_hits} "
        f"peticiones={geocode.http_requests}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``foto-index``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("Configuración inválida: %s", exc)
        return 2

    try:
        root = _resolve_library(args)
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

    try:
        bootstrap = ensure_library(root, metadata_dir, dry_run=args.dry_run)
    except LibraryError as exc:
        logger.error("%s", exc)
        return 2

    if bootstrap.would_create:
        logger.info("Se crearía library.toml en %s: la biblioteca aún no existe", metadata_dir)
    elif bootstrap.created:
        logger.info("Biblioteca preparada con un nuevo library.toml en %s", metadata_dir)

    catalog_path = metadata_dir / CATALOG_FILE_NAME

    lock: IndexerLock | None = None
    catalog: Catalog | None = None
    try:
        # El lock se adquiere antes de abrir el catálogo: la apertura puede
        # activar WAL y crear o migrar el esquema, y son escrituras que deben
        # quedarse dentro de la garantía de escritor único.
        if not args.dry_run:
            lock = IndexerLock(metadata_dir / LOCK_FILE_NAME)
            try:
                lock.acquire()
            except LockBusyError as exc:
                logger.error("%s", exc)
                return 3
            except LockError as exc:
                logger.error("%s", exc)
                return 2

        try:
            if args.dry_run:
                catalog = Catalog.open_dry_run(catalog_path)
            else:
                catalog = Catalog.open(catalog_path)
        except CatalogVersionError as exc:
            logger.error("%s", exc)
            return 2
        except CatalogError as exc:
            logger.error("No se pudo abrir el catálogo: %s", exc)
            return 2

        if args.verbose:
            logger.debug("Indexando la biblioteca en %s", root)

        extractor = ExifToolExtractor.from_environment()
        if extractor is None:
            logger.info(
                "exiftool no está disponible: se indexarán hashes y ubicaciones "
                "y los metadatos quedarán pendientes"
            )

        skip = set(args.skip)
        geocoder = None
        if config.geocoding.enabled and "geocode" not in skip:
            geocoder = NominatimGeocoder(
                ReverseGeocoderConfig(
                    endpoint=config.geocoding.endpoint,
                    user_agent=config.geocoding.user_agent,
                    language=config.geocoding.language,
                    request_interval_seconds=config.geocoding.request_interval_seconds,
                    timeout_seconds=config.geocoding.timeout_seconds,
                )
            )
        elif "geocode" not in skip:
            skip.add("geocode")
            logger.info(
                "Geocodificación desactivada en la configuración "
                "([geocoding] enabled = false)"
            )

        indexer = Indexer(
            catalog,
            root,
            options=IndexingOptions(
                full=args.full,
                prune=args.prune,
                prune_after_days=args.prune_after,
                dry_run=args.dry_run,
                skip=frozenset(skip),
            ),
            extractor=extractor,
            geocoder=geocoder,
        )
        summary = indexer.run()
        _emit_summary(summary, args)
        return 0 if summary.status == "ok" else 1
    except KeyboardInterrupt:
        logger.error("Indexado interrumpido")
        return 130
    except LibraryError as exc:
        logger.error("%s", exc)
        return 2
    except IndexingError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        if args.verbose:
            logger.exception("Error inesperado durante el indexado")
        else:
            logger.error("Error inesperado durante el indexado: %s", exc)
        return 1
    finally:
        if catalog is not None:
            catalog.close()
        if lock is not None:
            lock.release()
