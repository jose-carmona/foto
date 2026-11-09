"""Lógica de biblioteca."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from foto.storage.catalog import Catalog

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".raf", ".cr2", ".nef", ".arw", ".dng", ".png", ".tif", ".tiff"}


@dataclass(frozen=True)
class CatalogPhoto:
    """Foto cargada desde el catálogo para la UI."""

    name: str
    taken: str
    camera: str
    lens: str
    focal: str
    aperture: str
    shutter: str
    iso: str
    folder: str
    fmt: str
    dimensions: str
    size: str
    location: str
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    place_country: str | None = None
    place_country_code: str | None = None
    place_state: str | None = None
    place_city: str | None = None
    place_municipality: str | None = None
    place_display_name: str | None = None
    place_address_json: str | None = None
    favorite: bool = False
    path: Path | None = None
    canonical_rel_path: str | None = None
    other_locations: tuple[str, ...] = ()
    photo_hash: str | None = None

    @property
    def key(self) -> str:
        return str(self.path) if self.path is not None else self.name



def list_images(library_path: Path) -> list[Path]:
    """Lista imágenes en la biblioteca (recursivo).

    Obsoleto para la UI: el visor consume el catálogo. Se mantiene por compatibilidad.
    """
    images: list[Path] = []
    if not library_path.exists():
        return images
    for root, dirs, files in os.walk(library_path):
        # Ignorar .foto
        if ".foto" in Path(root).parts:
            continue
        for name in files:
            ext = Path(name).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                images.append(Path(root) / name)
    # Ordenar por nombre para tener un orden estable
    images.sort(key=lambda p: str(p).lower())
    return images


def _format_exposure_time(value: float | None) -> str:
    if value is None or value <= 0:
        return ""
    if value >= 1:
        text = f"{value:g}"
        return f"{text.replace('.', ',')} s"
    denominator = round(1 / value)
    if denominator <= 0:
        return ""
    return f"1/{denominator} s"


def _format_aperture(value: float | None) -> str:
    if value is None:
        return ""
    text = f"f/{value:g}"
    return text.replace(".", ",")


def _format_focal_length(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:g} mm"
    return text.replace(".", ",")


def _format_iso(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _format_taken(captured_at: str | None) -> str:
    if not captured_at:
        return ""
    try:
        dt = datetime.fromisoformat(captured_at)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return captured_at


def _format_location(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "O"
    return f"{abs(lat):.4f} {lat_dir} · {abs(lon):.4f} {lon_dir}"


def _format_camera(make: str | None, model: str | None) -> str:
    parts = [part for part in (make, model) if part]
    return " ".join(parts)


def _format_size_from_bytes(size: int | None) -> str:
    if not size or size <= 0:
        return ""
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= factor:
            value = f"{size / factor:.1f}".rstrip("0").rstrip(".")
            return f"{value.replace('.', ',')} {unit}"
    return f"{size} B"


def _format_dimensions(width: int | None, height: int | None) -> str:
    if width and height:
        return f"{width} × {height} px"
    return ""


def load_from_catalog(catalog: Catalog, library_path: Path) -> list[CatalogPhoto]:
    """Carga fotos desde el catálogo SQLite (solo ubicaciones presentes).

    Agrupa por hash: una entrada por foto. Ubicación canónica = menor rel_path
    en orden lexicográfico POSIX. El resto se guardan como ubicaciones alternativas.
    """
    rows = catalog.list_present_locations()
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["photo_hash"], []).append(row)

    photos: list[CatalogPhoto] = []
    for photo_hash, items in grouped.items():
        items_sorted = sorted(items, key=lambda r: str(r["rel_path"]))
        canonical = items_sorted[0]
        others = items_sorted[1:]

        rel_path = Path(canonical["rel_path"])
        full_path = (library_path / rel_path).resolve()
        folder = str(rel_path.parent)
        if folder == ".":
            folder = "."
        else:
            folder = folder.replace(os.sep, "/")

        extension = canonical["extension"] or rel_path.suffix
        file_type = canonical["file_type"] or ""
        fmt = file_type.upper() if file_type else extension.lstrip(".").upper() or "—"

        size = ""
        try:
            stat = full_path.stat()
            size = _format_size_from_bytes(stat.st_size)
        except OSError:
            size = _format_size_from_bytes(canonical["size"])

        camera = _format_camera(canonical["camera_make"], canonical["camera_model"])
        photo = CatalogPhoto(
            name=rel_path.name,
            taken=_format_taken(canonical["captured_at"]),
            camera=camera,
            lens=canonical["lens_model"] or "",
            focal=_format_focal_length(canonical["focal_length"]),
            aperture=_format_aperture(canonical["f_number"]),
            shutter=_format_exposure_time(canonical["exposure_time"]),
            iso=_format_iso(canonical["iso"]),
            folder=folder,
            fmt=fmt,
            dimensions=_format_dimensions(canonical["width"], canonical["height"]),
            size=size,
            location=_format_location(canonical["gps_latitude"], canonical["gps_longitude"]),
            gps_latitude=canonical["gps_latitude"],
            gps_longitude=canonical["gps_longitude"],
            place_country=canonical["place_country"],
            place_country_code=canonical["place_country_code"],
            place_state=canonical["place_state"],
            place_city=canonical["place_city"],
            place_municipality=canonical["place_municipality"],
            place_display_name=canonical["place_display_name"],
            place_address_json=canonical["place_address_json"],
            path=full_path,
            canonical_rel_path=str(canonical["rel_path"]),
            other_locations=tuple(str(r["rel_path"]) for r in others),
            photo_hash=photo_hash,
        )
        photos.append(photo)

    photos.sort(key=lambda p: p.canonical_rel_path or p.name.lower())
    return photos
