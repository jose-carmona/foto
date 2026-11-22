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
from foto.core.scanner import is_apple_photos_library
from foto.indexer.geonames import NominatimGeocoder, ReverseGeocoderConfig
from foto.storage.apple_photos import ApplePhotosLibrary
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import ConfigError, load_config
from foto.storage.exiftool import ExifToolExtractor
from foto.storage.lock import LOCK_FILE_NAME, IndexerLock
from foto.storage.resolver import ORIGIN_DEFAULT, resolve_locations, resolve_metadata_dir

logger = logging.getLogger("foto.indexer")

EPILOG = """\
ubicaciones de la fototeca (ADR 0010): se declaran solo en config.toml
  [[library.locations]] (version = 2) o [library].path (version = 1).
  No existe --library: si FOTO_LIBRARY está definida, error explícito.
  Al arrancar, foto-index sincroniza la declaración con el catálogo:
  registra las ubicaciones nuevas y marca deleted las quitadas de la
  config (no borra nada: la limpieza de filas es de --prune). Una
  ubicación ausente (USB desmontado) avisa y se degrada: salida 0, sus
  fotos no se tocan y jamás se da de baja por estar desconectada.
precedencia de la metainformación (.foto, ADR 0008):
  1. --metadata PATH
  2. FOTO_METADATA
  3. [metadata].path de config.toml
  4. <única ubicación declarada>/.foto (por defecto; con varias ubicaciones
     hay que concretarla: --metadata, FOTO_METADATA o [metadata].path)
Para el fichero de configuración: --config PATH, FOTO_CONFIG o platformdirs.

fototeca de Apple Photos (.photoslibrary, ADR 0009): la política
  (solo originals/) se aplica por ubicación; la metainformación debe vivir
  fuera del paquete (--metadata, FOTO_METADATA o [metadata].path). Sin
  «Acceso total al disco» (Full Disk Access) el paquete no se puede leer.
  La fase apple-import importa favoritos y personas desde Photos.sqlite;
  es opt-in: [apple_photos] enabled = true en config.toml. Solo importa
  eso: álbumes, títulos y descripciones quedan para una fase posterior.

fases del pipeline (todas por defecto); --skip FASE las excluye:
  scan          recorrido del filesystem, hashing, metadatos y ausencias
  geocode       geocodificación inversa (OpenStreetMap/Nominatim) de las fotos con GPS
  apple-import  favoritos y personas de una fototeca Apple (opt-in, ver arriba)

códigos de salida:
  0  éxito, con o sin cambios (una ubicación ausente no es error)
  1  error no fatal, recorrido parcial, errores de ficheros o fase detenida
  2  configuración inválida, metainformación no disponible o esquema incompatible
  3  otro indexador tiene el lock

ejemplos:
  uv run foto-index
  uv run foto-index --metadata ~/Fotos.foto
  uv run foto-index --json
  uv run foto-index --skip geocode
  uv run foto-index --prune --prune-after 30
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
    parser.add_argument(
        "--metadata",
        metavar="PATH",
        type=Path,
        help="directorio de metainformación; por defecto: <única ubicación "
        "declarada>/.foto (nunca dentro de un paquete .photoslibrary)",
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


def _resolve_locations(args: argparse.Namespace) -> list:
    """Ubicaciones declaradas en la config (única fuente, ADR 0010)."""
    return list(resolve_locations(config_path=args.config))


def _check_apple_package(root: Path) -> None:
    """Paquete Apple anómalo: ``originals/`` ausente es un error temprano.

    Si el acceso falla (p. ej. TCC sin *Full Disk Access*) no se puede saber:
    el recorrido lo reportará como parcial con la causa explícita
    (ADR 0009, § 3), no aquí. Solo se llama con ubicaciones disponibles:
    una desmontada no puede ser «anómala».
    """
    try:
        present = (root / "originals").is_dir()
    except OSError as exc:
        logger.debug(
            "No se pudo comprobar originals/ del paquete Apple: %s",
            exc.strerror or type(exc).__name__,
        )
        return
    if not present:
        raise LibraryError(
            "La raíz parece un paquete de Apple Photos pero no contiene "
            "originals/: paquete anómalo o no es una fototeca de Photos"
        )


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
    _emit_locations_block(summary)
    _emit_geocode_phase(summary)
    _emit_apple_import_phase(summary)
    if summary.partial_reason:
        print(f"  aviso: {summary.partial_reason}")


def _emit_locations_block(summary: IndexingSummary) -> None:
    """Resumen de la sincronización config ↔ catálogo y de la disponibilidad."""
    sync = summary.locations_sync
    if sync.changed:
        parts = []
        if sync.added:
            parts.append(f"altas={len(sync.added)} ({', '.join(sync.added)})")
        if sync.removed:
            parts.append(f"bajas={len(sync.removed)} ({', '.join(sync.removed)})")
        if sync.reactivated:
            parts.append(f"reactivadas={len(sync.reactivated)} ({', '.join(sync.reactivated)})")
        if sync.path_changed:
            parts.append(f"cambios={len(sync.path_changed)} ({', '.join(sync.path_changed)})")
        print(f"  ubicaciones: {'; '.join(parts)}")
    for name in summary.locations_unavailable:
        print(f"  aviso: la ubicación «{name}» no está disponible; sus fotos no se tocan")


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


def _emit_apple_import_phase(summary: IndexingSummary) -> None:
    """Resumen de la fase apple-import en una línea, según cómo terminó."""
    apple = summary.apple_import
    if apple.status == "skipped":
        reason = apple.reason or "excluida con --skip apple-import"
        print(f"  apple-import: omitida ({reason})")
        return
    print(
        "  "
        f"apple-import: favoritos={apple.favorites_imported} "
        f"personas={apple.persons_imported} "
        f"no_resueltos={apple.unresolved} errores={apple.errors}"
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
        locations = _resolve_locations(args)
    except ConfigError as exc:
        logger.error("Configuración inválida: %s", exc)
        return 2

    if not locations:
        logger.error(
            "No hay ubicaciones declaradas: decláralas en "
            "'[[library.locations]]' de config.toml ('[library].path' en un "
            "fichero 'version = 1')"
        )
        return 2

    # ADR 0010 (Decisión 9): la validación de paquetes Apple pasa de global
    # a por ubicación; solo para las disponibles (una desmontada no puede
    # ser «anómala»: la tolera el indexado con un aviso).
    for location in locations:
        if location.path.is_dir() and is_apple_photos_library(location.path):
            try:
                _check_apple_package(location.path)
            except LibraryError as exc:
                logger.error("%s", exc)
                return 2

    # ADR 0008: la metainformación se resuelve solo por el resolvedor
    # compartido, nunca leyendo FOTO_METADATA ni config.toml por su cuenta.
    try:
        resolution = resolve_metadata_dir(
            locations, args.metadata, config_path=args.config
        )
    except (ConfigError, LibraryError) as exc:
        logger.error("Metainformación inválida: %s", exc)
        return 2
    metadata_dir = resolution.metadata_dir
    assert metadata_dir is not None

    # ADR 0010 (Decisión 6): la metainformación es la fuente de verdad única
    # de todas las ubicaciones; si no está disponible, error duro. Con el
    # default derivado de la única ubicación declarada, esa ubicación
    # ausente deja la sesión sin catálogo: error con la acción sugerida.
    if resolution.origin == ORIGIN_DEFAULT and not metadata_dir.parent.is_dir():
        logger.error(
            "La metainformación por defecto vive en %s y la única ubicación "
            "declarada no está disponible: monta el volumen o concreta la "
            "metainformación con --metadata PATH, FOTO_METADATA o "
            "[metadata].path",
            metadata_dir,
        )
        return 2

    if not args.dry_run:
        try:
            metadata_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(
                "No se pudo crear el directorio de metainformación: %s",
                exc.strerror or type(exc).__name__,
            )
            return 2

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
                catalog = Catalog.open_dry_run(
                    catalog_path, declared_locations=tuple(locations)
                )
            else:
                catalog = Catalog.open(catalog_path, declared_locations=tuple(locations))
        except CatalogVersionError as exc:
            logger.error("%s", exc)
            return 2
        except CatalogError as exc:
            logger.error("No se pudo abrir el catálogo: %s", exc)
            return 2

        if args.verbose:
            logger.debug("Indexando las ubicaciones declaradas en %s", metadata_dir)

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

        # ADR 0009 (§ 13): la fase apple-import es opt-in por configuración,
        # sin flags nuevos. Desactivada, sin lector: la fase se registra
        # skipped con su motivo explícito.
        apple_library_factory = None
        if config.apple_photos.enabled:
            apple_library_factory = ApplePhotosLibrary
        elif "apple-import" not in skip:
            logger.info(
                "Importación de Apple Photos desactivada en la "
                "configuración ([apple_photos] enabled = false)"
            )

        indexer = Indexer(
            catalog,
            tuple(locations),
            options=IndexingOptions(
                full=args.full,
                prune=args.prune,
                prune_after_days=args.prune_after,
                dry_run=args.dry_run,
                skip=frozenset(skip),
            ),
            extractor=extractor,
            geocoder=geocoder,
            apple_library_factory=apple_library_factory,
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
