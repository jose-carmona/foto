"""Inventario de la conducta final del mapeo de teclas (P1.5, fases 0–3).

Empiezo congelando el comportamiento de ``PanelScroll`` y
``PhotoWindow.keyPressEvent`` (fase 0) y lo actualizo al modelo unificado:
tabla ``ui/keybindings.py``, dispatcher único ``_dispatch`` con registro de
acciones (fase 2) y rarezas R1–R11 resueltas (fase 3) — dígitos 1–5 fuera de
servicio, ``$`` del panel en paridad con la rejilla, ``Shift+G`` del panel
emitiendo ``end`` y el fallback de la ventana reutilizando las filas de la
rejilla. Además congela el mecanismo: el panel solo traduce y emite (el scroll
lo ejecuta ``_panel_scroll`` desde el dispatcher).

El timeout de 500 ms de la secuencia ``g g`` no se congela aquí (depende del
reloj); ya está cubierto en ``test_ui_documented_features.py`` con
``repeticiones=2``.
"""

from __future__ import annotations

import os
import sys

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from foto.ui.keybindings import CTRL_MASK
from foto.ui.models import Photo
from foto.ui.panels import PanelScroll
from foto.ui.window import PhotoWindow


def _photo(
    name: str,
    taken: str,
    *,
    camera: str = "Nikon Z 6",
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
    )


# Dos grupos de fecha (junio y mayo de 2024) para ejercitar los límites de
# grupo con datos reales, igual que la suite de contrato.
PHOTOS = [
    _photo(
        f"junio-{index}.jpg",
        f"2024-06-{index + 1:02d} 08:00",
        camera="Canon EOS R5" if index % 2 else "Nikon Z 6",
    )
    for index in range(5)
] + [
    _photo(
        f"mayo-{index}.jpg",
        f"2024-05-{index + 1:02d} 08:00",
        camera="Canon EOS R5" if index % 2 else "Nikon Z 6",
    )
    for index in range(3)
]


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _make_window(qapp, photos: list[Photo] | None = None) -> PhotoWindow:
    """Ventana de la app real: fila en el visor y panel oculto al arrancar."""
    window = PhotoWindow(
        photos or PHOTOS,
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


def _event(key: int, modifiers: int = 0) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier(modifiers))


def _select_index(window: PhotoWindow, index: int) -> None:
    window._select_key(window._visible_keys_cache[index])


def _may_visible(window: PhotoWindow) -> list[str]:
    return [
        key
        for key in window._visible_keys_cache
        if window.photos[key].taken.startswith("2024-05")
    ]


def _select_may_group(window: PhotoWindow, *, last: bool = False) -> None:
    members = _may_visible(window)
    window._select_key(members[-1] if last else members[0])


def _setup_viewer_with_image(window: PhotoWindow, qapp, *, zoomed: bool = False) -> None:
    """Visor abierto con una imagen sintética más grande que el lienzo."""
    window._open_viewer()
    pixmap = QPixmap(2000, 1500)
    pixmap.fill()
    window.viewer.canvas.set_image(pixmap)
    if zoomed:
        window.viewer.canvas.toggle_zoom()
    qapp.processEvents()


# ----------------------------------------------------------------------
# Panel: PanelScroll standalone, scroll local
# ----------------------------------------------------------------------
# Las filas de scroll local del panel (flechas, PgUp/PgDn, Home/End) no
# dependen del dispatch de la ventana: se congelan sobre un ``PanelScroll``
# real con contenido desbordado para tener un rango determinista.
def _standalone_panel(qapp) -> PanelScroll:
    panel = PanelScroll()
    inner = QWidget()
    inner.setFixedSize(150, 800)
    panel.setWidget(inner)
    panel.resize(200, 200)
    panel.show()
    qapp.processEvents()
    return panel


