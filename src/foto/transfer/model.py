"""Modelo de los ficheros JSON de transferencia y validación pura (ADR 0007).

Los dataclasses representan los dos formatos congelados de la versión 1
(``foto/tags`` y ``foto/places``). Las funciones de aquí son puras: convierten
el modelo a ``dict`` serializable, el ``dict`` a texto JSON determinista y
validan los valores contra las reglas del formato (hash bien formado,
``kind``/``source``/``status`` del dominio, ``region`` en rango y versión de
formato conocida al leer). Ninguna toca disco ni SQL: eso es trabajo de
``exporter`` (y del futuro ``importer``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from foto.core.errors import TransferError

#: Valores de la cabecera ``format`` de cada fichero (v1, congelados).
TAGS_FORMAT = "foto/tags"
PLACES_FORMAT = "foto/places"

#: Versión del formato de los ficheros. Congelada: cambiarla exige migrar
#: el lector (ADR 0007, Decisión 3).
FORMAT_VERSION = 1

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Dominios del catálogo que el fichero debe respetar (ADR 0006 + ADR 0007).
TAG_KINDS = frozenset({"system", "person", "place", "event", "free"})
ASSIGNMENT_SOURCES = frozenset({"manual", "ml", "import"})
ASSIGNMENT_STATUSES = frozenset({"confirmed", "suggested", "rejected"})

Region = tuple[float, float, float, float]


@dataclass(frozen=True)
class TagEntry:
    """Etiqueta del fichero ``tags.json``.

    ``normalized`` es la referencia estable: las asignaciones apuntan a ella,
    nunca al ``id`` AUTOINCREMENT (varía entre bibliotecas).
    """

    name: str
    normalized: str
    kind: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AssignmentEntry:
    """Asignación foto–etiqueta tal como viaja en el fichero.

    ``tag`` es el ``name_normalized`` de la etiqueta; ``region`` es ``None``
    (foto completa) o ``(x, y, w, h)`` en ``[0, 1]`` con ``w, h > 0``.
    ``source`` y ``status`` se conservan tal cual para trazabilidad; al
    importar, la fila nace ``source='import'`` (ADR 0007, Decisión 8).
    """

    photo: str
    tag: str
    source: str
    status: str
    confidence: float | None
    region: Region | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TagsFile:
    """Contenido íntegro de un ``tags.json`` (sin la cabecera serializada)."""

    generated_at: str
    tags: tuple[TagEntry, ...]
    assignments: tuple[AssignmentEntry, ...]


@dataclass(frozen=True)
class PlaceEntry:
    """Lugar resuelto de una foto para ``places.json``.

    Solo viajan filas con ``place_status='ok'``; ``address`` es el objeto
    ``address`` íntegro de Nominatim parseado, o ``None`` si el JSON del
    catálogo no parseó (se avisa, nunca se aborta).
    """

    photo: str
    country: str | None
    country_code: str | None
    state: str | None
    city: str | None
    municipality: str | None
    display_name: str | None
    address: dict[str, object] | None
    resolved_at: str | None


@dataclass(frozen=True)
class PlacesFile:
    """Contenido íntegro de un ``places.json``."""

    generated_at: str
    places: tuple[PlaceEntry, ...]


# ----------------------------------------------------------------------
# Serialización pura: dataclass -> dict -> texto JSON determinista
# ----------------------------------------------------------------------
def region_to_value(region: Region | None) -> list[float] | None:
    """Región del modelo a JSON: ``null`` o ``[x, y, w, h]``."""
    return None if region is None else [region[0], region[1], region[2], region[3]]


def tags_file_to_dict(data: TagsFile) -> dict[str, object]:
    """``TagsFile`` al dict exacto del formato ``foto/tags`` v1."""
    return {
        "format": TAGS_FORMAT,
        "version": FORMAT_VERSION,
        "generated_at": data.generated_at,
        "counts": {"tags": len(data.tags), "assignments": len(data.assignments)},
        "tags": [
            {
                "name": tag.name,
                "normalized": tag.normalized,
                "kind": tag.kind,
                "created_at": tag.created_at,
                "updated_at": tag.updated_at,
            }
            for tag in data.tags
        ],
        "assignments": [
            {
                "photo": assignment.photo,
                "tag": assignment.tag,
                "source": assignment.source,
                "status": assignment.status,
                "confidence": assignment.confidence,
                "region": region_to_value(assignment.region),
                "created_at": assignment.created_at,
                "updated_at": assignment.updated_at,
            }
            for assignment in data.assignments
        ],
    }


def places_file_to_dict(data: PlacesFile) -> dict[str, object]:
    """``PlacesFile`` al dict exacto del formato ``foto/places`` v1."""
    return {
        "format": PLACES_FORMAT,
        "version": FORMAT_VERSION,
        "generated_at": data.generated_at,
        "counts": {"photos": len(data.places)},
        "places": [
            {
                "photo": place.photo,
                "country": place.country,
                "country_code": place.country_code,
                "state": place.state,
                "city": place.city,
                "municipality": place.municipality,
                "display_name": place.display_name,
                "address": place.address,
                "resolved_at": place.resolved_at,
            }
            for place in data.places
        ],
    }


def serialize_json(payload: dict[str, object]) -> str:
    """Serializa un fichero con las convenciones del formato (ADR 0007).

    UTF-8, ``indent=2``, ``ensure_ascii=False`` y terminado en ``\\n``. El
    orden determinista lo aportan los dicts en inserción y las tuplas ya
    ordenadas del modelo, no ``sort_keys``.
    """
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ----------------------------------------------------------------------
# Validación pura (compartida con el futuro import)
# ----------------------------------------------------------------------
def validate_photo_hash(value: object) -> str | None:
    """Devuelve un mensaje si el valor no es un hash ``sha256:<64 hex>``."""
    if not isinstance(value, str) or _HASH_PATTERN.match(value) is None:
        return f"hash de foto mal formado: {value!r} (se espera 'sha256:<64 hex>')"
    return None


def validate_tag_kind(value: object) -> str | None:
    if not isinstance(value, str) or value not in TAG_KINDS:
        return f"kind de etiqueta fuera de rango: {value!r}"
    return None


def validate_assignment_source(value: object) -> str | None:
    if not isinstance(value, str) or value not in ASSIGNMENT_SOURCES:
        return f"source de asignación fuera de rango: {value!r}"
    return None


def validate_assignment_status(value: object) -> str | None:
    if not isinstance(value, str) or value not in ASSIGNMENT_STATUSES:
        return f"status de asignación fuera de rango: {value!r}"
    return None


def validate_confidence(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"confidence no es numérico: {value!r}"
    if not 0.0 <= float(value) <= 1.0:
        return f"confidence fuera de rango [0, 1]: {value!r}"
    return None


def validate_region(value: object) -> str | None:
    """Valida ``region``: ``None`` o ``(x, y, w, h)`` en ``[0, 1]``, ``w, h > 0``."""
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 4:
        return f"región mal formada: {value!r} (se espera null o [x, y, w, h])"
    x, y, w, h = value
    for name, coordinate in (("x", x), ("y", y)):
        if not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool):
            return f"coordenada {name} de la región no es numérica: {coordinate!r}"
        if not 0.0 <= float(coordinate) <= 1.0:
            return f"coordenada {name} de la región fuera de rango [0, 1]: {coordinate!r}"
    for name, size in (("w", w), ("h", h)):
        if not isinstance(size, (int, float)) or isinstance(size, bool):
            return f"tamaño {name} de la región no es numérico: {size!r}"
        if not 0.0 < float(size) <= 1.0:
            return f"tamaño {name} de la región fuera de rango (0, 1]: {size!r}"
    return None


def validate_tags_file(data: TagsFile) -> list[str]:
    """Valida un ``TagsFile`` completo contra el formato; devuelve problemas.

    Devuelve la lista completa de problemas (vacía si es válido) para que el
    llamador decida: la exportación aborta con cualquiera y el import
    validará el fichero entero antes de escribir nada (ADR 0007, Decisión 5).
    """
    problems: list[str] = []
    for tag in data.tags:
        message = validate_tag_kind(tag.kind)
        if message is not None:
            problems.append(f"etiqueta {tag.normalized!r}: {message}")
    for assignment in data.assignments:
        prefix = f"asignación de {assignment.tag!r} a {assignment.photo!r}"
        for message in (
            validate_photo_hash(assignment.photo),
            validate_assignment_source(assignment.source),
            validate_assignment_status(assignment.status),
            validate_confidence(assignment.confidence),
            validate_region(assignment.region),
        ):
            if message is not None:
                problems.append(f"{prefix}: {message}")
    return problems


def validate_places_file(data: PlacesFile) -> list[str]:
    """Valida un ``PlacesFile`` completo contra el formato; devuelve problemas."""
    problems: list[str] = []
    for place in data.places:
        message = validate_photo_hash(place.photo)
        if message is not None:
            problems.append(message)
    return problems


def require_valid_tags_file(data: TagsFile) -> None:
    """Lanza ``TransferError`` si el ``TagsFile`` viola el formato."""
    problems = validate_tags_file(data)
    if problems:
        raise TransferError(
            "El fichero de etiquetas viola el formato v1: " + "; ".join(problems)
        )


def require_valid_places_file(data: PlacesFile) -> None:
    """Lanza ``TransferError`` si el ``PlacesFile`` viola el formato."""
    problems = validate_places_file(data)
    if problems:
        raise TransferError(
            "El fichero de lugares viola el formato v1: " + "; ".join(problems)
        )
