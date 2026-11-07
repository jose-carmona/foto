"""Demo de la interfaz con datos falsos.

No consume biblioteca ni catálogo: genera un puñado de fotos inventadas con
miniaturas pintadas a mano y abre la misma ventana que usa la app real.
Sirve para evaluar composición, navegación y temas sin indexar nada.

Se ejecuta con:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m foto.ui.prototype
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from foto.ui.grid import default_thumbnail
from foto.ui.models import Photo
from foto.ui.presentation import sorted_photos
from foto.ui.theme import apply_theme
from foto.ui.window import (
    PhotoWindow,
)

CAMERAS = (
    ("Fujifilm X100V", "XF23mmF2 R WR", "23 mm"),
    ("Sony ILCE-7RM5", "FE 35mm F1.8", "35 mm"),
    ("Leica Q2", "Summilux 28mm f/1.7", "28 mm"),
    ("Nikon Zf", "NIKKOR Z 40mm f/2", "40 mm"),
)


def make_photos() -> list[Photo]:
    photos: list[Photo] = []
    for i in range(24):
        camera, lens, focal = CAMERAS[i % len(CAMERAS)]
        day = 24 - (i % 20)
        month = 10 if i < 12 else 9
        photos.append(
            Photo(
                name=f"DSCF{1001 + i}.{'RAF' if i % 3 else 'JPG'}",
                taken=f"2026-{month:02d}-{day:02d} {18 - (i % 8):02d}:{(i * 7) % 60:02d}",
                camera=camera,
                lens="" if i % 7 == 3 else lens,
                focal=focal,
                aperture=f"f/{(1.4, 2.0, 2.8, 4.0, 5.6)[i % 5]}",
                shutter=("1/125 s", "1/250 s", "1/500 s", "1/60 s", "2,5 s")[i % 5],
                iso=str((100, 200, 400, 800, 1600)[i % 5]),
                folder=f"2026/{month:02d}",
                fmt="RAW (RAF)" if i % 3 else "JPEG",
                dimensions=f"{6240 - (i % 3) * 240} × {4160 - (i % 3) * 160} px",
                size=f"{18 + (i % 11) * 2},{(i * 3) % 10} MB",
                location="" if i % 7 == 1 else f"41,{3800 + i} N · 2,{1600 + i} E",
                favorite=i % 4 == 0,
            )
        )
    return photos


def demo_thumbnails(photos: list[Photo]) -> dict[str, QIcon]:
    """Miniaturas falsas por foto, en el orden con que la rejilla las pinta."""
    icons: dict[str, QIcon] = {}
    for index, photo in enumerate(sorted_photos(photos, "date")):
        icons[photo.key] = default_thumbnail(index)
    return icons


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    photos = make_photos()
    window = PhotoWindow(
        photos,
        thumbnails=demo_thumbnails(photos),
        title="foto — prototipo",
        grid_row=True,
    )
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