def _key_event(key: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


def test_panel_scroll_keys_are_emitted_for_the_window_to_scroll(qapp):
    """Fase 2: el panel solo traduce; el scroll lo ejecuta _panel_scroll."""
    panel = _standalone_panel(qapp)
    seen: list[str] = []
    panel.action_requested.connect(seen.append)

    for key in (
        Qt.Key_Down,
        Qt.Key_Up,
        Qt.Key_PageDown,
        Qt.Key_PageUp,
        Qt.Key_Home,
        Qt.Key_End,
        Qt.Key_Left,
        Qt.Key_Right,
    ):
        panel.keyPressEvent(_key_event(key))

    assert seen == [
        "scroll_down",
        "scroll_up",
        "scroll_page_down",
        "scroll_page_up",
        "scroll_top",
        "scroll_end",
        "ignore",
        "ignore",
    ]


def test_panel_scroll_moves_by_the_window_dispatcher(qapp):
    """El scroll real del panel pasa por el dispatcher de la ventana."""
    window = _make_window(qapp)
    window.toggle_panel()
    inner = window.panel.scroll.widget()
    assert inner is not None
    inner.setFixedSize(150, 800)
    window.resize(300, 150)
    qapp.processEvents()
    bar = window.panel.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    limit = bar.maximum()

    def scroll_delta(key: Qt.Key) -> None:
        """Ejecuta la tecla y comprueba el desplazamiento sujeto al rango."""
        start = bar.value()
        window.panel.scroll.keyPressEvent(_key_event(key))
        if key in (Qt.Key_Up, Qt.Key_PageUp):
            delta = -32 if key == Qt.Key_Up else -bar.pageStep()
        elif key in (Qt.Key_Down, Qt.Key_PageDown):
            delta = 32 if key == Qt.Key_Down else bar.pageStep()
        else:
            pytest.fail(f"tecla no contemplada: {key}")
        expected = max(bar.minimum(), min(bar.maximum(), start + delta))
        assert bar.value() == expected, key

    scroll_delta(Qt.Key_Down)  # 0 → min(max, 32)
    bar.setValue(limit)
    scroll_delta(Qt.Key_Up)

    scroll_delta(Qt.Key_PageDown)
    bar.setValue(limit)
    scroll_delta(Qt.Key_PageUp)

    bar.setValue(limit)
    window.panel.scroll.keyPressEvent(_key_event(Qt.Key_Home))
    assert bar.value() == 0

    bar.setValue(0)
    window.panel.scroll.keyPressEvent(_key_event(Qt.Key_End))
    assert bar.value() == limit

    # ←/→ siguen sin hacer nada.
    bar.setValue(32)
    window.panel.scroll.keyPressEvent(_key_event(Qt.Key_Left))
    window.panel.scroll.keyPressEvent(_key_event(Qt.Key_Right))
    assert bar.value() == 32


# ----------------------------------------------------------------------
# Panel: comportamiento completo sobre la ventana real
# ----------------------------------------------------------------------
def _panel_focused(window: PhotoWindow, qapp) -> None:
    """Muestra el panel de metadatos y le da el foco."""
    if not window.panel_visible:
        window.toggle_panel()
    window.panel.focus_scroll()
    qapp.processEvents()


def _current_photo_name(window: PhotoWindow) -> str:
    photo = window._current_photo()
    assert photo is not None
    return photo.name


def _visible_index(window: PhotoWindow) -> int:
    """Índice en la lista visible de la foto con el cursor."""
    current = window.grid.currentItem()
    assert current is not None
    for index, key in enumerate(window._visible_keys_cache):
        item = window._grid_items.get(key)
        if item is current:
            return index
    raise AssertionError("el cursor no está sobre una foto visible")


def test_panel_h_moves_the_grid_cursor_left(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)
    bar = window.panel.scroll.verticalScrollBar()
    if bar.maximum() > 0:
        bar.setValue(1)
    _select_index(window, 2)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_H))

    assert _visible_index(window) == 1
    if bar.maximum() > 0:
        assert bar.value() == 1  # el scroll del panel no se ha movido


def test_panel_l_moves_the_grid_cursor_right(qapp):
    window = _make_window(qapp)
    _select_index(window, 2)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_L))

    assert _visible_index(window) == 3


def test_panel_j_moves_the_grid_cursor_forward_not_the_scroll(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)
    _select_index(window, 0)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_J))

    assert _visible_index(window) > 0


