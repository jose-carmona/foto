"""Orquestación del indexado incremental de la fototeca."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

UTC = timezone.utc

from foto.core.errors import HashError, IndexingError, LibraryError
from foto.core.hashing import Hasher, Sha256Hasher
from foto.core.metadata import ExtractionOutcome, MetadataExtractor
from foto.core.scanner import FileSignature, LibraryScanner, ScanIssue
from foto.storage.catalog import Catalog, LocationRow

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 250
DEFAULT_METADATA_BATCH_SIZE = 100


@dataclass(frozen=True)
class IndexingOptions:
    """Opciones de una pasada del indexador."""

    full: bool = False
    prune: bool = False
    prune_after_days: int = 30
    dry_run: bool = False

    @property
    def mode(self) -> str:
        return "full" if self.full else "incremental"


@dataclass
class IndexingSummary:
    """Resumen de una pasada, apto para consola y para ``--json``."""

    mode: str
    status: str = "ok"
    dry_run: bool = False
    scanned: int = 0
    unchanged: int = 0
    added: int = 0
    modified: int = 0
    missing: int = 0
    photos_total: int = 0
    locations_total: int = 0
    duplicate_photos: int = 0
    hashed_bytes: int = 0
    hashed_files: int = 0
    metadata_extracted: int = 0
    errors: int = 0
    metadata_pending: int = 0
    pruned_locations: int = 0
    pruned_photos: int = 0
    duration_seconds: float = 0.0
    partial_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Representación serializable del resumen."""
        return asdict(self)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _stat_signature(path) -> FileSignature:
    try:
        stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise HashError(f"No se pudo leer el fichero: {reason}") from exc
    return FileSignature(
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        inode=getattr(stat, "st_ino", None),
        device=getattr(stat, "st_dev", None),
    )


