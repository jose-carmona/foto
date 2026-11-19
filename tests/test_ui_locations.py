"""Tolerancia en la ventana (T4.2, T4.3, T4.4; ADR 0010, Decisión 6 y 7).

Banner de ubicaciones no disponibles, panel ``:locations``, visor bloqueado
para las fotos fantasma y favoritos/etiquetas offline. El nivel de carga
(sin Qt) está en ``tests/test_ui_tolerancia.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from PySide6.QtWidgets import QToolButton

from foto.core.hashing import hash_bytes
from foto.storage.catalog import Catalog
from foto.storage.config import Location
from foto.storage.tags import connect_read_write, toggle_favorite
from foto.ui.app import load_locations, load_photos, load_tags
from foto.ui.window import PhotoWindow
from tests.conftest import write_image

NOW = "2026-11-18T09:00:00+00:00"
SSD_DATA = b"ssd-una"
USB_DATA = b"usb-tres"


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def library(tmp_path) -> Path:
    """Dos discos (SSD + USB «desmontado») con metainformación externa.

    Devuelve la raíz del SSD; el directorio de metainformación vive en
    ``tmp_path/foto-meta`` y el USB ya está fuera (renombrado).
    """
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "2024" / "una.jpg", SSD_DATA)
    write_image(usb / "archivos" / "tres.jpg", USB_DATA)
    metadata = tmp_path / "foto-meta"
    metadata.mkdir()
    declared = (
        Location("ssd", ssd, "fast"),
        Location("usb", usb, "archive"),
    )
    catalog = Catalog.open(metadata / "catalog.sqlite", declared_locations=declared)
    try:
        ids = {
            location.name: catalog.insert_named_location(
                name=location.name,
                path=location.path,
                tier=location.tier,
                now=NOW,
            )
            for location in declared
        }
        for location_name, rel_path, data in (
            ("ssd", "2024/una.jpg", SSD_DATA),
            ("usb", "archivos/tres.jpg", USB_DATA),
        ):
            photo_hash = f"sha256:{hash_bytes(data)}"
            catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
            catalog.insert_file(
                location_id=ids[location_name],
                rel_path=rel_path,
                photo_hash=photo_hash,
                size=len(data),
                mtime_ns=1,
                inode=1,
                device=1,
                now=NOW,
            )
        catalog.commit()
    finally:
        catalog.close()
    connection = connect_read_write(metadata / "catalog.sqlite")
    try:
        toggle_favorite(connection, f"sha256:{hash_bytes(USB_DATA)}", now=NOW)
    finally:
        connection.close()
    usb.rename(usb.with_name("desmontado"))
    return tmp_path


def _window(qapp, library) -> PhotoWindow:
    ssd = library / "ssd"
    metadata = library / "foto-meta"
    return PhotoWindow(
        load_photos(ssd, metadata),
        tag_connection=connect_read_write(metadata / "catalog.sqlite"),
        tag_catalog=load_tags(ssd, metadata),
        locations=load_locations(metadata),
    )


def _photo_by_name(window: PhotoWindow, name: str):
    for photo in window.photos.values():
        if photo.name == name:
            return photo
    raise AssertionError(name)


def _select(window: PhotoWindow, name: str) -> None:
    window._select_key(_photo_by_name(window, name).key)


# ----------------------------------------------------------------------
# T4.2 — banner de estado y panel de ubicaciones
# ----------------------------------------------------------------------
def test_banner_lists_the_unavailable_location_and_opens_the_panel(qapp, library):
    window = _window(qapp, library)
    window.show()

    assert window.banner.isVisible()
    text = window.banner.text()
    assert "Ubicación «usb» no disponible" in text
    assert "1 foto no visible" in text

    # El banner abre el panel (clic); el comando ``:locations`` es la vía de
    # teclado y se comprueba en el test siguiente.
    window.banner.opened.emit()
    assert window.locations_overlay.isVisible()

    window._dispatch("escape")
    assert not window.locations_overlay.isVisible()
    window.close()


def test_banner_is_hidden_when_every_location_is_available(qapp, library):
    ssd = library / "ssd"
    metadata = library / "foto-meta"
    # Vuelve a montar el USB: las dos ubicaciones quedan disponibles.
    (library / "desmontado").rename(library / "usb")
    locations = load_locations(metadata)
    assert all(location.available for location in locations)

    window = PhotoWindow(
        load_photos(ssd, metadata),
        locations=locations,
    )
    window.show()

    assert not window.banner.isVisible()
    window.close()


def test_banner_has_no_mouse_only_actions_and_keeps_the_adr_text(qapp, library):
    window = _window(qapp, library)
    window.show()

    # La app es teclado primero: sin botones que exijan el ratón para
    # revisar o cerrar; ``:locations`` abre el panel y Esc cierra el aviso.
    buttons = [
        button
        for button in window.banner.findChildren(QToolButton)
        if button.isVisible()
    ]
    assert [button.text() for button in buttons] == ["✕"]    # El texto completo del ADR queda disponible (tooltip, accesibilidad,
    # pruebas) aunque la tarjeta muestre el titular y el detalle por partes.
    text = window.banner.text()
    assert "Ubicación «usb» no disponible" in text
    assert "1 foto no visible" in text
    assert window.banner.accessibleDescription() == text
    window.close()


def test_escape_dismisses_the_banner(qapp, library):
    window = _window(qapp, library)
    window.show()

    assert window.banner.isVisible()
    window._dispatch("escape")
    assert window.banner.is_dismissed()
    assert not window.banner.isVisible()
    window.close()


def test_banner_auto_hides_after_30_seconds(qapp, library):
    window = _window(qapp, library)
    window.show()

    # El autocierre usa el temporizador interno: lo disparamos directamente
    # (30 s reales no caben en una prueba).
    window.banner._autohide.timeout.emit()
    assert window.banner.is_dismissed()
    assert not window.banner.isVisible()
    window.close()


def test_close_button_dismisses_the_banner(qapp, library):
    window = _window(qapp, library)
    window.show()

    window.banner.close_button.clicked.emit()
    assert window.banner.is_dismissed()
    assert not window.banner.isVisible()
    window.close()


def test_ghost_cells_carry_the_unavailable_role(qapp, library):
    from PySide6.QtCore import Qt

    from foto.ui.grid import UNAVAILABLE_ROLE

    window = _window(qapp, library)
    window.show()

    ghosts = [photo for photo in window.photos.values() if not photo.available]
    available = [photo for photo in window.photos.values() if photo.available]
    assert ghosts and available
    for photo in ghosts:
        item = window._grid_items[photo.key]
        assert item.data(UNAVAILABLE_ROLE) is True
        assert "no disponible" in str(item.data(Qt.AccessibleTextRole))
    for photo in available:
        item = window._grid_items[photo.key]
        assert item.data(UNAVAILABLE_ROLE) is False
    window.close()


def test_locations_command_toggles_the_panel(qapp, library):
    window = _window(qapp, library)
    window.show()

    window._run_command("locations")
    assert window.locations_overlay.isVisible()
    html = window.locations_overlay.body.toHtml()
    assert "ssd" in html and "usb" in html
    assert "no disponible" in html

    window._dispatch("escape")
    assert not window.locations_overlay.isVisible()
    window.close()


# ----------------------------------------------------------------------
# T4.3 — placeholders fantasma: visor desactivado con mensaje claro
# ----------------------------------------------------------------------
def test_ghost_photo_cannot_open_the_viewer(qapp, library):
    window = _window(qapp, library)
    window.show()
    _select(window, "tres.jpg")
    assert window._current_photo().available is False

    window._open_viewer()

    assert not window._viewer_visible
    assert "no está disponible" in window.status.message_label.text()
    assert "usb" in window.status.message_label.text()

    # Y en la ficha del panel: la fila «Disponibilidad» del catálogo.
    from PySide6.QtWidgets import QLabel

    texts = [label.text() for label in window.panel.findChildren(QLabel)]
    assert any("no disponible" in text for text in texts)
    window.close()


def test_moving_the_cursor_to_a_ghost_closes_the_viewer(qapp, library):
    window = _window(qapp, library)
    window.show()
    _select(window, "una.jpg")
    window._open_viewer()
    assert window._viewer_visible

    _select(window, "tres.jpg")

    assert not window._viewer_visible
    assert "no está disponible" in window.status.message_label.text()
    window.close()


# ----------------------------------------------------------------------
# T4.4 — favoritas y etiquetas offline (todo son metadatos, ADR 0006)
# ----------------------------------------------------------------------
def test_favorite_persists_while_the_volume_is_unmounted(qapp, library):
    window = _window(qapp, library)
    window.show()
    _select(window, "tres.jpg")
    assert window._current_photo().favorite is True

    # `f` por la vía real del dispatcher: la rejilla emite «favorite».
    window._dispatch("favorite")
    assert window._current_photo().favorite is False

    reloaded = {
        photo.name: photo
        for photo in load_photos(library / "ssd", library / "foto-meta")
    }
    assert reloaded["tres.jpg"].favorite is False

    window._dispatch("favorite")
    reloaded = {
        photo.name: photo
        for photo in load_photos(library / "ssd", library / "foto-meta")
    }
    assert reloaded["tres.jpg"].favorite is True
    window.close()


def test_tag_filter_includes_ghost_photos(qapp, library):
    window = _window(qapp, library)
    window.show()

    window._run_command("tag fav")

    visible = {window.photos[key].name for key in window._visible_keys_cache}
    assert visible == {"tres.jpg"}
    window.close()
