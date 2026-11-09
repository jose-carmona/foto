"""Orquestación del indexado de la fototeca como pipeline de fases.

Fases (ADR 0005): ``scan`` (recorrido, hashing, ubicaciones, metadatos y
reconciliación) y ``geocode`` (geocodificación inversa de las fotos con GPS).
Cada fase registra su estado, sus contadores y su motivo en el historial de la
pasada y el resumen; el servicio de geocodificación se inyecta y no conoce
HTTP desde aquí.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from foto.core.errors import (
    GeocodingRejectedError,
    GeocodingResponseError,
    HashError,
    IndexingError,
    LibraryError,
)
from foto.core.geocoding import REVERSE_ZOOM, Geocoder, cell_for
from foto.core.hashing import Hasher, Sha256Hasher
from foto.core.metadata import ExtractionOutcome, MetadataExtractor
from foto.core.scanner import FileSignature, LibraryScanner, ScanIssue
from foto.storage.catalog import Catalog, LocationRow

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 250
DEFAULT_METADATA_BATCH_SIZE = 100
DEFAULT_GEOCODE_BATCH_SIZE = 100

#: Fases del pipeline, en orden de ejecución.
INDEXING_PHASES: tuple[str, ...] = ("scan", "geocode")


@dataclass(frozen=True)
class IndexingOptions:
    """Opciones de una pasada del indexador."""

    full: bool = False
    prune: bool = False
    prune_after_days: int = 30
    dry_run: bool = False
    skip: frozenset[str] = frozenset()

    @property
    def mode(self) -> str:
        return "full" if self.full else "incremental"


@dataclass
class PhaseResult:
    """Resultado de una fase del pipeline: estado y motivo.

    ``status`` admite ``ok``, ``partial``, ``skipped`` y ``error``.
    """

    status: str = "ok"
    reason: str | None = None


@dataclass
class GeocodePhaseResult:
    """Resultado y contadores de la fase de geocodificación."""

    status: str = "ok"
    reason: str | None = None
    candidates: int = 0
    geocoded_ok: int = 0
    geocoded_error: int = 0
    cache_hits: int = 0
    http_requests: int = 0
    unique_cells: int = 0
    dry_run: bool = False


@dataclass
class IndexingSummary:
    """Resumen de una pasada, apto para consola y para ``--json``.

    Los contadores de la fase ``scan`` se mantienen a nivel de pasada, como
    hasta ahora; cada fase aporta además su ``status``, su ``reason`` y sus
    contadores propios al JSON segmentado de ``to_dict``.
    """

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
    place_pending: int = 0
    pruned_locations: int = 0
    pruned_photos: int = 0
    duration_seconds: float = 0.0
    partial_reason: str | None = None
    scan_phase: PhaseResult = field(default_factory=PhaseResult)
    geocode: GeocodePhaseResult = field(default_factory=GeocodePhaseResult)

    def to_dict(self) -> dict[str, object]:
        """Representación serializable del resumen, segmentada por fase."""
        return {
            "mode": self.mode,
            "status": self.status,
            "dry_run": self.dry_run,
            "duration_seconds": self.duration_seconds,
            "totals": {
                "photos": self.photos_total,
                "locations": self.locations_total,
                "duplicate_photos": self.duplicate_photos,
                "metadata_pending": self.metadata_pending,
                "place_pending": self.place_pending,
            },
            "phases": {
                "scan": {
                    "status": self.scan_phase.status,
                    "reason": self.scan_phase.reason,
                    "scanned": self.scanned,
                    "unchanged": self.unchanged,
                    "added": self.added,
                    "modified": self.modified,
                    "missing": self.missing,
                    "errors": self.errors,
                    "hashed_files": self.hashed_files,
                    "hashed_bytes": self.hashed_bytes,
                    "metadata_extracted": self.metadata_extracted,
                    "metadata_pending": self.metadata_pending,
                    "pruned_locations": self.pruned_locations,
                    "pruned_photos": self.pruned_photos,
                },
                "geocode": {
                    "status": self.geocode.status,
                    "reason": self.geocode.reason,
                    "candidates": self.geocode.candidates,
                    "geocoded_ok": self.geocode.geocoded_ok,
                    "geocoded_error": self.geocode.geocoded_error,
                    "cache_hits": self.geocode.cache_hits,
                    "http_requests": self.geocode.http_requests,
                    "unique_cells": self.geocode.unique_cells,
                    "dry_run": self.geocode.dry_run,
                },
            },
        }


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
    """Ejecuta una pasada incremental o completa sobre una biblioteca.

    La pasada es un pipeline de fases: cada fase se ejecuta solo si la pasada
    llegó a ella sin error fatal, y todas comparten lock, catálogo y resumen.
    """

    def __init__(
        self,
        catalog: Catalog,
        root,
        *,
        options: IndexingOptions,
        hasher: Hasher | None = None,
        extractor: MetadataExtractor | None = None,
        geocoder: Geocoder | None = None,
        now: Callable[[], datetime] = _utcnow,
        batch_size: int = DEFAULT_BATCH_SIZE,
        metadata_batch_size: int = DEFAULT_METADATA_BATCH_SIZE,
        geocode_batch_size: int = DEFAULT_GEOCODE_BATCH_SIZE,
    ) -> None:
        if options.skip - set(INDEXING_PHASES):
            unknown = ", ".join(sorted(options.skip - set(INDEXING_PHASES)))
            raise IndexingError(f"Se desconoce la fase a omitir: {unknown}")
        self.catalog = catalog
        self.root = root
        self.options = options
        self.hasher = hasher or Sha256Hasher()
        self.extractor = extractor
        self.geocoder = geocoder
        self._now = now
        self.batch_size = max(1, batch_size)
        self.metadata_batch_size = max(1, metadata_batch_size)
        self.geocode_batch_size = max(1, geocode_batch_size)

    # ------------------------------------------------------------------
    # Pasada
    # ------------------------------------------------------------------
    def run(self) -> IndexingSummary:
        """Ejecuta el pipeline de fases y devuelve su resumen."""
        if not self.root.is_dir():
            raise LibraryError("La raíz de la biblioteca no existe o no es un directorio")
        if self.options.prune_after_days < 0:
            raise IndexingError("--prune-after no puede ser negativo")

        started_monotonic = time.monotonic()
        started_at = _iso(self._now())
        summary = IndexingSummary(
            mode=self.options.mode,
            dry_run=self.options.dry_run,
            geocode=GeocodePhaseResult(dry_run=self.options.dry_run),
        )

        self.catalog.mark_interrupted_runs(started_at)
        run_id = self.catalog.start_run(self.options.mode, started_at)

        self._scan_complete = True
        completed: set[str] = set()
        try:
            interrupted = False
            for phase in INDEXING_PHASES:
                if interrupted:
                    self._record_phase(
                        run_id, summary, phase, status="skipped",
                        reason="La pasada se interrumpió en una fase anterior",
                    )
                    continue
                if phase in self.options.skip:
                    self._record_phase(
                        run_id, summary, phase, status="skipped",
                        reason=f"Excluida con --skip {phase}",
                    )
                    continue
                try:
                    if phase == "scan":
                        self._run_scan_phase(summary, run_id)
                    elif phase == "geocode":
                        self._run_geocode_phase(summary, run_id)
                    completed.add(phase)
                except KeyboardInterrupt:
                    interrupted = True
                    raise

            counts = self.catalog.counts()
            summary.photos_total = counts.photos
            summary.locations_total = counts.locations
            summary.duplicate_photos = counts.duplicate_photos
            summary.metadata_pending = counts.metadata_pending
            summary.place_pending = counts.place_pending
            summary.duration_seconds = round(time.monotonic() - started_monotonic, 3)
            _apply_phase_status(summary)
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
            try:
                self._record_interrupted_phases(run_id, summary, completed)
            except Exception:
                logger.debug(
                    "No se pudo registrar la interrupción de fase en el catálogo",
                    exc_info=True,
                )
            self._finish_aborted_run(run_id, summary, "interrupted")
            raise
        except Exception:
            try:
                self._record_pending_phases_as_error(run_id, summary, completed)
            except Exception:
                logger.debug(
                    "No se pudo registrar el error de fase en el catálogo",
                    exc_info=True,
                )
            self._finish_aborted_run(run_id, summary, "error")
            raise
        finally:
            self.catalog.drop_seen()

    def _finish_aborted_run(self, run_id: int, summary: IndexingSummary, status: str) -> None:
        try:
            self.catalog.finish_run(
                run_id,
                status=status,
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
            logger.debug("No se pudo cerrar el run del catálogo", exc_info=True)

    # ------------------------------------------------------------------
    # Fase scan
    # ------------------------------------------------------------------
    def _run_scan_phase(self, summary: IndexingSummary, run_id: int) -> None:
        """Lanza la fase ``scan``: recorrido, metadatos y reconciliación.

        Un recorrido parcial o con errores de ficheros no es fatal: la fase
        queda ``partial`` y el pipeline continúa. Solo una excepción (raíz
        ausente, catálogo, disco…) aborta la pasada.
        """
        self.catalog.prepare_seen()
        self._scan(summary)
        if self._scan_complete:
            summary.missing = self.catalog.mark_missing(_iso(self._now()))
            self.catalog.commit()
        else:
            summary.scan_phase.reason = (
                "El recorrido fue parcial; no se marcaron ausencias para no "
                "confundir un error de acceso con fotos borradas"
            )
            logger.warning(summary.scan_phase.reason)
        if self.extractor is not None:
            self._extract_metadata(summary)
        # prune: no es una fase propia, es trabajo opcional del scan.
        if self.options.prune:
            (
                summary.pruned_locations,
                summary.pruned_photos,
            ) = self.catalog.prune(self._now(), self.options.prune_after_days)
        summary.scan_phase.status = "ok" if self._scan_phase_ok(summary) else "partial"
        self.catalog.record_phase(
            run_id,
            "scan",
            status=summary.scan_phase.status,
            reason=summary.scan_phase.reason,
            now=_iso(self._now()),
        )

    def _scan_phase_ok(self, summary: IndexingSummary) -> bool:
        return self._scan_complete and summary.errors == 0

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

    # ------------------------------------------------------------------
    # Geocodificación inversa como fase del pipeline
    # ------------------------------------------------------------------
    def _run_geocode_phase(self, summary: IndexingSummary, run_id: int) -> None:
        geocode = summary.geocode
        geocode.dry_run = self.options.dry_run
        try:
            if self.options.dry_run:
                self._geocode_dry_run(summary)
            elif self.geocoder is None:
                geocode.status = "skipped"
                geocode.reason = "sin geocodificador configurado"
            else:
                self._geocode(summary)
        except KeyboardInterrupt:
            geocode.status = "partial"
            geocode.reason = "Interrumpido con SIGINT"
            try:
                self.catalog.commit()
            except Exception:
                logger.debug(
                    "No se pudo cerrar la transacción al interrumpir geocodificar",
                    exc_info=True,
                )
            raise
        self.catalog.record_phase(
            run_id,
            "geocode",
            status=geocode.status,
            reason=geocode.reason,
            candidates=geocode.candidates,
            processed=geocode.geocoded_ok,
            errors=geocode.geocoded_error,
            cache_hits=geocode.cache_hits,
            http_requests=geocode.http_requests,
            now=_iso(self._now()),
        )

    def _geocode_dry_run(self, summary: IndexingSummary) -> None:
        """Informe de la fase sin tocar la red ni escribir."""
        geocode = summary.geocode
        full = self.options.full
        geocode.candidates = self.catalog.count_geocode_candidates(full=full)
        targets = self.catalog.fetch_geocode_targets(full=full, after_hash=None, limit=None)
        cells = {cell_for(target.latitude, target.longitude).key for target in targets}
        geocode.unique_cells = len(cells)
        requested = sum(
            1 for latitude, longitude in cells
            if self.catalog.get_place_cell(latitude, longitude, min_zoom=REVERSE_ZOOM)
            is None
        )
        geocode.http_requests = requested
        geocode.cache_hits = geocode.candidates - requested
        geocode.status = "ok"
        logger.info(
            "Simulación de la fase geocode: %d candidatas, %d celdas a pedir",
            geocode.candidates,
            requested,
        )

    def _geocode(self, summary: IndexingSummary) -> None:
        """Geocodifica las fotos con GPS y sin lugar, con caché por celda."""
        geocode = summary.geocode
        full = self.options.full
        geocoder = self.geocoder
        assert geocoder is not None  # la fase solo arranca con servicio
        geocode.candidates = self.catalog.count_geocode_candidates(full=full)

        after_hash: str | None = None
        stop_reason: str | None = None
        while stop_reason is None:
            targets = self.catalog.fetch_geocode_targets(
                full=full,
                after_hash=after_hash,
                limit=self.geocode_batch_size,
            )
            if not targets:
                break

            for target in targets:
                cell = cell_for(target.latitude, target.longitude)
                record = self.catalog.get_place_cell(
                    cell.latitude, cell.longitude, min_zoom=REVERSE_ZOOM
                )
                if record is not None:
                    geocode.cache_hits += 1
                else:
                    try:
                        record = geocoder.reverse(cell.latitude, cell.longitude)
                    except GeocodingResponseError as exc:
                        # Respuesta no interpretable: error de esta foto, la
                        # fase continúa con el resto.
                        self.catalog.update_place_error(
                            target.hash, str(exc), _iso(self._now())
                        )
                        geocode.geocoded_error += 1
                        logger.warning("No se pudo geocodificar una celda: %s", exc)
                        continue
                    except GeocodingRejectedError as exc:
                        # Rechazo o fallo de red: parar la fase en el acto.
                        stop_reason = str(exc)
                        break
                    geocode.http_requests += 1
                    self.catalog.put_place_cell(
                        cell.latitude,
                        cell.longitude,
                        record,
                        zoom=REVERSE_ZOOM,
                        now=_iso(self._now()),
                    )
                self.catalog.update_place(target.hash, record, _iso(self._now()))
                geocode.geocoded_ok += 1

            self.catalog.commit()
            after_hash = targets[-1].hash

        geocode.status = "ok"
        if stop_reason is not None:
            geocode.status = "partial"
            geocode.reason = f"La fase se detuvo: {stop_reason}"
            logger.warning("%s", geocode.reason)

    # ------------------------------------------------------------------
    # Registro de fases en el catálogo
    # ------------------------------------------------------------------
    def _record_phase(
        self,
        run_id: int,
        summary: IndexingSummary,
        phase: str,
        *,
        status: str,
        reason: str,
    ) -> None:
        """Registra una fase no ejecutada y la refleja en el resumen."""
        if phase == "geocode":
            summary.geocode.status = status
            summary.geocode.reason = (
                reason if summary.geocode.reason is None else summary.geocode.reason
            )
        else:
            summary.scan_phase.status = status
            summary.scan_phase.reason = reason
        self.catalog.record_phase(run_id, phase, status=status, reason=reason,
                                  now=_iso(self._now()))

    def _record_interrupted_phases(
        self, run_id: int, summary: IndexingSummary, completed: set[str]
    ) -> None:
        """Registra la fase interrumpida como ``partial`` y el resto ``skipped``."""
        interrupt_recorded = False
        for phase in INDEXING_PHASES:
            if phase in completed:
                continue
            if interrupt_recorded:
                self._record_phase(
                    run_id, summary, phase, status="skipped",
                    reason="La pasada se interrumpió en una fase anterior",
                )
                continue
            interrupt_recorded = True
            phase_result = self._phase_result(summary, phase)
            status = phase_result.status if phase_result.status == "partial" else "partial"
            reason = phase_result.reason or "Interrumpido con SIGINT"
            self.catalog.record_phase(run_id, phase, status=status, reason=reason,
                                      now=_iso(self._now()))

    def _record_pending_phases_as_error(
        self, run_id: int, summary: IndexingSummary, completed: set[str]
    ) -> None:
        """Marca como ``error`` la fase en curso y ``skipped`` el resto.

        Solo se llama cuando una excepción fatal abortó la pasada; las
        fases que no llegaron a ejecutarse quedan registradas.
        """
        failed_recorded = False
        for phase in INDEXING_PHASES:
            if phase in completed:
                continue
            failed = not failed_recorded
            failed_recorded = True
            status = "error" if failed else "skipped"
            reason = (
                "La pasada abortó con un error fatal en esta fase"
                if failed else "La pasada abortó en una fase anterior"
            )
            self._record_phase(run_id, summary, phase, status=status, reason=reason)

    @staticmethod
    def _phase_result(summary: IndexingSummary, phase: str) -> PhaseResult:
        """El ``PhaseResult`` de una fase para el registro de interrupciones."""
        if phase == "scan":
            return summary.scan_phase
        return PhaseResult(status=summary.geocode.status, reason=summary.geocode.reason)


def _apply_phase_status(summary: IndexingSummary) -> None:
    """Deriva el estado de la pasada del estado de sus fases.

    Una pasada es ``partial`` si alguna fase ejecutada terminó parcial; el
    resto del estado (``interrupted``/``error``) lo decide quien captura la
    excepción correspondiente.
    """
    partial: list[tuple[str, str | None]] = []
    if summary.scan_phase.status == "partial":
        partial.append(("scan", summary.scan_phase.reason))
    if summary.geocode.status == "partial":
        partial.append(("geocode", summary.geocode.reason))

    if not partial:
        summary.status = "ok"
        summary.partial_reason = None
        return

    summary.status = "partial"
    motive = next((reason for _, reason in partial if reason), None)
    summary.partial_reason = motive or "; ".join(f"{name} parcial" for name, _ in partial)
