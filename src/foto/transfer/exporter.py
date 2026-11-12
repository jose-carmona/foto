"""Lectura del catálogo y generación de los ficheros JSON de exportación.

Único sitio del paso 2 con SQL: lee con la conexión de solo lectura del
catálogo (``Catalog.open_read_only``, WAL) y nunca escribe en él (ADR 0007,
Decisión 4). La escritura de los ficheros es atómica: temporal en el
directorio de destino + ``os.replace``, como las miniaturas y la
configuración.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from foto.core.errors import TransferError
from foto.transfer import model
from foto.transfer.model import (
    AssignmentEntry,
    PlaceEntry,
    PlacesFile,
    TagEntry,
    TagsFile,
)

logger = logging.getLogger("foto.transfer")

#: Nombres fijos de los ficheros de exportación (ADR 0007, Decisión 2).
TAGS_FILE_NAME = "tags.json"
PLACES_FILE_NAME = "places.json"


@dataclass(frozen=True)
class ExportSummary:
    """Resumen de una exportación, apto para consola y para ``--json``.

    Solo conteos y rutas de destino: **nunca** nombres de etiquetas ni
    direcciones (privacidad, ADR 0007, Decisión 10). ``sizes`` informa del
    tamaño del texto serializado (previsto si ``dry_run``).
    """

    dry_run: bool
    tags: int
    assignments: int
    orphan_tags: int
    places: int
    address_warnings: int
    destinations: dict[str, Path] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Representación serializable del resumen."""
        return {
            "dry_run": self.dry_run,
            "tags": self.tags,
            "assignments": self.assignments,
            "orphan_tags": self.orphan_tags,
            "places": self.places,
            "address_warnings": self.address_warnings,
            "destinations": {
                kind: str(path) for kind, path in self.destinations.items()
            },
            "sizes": dict(self.sizes),
        }


