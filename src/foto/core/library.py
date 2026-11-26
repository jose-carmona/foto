"""Lógica de biblioteca."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from foto.storage.availability import AVAILABLE, UNAVAILABLE, availability_state
from foto.storage.catalog import TIER_RANK, Catalog
from foto.storage.tags import TagAssignment, normalize_tag_name

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".raf", ".cr2", ".nef", ".arw", ".dng", ".png", ".tif", ".tiff"}

T = TypeVar("T")


class LibraryLoadCancelled(Exception):
    """Señal de control de la carga del catálogo (T2.3 del prompt de arranque,
    ADR 0011).

    No es un error del catálogo: ``load_from_catalog`` la lanza cuando el
    parámetro ``should_continue`` deja de ser cierto y corta sin resultados
    parciales. Nadie debe mostrarla al usuario: el llamador (el worker del
    arranque progresivo) la usa para detener el hilo de carga limpiamente
    cuando la ventana se cierra a mitad.
    """


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
    tags: tuple[TagAssignment, ...] = ()
    path: Path | None = None
    canonical_rel_path: str | None = None
    #: Nombre de la ubicación de la aparición que sirve la foto.
    canonical_location: str | None = None
    #: ``False`` cuando la raíz de esa ubicación no está a mano (USB
    #: desmontado): la foto se degrada a *placeholder* (ADR 0010, Decisión 6).
    available: bool = True
    other_locations: tuple[str, ...] = ()
    photo_hash: str | None = None

    @property
    def favorite(self) -> bool:
        """Favorito según la etiqueta de sistema (ADR 0006, Decisión 8).

        Misma regla que ``Photo.favorite`` en la UI; se duplica aquí a
        propósito para que el núcleo no dependa del modelo de presentación.
        """
        return any(
            tag.kind == "system" and tag.status in ("confirmed", "suggested")
            for tag in self.tags
        )

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


def _tier_rank(tier: str | None) -> int:
    """Orden canónico por ``tier`` (ADR 0010, Decisión 4): fast < archive."""
    if tier is None:
        return 1
    return TIER_RANK.get(tier, 1)


def load_from_catalog[T](
    catalog: Catalog,
    factory: Callable[..., T] = CatalogPhoto,  # type: ignore[assignment]
    should_continue: Callable[[], bool] | None = None,
    *,
    system_tag: str | None = None,
) -> list[T]:
    """Carga fotos desde el catálogo SQLite (solo ubicaciones activas).

    El catálogo es la única fuente de verdad (ADR 0010, Decisión 7): no hay
    parámetro de raíz. Agrupa por hash: una entrada por foto. Ubicación
    canónica = ``tier`` primero (``fast`` gana a ``archive``) y menor
    ``rel_path`` dentro del mismo ``tier``; las rutas absolutas salen del
    join ``files.location_id`` → ``locations.path``. Las apariciones de
    ubicaciones ``deleted`` no se muestran (pendientes de ``--prune``).

    Tolerancia (ADR 0010, Decisión 6): la disponibilidad se evalúa **por
    ubicación** al cargar —nunca por fichero— con la función pura de
    ``storage/availability``. Entre apariciones disponibles manda el
    ``tier``; si la ubicación canónica no está a mano y hay una copia en
    una ubicación disponible, la foto se sirve desde ella; si no hay
    ninguna, la foto viaja como *placeholder* (``available=False``) con sus
    metadatos, fecha y etiquetas del catálogo.

    ``factory`` (T1.4 del prompt de arranque, opción b): el constructor de
    los objetos de salida. Por defecto ``CatalogPhoto``; la UI inyecta
    ``Photo.from_catalog`` para que los 25k objetos se materialicen una
    sola vez, sin la copia campo a campo que hacía ``_to_ui_photo``. El
    núcleo no conoce el modelo de UI: solo recibe el callable.

    ``should_continue`` (T2.3, ADR 0011): guardia de cancelación cooperativa,
    comprobada por foto. Cuando devuelve ``False`` se corta con
    ``LibraryLoadCancelled`` y no se devuelve lista parcial. Es un callable
    puro: el núcleo sigue sin conocer Qt ni a la UI.

    ``system_tag`` (A.1/A.2 del informe de arranque, ADR 0006) es el nombre
    de una etiqueta de sistema. Se filtra en SQL vía
    ``catalog.list_present_files(system_tag=...)`` y el diccionario
    ``grouped`` se recorta **antes** del bucle que llama a ``factory``, de
    modo que no se construye ningún objeto para las fotos que no cumplen. El
    predicado es el de ``CatalogPhoto.favorite`` más la comprobación del
    nombre normalizado: ``kind='system'``, estado ``confirmed``/``suggested``
    y nombre igual al solicitado (``fav`` es hoy la única etiqueta de sistema,
    pero comprobar el nombre mantiene la paridad con la cláusula SQL). Con
    ``None`` el comportamiento es el de siempre: sin filtro.
    """
    rows = catalog.list_present_files(system_tag=system_tag)
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["photo_hash"], []).append(row)

    assignments = catalog.tag_assignments()

    if system_tag is not None:
        normalized = normalize_tag_name(system_tag)
        grouped = {
            photo_hash: items
            for photo_hash, items in grouped.items()
            if any(
                tag.kind == "system"
                and tag.status in ("confirmed", "suggested")
                and normalize_tag_name(tag.tag_name) == normalized
                for tag in assignments.get(photo_hash, ())
            )
        }

    # Disponibilidad por ubicación, evaluada una vez por raíz distinta.
    # (Ojo: ``setdefault`` evalúa su argumento siempre; con 25k filas eran
    # 25k ``is_dir()``. La guardia explícita hace lo que el comentario dice.)
    states: dict[str, str] = {}
    for row in rows:
        location_path = str(row["location_path"])
        if location_path not in states:
            states[location_path] = availability_state(Path(location_path))

    # Resolución de rutas (T1.8): ``(raíz / rel_path).resolve()`` ejecuta
    # ``realpath``, que lanza un ``lstat`` por componente —~11 por foto con
    # 25k fotos, más que el ``stat()`` de tamaño que quitó T1.2. El padre se
    # resuelve una vez por directorio distinto y la hoja se une por nombre:
    # el resultado es idéntico para ficheros regulares (el caso real; una
    # foto-hoja que fuera un enlace simbólico quedaría sin resolver, misma
    # imagen a través del enlace) y también cuando la hoja no existe.
    resolved_parents: dict[str, Path] = {}

    photos: list[T] = []
    for photo_hash, items in grouped.items():
        if should_continue is not None and not should_continue():
            raise LibraryLoadCancelled
        items_sorted = sorted(
            items,
            key=lambda r: (
                states[str(r["location_path"])] == UNAVAILABLE,
                _tier_rank(r["location_tier"]),
                str(r["rel_path"]),
            ),
        )
        canonical = items_sorted[0]
        others = items_sorted[1:]

        location_path = Path(canonical["location_path"])
        rel_path = Path(canonical["rel_path"])
        parent_key = str(location_path / rel_path.parent)
        resolved_parent = resolved_parents.get(parent_key)
        if resolved_parent is None:
            resolved_parent = (location_path / rel_path.parent).resolve()
            resolved_parents[parent_key] = resolved_parent
        full_path = resolved_parent / rel_path.name
        folder = str(rel_path.parent)
        if folder == ".":
            folder = "."
        else:
            folder = folder.replace(os.sep, "/")

        extension = canonical["extension"] or rel_path.suffix
        file_type = canonical["file_type"] or ""
        fmt = file_type.upper() if file_type else extension.lstrip(".").upper() or "—"

        # El tamaño canónico ya vive en la BD (T1.2 del prompt de arranque):
        # no hay ``stat()`` por foto al cargar. Su frescura es responsabilidad
        # del indexador (``foto-index``), que la recalcula en cada pasada.
        size = _format_size_from_bytes(canonical["size"])

        camera = _format_camera(canonical["camera_make"], canonical["camera_model"])
        photo = factory(
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
            tags=assignments.get(photo_hash, ()),
            path=full_path,
            canonical_rel_path=str(canonical["rel_path"]),
            canonical_location=str(canonical["location_name"]),
            available=states[str(canonical["location_path"])] == AVAILABLE,
            other_locations=tuple(
                f"{row['location_name']}:{row['rel_path']}" for row in others
            ),
            photo_hash=photo_hash,
        )
        photos.append(photo)

    photos.sort(key=lambda p: p.canonical_rel_path or p.name.lower())
    return photos
