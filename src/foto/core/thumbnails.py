"""Geometría y nombres de fichero de las miniaturas de la UI."""

from __future__ import annotations

import hashlib
import string

CACHE_VERSION = 1


def cover_size(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> tuple[int, int]:
    """Tamaño mínimo que cubre el objetivo conservando la proporción."""
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Las dimensiones de origen deben ser positivas")
    if target_width <= 0 or target_height <= 0:
        raise ValueError("Las dimensiones objetivo deben ser positivas")

    factor = max(target_width / source_width, target_height / source_height)
    return (
        max(target_width, round(source_width * factor)),
        max(target_height, round(source_height * factor)),
    )


def reader_scaled_size(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    *,
    rotate_90: bool,
) -> tuple[int, int]:
    """Tamaño que hay que pedir a ``QImageReader`` antes de orientar.

    Qt aplica la transformación EXIF después de escalar durante la lectura. Si
    la imagen gira 90° o 270°, el tamaño solicitado debe ir en las dimensiones
    originales para que el resultado final cubra el objetivo ya orientado.
    """
    display_width, display_height = (
        (source_height, source_width) if rotate_90 else (source_width, source_height)
    )
    scaled_width, scaled_height = cover_size(
        display_width,
        display_height,
        target_width,
        target_height,
    )
    if rotate_90:
        return scaled_height, scaled_width
    return scaled_width, scaled_height


def _cache_key(photo_hash: str) -> str:
    """Normaliza el hash de contenido para usarlo como nombre de fichero."""
    digest = photo_hash.removeprefix("sha256:")
    if len(digest) == 64 and all(character in string.hexdigits for character in digest):
        return digest.lower()
    return hashlib.sha256(photo_hash.encode("utf-8")).hexdigest()


def cache_filename(
    photo_hash: str,
    target_width: int,
    target_height: int,
    *,
    version: int = CACHE_VERSION,
) -> str:
    """Nombre del fichero de caché para una foto y un tamaño de miniatura.

    El hash del contenido mantiene la caché válida al mover o renombrar la foto;
    el tamaño y la versión permiten invalidarla si cambia el formato derivado.
    """
    if target_width <= 0 or target_height <= 0:
        raise ValueError("Las dimensiones objetivo deben ser positivas")
    if version < 1:
        raise ValueError("La versión de caché debe ser positiva")
    return f"{_cache_key(photo_hash)}-{target_width}x{target_height}-v{version}.jpg"