def read_tags_file(connection: sqlite3.Connection, generated_at: str) -> TagsFile:
    """Construye el ``TagsFile`` del catálogo: todas las etiquetas y asignaciones.

    Orden determinista: etiquetas por ``name_normalized`` (clave ``UNIQUE``);
    asignaciones por ``(foto, etiqueta, región)``, con la región nula
    primera (``COALESCE`` a ``-1``, el mismo truco del índice único).
    """
    tag_rows = connection.execute(
        "SELECT name, name_normalized, kind, created_at, updated_at "
        "FROM tags ORDER BY name_normalized"
    ).fetchall()
    tags = tuple(
        TagEntry(
            name=str(row["name"]),
            normalized=str(row["name_normalized"]),
            kind=str(row["kind"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        for row in tag_rows
    )

    assignment_rows = connection.execute(
        "SELECT pt.photo_hash, t.name_normalized, pt.source, pt.status, "
        "pt.confidence, pt.region_x, pt.region_y, pt.region_w, pt.region_h, "
        "pt.created_at, pt.updated_at "
        "FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id "
        "ORDER BY pt.photo_hash, t.name_normalized, "
        "COALESCE(pt.region_x, -1.0), COALESCE(pt.region_y, -1.0), "
        "COALESCE(pt.region_w, -1.0), COALESCE(pt.region_h, -1.0)"
    ).fetchall()
    assignments = tuple(
        AssignmentEntry(
            photo=str(row["photo_hash"]),
            tag=str(row["name_normalized"]),
            source=str(row["source"]),
            status=str(row["status"]),
            confidence=(
                float(row["confidence"]) if row["confidence"] is not None else None
            ),
            region=(
                (
                    float(row["region_x"]),
                    float(row["region_y"]),
                    float(row["region_w"]),
                    float(row["region_h"]),
                )
                if row["region_x"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        for row in assignment_rows
    )

    return TagsFile(
        generated_at=generated_at, tags=tags, assignments=assignments
    )


def read_places_file(
    connection: sqlite3.Connection, generated_at: str
) -> tuple[PlacesFile, int]:
    """Construye el ``PlacesFile`` del catálogo y devuelve (fichero, avisos).

    Solo filas con ``place_status='ok'``, ordenadas por hash. El
    ``place_address_json`` se exporta parseado; si no parsea, ``address``
    viaja ``None`` y se cuenta un aviso: nunca se aborta (ADR 0007, § 3).
    """
    rows = connection.execute(
        "SELECT hash, place_country, place_country_code, place_state, place_city, "
        "place_municipality, place_display_name, place_address_json, "
        "place_updated_at FROM photos "
        "WHERE place_status = 'ok' ORDER BY hash"
    ).fetchall()
    places: list[PlaceEntry] = []
    warnings = 0
    for row in rows:
        address: dict[str, object] | None = None
        raw_address = row["place_address_json"]
        if raw_address is not None:
            try:
                parsed = json.loads(str(raw_address))
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                address = parsed
            else:
                warnings += 1
        places.append(
            PlaceEntry(
                photo=str(row["hash"]),
                country=row["place_country"],
                country_code=row["place_country_code"],
                state=row["place_state"],
                city=row["place_city"],
                municipality=row["place_municipality"],
                display_name=row["place_display_name"],
                address=address,
                resolved_at=row["place_updated_at"],
            )
        )
    return (
        PlacesFile(generated_at=generated_at, places=tuple(places)),
        warnings,
    )


def _write_atomically(path: Path, content: bytes) -> None:
    """Escribe ``content`` en ``path`` con un temporal del mismo directorio.

    El destino nunca contiene contenido parcial: o sigue intacto, o recibe
    el fichero completo de una pieza con ``os.replace``.
    """
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("No se pudo eliminar el temporal de exportación", exc_info=True)
        raise


def export_library(
    connection: sqlite3.Connection,
    out_dir: Path,
    *,
    export_tags: bool,
    export_places: bool,
    generated_at: str,
    dry_run: bool,
) -> ExportSummary:
    """Exporta los ficheros seleccionados y devuelve el resumen.

    Valida ambos ficheros contra el formato **antes** de escribir nada: si
    el catálogo contiene datos fuera de rango, aborta sin dejar ficheros a
    medias. Con ``dry_run`` calcula y serializa todo pero no toca disco
    (informa del tamaño y conteos previstos).
    """
    tags_data = read_tags_file(connection, generated_at) if export_tags else None
    places_data: PlacesFile | None = None
    address_warnings = 0
    if export_places:
        places_data, address_warnings = read_places_file(connection, generated_at)

    if tags_data is not None:
        model.require_valid_tags_file(tags_data)
    if places_data is not None:
        model.require_valid_places_file(places_data)

    payloads: dict[str, dict[str, object]] = {}
    if tags_data is not None:
        payloads["tags"] = model.tags_file_to_dict(tags_data)
    if places_data is not None:
        payloads["places"] = model.places_file_to_dict(places_data)

    destinations: dict[str, Path] = {}
    sizes: dict[str, int] = {}
    names = {"tags": TAGS_FILE_NAME, "places": PLACES_FILE_NAME}
    for kind, payload in payloads.items():
        destinations[kind] = out_dir / names[kind]
        text = model.serialize_json(payload)
        sizes[kind] = len(text.encode("utf-8"))

    if not dry_run:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise TransferError(
                f"No se pudo crear el directorio de destino: {reason}"
            ) from exc
        for kind, payload in payloads.items():
            path = destinations[kind]
            try:
                _write_atomically(path, model.serialize_json(payload).encode("utf-8"))
            except OSError as exc:
                reason = exc.strerror or type(exc).__name__
                raise TransferError(
                    f"No se pudo escribir {path.name}: {reason}"
                ) from exc
            logger.info("Exportado %s", path)

    orphans = 0
    if tags_data is not None:
        assigned = {assignment.tag for assignment in tags_data.assignments}
        orphans = sum(1 for tag in tags_data.tags if tag.normalized not in assigned)

    return ExportSummary(
        dry_run=dry_run,
        tags=len(tags_data.tags) if tags_data is not None else 0,
        assignments=len(tags_data.assignments) if tags_data is not None else 0,
        orphan_tags=orphans,
        places=len(places_data.places) if places_data is not None else 0,
        address_warnings=address_warnings,
        destinations={kind: path for kind, path in destinations.items() if kind in payloads},
        sizes=sizes,
    )