class Indexer:
    """Ejecuta una pasada incremental o completa sobre una biblioteca."""

    def __init__(
        self,
        catalog: Catalog,
        root,
        *,
        options: IndexingOptions,
        hasher: Hasher | None = None,
        extractor: MetadataExtractor | None = None,
        now: Callable[[], datetime] = _utcnow,
        batch_size: int = DEFAULT_BATCH_SIZE,
        metadata_batch_size: int = DEFAULT_METADATA_BATCH_SIZE,
    ) -> None:
        self.catalog = catalog
        self.root = root
        self.options = options
        self.hasher = hasher or Sha256Hasher()
        self.extractor = extractor
        self._now = now
        self.batch_size = max(1, batch_size)
        self.metadata_batch_size = max(1, metadata_batch_size)

    def run(self) -> IndexingSummary:
        """Ejecuta una pasada completa y devuelve su resumen."""
        if not self.root.is_dir():
            raise LibraryError("La raíz de la biblioteca no existe o no es un directorio")
        if self.options.prune_after_days < 0:
            raise IndexingError("--prune-after no puede ser negativo")

        started_monotonic = time.monotonic()
        started_at = _iso(self._now())
        summary = IndexingSummary(mode=self.options.mode, dry_run=self.options.dry_run)

        self.catalog.mark_interrupted_runs(started_at)
        run_id = self.catalog.start_run(self.options.mode, started_at)

        try:
            self.catalog.prepare_seen()
            self._scan(summary)
            if self._scan_complete:
                summary.missing = self.catalog.mark_missing(_iso(self._now()))
                self.catalog.commit()
            else:
                summary.partial_reason = (
                    "El recorrido fue parcial; no se marcaron ausencias para no "
                    "confundir un error de acceso con fotos borradas"
                )

            if self.extractor is not None:
                self._extract_metadata(summary)

            if self.options.prune:
                (
                    summary.pruned_locations,
                    summary.pruned_photos,
                ) = self.catalog.prune(self._now(), self.options.prune_after_days)

            counts = self.catalog.counts()
            summary.photos_total = counts.photos
            summary.locations_total = counts.locations
            summary.duplicate_photos = counts.duplicate_photos
            summary.metadata_pending = counts.metadata_pending
            summary.duration_seconds = round(time.monotonic() - started_monotonic, 3)
            summary.status = self._final_status(summary)

            self.catalog.finish_run(
                run_id,
                status=summary.status,
                finished_at=_iso(self._now()),
                scanned=summary.scanned,
                unchanged=summary.unchanged,
                added=summary.added,
                modified=summary.modified,
                missing=summary.missing,
                errors=summary.errors,
                hashed_bytes=summary.hashed_bytes,
            )
            return summary
        except KeyboardInterrupt:
            self.catalog.finish_run(
                run_id,
                status="interrupted",
                finished_at=_iso(self._now()),
                scanned=summary.scanned,
                unchanged=summary.unchanged,
                added=summary.added,
                modified=summary.modified,
                missing=summary.missing,
                errors=summary.errors,
                hashed_bytes=summary.hashed_bytes,
            )
            raise
        except Exception:
            try:
                self.catalog.finish_run(
                    run_id,
                    status="error",
                    finished_at=_iso(self._now()),
                    scanned=summary.scanned,
                    unchanged=summary.unchanged,
                    added=summary.added,
                    modified=summary.modified,
                    missing=summary.missing,
                    errors=summary.errors,
                    hashed_bytes=summary.hashed_bytes,
                )
            except Exception:
                logger.debug("No se pudo cerrar scan_runs como error", exc_info=True)
            raise
        finally:
            self.catalog.drop_seen()

    def _final_status(self, summary: IndexingSummary) -> str:
        if not self._scan_complete or summary.errors > 0:
            return "partial"
        return "ok"

    def _scan(self, summary: IndexingSummary) -> None:
        scanner = LibraryScanner(self.root)
        seen_buffer: list[str] = []

        for event in scanner.scan():
            if isinstance(event, ScanIssue):
                summary.errors += 1
                if event.kind == "file":
                    summary.scanned += 1
                    seen_buffer.append(event.rel_path)
                seen_buffer = self._flush_seen(seen_buffer)
                continue

            summary.scanned += 1
            self._process_file(event, summary)
            seen_buffer.append(event.rel_path)
            seen_buffer = self._flush_seen(seen_buffer)

        self._flush_seen(seen_buffer, force=True)
        self.catalog.commit()
        self._scan_complete = scanner.complete
        if not scanner.complete:
            logger.warning(
                "El recorrido no fue completo: %s directorios fallaron",
                scanner.directory_errors,
            )

    def _flush_seen(self, buffer: list[str], *, force: bool = False) -> list[str]:
        if not buffer:
            return buffer
        if not force and len(buffer) < self.batch_size:
            return buffer
        self.catalog.add_seen(buffer)
        self.catalog.commit()
        return []

    def _process_file(self, scanned, summary: IndexingSummary) -> None:
        now = _iso(self._now())
        signature = scanned.signature
        existing = self.catalog.get_location(scanned.rel_path)

        if existing is None:
            self._index_new(scanned, summary, now)
            return

        same_signature = self._same_signature(existing, signature)
        if not self.options.full and same_signature:
            if existing.status == "present":
                summary.unchanged += 1
            else:
                self.catalog.mark_location_present(scanned.rel_path, now)
                logger.info("La foto reaparece en %s", scanned.rel_path)
                summary.unchanged += 1
            return

        # Firma distinta o modo --full: hay que leer y hashear.
        try:
            photo_hash, fresh, hashed_bytes = self._stable_hash(scanned.path)
        except (HashError, OSError) as exc:
            summary.errors += 1
            logger.warning("No se pudo hashear %s: %s", scanned.rel_path, exc)
            return

        summary.hashed_bytes += hashed_bytes
        summary.hashed_files += 1
        if photo_hash == existing.photo_hash:
            self.catalog.update_location_signature(
                rel_path=scanned.rel_path,
                photo_hash=photo_hash,
                size=fresh.size,
                mtime_ns=fresh.mtime_ns,
                inode=fresh.inode,
                device=fresh.device,
                now=now,
            )
            summary.unchanged += 1
            return

        self.catalog.insert_photo(
            photo_hash,
            scanned.path.suffix.lower(),
            None,
            now,
        )
        self.catalog.update_location_signature(
            rel_path=scanned.rel_path,
            photo_hash=photo_hash,
            size=fresh.size,
            mtime_ns=fresh.mtime_ns,
            inode=fresh.inode,
            device=fresh.device,
            now=now,
        )
        summary.modified += 1
        logger.info("Contenido modificado en %s", scanned.rel_path)

    def _index_new(self, scanned, summary: IndexingSummary, now: str) -> None:
        try:
            photo_hash, fresh, hashed_bytes = self._stable_hash(scanned.path)
        except (HashError, OSError) as exc:
            summary.errors += 1
            logger.warning("No se pudo hashear %s: %s", scanned.rel_path, exc)
            return

        summary.hashed_bytes += hashed_bytes
        summary.hashed_files += 1
        self.catalog.insert_photo(
            photo_hash,
            scanned.path.suffix.lower(),
            None,
            now,
        )
        self.catalog.insert_location(
            rel_path=scanned.rel_path,
            photo_hash=photo_hash,
            size=fresh.size,
            mtime_ns=fresh.mtime_ns,
            inode=fresh.inode,
            device=fresh.device,
            now=now,
        )
        summary.added += 1
        logger.info("Nueva imagen en %s", scanned.rel_path)

    def _stable_hash(self, path) -> tuple[str, FileSignature, int]:
        """Hashea comprobando que la firma no cambió durante la lectura."""
        for _ in range(2):
            before = _stat_signature(path)
            photo_hash = self.hasher.hash_file(path)
            after = _stat_signature(path)
            if before == after:
                return photo_hash, after, before.size
        raise HashError("El fichero cambió durante la lectura")

    @staticmethod
    def _same_signature(existing: LocationRow, signature: FileSignature) -> bool:
        return (
            existing.size == signature.size
            and existing.mtime_ns == signature.mtime_ns
            and existing.inode == signature.inode
            and existing.device == signature.device
        )

    def _extract_metadata(self, summary: IndexingSummary) -> None:
        after_hash: str | None = None
        while True:
            targets = self.catalog.fetch_metadata_batch(
                full=self.options.full,
                after_hash=after_hash,
                limit=self.metadata_batch_size,
            )
            if not targets:
                break

            paths = [self.root / target.rel_path for target in targets]
            outcomes = self.extractor.extract_batch(paths)
            now = _iso(self._now())

            for target, path in zip(targets, paths):
                outcome = outcomes.get(path)
                if outcome is None:
                    outcome = ExtractionOutcome(error="El extractor no devolvió resultado")
                if outcome.error is not None:
                    self.catalog.update_metadata_error(target.hash, outcome.error, now)
                    summary.errors += 1
                    logger.warning(
                        "No se pudieron extraer metadatos de %s: %s",
                        target.rel_path,
                        outcome.error,
                    )
                elif outcome.metadata is not None:
                    self.catalog.update_metadata(target.hash, outcome.metadata, now)
                    summary.metadata_extracted += 1
                else:
                    self.catalog.update_metadata_error(
                        target.hash, "El extractor devolvió un resultado vacío", now
                    )
                    summary.errors += 1

            self.catalog.commit()
            after_hash = targets[-1].hash
