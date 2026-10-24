"""Lógica de biblioteca."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".raf", ".cr2", ".nef", ".arw", ".dng", ".png", ".tif", ".tiff"}


def list_images(library_path: Path) -> List[Path]:
    """Lista imágenes en la biblioteca (recursivo)."""
    images: List[Path] = []
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
