"""Geometría de las miniaturas de la UI."""

from __future__ import annotations


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
