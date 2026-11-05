"""Salto por grupos en la rejilla: ``0`` al inicio y ``$`` al final del grupo.

Las cabeceras de grupo parten la rejilla en bloques (``photo_group``); el
movimiento por grupo debe respetar las fotos visibles bajo el filtro activo y
regir también en la fila del visor, donde las cabeceras están ocultas pero
cada celda conserva su grupo.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from foto.ui.models import Photo
from foto.ui.window import PhotoWindow


def _photo(name: str, taken: str, camera: str = "Nikon Z 6") -> Photo:
    return Photo(
        name=name,
        taken=taken,
        camera=camera,
        lens="Nikkor 35mm",
        focal="35 mm",
        aperture="f/1,8",
        shutter="1/250 s",
        iso="200",
        folder="2024",
        fmt="JPEG",
        dimensions="6000 × 4000",
        size="2 MB",
        location="",
        photo_hash=f"sha256:{name}",
    )


# Tres grupos por mes: mayo (3), junio (2), julio (1). Ordenados por fecha.
PHOTOS = [
    _photo("amanece.jpg", "2024-05-01 08:00"),
    _photo("bruma.jpg", "2024-05-02 08:00"),
    _photo("claros.jpg", "2024-05-03 08:00"),
    _photo("duna.jpg", "2024-06-01 08:00"),
    _photo("estuario.jpg", "2024-06-02 08:00"),
    _photo("farol.jpg", "2024-07-01 08:00"),
]


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp):
    return PhotoWindow(
        PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
    )


def _select(window, name: str) -> None:
    """Deja como foto actual la elegida por su nombre de fichero."""
    item = window._grid_items[name]
    window.grid.setCurrentItem(item)


def _current(window) -> str:
    return window.grid.currentItem().data(Qt.AccessibleTextRole)


# ----------------------------------------------------------------------
# Movimiento dentro del grupo en la rejilla
# ----------------------------------------------------------------------
def test_0_jumps_to_the_first_photo_of_the_group(window):
    _select(window, "bruma.jpg")
    window._on_action("0")

    # El orden es el de la rejilla: en el mes de mayo la miniatura primera es
    # ``claros`` (la de fecha más reciente).
    assert _current(window) == "claros.jpg"


def test_dollar_jumps_to_the_last_photo_of_the_group(window):
    _select(window, "bruma.jpg")
    window._on_action("$")

    assert _current(window) == "amanece.jpg"


def test_0_and_dollar_never_cross_the_group_boundary(window):
    _select(window, "duna.jpg")
    window._on_action("$")
    assert _current(window) == "duna.jpg"

    window._on_action("0")
    assert _current(window) == "estuario.jpg"


def test_group_edges_report_no_movement(window):
    _select(window, "claros.jpg")

    assert window.grid.move_selection("0") is False

    _select(window, "amanece.jpg")
    assert window.grid.move_selection("$") is False


def test_group_movement_respects_the_active_filter(window):
    _select(window, "estuario.jpg")
    window._apply_filter("estuario")

    window._on_action("$")

    # El filtro deja al grupo de junio con una única foto visible.
    assert _current(window) == "estuario.jpg"


# ----------------------------------------------------------------------
# La fila del visor comparte la rejilla: mismos límites de grupo
# ----------------------------------------------------------------------
def test_group_edges_in_the_viewer_row(qapp):
    viewer_window = PhotoWindow(
        PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    _select(viewer_window, "duna.jpg")
    viewer_window._open_viewer()
    assert viewer_window._viewer_visible

    viewer_window._on_action("0")
    assert viewer_window._current_photo().key == "estuario.jpg"

    viewer_window._on_action("$")
    assert viewer_window._current_photo().key == "duna.jpg"


# ----------------------------------------------------------------------
# La fila comprimida muestra la cabecera del grupo de la foto mostrada
# ----------------------------------------------------------------------
def test_viewer_row_shows_the_group_header(qapp):
    viewer_window = PhotoWindow(
        PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    _select(viewer_window, "duna.jpg")
    viewer_window._open_viewer()

    # Encima de la fila va la cabecera del grupo de la foto mostrada.
    assert viewer_window.row_header.text() == "junio de 2024"
    assert viewer_window.row_header.isVisibleTo(viewer_window.content)

    # Al navegar más allá del límite del grupo cambia al grupo nuevo.
    viewer_window._on_action("l")  # duna (junio) -> claros (mayo)
    assert viewer_window._current_photo().key == "claros.jpg"
    assert viewer_window.row_header.text() == "mayo de 2024"

    # Dentro del mismo grupo el rótulo no cambia.
    viewer_window._on_action("l")  # claros -> bruma
    assert viewer_window.row_header.text() == "mayo de 2024"

    viewer_window._on_action("h")  # bruma -> claros
    viewer_window._on_action("h")  # de mayo a junio de nuevo
    assert viewer_window._current_photo().key == "duna.jpg"
    assert viewer_window.row_header.text() == "junio de 2024"

    # Al cerrar el visor la cabecera desaparece con la fila.
    viewer_window._close_viewer()
    assert viewer_window._viewer_visible is False
    assert viewer_window.row_header.isHidden()


def test_viewer_row_header_hides_without_current_photo(qapp):
    viewer_window = PhotoWindow(
        [],
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    viewer_window._open_viewer()
    assert viewer_window._viewer_visible is False

    # Sin fotos no hay visor ni cabecera que mostrar.
    assert viewer_window.row_header.isHidden()