def test_panel_k_moves_the_grid_cursor_backward(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)
    _select_index(window, len(window._visible_keys_cache) - 1)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_K))

    assert _visible_index(window) < len(window._visible_keys_cache) - 1


def test_panel_zero_jumps_to_the_first_photo_of_the_group(qapp):
    window = _make_window(qapp)
    _select_may_group(window, last=True)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_0))

    assert _current_photo_name(window) == _may_visible(window)[0]


def test_panel_dollar_jumps_to_the_group_end(qapp):
    """R2: el panel comparte las filas de borde de grupo con la rejilla.

    ``$`` en su tecla propia (Key_Dollar sin shift) y como Shift+4.
    """
    window = _make_window(qapp)
    for key, shift in (
        (Qt.Key_Dollar, Qt.KeyboardModifier.NoModifier),
        (Qt.Key_4, Qt.KeyboardModifier.ShiftModifier),
    ):
        _select_may_group(window)
        window.panel.scroll.keyPressEvent(_event(key, shift))
        assert _current_photo_name(window) == _may_visible(window)[-1], key


@pytest.mark.parametrize("key", [Qt.Key_Return, Qt.Key_O])
def test_panel_enter_and_o_open_the_viewer(qapp, key):
    window = _make_window(qapp)

    window.panel.scroll.keyPressEvent(_event(key))

    assert window._viewer_visible


def test_panel_zoom_keys_act_with_the_viewer_open(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)
    _setup_viewer_with_image(window, qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Plus))
    assert window.viewer.canvas.is_zoomed()

    zoom_after_in = window.viewer.canvas._zoom
    window.panel.scroll.keyPressEvent(_event(Qt.Key_Minus))
    # Desde un paso de zoom, alejar vuelve al ajuste (zoom None = ajustado).
    assert window.viewer.canvas._zoom is None
    assert zoom_after_in is not None


def test_panel_z_requires_no_shift(qapp):
    """R4: el panel exige ``z`` sin shift, paridad con rejilla y visor."""
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp)
    assert not window.viewer.canvas.is_zoomed()

    # Shift+Z ya no alterna: la tecla se anula.
    window.panel.scroll.keyPressEvent(
        _event(Qt.Key_Z, Qt.KeyboardModifier.ShiftModifier)
    )
    assert not window.viewer.canvas.is_zoomed()

    # Sin shift alterna, como en la rejilla y el visor.
    window.panel.scroll.keyPressEvent(_event(Qt.Key_Z))
    assert window.viewer.canvas.is_zoomed()


def test_panel_shift_g_moves_the_grid_cursor_to_the_end(qapp):
    """R3: Shift+G del panel emite ``end``, paridad con la rejilla.

    La rejilla manda en navegación: con el visor abierto como fila, cursor
    al final de la lista.
    """
    window = _make_window(qapp)
    _select_index(window, 0)

    window.panel.scroll.keyPressEvent(
        _event(Qt.Key_G, Qt.KeyboardModifier.ShiftModifier)
    )

    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[-1]
    ]


def test_panel_shift_g_moves_the_grid_cursor_with_the_viewer_open(qapp):
    window = _make_window(qapp)
    _select_index(window, 0)
    _setup_viewer_with_image(window, qapp)

    window.panel.scroll.keyPressEvent(
        _event(Qt.Key_G, Qt.KeyboardModifier.ShiftModifier)
    )

    # La rejilla manda: la fila del visor comparte el mismo cursor.
    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[-1]
    ]


@pytest.mark.parametrize("digit", range(1, 6))
def test_panel_digits_1_to_5_are_dead_today(qapp, digit):
    """Los dígitos del panel son acciones muertas (R1 congelado)."""
    window = _make_window(qapp)
    _select_index(window, 2)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_0 + digit))

    assert _visible_index(window) == 2


def test_panel_tab_and_shift_tab_give_the_focus_back(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Tab))
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid

    # De nuevo en el panel: Backtab también devuelve el foco (conmutador).
    window.panel.focus_scroll()
    qapp.processEvents()
    window.panel.scroll.keyPressEvent(_key_event(Qt.Key_Backtab))
    qapp.processEvents()
    assert qapp.focusWidget() is window.grid


def test_panel_esc_closes_the_active_prompt(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)
    window.status.open_search(window.search_text)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Escape))

    assert not window.status.prompt_active


def test_panel_q_shows_the_quit_hint_today(qapp):
    window = _make_window(qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Q))

    assert "Ctrl+q" in window.status.message_label.text()


def test_panel_q_closes_the_viewer(qapp):
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Q))

    assert not window._viewer_visible


def test_panel_toggles_metadata_panel_with_i(qapp):
    window = _make_window(qapp)
    _panel_focused(window, qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_I))

    assert not window.panel_visible


def test_panel_toggles_favorite_with_f(qapp):
    window = _make_window(qapp)
    _select_index(window, 1)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_F))

    photo = window._current_photo()
    assert photo is not None
    assert photo.favorite is True


def test_panel_opens_the_command_line(qapp):
    window = _make_window(qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Colon))

    assert window.status.prompt_active
    assert window.status.prompt_mode == "command"


def test_panel_opens_the_search(qapp):
    window = _make_window(qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Slash))

    assert window.status.prompt_active
    assert window.status.prompt_mode == "search"


def test_panel_opens_the_help(qapp):
    window = _make_window(qapp)

    window.panel.scroll.keyPressEvent(_event(Qt.Key_Question))

    assert window.help_overlay.isVisible()


# ----------------------------------------------------------------------
# Ventana-fallback: PhotoWindow.keyPressEvent
# ----------------------------------------------------------------------
def test_window_fallback_letters_move_the_cursor(qapp):
    window = _make_window(qapp)
    _select_index(window, 2)

    window.keyPressEvent(_event(Qt.Key_H))
    assert _visible_index(window) == 1

    window.keyPressEvent(_event(Qt.Key_L))
    assert _visible_index(window) == 2

    start = _visible_index(window)
    window.keyPressEvent(_event(Qt.Key_J))
    assert _visible_index(window) > start

    last = len(window._visible_keys_cache) - 1
    _select_index(window, last)
    window.keyPressEvent(_event(Qt.Key_K))
    assert _visible_index(window) < last


def test_window_fallback_enter_and_o_open_the_viewer(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_event(Qt.Key_Return))
    assert window._viewer_visible


def test_window_fallback_g_g_goes_to_the_first_photo(qapp):
    window = _make_window(qapp)
    _select_index(window, len(window._visible_keys_cache) - 1)

    window.keyPressEvent(_event(Qt.Key_G))
    window.keyPressEvent(_event(Qt.Key_G))

    assert window._visible_keys_cache[0] == window._visible_keys_cache[0]
    assert window._viewer_visible is False
    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[0]
    ]


def test_window_fallback_shift_g_moves_to_the_end_without_viewer(qapp):
    """Semántica actual: `G` sin visor mueve el cursor al final."""
    window = _make_window(qapp)

    window.keyPressEvent(
        _event(Qt.Key_G, Qt.KeyboardModifier.ShiftModifier)
    )

    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[-1]
    ]


def test_window_fallback_shift_g_moves_the_grid_cursor_with_viewer_open(qapp):
    """R6: la rejilla manda; ``G`` con visor abierta mueve el cursor al final.

    Antes la ventana-fallback llamaba ``_viewer_last`` (divergencia R6); la
    semántica unificada deja la navegación al ``end`` de la rejilla.
    """
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp)

    window.keyPressEvent(
        _event(Qt.Key_G, Qt.KeyboardModifier.ShiftModifier)
    )

    # La fila del visor con grid_row=True comparte el mismo cursor de la
    # rejilla: el cursor acaba al final de la lista.
    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[-1]
    ]


