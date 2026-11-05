"""Rutas críticas de ``PhotoWindow`` ejercitadas sin pantalla.

Cubre con la infraestructura offscreen existente (``QApplication`` +
``QT_QPA_PLATFORM=offscreen``, señales síncronas y ``processEvents``; sin
esperas ni dependencias nuevas) las rutas que faltaban en la suite: carga y
fallo de imagen del visor, prompts de búsqueda y comando, y la gestión de
foco al entrar y salir del visor, del panel y de los prompts.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from foto.ui.window import ImageLoadTask, Photo, PhotoWindow


def _photo(
    name: str,
    taken: str,
    *,
    camera: str = "Nikon Z 6",
    path: Path | None = None,
) -> Photo:
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
        path=path,
    )


def _valid_image(path: Path) -> Path:
    """Escribe una imagen válida mínima (PNG o JPEG según la extensión)."""
    image = QImage(QSize(24, 18), QImage.Format.Format_RGB32)
    image.fill(Qt.green)
    assert image.save(str(path)), f"no se pudo crear {path}"
    return path


SEARCH_PHOTOS = [
    _photo("bosque.jpg", "2024-05-03 08:00"),
    _photo("playa.jpg", "2024-05-02 08:00"),
    _photo("ciudad.jpg", "2024-05-01 08:00"),
]


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _make_window(qapp, photos: list[Photo] | None = None, *, grid_row: bool = True):
    window = PhotoWindow(
        photos or SEARCH_PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=grid_row,
    )
    window.show()
    qapp.processEvents()
    window.activateWindow()
    qapp.processEvents()
    return window


def _key_event(key: int, text: str = "") -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text)


def _type(prompt, text: str) -> None:
    """Escribe ``text`` en un ``PromptEdit`` con eventos de teclado."""
    for ch in text:
        prompt.keyPressEvent(_key_event(ord(ch.upper()), ch))


# ----------------------------------------------------------------------
# 1. Carga de imagen
# ----------------------------------------------------------------------
def test_image_task_emits_loaded_with_a_non_null_image(qapp, tmp_path):
    path = _valid_image(tmp_path / "original.png")
    task = ImageLoadTask("clave", path)
    loaded = QSignalSpy(task.signals.loaded)
    failed = QSignalSpy(task.signals.failed)

    task.run()  # decodificación síncrona: no bloquea el hilo de UI

    assert loaded.count() == 1
    assert failed.count() == 0
    key, image = loaded.at(0)
    assert key == "clave"
    assert isinstance(image, QImage)
    assert not image.isNull()


def test_viewer_opens_and_shows_the_loaded_original(qapp, tmp_path):
    photos = [
        _photo("a.jpg", "2024-05-02 08:00", path=_valid_image(tmp_path / "a.png")),
        _photo("b.jpg", "2024-05-01 08:00", path=_valid_image(tmp_path / "b.png")),
    ]
    window = _make_window(qapp, photos)
    expected = photos[0].key

    window._open_viewer()
    qapp.processEvents()
    # Dos rondas: la carga del original y el precargado del vecino.
    window._loader_pool.waitForDone(5000)
    qapp.processEvents()
    window._loader_pool.waitForDone(5000)
    qapp.processEvents()

    assert window._viewer_visible
    assert window._displayed_key == expected
    assert window.viewer.canvas.has_image()
    # La tarea terminó limpiamente: sin trabajos pendientes ni en curso.
    assert window._pending_image_key is None
    assert window._loading_keys == set()


# ----------------------------------------------------------------------
# 2. Fallo de imagen
# ----------------------------------------------------------------------
def test_image_task_emits_failed_for_a_missing_file(qapp, tmp_path):
    task = ImageLoadTask("clave", tmp_path / "ausente.png")
    loaded = QSignalSpy(task.signals.loaded)
    failed = QSignalSpy(task.signals.failed)

    task.run()

    assert loaded.count() == 0
    assert failed.count() == 1
    key, message = failed.at(0)
    assert key == "clave"
    assert message


def test_image_task_emits_failed_for_an_invalid_file(qapp, tmp_path):
    roto = tmp_path / "roto.png"
    roto.write_bytes(b"esto no es una imagen")
    task = ImageLoadTask("clave", roto)
    loaded = QSignalSpy(task.signals.loaded)
    failed = QSignalSpy(task.signals.failed)

    task.run()

    assert loaded.count() == 0
    assert failed.count() == 1


def _current_key(window: PhotoWindow) -> str | None:
    photo = window._current_photo()
    return photo.key if photo is not None else None


def test_failed_original_keeps_the_thumbnail_and_does_not_retry(qapp, tmp_path):
    _valid_image(tmp_path / "bien.png")  # otra imagen válida en la biblioteca
    photos = [
        _photo("rota.jpg", "2024-05-02 08:00", path=tmp_path / "no-existe.png"),
        _photo("bien.jpg", "2024-05-01 08:00", path=tmp_path / "bien.png"),
    ]
    window = _make_window(qapp, photos)
    failed_key = photos[0].key
    neighbor_key = photos[1].key

    window._open_viewer()
    qapp.processEvents()
    window._loader_pool.waitForDone(5000)
    qapp.processEvents()

    # La ventana no se rompe: sigue en el visor con la miniatura de reserva,
    # avisa en la barra de estado y deja la tarea cancelada.
    assert window._viewer_visible
    assert window._displayed_key == failed_key
    assert window.viewer.canvas.has_image()
    assert window._failed_images == {failed_key}
    assert window._loading_keys == set()
    assert window._pending_image_key is None
    assert "no se pudo cargar" in window.status.message_label.text()

    # No reintenta indefinidamente: más ciclos de eventos no reencolan nada,
    # ni volver a mostrar la foto intenta otra decodificación.
    for _ in range(3):
        qapp.processEvents()
        window._loader_pool.waitForDone(100)
    assert window._loading_keys == set()
    window._show_viewer_photo(photos[0])
    qapp.processEvents()
    assert failed_key in window._failed_images
    # Y la navegación sigue operando hacia la foto vecina.
    window._viewer_navigate(1)
    qapp.processEvents()
    window._loader_pool.waitForDone(5000)
    qapp.processEvents()
    assert _current_key(window) == neighbor_key
    assert window.viewer.canvas.has_image()


def test_invalid_original_fails_without_breaking_the_viewer(qapp, tmp_path):
    roto = tmp_path / "roto.jpg"
    roto.write_bytes(b"esto no es una imagen")
    photos = [_photo("rota.jpg", "2024-05-01 08:00", path=roto)]
    window = _make_window(qapp, photos)

    window._open_viewer()
    qapp.processEvents()
    window._loader_pool.waitForDone(5000)
    qapp.processEvents()

    assert window._viewer_visible
    assert window._failed_images == {photos[0].key}
    assert window._loading_keys == set()
    assert "no se pudo cargar" in window.status.message_label.text()


# ----------------------------------------------------------------------
# 3. Prompt de búsqueda
# ----------------------------------------------------------------------
def test_slash_opens_the_prompt_with_the_current_filter(qapp):
    window = _make_window(qapp)
    window._commit_search("bosque")
    qapp.processEvents()

    window._open_search()
    qapp.processEvents()

    assert window.status.prompt_active
    assert window.status.prompt_mode == "search"
    assert window.status.prompt.text() == "bosque"
    # Al reabrir ``/`` el origen queda guardado por si se cancela.
    assert window._search_origin is not None


def test_typing_filters_incrementally(qapp):
    window = _make_window(qapp)
    window._open_search()

    _type(window.status.prompt, "b")
    qapp.processEvents()
    assert window._visible_count() == 1  # bosque.jpg

    _type(window.status.prompt, "osque")
    qapp.processEvents()
    assert window.status.prompt.text() == "bosque"
    assert window._visible_count() == 1


def test_enter_completes_and_closes_the_search(qapp):
    window = _make_window(qapp)
    window._open_search()
    _type(window.status.prompt, "bosque")

    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Return))
    qapp.processEvents()

    assert window.search_text == "bosque"
    assert not window.status.prompt_active
    assert window._visible_count() == 1
    assert window._search_origin is None
    assert qapp.focusWidget() is window.grid


def test_escape_restores_the_previous_search_state(qapp):
    window = _make_window(qapp)
    window._select_key(window._visible_keys_cache[1])  # playa.jpg
    window._open_search()
    _type(window.status.prompt, "zzz")
    qapp.processEvents()
    # El texto sin coincidencias deja la rejilla vacía (estado vacío).
    assert window._visible_count() == 0

    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Escape))
    qapp.processEvents()

    assert not window.status.prompt_active
    assert window.search_text == ""
    assert window._visible_count() == 3
    assert _current_key(window) == "playa.jpg"  # se restaura el origen


# ----------------------------------------------------------------------
# 4. Prompt de comando
# ----------------------------------------------------------------------
def test_colon_opens_the_command_prompt(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    qapp.processEvents()

    assert window.status.prompt_active
    assert window.status.prompt_mode == "command"
    assert qapp.focusWidget() is window.status.prompt


def test_sort_commands_reorder_the_grid(qapp):
    photos = [
        _photo("a.jpg", "2024-05-03 08:00", camera="Canon EOS R5"),
        _photo("b.jpg", "2024-05-02 08:00"),
        _photo("c.jpg", "2024-05-01 08:00", camera="Canon EOS R5"),
    ]
    window = _make_window(qapp, photos)

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    _type(window.status.prompt, "sort date")
    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Return))
    qapp.processEvents()
    assert window._sort_key == "date"

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    _type(window.status.prompt, "sort camera")
    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Return))
    qapp.processEvents()
    assert window._sort_key == "camera"
    # Reordenado real: las fotos Canon (a y c) agrupan primero.
    first = window.photos[window._visible_keys_cache[0]]
    assert first.camera == "Canon EOS R5"


def test_help_command_opens_the_overlay(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    _type(window.status.prompt, "help")
    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Return))
    qapp.processEvents()
    assert window.help_overlay.isVisible()

    window.keyPressEvent(_key_event(Qt.Key_Escape))
    qapp.processEvents()
    assert not window.help_overlay.isVisible()


def test_unknown_command_shows_an_error_without_raising(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    _type(window.status.prompt, "cataclismo")
    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Return))
    qapp.processEvents()

    assert window.status.error_text
    assert "cataclismo" in window.status.message_label.text()


def test_escape_closes_the_command_without_executing_it(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    _type(window.status.prompt, "sort camera")
    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Escape))
    qapp.processEvents()

    assert not window.status.prompt_active
    assert window._sort_key == "date"  # no se ejecutó


# ----------------------------------------------------------------------
# 5. Foco
# ----------------------------------------------------------------------
def test_opening_and_closing_the_viewer_returns_focus_to_the_grid(qapp):
    window = _make_window(qapp)
    assert qapp.focusWidget() is window.grid

    window._open_viewer()
    qapp.processEvents()
    # Con la fila del visor, la dueña del foco sigue siendo la rejilla.
    assert qapp.focusWidget() is window.grid

    window._close_viewer()
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid


def test_viewer_focus_goes_to_the_canvas_without_grid_row(qapp):
    window = _make_window(qapp, grid_row=False)

    window._open_viewer()
    qapp.processEvents()
    assert qapp.focusWidget() is window.viewer.canvas

    window._close_viewer()
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid


def test_tab_moves_focus_between_grid_and_metadata_panel(qapp):
    window = _make_window(qapp)
    window.toggle_panel()
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid

    window.grid.keyPressEvent(_key_event(Qt.Key_Tab))
    qapp.processEvents()
    assert qapp.focusWidget() is window.panel.scroll

    window.panel.scroll.keyPressEvent(_key_event(Qt.Key_Backtab))
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid


def test_prompt_open_and_close_returns_focus_to_the_grid(qapp):
    window = _make_window(qapp)
    assert qapp.focusWidget() is window.grid

    window.keyPressEvent(_key_event(Qt.Key_Slash))
    qapp.processEvents()
    assert qapp.focusWidget() is window.status.prompt

    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Escape))
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid

    window.keyPressEvent(_key_event(Qt.Key_Colon))
    qapp.processEvents()
    assert qapp.focusWidget() is window.status.prompt

    window.status.prompt.keyPressEvent(_key_event(Qt.Key_Escape))
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid
