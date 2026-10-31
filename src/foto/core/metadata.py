"""Modelo normalizado de metadatos y contrato de extracción."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PhotoMetadata:
    """Metadatos intrínsecos de una foto, ya normalizados.

    Los campos desconocidos son ``None``: nunca se rellenan con cadenas vacías.
    """

    file_type: str | None = None
    width: int | None = None
    height: int | None = None
    captured_at: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens_model: str | None = None
    iso: int | None = None
    f_number: float | None = None
    exposure_time: float | None = None
    focal_length: float | None = None
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    orientation: int | None = None


@dataclass(frozen=True)
class ExtractionOutcome:
    """Resultado de extraer metadatos de un fichero concreto."""

    metadata: PhotoMetadata | None = None
    error: str | None = None


class MetadataExtractor(Protocol):
    """Contrato de un extractor de metadatos por lotes."""

    def extract_batch(self, paths: Sequence[Path]) -> dict[Path, ExtractionOutcome]:
        """Devuelve un resultado por ruta, sin abortar por errores individuales."""


_EXIF_DATE_RE = re.compile(
    r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*(.*)$"
)


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower() in {"unknown", "none", "null", "n/a"}:
        return None
    return text


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if "/" in text:
                numerator, denominator = text.split("/", 1)
                return float(numerator) / float(denominator)
            return float(text)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def normalize_datetime(value: Any) -> str | None:
    """Normaliza una fecha de exiftool a ISO 8601; ``None`` si no es válida."""
    text = _clean_str(value)
    if text is None:
        return None
    if text.startswith("0000"):
        return None

    match = _EXIF_DATE_RE.match(text)
    if match is not None:
        year, month, day, clock, timezone = match.groups()
        timezone = timezone.strip()
        if timezone.upper() == "Z":
            timezone = "+00:00"
        text = f"{year}-{month}-{day}T{clock}{timezone}"

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.isoformat()


def _pick(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _gps(
    record: Mapping[str, Any],
    coordinate_key: str,
    reference_key: str,
    negative_references: set[str],
) -> float | None:
    coordinate = _to_float(record.get(coordinate_key))
    if coordinate is None:
        return None
    reference = _clean_str(record.get(reference_key))
    if reference is None:
        return coordinate
    if reference.strip().lower() in negative_references:
        return -abs(coordinate)
    return abs(coordinate)


def _dimension(record: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _to_int(record.get(key))
        if value is not None and value > 0:
            return value
    return None


def map_exiftool_record(record: Mapping[str, Any]) -> PhotoMetadata:
    """Mapea un registro JSON de exiftool al modelo normalizado."""
    width = _dimension(record, "ImageWidth", "ExifImageWidth")
    height = _dimension(record, "ImageHeight", "ExifImageHeight")

    # Si solo se pudo leer ImageSize, se usan sus dos dimensiones.
    if width is None or height is None:
        size = _clean_str(record.get("ImageSize"))
        if size is not None:
            match = re.match(r"^(\d+)\s*[xX]\s*(\d+)$", size)
            if match is not None:
                width = width or int(match.group(1))
                height = height or int(match.group(2))

    return PhotoMetadata(
        file_type=_clean_str(_pick(record, "FileType", "FileTypeExtension")),
        width=width,
        height=height,
        captured_at=normalize_datetime(
            _pick(record, "DateTimeOriginal", "CreateDate", "MediaCreateDate")
        ),
        camera_make=_clean_str(_pick(record, "Make", "CameraMake")),
        camera_model=_clean_str(_pick(record, "Model", "CameraModel")),
        lens_model=_clean_str(_pick(record, "LensModel")),
        iso=_to_int(_pick(record, "ISO", "ISOSpeedRatings")),
        f_number=_to_float(_pick(record, "FNumber")),
        exposure_time=_to_float(_pick(record, "ExposureTime")),
        focal_length=_to_float(_pick(record, "FocalLength")),
        gps_latitude=_gps(record, "GPSLatitude", "GPSLatitudeRef", {"s", "south"}),
        gps_longitude=_gps(record, "GPSLongitude", "GPSLongitudeRef", {"w", "west"}),
        orientation=_to_int(_pick(record, "Orientation")),
    )