def test_window_fallback_navigation_keys_act(qapp):
    """R6: la ventana-fallback reutiliza las filas de la rejilla."""
    window = _make_window(qapp)
    last = len(window._visible_keys_cache) - 1

    window.keyPressEvent(_event(Qt.Key_Home))
    assert _visible_index(window) == 0

    window.keyPressEvent(_event(Qt.Key_End))
    assert _visible_index(window) == last

    window.keyPressEvent(_event(Qt.Key_PageUp))
    after_page_up = _visible_index(window)
    window.keyPressEvent(_event(Qt.Key_PageDown))
    assert _visible_index(window) >= after_page_up

    # Bordes de grupo reutilizan el mapeo de la rejilla (mayo → extremos).
    _select_may_group(window)
    window.keyPressEvent(_event(Qt.Key_0))
    assert _current_photo_name(window) == _may_visible(window)[0]
    window.keyPressEvent(_event(Qt.Key_Dollar))
    assert _current_photo_name(window) == _may_visible(window)[-1]


def test_window_fallback_ctrl_d_and_ctrl_u_move_half_page(qapp):
    """R6: la media página con la Ctrl física llega también al fallback."""
    window = _make_window(qapp)

    window.keyPressEvent(_event(Qt.Key_D, Qt.KeyboardModifier(CTRL_MASK)))
    first = _visible_index(window)
    assert 0 < first < len(window._visible_keys_cache) - 1

    window.keyPressEvent(_event(Qt.Key_U, Qt.KeyboardModifier(CTRL_MASK)))
    assert _visible_index(window) < first

    # ⌘d no dispara nada en macOS: la Ctrl física es la única (§7.3/R8).
    # En Mac ⌘ llega como ControlModifier; en el resto no hay ⌘ y no se simula.
    if sys.platform == "darwin":
        before = _visible_index(window)
        window.keyPressEvent(_event(Qt.Key_D, Qt.KeyboardModifier.ControlModifier.value))
        assert _visible_index(window) == before


def test_window_fallback_zoom_keys_do_nothing_today(qapp):
    """La ventana-fallback no tiene +, - ni z (R6 congelado)."""
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp)
    assert not window.viewer.canvas.is_zoomed()

    window.keyPressEvent(_event(Qt.Key_Plus))
    window.keyPressEvent(_event(Qt.Key_Minus))
    window.keyPressEvent(_event(Qt.Key_Z))

    assert not window.viewer.canvas.is_zoomed()


def test_window_fallback_q_shows_the_quit_hint(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_event(Qt.Key_Q))

    assert "Ctrl+q" in window.status.message_label.text()


@pytest.mark.parametrize(
    ("key", "verify"),
    [
        (Qt.Key_Colon, "command"),
        (Qt.Key_Slash, "search"),
        (Qt.Key_Question, "help"),
        (Qt.Key_F1, "help"),
    ],
)
def test_window_fallback_global_keys_act(qapp, key, verify):
    window = _make_window(qapp)

    window.keyPressEvent(_event(key))

    if verify == "command":
        assert window.status.prompt_active
        assert window.status.prompt_mode == "command"
    elif verify == "search":
        assert window.status.prompt_active
        assert window.status.prompt_mode == "search"
    else:
        assert window.help_overlay.isVisible()


def test_window_fallback_i_toggles_the_panel(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_event(Qt.Key_I))

    assert window.panel_visible


def test_window_fallback_f_toggles_favorite(qapp):
    window = _make_window(qapp)

    window.keyPressEvent(_event(Qt.Key_F))

    photo = window._current_photo()
    assert photo is not None
    assert photo.favorite is True


def test_window_fallback_esc_resets_then_closes_the_viewer(qapp):
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp, zoomed=True)

    window.keyPressEvent(_event(Qt.Key_Escape))
    assert window._viewer_visible
    assert not window.viewer.canvas.is_zoomed()

    window.keyPressEvent(_event(Qt.Key_Escape))
    assert not window._viewer_visible


def test_window_fallback_ignores_keys_with_a_prompt_open(qapp):
    window = _make_window(qapp)
    window.status.open_command()

    window.keyPressEvent(_event(Qt.Key_I))

    assert window.panel_visible is False
    assert window.status.prompt_active


def test_window_fallback_esc_with_a_prompt_open_closes_the_prompt(qapp):
    window = _make_window(qapp)
    window.status.open_command()

    window.keyPressEvent(_event(Qt.Key_Escape))

    assert not window.status.prompt_active


