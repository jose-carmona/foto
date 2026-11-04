"""Composición de las cuatro regiones de la ventana (refabricación visual).

Fija los invariantes de ``prompts/refabricacion-visual-visor.md``: la rejilla y
la zona de estado están siempre visibles; el panel de metadatos ocupa el lateral
derecho por encima de la zona de estado, sin alterar esta; mostrar el panel de
foto deja la zona de rejilla en un título de grupo y una única fila de celdas;
la rejilla no cambia de comportamiento en ningún estado, solo de dimensión; y
la miniatura seleccionada queda siempre a la vista tras cualquier cambio de
geometría.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from foto.ui.window import (
    BAR_H,
    GRID_MARGIN,
    GROUP_HEADER_H,
    ITEM_H,
    PANEL_W,
    Photo,
    PhotoWindow,
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


# La ordenación es de más reciente a más antiguo: la foto más antigua
# («foto-00») queda al final de la rejilla, fuera de la vista con la ventana
# de 900 × 500.
PHOTOS = [_photo(f"foto-{index:02d}.jpg", f"2024-05-{index + 1:02d} 08:00") for index in range(30)]

FIRST_PHOTO = "foto-00.jpg"  # la más antigua: la última celda de la rejilla
LAST_PHOTO = "foto-29.jpg"  # la más reciente: la primera celda

WINDOW_W, WINDOW_H = 900, 500


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _make_window(qapp) -> PhotoWindow:
    """Ventana como la app real: rejilla propia del visor y paneles ocultos."""
    window = PhotoWindow(
        PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    window.resize(WINDOW_W, WINDOW_H)
    window.show()
    qapp.processEvents()
    return window


def _select(window: PhotoWindow, name: str) -> None:
    item = window._grid_items[name]
    window.grid.setCurrentItem(item)


def _current_index(window: PhotoWindow) -> int:
    current = window.grid.currentItem().data(Qt.AccessibleTextRole)
    return int(current.removeprefix("foto-").removesuffix(".jpg"))


def _select_far(window: PhotoWindow) -> None:
    """Selecciona la foto más antigua, fuera de la vista tras el scroll arriba."""
    _select(window, FIRST_PHOTO)


def _current_visible(window: PhotoWindow) -> bool:
    return window.grid.visualItemRect(window.grid.currentItem()).intersects(
        window.grid.viewport().rect()
    )


def _scroll_top(window: PhotoWindow) -> None:
    window.grid.verticalScrollBar().setValue(0)
    qapp = QApplication.instance()
    if qapp is not None:
        qapp.processEvents()


def test_status_bar_is_fixed_at_the_bottom_and_always_visible(qapp):
    window = _make_window(qapp)
    assert window.status.isVisibleTo(window)
    assert window.status.height() == BAR_H
    assert window.status.y() == window.height() - BAR_H
    assert window.status.width() == window.width()


def test_metadata_panel_fills_the_right_side_above_the_status_bar(qapp):
    window = _make_window(qapp)
    window.toggle_panel()
    qapp.processEvents()

    assert window.panel.isVisibleTo(window.content)
    # Ancho fijo y de borde superior hasta donde empieza la zona de estado.
    assert window.panel.width() == PANEL_W
    assert window.panel.x() == window.content.width() - PANEL_W
    assert window.panel.y() == 0
    assert window.panel.height() == window.content.height()
    # La zona de estado no se altera por el panel: tamaño y ancho completos.
    assert window.status.height() == BAR_H
    assert window.status.width() == window.width()
    assert window.status.y() == window.height() - BAR_H
    # La rejilla se acoge al ancho restante, sin cambiar de comportamiento.
    assert window.grid.width() == window.content.width() - PANEL_W - 2 * GRID_MARGIN


def test_photo_panel_occupies_the_available_space_above_one_row(qapp):
    window = _make_window(qapp)
    _select(window, LAST_PHOTO)
    window._open_viewer()
    qapp.processEvents()

    content_h = window.content.height()
    assert window.viewer.isVisibleTo(window.content)
    assert window.viewer.height() == content_h - GROUP_HEADER_H - ITEM_H
    assert window.viewer.width() == window.content.width()
    assert window.row_header.height() == GROUP_HEADER_H
    assert window.grid.geometry().height() == ITEM_H
    assert window.grid.width() == window.content.width()
    # La zona de estado sigue en su sitio.
    assert window.status.y() == window.height() - BAR_H

    window._close_viewer()
    qapp.processEvents()
    assert window.viewer.isHidden()


def test_photo_panel_and_metadata_panel_share_the_window(qapp):
    window = _make_window(qapp)
    _select(window, LAST_PHOTO)
    window._open_viewer()
    window.toggle_panel()
    qapp.processEvents()

    content_w = window.content.width()
    # El panel de metadatos abarca por encima de la zona de estado y a la
    # altura completa del contenido: foto, título de grupo y fila a su
    # izquierda se acogen al ancho restante.
    assert window.panel.width() == PANEL_W
    assert window.panel.height() == window.content.height()
    assert window.viewer.width() == content_w - PANEL_W
    assert window.row_header.width() == content_w - PANEL_W
    assert window.grid.width() == content_w - PANEL_W
    # La zona de estado no se altera por ninguno de los dos paneles.
    assert window.status.height() == BAR_H
    assert window.status.width() == window.width()
    assert window.status.y() == window.height() - BAR_H


def test_minimum_window_size_keeps_the_regions_valid(qapp):
    window = _make_window(qapp)
    window.resize(640, 480)
    qapp.processEvents()

    content_h = window.content.height()

    # Estado de arranque.
    assert window.grid.isVisibleTo(window.content)
    assert window.status.y() == window.height() - BAR_H

    # Estado 2: panel de metadatos. Ventana estrecha: capa temporal.
    window.toggle_panel()
    qapp.processEvents()
    assert window.panel_overlay is True
    assert window.panel.height() == content_h
    assert window.status.y() == window.height() - BAR_H

    # Estado 3: panel de foto a ancho completo.
    window.toggle_panel()
    _select(window, LAST_PHOTO)
    window._open_viewer()
    qapp.processEvents()
    header_h = min(GROUP_HEADER_H, content_h)
    row_h = min(ITEM_H, content_h - header_h)
    assert window.viewer.height() == content_h - header_h - row_h
    assert window.grid.geometry().height() == row_h
    assert window.status.y() == window.height() - BAR_H

    # Estado 4: foto + metadatos. A 640 px el panel es capa temporal
    # (overlay): la rejilla y el panel de foto conservan el ancho completo.
    window.toggle_panel()
    qapp.processEvents()
    assert window.panel_overlay is True
    assert window.viewer.width() == window.content.width()
    assert window.grid.width() == window.content.width()
    assert window.status.y() == window.height() - BAR_H


def test_grid_behavior_does_not_change_when_the_photo_panel_opens(qapp):
    window = _make_window(qapp)
    wrapping_before = window.grid.isWrapping()
    policy_before = window.grid.verticalScrollBarPolicy()

    window._open_viewer()
    qapp.processEvents()

    assert window.grid.isWrapping() == wrapping_before
    assert window.grid.verticalScrollBarPolicy() == policy_before
    assert window.grid.isVisibleTo(window.content)

    window._close_viewer()
    qapp.processEvents()

    assert window.grid.isWrapping() == wrapping_before
    assert window.grid.verticalScrollBarPolicy() == policy_before


def test_selected_photo_stays_visible_after_panel_and_window_changes(qapp):
    window = _make_window(qapp)
    # La foto más antigua, lejos del tope: sin desplazamiento no cabe.
    _select_far(window)
    _scroll_top(window)
    assert not _current_visible(window)

    window.toggle_panel()
    qapp.processEvents()
    assert _current_visible(window)

    window.toggle_panel()
    qapp.processEvents()
    assert _current_visible(window)

    window._open_viewer()
    qapp.processEvents()
    assert _current_visible(window)

    window.resize(1280, 960)
    qapp.processEvents()
    assert _current_visible(window)


def test_selected_photo_is_scrolled_into_view_when_the_viewer_opens(qapp):
    window = _make_window(qapp)
    _select_far(window)
    _scroll_top(window)
    assert not _current_visible(window)

    window._open_viewer()
    qapp.processEvents()

    # La fila única visible es la que contiene al cursor.
    assert _current_visible(window)
