"""Aplicación de los ficheros JSON sobre el catálogo: merge aditivo (ADR 0007).

El import **nunca borra** y **no toca lo existente**: añade etiquetas que
faltan, inserta asignaciones con ``INSERT OR IGNORE`` (la unicidad
``(foto, etiqueta, región)`` la garantiza el índice con ``COALESCE`` del
esquema) y rellena ``place_*`` solo de fotos con ``place_status ∈ ('pending',
'none')`` —nunca ``ok``, y ``error`` solo con ``--force``. Todo el fichero se
aplica en **una única transacción**: o se aplica entero o no aplica nada.

Las filas de asignación creadas por el import nacen ``source='import'``
(ADR 0007, Decisión 8) aunque el fichero traiga ``manual`` o ``ml``; el
``status`` y la ``confidence`` del fichero se conservan tal cual, incluidas
las filas ``rejected``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from foto.core.errors import TransferError
from foto.core.geocoding import Place, PlaceRecord
from foto.storage.catalog import Catalog
from foto.storage.tags import FAVORITE_TAG
from foto.transfer import model
from foto.transfer.model import PlacesFile, TagsFile

logger = logging.getLogger("foto.transfer")

#: Re-serialización canónica del ``address`` para ``place_address_json``.
_ADDRESS_JSON_KWARGS = {"ensure_ascii": False, "sort_keys": True}


@dataclass(frozen=True)
class ImportCounters:
    """Contadores de un fichero aplicado; sirve también para los totales."""

    tags_created: int = 0
    tags_existing: int = 0
    kind_warnings: int = 0
    ignored_tags: int = 0
    assignments_applied: int = 0
    assignments_existing: int = 0
    assignments_without_photo: int = 0
    assignments_ignored: int = 0
    places_filled: int = 0
    places_filled_force: int = 0
    places_skipped_resolved: int = 0
    places_skipped_error: int = 0
    missing_hashes: tuple[str, ...] = ()

    @property
    def warnings(self) -> int:
        """Avisos no fatales: salen en el resumen y piden código de salida 1."""
        return (
            self.kind_warnings
            + self.ignored_tags
            + self.assignments_ignored
            + self.places_skipped_error
        )

    def to_dict(self) -> dict[str, object]:
        """Representación serializable, con los hashes ausentes solo contados."""
        return {
            "tags_created": self.tags_created,
            "tags_existing": self.tags_existing,
            "kind_warnings": self.kind_warnings,
            "ignored_tags": self.ignored_tags,
            "assignments_applied": self.assignments_applied,
            "assignments_existing": self.assignments_existing,
            "assignments_without_photo": self.assignments_without_photo,
            "assignments_ignored": self.assignments_ignored,
            "places_filled": self.places_filled,
            "places_filled_force": self.places_filled_force,
            "places_skipped_resolved": self.places_skipped_resolved,
            "places_skipped_error": self.places_skipped_error,
            "missing_hashes": len(self.missing_hashes),
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class AppliedFile:
    """Resultado de aplicar un fichero concreto."""

    kind: str
    path: Path
    counters: ImportCounters


@dataclass(frozen=True)
class ImportSummary:
    """Resumen de una pasada de ``foto-import``, por fichero y total."""

    dry_run: bool
    force: bool
    files: tuple[AppliedFile, ...]
    totals: ImportCounters

    def to_dict(self) -> dict[str, object]:
        """Representación serializable del resumen."""
        return {
            "dry_run": self.dry_run,
            "force": self.force,
            "files": [
                {"kind": applied.kind, "path": str(applied.path), **applied.counters.to_dict()}
                for applied in self.files
            ],
            "totals": self.totals.to_dict(),
        }


def read_tags_file(path: Path) -> TagsFile:
    """Lee y valida por completo un ``tags.json`` (sin tocar el catálogo)."""
    return model.tags_file_from_dict(_read_json(path))


def read_places_file(path: Path) -> PlacesFile:
    """Lee y valida por completo un ``places.json`` (sin tocar el catálogo)."""
    return model.places_file_from_dict(_read_json(path))


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise TransferError(f"No se pudo leer {path.name}: {reason}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransferError(f"{path.name} no es un JSON válido: {exc}") from exc


def _finish_transaction(catalog: Catalog, message: str) -> None:
    """Confirma la transacción única del fichero; ante cualquier fallo revierte."""
    try:
        catalog.commit()
    except BaseException:
        catalog.rollback()
        logger.debug("Transacción de import revertida: %s", message, exc_info=True)
        raise


def apply_tags_file(catalog: Catalog, data: TagsFile, *, now: str) -> ImportCounters:
    """Aplica ``tags.json`` al catálogo con merge aditivo idempotente.

    Etiquetas por ``name_normalized``: si no existe se crea con el ``kind`` y
    las fechas del fichero; si existe se conserva sin tocarla (con aviso si el
    ``kind`` difiere). Una etiqueta ``system`` distinta de ``fav`` no puede
    existir por el índice singleton: aviso y fila ignorada, igual que sus
    asignaciones. Las asignaciones con foto presente se insertan con
    ``INSERT OR IGNORE`` y ``source='import'``; las existentes quedan
    intocables. Los hashes ausentes se cuentan, no abortan y no se escriben.
    """
    connection = catalog.connection
    tags_created = tags_existing = kind_warnings = ignored_tags = 0
    applied = assignments_existing = without_photo = ignored_assignments = 0
    missing_hashes: set[str] = set()
    skipped_tags: set[str] = set()
    tag_ids: dict[str, int] = {}
    try:
        catalog_tags = {
            str(row["name_normalized"]): (int(row["id"]), str(row["kind"]))
            for row in connection.execute(
                "SELECT id, name_normalized, kind FROM tags"
            ).fetchall()
        }
        for tag in data.tags:
            current = catalog_tags.get(tag.normalized)
            if current is None:
                if tag.kind == "system" and tag.normalized != FAVORITE_TAG:
                    # El índice tags_system_singleton_idx impide una segunda
                    # etiqueta de sistema: aviso y fila ignorada.
                    ignored_tags += 1
                    skipped_tags.add(tag.normalized)
                    logger.warning(
                        "Etiqueta de sistema distinta de 'fav' ignorada: no puede existir"
                    )
                    continue
                cursor = connection.execute(
                    "INSERT INTO tags (name, name_normalized, kind, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?)",
                    (tag.name, tag.normalized, tag.kind, tag.created_at, tag.updated_at),
                )
                tag_ids[tag.normalized] = int(cursor.lastrowid)
                tags_created += 1
                continue
            tag_ids[tag.normalized] = current[0]
            tags_existing += 1
            if current[1] != tag.kind:
                kind_warnings += 1
                logger.warning(
                    "Etiqueta existente con kind distinto al del fichero: se conserva la existente"
                )

        for assignment in data.assignments:
            if assignment.tag in skipped_tags:
                ignored_assignments += 1
                continue
            tag_id = tag_ids.get(assignment.tag)
            if tag_id is None:  # imposible tras la validación del fichero
                raise TransferError(
                    f"La asignación referencia una etiqueta ausente del fichero: "
                    f"{assignment.tag!r}"
                )
            present = connection.execute(
                "SELECT 1 FROM photos WHERE hash = ?", (assignment.photo,)
            ).fetchone()
            if present is None:
                missing_hashes.add(assignment.photo)
                without_photo += 1
                continue
            region_values = (
                (assignment.region[0], assignment.region[1],
                 assignment.region[2], assignment.region[3])
                if assignment.region is not None
                else (None, None, None, None)
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO photo_tags (photo_hash, tag_id, source, "
                "status, confidence, region_x, region_y, region_w, region_h, "
                "created_at, updated_at) VALUES (?, ?, 'import', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment.photo,
                    tag_id,
                    assignment.status,
                    assignment.confidence,
                    *region_values,
                    assignment.created_at,
                    assignment.updated_at,
                ),
            )
            if cursor.rowcount == 1:
                applied += 1
            else:
                assignments_existing += 1
        _finish_transaction(catalog, "aplicación de tags.json")
    except sqlite3.Error as exc:
        catalog.rollback()
        raise TransferError(f"No se pudo aplicar el fichero de etiquetas: {exc}") from exc
    except BaseException:
        catalog.rollback()
        raise
    return ImportCounters(
        tags_created=tags_created,
        tags_existing=tags_existing,
        kind_warnings=kind_warnings,
        ignored_tags=ignored_tags,
        assignments_applied=applied,
        assignments_existing=assignments_existing,
        assignments_without_photo=without_photo,
        assignments_ignored=ignored_assignments,
        missing_hashes=tuple(sorted(missing_hashes)),
    )


def apply_places_file(
    catalog: Catalog, data: PlacesFile, *, now: str, force: bool
) -> ImportCounters:
    """Aplica ``places.json`` al catálogo: aditivo sobre lo ya resuelto.

    Solo fotos presentes. Se rellenan las columnas ``place_*`` (paquete
    completo del fichero, ``address_json`` re-serializado canónico,
    ``place_status='ok'``, ``place_error=NULL`` y ``place_updated_at`` con la
    hora del import, por parámetro) **solo** de fotos con estado
    ``pending`` o ``none``; un ``ok`` jamás se pisa y un ``error`` solo con
    ``--force``. La caché ``geocode_cells`` no se toca: el import no habla
    con la red.
    """
    connection = catalog.connection
    filled = filled_force = skipped_resolved = skipped_error = 0
    missing_hashes: set[str] = set()
    try:
        for place in data.places:
            row = connection.execute(
                "SELECT place_status FROM photos WHERE hash = ?", (place.photo,)
            ).fetchone()
            if row is None:
                missing_hashes.add(place.photo)
                continue
            status = str(row["place_status"])
            if status == "ok":
                # Lo resuelto manda sobre lo importado: merge aditivo puro.
                skipped_resolved += 1
                continue
            if status == "error" and not force:
                skipped_error += 1
                continue
            address_json = (
                json.dumps(place.address, **_ADDRESS_JSON_KWARGS)
                if place.address is not None
                else None
            )
            record = PlaceRecord(
                place=Place(
                    country=place.country,
                    country_code=place.country_code,
                    state=place.state,
                    city=place.city,
                    municipality=place.municipality,
                ),
                display_name=place.display_name,
                address_json=address_json,
            )
            catalog.update_place(place.photo, record, now)
            filled += 1
            if status == "error":
                filled_force += 1
        _finish_transaction(catalog, "aplicación de places.json")
    except sqlite3.Error as exc:
        catalog.rollback()
        raise TransferError(f"No se pudo aplicar el fichero de lugares: {exc}") from exc
    except BaseException:
        catalog.rollback()
        raise
    return ImportCounters(
        places_filled=filled,
        places_filled_force=filled_force,
        places_skipped_resolved=skipped_resolved,
        places_skipped_error=skipped_error,
        missing_hashes=tuple(sorted(missing_hashes)),
    )