def test_window_fallback_only_close_keys_work_with_help_visible(qapp):
    window = _make_window(qapp)
    window._toggle_help()
    assert window.help_overlay.isVisible()

    window.keyPressEvent(_event(Qt.Key_I))
    assert window.panel_visible is False
    window.keyPressEvent(_event(Qt.Key_G))
    assert window.grid.currentItem() is window._grid_items[
        window._visible_keys_cache[0]
    ]

    window.keyPressEvent(_event(Qt.Key_Question))
    assert not window.help_overlay.isVisible()


# ----------------------------------------------------------------------
# Rejilla: conducta final de fase 3
# ----------------------------------------------------------------------
def test_grid_ctrl_j_is_annulled(qapp):
    """R11: cualquier tecla con Ctrl/⌘ no declarada se anula en la rejilla."""
    window = _make_window(qapp)
    _select_index(window, 0)

    window.grid.keyPressEvent(_event(Qt.Key_J, Qt.KeyboardModifier(CTRL_MASK)))

    assert _visible_index(window) == 0
    # Y no abre el visor ni nada parecido: la tecla no hace nada.
    assert not window._viewer_visible


@pytest.mark.parametrize("digit", range(1, 6))
def test_grid_digits_1_to_5_are_dead_today(qapp, digit):
    """R1: los dígitos de la rejilla quedan fuera de servicio (caen a super())."""
    window = _make_window(qapp)
    _select_index(window, 2)

    window.grid.keyPressEvent(_event(Qt.Key_0 + digit))

    assert _visible_index(window) == 2
    assert not window._viewer_visible


def test_grid_shift_h_falls_back_to_plain_h_without_viewer_keys(qapp):
    """Sin ``viewer_keys``, Shift+H emite ``h`` (movimiento de cursor)."""
    window = _make_window(qapp)  # visor cerrado → viewer_keys False
    _select_index(window, 2)

    window.grid.keyPressEvent(
        _event(Qt.Key_H, Qt.KeyboardModifier.ShiftModifier)
    )

    assert _visible_index(window) == 1
    assert not window._viewer_visible


def test_grid_shift_4_emits_the_group_end(qapp):
    """``$`` vive en ``Shift+4``/``Shift+5`` en algunas disposiciones."""
    window = _make_window(qapp)
    _select_may_group(window)

    window.grid.keyPressEvent(
        _event(Qt.Key_4, Qt.KeyboardModifier.ShiftModifier)
    )

    assert _current_photo_name(window) == _may_visible(window)[-1]


# ----------------------------------------------------------------------
# Canvas: Tab se sirve desde event(), no desde keyPressEvent
# ----------------------------------------------------------------------
def test_canvas_event_serves_tab_and_emits_focus_panel(qapp):
    """Qt consume Tab para la cadena de foco: el canvas lo atiende en event()."""
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp, zoomed=True)
    canvas = window.viewer.canvas
    seen: list[str] = []
    canvas.action_requested.connect(seen.append)

    consumed = canvas.event(_key_event(Qt.Key_Tab))

    assert consumed is True
    assert seen == ["focus_panel"]
    assert window._viewer_visible  # no cierra nada


def test_canvas_event_serves_backtab_with_shift(qapp):
    window = _make_window(qapp)
    _setup_viewer_with_image(window, qapp, zoomed=True)
    canvas = window.viewer.canvas
    seen: list[str] = []
    canvas.action_requested.connect(seen.append)

    consumed = canvas.event(QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key_Backtab,
        Qt.KeyboardModifier.ShiftModifier,
    ))

    assert consumed is True
    assert seen == ["focus_panel"]


# ----------------------------------------------------------------------
# Guardas de modo (únicas, cortesía de _dispatch)
# ----------------------------------------------------------------------
def test_help_visible_and_i_is_ignored_via_window(qapp):
    window = _make_window(qapp)
    window._toggle_help()

    window.keyPressEvent(_event(Qt.Key_I))

    assert window.help_overlay.isVisible()
    assert window.panel_visible is False
