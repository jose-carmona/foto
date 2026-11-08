"""Contrato de ``g m``: abrir la ubicación de la foto en el navegador.

La apertura es siempre ``QDesktopServices.openUrl`` desde ``window.py``:
Qt delega en el sistema —``open``/NSWorkspace en macOS, portal y
``xdg-open`` en Omarchy/Wayland—, así que no hay código por plataforma y
en los tests basta con sustituir el doble de grabación en el módulo
``foto.ui.window`` para no abrir un navegador real.

``map_url`` vive en ``ui/models.py`` (módulo sin Qt) y sus tests no
necesitan pantalla: aquí se cubren tanto la URL pura como el flujo de
teclado sobre la ventana real (prefijo ``g`` pendiente + ``m``).
"""

from __future__ import annotations

import os
from typing import ClassVar

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from foto.ui import window as window_module
from foto.ui.models import Photo, map_url
from foto.ui.window import PhotoWindow


# ----------------------------------------------------------------------
# map_url: URL pura, sin Qt
# ----------------------------------------------------------------------
def test_map_url_builds_the_google_maps_query():
    assert map_url(40.4168, -3.7038) == (
        "https://www.google.com/maps?q=40.4168,-3.7038"
    )


def test_map_url_keeps_negative_coordinates():
    # Hemisferio sur y oeste: el signo entra en la consulta sin escapar.
    assert map_url(-33.4489, -70.6693) == (
        "https://www.google.com/maps?q=-33.4489,-70.6693"
    )


# ----------------------------------------------------------------------
# Ventana real: prefijo g pendiente + m (con QDesktopServices sustituido)
# ----------------------------------------------------------------------
class _DesktopDouble:
    """Doble de ``QDesktopServices``: graba las URLs openUrl, no abre nada."""

    opened: ClassVar[list[str]] = []
    result: ClassVar[bool] = True

    @staticmethod
    def openUrl(url) -> bool:
        _DesktopDouble.opened.append(url.toString())
        return _DesktopDouble.result


def _photo(
    name: str,
    taken: str,
    *,
    lat: float | None = None,
    lon: float | None = None,
) -> Photo:
    return Photo(
        name=name,
        taken=taken,
        camera="Nikon Z 6",
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
        gps_latitude=lat,
        gps_longitude=lon,
        photo_hash=f"sha256:{name}",
    )


MADRID = _photo("madrid.jpg", "2024-06-01 08:00", lat=40.4168, lon=-3.7038)
SIN_GPS = _photo("sin-gps.jpg", "2024-05-01 08:00")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def browser(monkeypatch):
    """Sustituye QDesktopServices en window por el doble de grabación."""
    _DesktopDouble.opened.clear()
    _DesktopDouble.result = True
    monkeypatch.setattr(window_module, "QDesktopServices", _DesktopDouble)
    return _DesktopDouble


def _make_window(qapp, photos: list[Photo]) -> PhotoWindow:
    window = PhotoWindow(
        photos,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    window.show()
    qapp.processEvents()
    window.activateWindow()
    qapp.processEvents()
    return window


def _key(key: int) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _press(window: PhotoWindow, key: int, qapp) -> None:
    window.keyPressEvent(_key(key))
    qapp.processEvents()


def _select(window: PhotoWindow, photo: Photo, qapp) -> None:
    """Deja el cursor sobre ``photo`` en la lista visible."""
    key = next(
        key for key in window._visible_keys_cache if window.photos[key] is photo
    )
    window._select_key(key)
    qapp.processEvents()


def test_g_m_opens_the_map_of_the_selected_photo(qapp, browser):
    window = _make_window(qapp, [MADRID, SIN_GPS])
    _select(window, MADRID, qapp)

    _press(window, Qt.Key_G, qapp)
    _press(window, Qt.Key_M, qapp)

    # La coordenada sale tal cual de map_url; el estado lo confirma.
    assert browser.opened == ["https://www.google.com/maps?q=40.4168,-3.7038"]
    assert "mapa" in window.status.message_label.text()


def test_m_alone_without_prefix_does_nothing(qapp, browser):
    window = _make_window(qapp, [MADRID])

    _press(window, Qt.Key_M, qapp)

    assert browser.opened == []
    # ``m`` queda reservada: sin prefijo pendiente no avisa ni actúa.
    assert window._g_pending is False


def test_g_m_without_gps_warns_and_does_not_open_the_browser(qapp, browser):
    window = _make_window(qapp, [SIN_GPS, MADRID])
    _select(window, SIN_GPS, qapp)

    _press(window, Qt.Key_G, qapp)
    _press(window, Qt.Key_M, qapp)

    assert browser.opened == []
    assert "GPS" in window.status.message_label.text()


def test_other_key_after_g_cancels_the_prefix(qapp, browser):
    window = _make_window(qapp, [MADRID, SIN_GPS])
    window._select_key(window._visible_keys_cache[1])
    qapp.processEvents()

    _press(window, Qt.Key_G, qapp)
    _press(window, Qt.Key_K, qapp)  # navegación, no segunda tecla
    _press(window, Qt.Key_M, qapp)

    assert browser.opened == []
    # ``k`` movió el cursor como siempre y el ``m`` posterior no actúa.
    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[0]
    ]


def test_g_g_still_goes_to_the_first_photo(qapp, browser):
    """Regresión: ``g g`` no lo rompe el nuevo ``m`` como segunda tecla."""
    window = _make_window(qapp, [MADRID, SIN_GPS])

    _press(window, Qt.Key_G, qapp)
    _press(window, Qt.Key_G, qapp)

    assert browser.opened == []
    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[0]
    ]


def test_g_m_failure_keeps_the_state_consistent(qapp, browser):
    """Si el sistema rechaza abrir la URL, la barra avisa sin romper nada."""
    browser.result = False
    window = _make_window(qapp, [MADRID])

    _press(window, Qt.Key_G, qapp)
    _press(window, Qt.Key_M, qapp)

    assert browser.opened == ["https://www.google.com/maps?q=40.4168,-3.7038"]
    assert "no se pudo abrir" in window.status.message_label.text()
    assert window._g_pending is False


def test_g_m_works_from_the_viewer_canvas(qapp, browser):
    """El mismo ``g m`` funciona desde el lienzo del visor (contexto viewer)."""
    window = _make_window(qapp, [MADRID, SIN_GPS])
    _select(window, MADRID, qapp)

    _press(window, Qt.Key_Return, qapp)  # abre el visor con la foto actual
    window.viewer.canvas.keyPressEvent(_key(Qt.Key_G))
    qapp.processEvents()
    window.viewer.canvas.keyPressEvent(_key(Qt.Key_M))
    qapp.processEvents()

    assert browser.opened == ["https://www.google.com/maps?q=40.4168,-3.7038"]
