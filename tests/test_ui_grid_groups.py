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
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from foto.ui.prototype import (
    Photo,
    PrototypeWindow,
    is_group_end,
    is_group_start,
)


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
    return PrototypeWindow(
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
# Detección de teclas sin pantalla: 0 no cae en los dígitos reservados
# ----------------------------------------------------------------------
def test_key_0_is_the_group_start_and_not_a_reserved_digit():
    assert is_group_start(Qt.Key_0, False)
    assert not is_group_start(Qt.Key_5, False)
    assert not is_group_start(Qt.Key_0, True)


def test_dollar_is_detected_on_every_layout_variant():
    as_shift_4 = QKeyEvent(
        QKeyEvent.KeyPress,
        Qt.Key_4,
        Qt.KeyboardModifier.ShiftModifier,
    )
    as_shift_5 = QKeyEvent(
        QKeyEvent.KeyPress,
        Qt.Key_5,
        Qt.KeyboardModifier.ShiftModifier,
    )

    # En algunas distribuciones el ``$`` es su propia tecla sin mayúsculas; en
    # otras vive en Shift+4 (español) o Shift+5 (inglés).
    assert is_group_end(Qt.Key_Dollar, False)
    assert is_group_end(as_shift_4.key(), bool(as_shift_4.modifiers() & Qt.ShiftModifier))
    assert is_group_end(as_shift_5.key(), bool(as_shift_5.modifiers() & Qt.ShiftModifier))
    assert not is_group_end(Qt.Key_4, False)


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
    viewer_window = PrototypeWindow(
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
