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

from foto.ui.grid import PhotoGrid
from foto.ui.window import PhotoWindow
from tests.ui_factories import make_photo

# Tres grupos por mes: mayo (3), junio (2), julio (1). Ordenados por fecha.
PHOTOS = [
    make_photo("amanece.jpg", "2024-05-01 08:00"),
    make_photo("bruma.jpg", "2024-05-02 08:00"),
    make_photo("claros.jpg", "2024-05-03 08:00"),
    make_photo("duna.jpg", "2024-06-01 08:00"),
    make_photo("estuario.jpg", "2024-06-02 08:00"),
    make_photo("farol.jpg", "2024-07-01 08:00"),
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
    window._dispatch("0")

    # El orden es el de la rejilla: en el mes de mayo la miniatura primera es
    # ``claros`` (la de fecha más reciente).
    assert _current(window) == "claros.jpg"


def test_dollar_jumps_to_the_last_photo_of_the_group(window):
    _select(window, "bruma.jpg")
    window._dispatch("$")

    assert _current(window) == "amanece.jpg"


def test_0_and_dollar_never_cross_the_group_boundary(window):
    _select(window, "duna.jpg")
    window._dispatch("$")
    assert _current(window) == "duna.jpg"

    window._dispatch("0")
    assert _current(window) == "estuario.jpg"


def test_group_edges_report_no_movement(window):
    _select(window, "claros.jpg")

    assert window.grid.move_selection("0") is False

    _select(window, "amanece.jpg")
    assert window.grid.move_selection("$") is False


def test_group_movement_respects_the_active_filter(window):
    _select(window, "estuario.jpg")
    window._apply_filter("estuario")

    window._dispatch("$")

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

    viewer_window._dispatch("0")
    assert viewer_window._current_photo().key == "estuario.jpg"

    viewer_window._dispatch("$")
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
    viewer_window._dispatch("l")  # duna (junio) -> claros (mayo)
    assert viewer_window._current_photo().key == "claros.jpg"
    assert viewer_window.row_header.text() == "mayo de 2024"

    # Dentro del mismo grupo el rótulo no cambia.
    viewer_window._dispatch("l")  # claros -> bruma
    assert viewer_window.row_header.text() == "mayo de 2024"

    viewer_window._dispatch("h")  # bruma -> claros
    viewer_window._dispatch("h")  # de mayo a junio de nuevo
    assert viewer_window._current_photo().key == "duna.jpg"
    assert viewer_window.row_header.text() == "junio de 2024"

    # Al cerrar el visor la cabecera desaparece con la fila.
    viewer_window._close_viewer()
    assert viewer_window._viewer_visible is False
    assert viewer_window.row_header.isHidden()


# ----------------------------------------------------------------------
# Caché de la geometría visual (R3)
# ----------------------------------------------------------------------
def test_vertical_navigation_reuses_the_layout_cache(qapp, window, monkeypatch):
    """Dos ``j`` sin cambio de layout recalculan la geometría una sola vez."""
    window.show()
    qapp.processEvents()
    window.grid._invalidate_layout_cache()

    llamadas: list = []
    original = PhotoGrid._compute_visual_layout

    def espia(self):
        llamadas.append(1)
        return original(self)

    monkeypatch.setattr(PhotoGrid, "_compute_visual_layout", espia)

    inicial = window.grid.currentItem()
    window._dispatch("j")
    tras_primera = window.grid.currentItem()
    window._dispatch("j")

    assert len(llamadas) == 1
    assert tras_primera is not inicial
    assert window.grid.currentItem() is not tras_primera
    window.close()


def test_layout_cache_is_invalidated_on_resize_filter_and_sort(
    qapp, window, monkeypatch
):
    """Resize, filtro y ``:sort`` invalidan la caché y el destino es correcto."""
    window.show()
    qapp.processEvents()
    window.grid._invalidate_layout_cache()

    llamadas: list = []
    original = PhotoGrid._compute_visual_layout

    def espia(self):
        llamadas.append(1)
        return original(self)

    monkeypatch.setattr(PhotoGrid, "_compute_visual_layout", espia)

    # Un resize cambia la composición: la caché se reconstruye.
    window.resize(window.width() + 160, window.height())
    qapp.processEvents()
    _select(window, "farol.jpg")
    window._dispatch("j")
    assert llamadas == [1]
    assert _current(window) == "estuario.jpg"
    window._dispatch("j")
    assert _current(window) == "claros.jpg"
    llamadas.clear()

    # Un filtro pasa por ``set_visible_rows``: la caché se reconstruye.
    window._apply_filter("2024-06")
    window._dispatch("j")
    assert llamadas == [1]
    assert _current(window) in {"duna.jpg", "estuario.jpg"}
    llamadas.clear()

    # Un ``:sort`` repuebla la rejilla: la caché se reconstruye y ``j``
    # sigue moviéndose solo entre las fotos visibles del nuevo orden.
    window._apply_filter("")
    window._sort_grid("camera")
    qapp.processEvents()
    origen = window._visible_keys_cache[0]
    _select(window, window.photos[origen].name)
    window._dispatch("j")
    assert llamadas == [1]
    assert _current(window) != window.photos[origen].name
    assert window._current_photo().key in window._visible_keys_cache
    window.close()


# ----------------------------------------------------------------------
# ``:sort`` con la misma clave: no-op barato (R10)
# ----------------------------------------------------------------------
def test_same_sort_key_does_not_repopulate(qapp, window, monkeypatch):
    """Repetir ``:sort`` con la clave vigente muestra el mensaje y no repuebla."""
    llamadas: list = []
    original = PhotoWindow._populate_grid

    def espia(self, photos):
        llamadas.append(1)
        return original(self, photos)

    monkeypatch.setattr(PhotoWindow, "_populate_grid", espia)

    window._sort_grid(window._sort_key)

    assert llamadas == []
    assert window._sort_key == "date"
    assert "ordenado por date" in window.status.message_label.text()


def test_different_sort_key_still_repopulates(qapp, window, monkeypatch):
    """Cambiar de clave sí repuebla, como siempre."""
    llamadas: list = []
    original = PhotoWindow._populate_grid

    def espia(self, photos):
        llamadas.append(1)
        return original(self, photos)

    monkeypatch.setattr(PhotoWindow, "_populate_grid", espia)

    window._sort_grid("camera")

    assert llamadas == [1]
    assert window._sort_key == "camera"


def test_vertical_navigation_after_filter_uses_the_new_rows(qapp, window):
    """Con un filtro activo, ``j``/``k`` solo se mueven entre las visibles."""
    window.show()
    qapp.processEvents()
    window._apply_filter("2024-06")
    assert window._visible_count() == 2

    _select(window, "estuario.jpg")
    for _ in range(4):
        window._dispatch("j")
        assert _current(window) in {"duna.jpg", "estuario.jpg"}
    for _ in range(4):
        window._dispatch("k")
        assert _current(window) in {"duna.jpg", "estuario.jpg"}
    window.close()


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
