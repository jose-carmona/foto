"""Contrato entre lo documentado (comandos, atajos) y lo implementado.

``docs/diseno-ui.md`` y ``src/foto/resources/ayuda.md`` fijan el vocabulario
visible: todo comando documentado debe existir en ``COMMANDS`` (aceptado por
``_run_command``), todo comando de ``COMMANDS`` debe aparecer en la ayuda
integrada y todo atajo documentado debe tener comportamiento real. El test
falla si se retira o renombra una tecla o comando, y también si se documenta
uno nuevo sin implementación.

La tabla de atajos es explícita (derivada de ``ayuda.md`` y de
``docs/diseno-ui.md``); no se infieren comportamientos del Markdown. El
análisis del documento se limita a comprobar que cada tecla documentada tiene
su entrada en la tabla.

Límite documentado en el propio test: con la fila del visor la navegación del
visor la atiende la rejilla (mismo widget), así que los eventos van al
``keyPressEvent`` del widget que atiende la tecla en la app real; los eventos
sobre el lienzo ejercitan las teclas propias del visor (flechas, zoom, paneo,
cierre). La secuencia «g g» (dos pulsaciones) cubre el token documentado
``g g``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication

from foto.ui.commands import COMMANDS
from foto.ui.grid import FAVORITE_ROLE, PHOTO_ROLE
from foto.ui.help import _help_markdown
from foto.ui.keybindings import CTRL_MASK
from foto.ui.models import Photo
from foto.ui.window import (
    PhotoWindow,
)

_DOC_UI = Path("docs/diseno-ui.md")


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


# Dos grupos de fecha (junio y mayo de 2024) y cámaras alternadas para que
# ``0``/``$``, el reordenado y los límites de lista se ejerzan con datos reales.
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

CAMERA_PHOTOS = [
    _photo("a.jpg", "2024-05-03 08:00", camera="Canon EOS R5"),
    _photo("b.jpg", "2024-05-02 08:00"),
    _photo("c.jpg", "2024-05-01 08:00", camera="Canon EOS R5"),
]

# Dos ciudades de países distintos más una foto sin lugar resuelto: con
# ``:sort location`` el grupo de París va primero (Francia < España), luego
# Barcelona, y la sin ubicación queda al final en su grupo propio.
LOCATION_PHOTOS = [
    Photo(
        name="paris.jpg",
        taken="2024-05-01 08:00",
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
        place_country="Francia",
        place_country_code="FR",
        place_city="París",
        photo_hash="sha256:" + "1" * 64,
    ),
    Photo(
        name="barcelona.jpg",
        taken="2024-05-02 08:00",
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
        place_country="España",
        place_country_code="ES",
        place_state="Cataluña",
        place_city="Barcelona",
        photo_hash="sha256:" + "2" * 64,
    ),
    Photo(
        name="sin-lugar.jpg",
        taken="2024-05-03 08:00",
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
        photo_hash="sha256:" + "3" * 64,
    ),
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


# ----------------------------------------------------------------------
# Parseo del vocabulario documentado (formato controlado de tablas)
# ----------------------------------------------------------------------
def _section(text: str, heading: str) -> str:
    """Contenido entre ``heading`` y el encabezado siguiente."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index
            break
    if start is None:
        raise AssertionError(f"falta la sección {heading!r} en la documentación")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("#"):
            end = index
            break
    return "\n".join(lines[start + 1 : end])


def _first_cells(section: str) -> list[str]:
    """Primera celda de cada fila de tabla; salta separadores y cabecera."""
    cells: list[str] = []
    for line in section.splitlines():
        bare = line.strip()
        if not bare.startswith("|"):
            continue
        row = [cell.strip() for cell in bare.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in row):
            continue
        cells.append(row[0])
    if cells and cells[0].replace("`", "") in ("Tecla", "Comando"):
        cells = cells[1:]
    return cells


def _key_tokens(cell: str) -> list[str]:
    """Teclas de una celda de la columna «Tecla» (formato controlado).

    Acepta ``?` / `F1``, ``↑`/`↓` o `j`/`k``, ``h` `j` `k` `l` / flechas``,
    ``Shift+Tab`` y ``g g`` (la palabra ``o`` actúa de separador). Las
    palabras libres como ``flechas`` se resuelven con ``_KEY_ALIASES``.
    """
    text = cell.replace("`", " ")
    tokens: list[str] = []
    for piece in text.split("/"):
        for word in piece.split():
            if word in ("o", "y", "u"):
                continue
            tokens.append(word)
    return tokens


def _documented_key_tokens() -> set[str]:
    tokens: set[str] = set()
    ay = _help_markdown()
    dis = _DOC_UI.read_text(encoding="utf-8")
    for text, headings in (
        (ay, ("## Globales", "## Rejilla", "## Visor")),
        (dis, ("### Atajos globales", "### Rejilla", "### Visor")),
    ):
        for heading in headings:
            for cell in _first_cells(_section(text, heading)):
                tokens.update(_key_tokens(cell))
    return tokens


def _command_names(cell: str) -> list[str]:
    """Normaliza una celda de comandos: ``:q``, ``:quit``, ``:sort date``…

    Acepta las variantes de los dos documentos (``:q`` / ``:quit``,
    ``:sort date``, ``:sort camera``, ``:help``) separadas por coma o barra.
    Un segmento sin ``:`` hereda el prefijo del comando anterior
    (``:sort`` date / camera).
    """
    text = cell.replace("`", "").strip()
    base = ""
    names: list[str] = []
    for piece in re.split(r"[,/]", text):
        piece = piece.strip()
        if not piece or set(piece) <= set("-"):
            continue
        if piece.startswith(":"):
            names.append(piece)
            base = piece.split()[0]
        elif base:
            names.append(f"{base} {piece}")
        else:
            raise AssertionError(f"celda de comando no reconocida: {cell!r}")
    return [name.casefold() for name in names]


def _documented_commands() -> set[str]:
    documented: set[str] = set()
    for cell in _first_cells(_section(_help_markdown(), "## Comandos")):
        documented.update(_command_names(cell))
    dis = _DOC_UI.read_text(encoding="utf-8")
    for cell in _first_cells(_section(dis, "### Línea de comandos")):
        documented.update(_command_names(cell))
    return documented


def test_documented_commands_exist_in_command_table():
    documented = _documented_commands()
    implemented = {f":{command.name}" for command in COMMANDS}

    assert documented, "no hay comandos en la documentación"
    assert documented <= implemented, (
        f"documentados sin implementar: {sorted(documented - implemented)}"
    )


def test_command_table_matches_the_documented_vocabulary():
    documented = _documented_commands()
    implemented = {f":{command.name}" for command in COMMANDS}

    assert implemented <= documented, (
        f"comandos de COMMANDS ausentes del vocabulario documentado: "
        f"{sorted(implemented - documented)}"
    )


def test_every_command_appears_in_the_integrated_help():
    documented = _documented_commands()
    for command in COMMANDS:
        assert f":{command.name}" in documented, command.name


# ----------------------------------------------------------------------
# Los comandos se ejecutan de verdad, no solo existen en una tabla
# ----------------------------------------------------------------------
def test_sort_date_command_sorts_by_date(qapp):
    window = _make_window(qapp, CAMERA_PHOTOS)

    window._run_command("sort date")

    assert window._sort_key == "date"
    # Más reciente primero (a.jpg).
    assert window._visible_keys_cache == ["a.jpg", "b.jpg", "c.jpg"]


def test_sort_camera_command_reorders_the_grid(qapp):
    window = _make_window(qapp, CAMERA_PHOTOS)
    assert window._sort_key == "date"

    window._run_command("sort camera")

    assert window._sort_key == "camera"
    # Canon EOS R5 agrupa primero (orden alfabético).
    assert window._visible_keys_cache == ["a.jpg", "c.jpg", "b.jpg"]


def test_sort_location_command_reorders_the_grid_by_place(qapp):
    window = _make_window(qapp, LOCATION_PHOTOS)
    assert window._sort_key == "date"

    window._run_command("sort location")

    assert window._sort_key == "location"
    # País → región → ciudad ascendente: «españa» antes que «francia» y, al
    # final, el grupo propio «Sin ubicación».
    assert window._visible_keys_cache == ["barcelona.jpg", "paris.jpg", "sin-lugar.jpg"]

    # La primera cabecera de grupo lleva el rótulo «Ciudad, País».
    assert window.grid.item(0).text() == "Barcelona, España"


def test_sort_unknown_key_reports_the_documented_usage(qapp):
    window = _make_window(qapp, CAMERA_PHOTOS)

    window._run_command("sort inventado")

    assert "date | camera | location" in window.status.message_label.text()


def test_help_command_toggles_the_overlay(qapp):
    window = _make_window(qapp)

    window._run_command("help")
    assert window.help_overlay.isVisible()

    window._run_command("help")
    assert not window.help_overlay.isVisible()


def test_unknown_command_shows_an_error_without_raising(qapp):
    window = _make_window(qapp)

    window._run_command("inventado")

    assert window.status.error_text
    assert "inventado" in window.status.message_label.text()


def test_empty_command_does_nothing(qapp):
    window = _make_window(qapp)

    window._run_command("   ")

    assert window.status.error_text is None
    assert not window.status.prompt_active


# ----------------------------------------------------------------------
# Tabla explícita de atajos documentados
# ----------------------------------------------------------------------
def _char_key(ch: str) -> int:
    if ch.isalpha():
        return ord(ch.upper())
    return ord(ch)


def _type(prompt, text: str) -> None:
    """Escribe ``text`` en un ``PromptEdit`` con eventos de teclado."""
    for ch in text:
        prompt.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, _char_key(ch), Qt.KeyboardModifier.NoModifier, ch
            )
        )


@dataclass(frozen=True)
class Shortcut:
    """Entrada de la tabla: una tecla documentada y cómo comprobarla."""

    tecla: str
    contexto: str  # "window" | "grid" | "viewer" | "prompt"
    key: int
    modifiers: int = 0
    repeticiones: int = 1
    setup: Callable[[PhotoWindow, object], None] | None = None
    texto: str = ""  # texto que se escribe antes de la tecla (contexto prompt)
    verify: Callable[[PhotoWindow, object], None] | None = None


def _press(window: PhotoWindow, spec: Shortcut, qapp) -> None:
    """Despacha el evento del atajo al widget que lo atiende de verdad."""
    event = QKeyEvent(
        QEvent.Type.KeyPress, spec.key, Qt.KeyboardModifier(spec.modifiers)
    )
    if spec.contexto == "prompt":
        _type(window.status.prompt, spec.texto)
        window.status.prompt.keyPressEvent(event)
        qapp.processEvents()
        return
    if spec.contexto == "panel":
        window.panel.scroll.keyPressEvent(event)
    elif spec.contexto == "window":
        window.keyPressEvent(event)
    elif spec.contexto == "grid":
        window.grid.keyPressEvent(event)
    elif spec.contexto == "viewer":
        window.viewer.canvas.keyPressEvent(event)
    else:  # pragma: no cover - contexto no contemplado
        raise AssertionError(f"contexto desconocido: {spec.contexto}")
    qapp.processEvents()


# - Preparaciones ------------------------------------------------------


def _setup_zoomed_viewer(w: PhotoWindow, q) -> None:
    """Visor abierto con una imagen sintética más grande que el lienzo."""
    w._open_viewer()
    pixmap = QPixmap(2000, 1500)
    pixmap.fill()
    w.viewer.canvas.set_image(pixmap)
    w.viewer.canvas.toggle_zoom()
    q.processEvents()


def _setup_unzoomed_viewer_with_image(w: PhotoWindow, q) -> None:
    w._open_viewer()
    pixmap = QPixmap(2000, 1500)
    pixmap.fill()
    w.viewer.canvas.set_image(pixmap)
    q.processEvents()


def _setup_viewer_on_second(w: PhotoWindow, q) -> None:
    w._open_viewer()
    w._select_key(w._visible_keys_cache[1])
    q.processEvents()


def _setup_viewer_on_last(w: PhotoWindow, q) -> None:
    w._select_key(w._visible_keys_cache[-1])
    w._open_viewer()
    q.processEvents()


def _setup_may_group_viewer(w: PhotoWindow, q) -> None:
    target = next(
        key
        for key in w._visible_keys_cache
        if w.photos[key].taken.startswith("2024-05")
    )
    w._select_key(target)
    w._open_viewer()
    q.processEvents()


def _setup_search_committed(w: PhotoWindow, q) -> None:
    w._commit_search("junio")
    q.processEvents()


def _setup_grid_last(w: PhotoWindow, q) -> None:
    w._select_key(w._visible_keys_cache[-1])
    q.processEvents()


def _setup_grid_third(w: PhotoWindow, q) -> None:
    w._select_key(w._visible_keys_cache[2])
    q.processEvents()


def _setup_may_group_grid(w: PhotoWindow, q) -> None:
    target = next(
        key
        for key in w._visible_keys_cache
        if w.photos[key].taken.startswith("2024-05")
    )
    w._select_key(target)
    q.processEvents()


def _setup_prompt_command(w: PhotoWindow, q) -> None:
    w.status.open_command()
    q.processEvents()


def _setup_prompt_history(w: PhotoWindow, q) -> None:
    """Alimenta el historial ejecutando ``sort camera`` por el prompt."""
    _setup_prompt_command(w, q)
    _type(w.status.prompt, "sort camera")
    w.status.prompt.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return, Qt.KeyboardModifier.NoModifier)
    )
    q.processEvents()
    _setup_prompt_command(w, q)


def _setup_prompt_tab(w: PhotoWindow, q) -> None:
    _setup_prompt_command(w, q)
    _type(w.status.prompt, "so")


def _setup_prompt_help_text(w: PhotoWindow, q) -> None:
    _setup_prompt_command(w, q)
    _type(w.status.prompt, "help")


def _setup_prompt_search_text(w: PhotoWindow, q) -> None:
    w.status.open_search("")
    q.processEvents()
    _type(w.status.prompt, "junio")


def _setup_prompt_camera_text(w: PhotoWindow, q) -> None:
    _setup_prompt_command(w, q)
    _type(w.status.prompt, "sort camera")


# - Ayudas de verificación ----------------------------------------------


def _v_map_without_gps(w: PhotoWindow, q) -> None:
    """``g m`` sobre una foto sin GPS: aviso en la barra, sin navegador."""
    assert "GPS" in w.status.message_label.text()


def _current_name(w: PhotoWindow) -> str:
    photo = w._current_photo()
    assert photo is not None
    return photo.name


def _current_index(w: PhotoWindow) -> int:
    current = w.grid.currentItem()
    return w._visible_keys_cache.index(str(current.data(PHOTO_ROLE)))


def _may_visible(w: PhotoWindow) -> list[str]:
    return [
        key
        for key in w._visible_keys_cache
        if w.photos[key].taken.startswith("2024-05")
    ]


def _v_help_open(w: PhotoWindow, q) -> None:
    assert w.help_overlay.isVisible()


def _v_command_open(w: PhotoWindow, q) -> None:
    assert w.status.prompt_active
    assert w.status.prompt_mode == "command"


def _v_search_open(w: PhotoWindow, q) -> None:
    assert w.status.prompt_active
    assert w.status.prompt_mode == "search"


def _v_panel_visible_with_focus(w: PhotoWindow, q) -> None:
    assert w.panel_visible
    assert w.panel.isVisibleTo(w.content)
    assert q.focusWidget() is w.panel.scroll


def _v_panel_visible_only(w: PhotoWindow, q) -> None:
    assert w.panel_visible
    assert w.panel.isVisibleTo(w.content)


def _v_grid_focus(w: PhotoWindow, q) -> None:
    assert q.focusWidget() is w.grid


def _v_viewer_open(w: PhotoWindow, q) -> None:
    assert w._viewer_visible
    assert w.viewer.isVisibleTo(w.content)


def _v_viewer_next(w: PhotoWindow, q) -> None:
    assert _current_name(w) == w._visible_keys_cache[1]


def _v_viewer_previous(w: PhotoWindow, q) -> None:
    assert _current_name(w) == w._visible_keys_cache[0]


def _v_grid_first(w: PhotoWindow, q) -> None:
    assert _current_index(w) == 0


def _v_grid_last(w: PhotoWindow, q) -> None:
    assert _current_index(w) == len(w._visible_keys_cache) - 1


def _v_grid_moved_forward(w: PhotoWindow, q) -> None:
    assert _current_index(w) > 0


def _v_grid_moved_backward(w: PhotoWindow, q) -> None:
    assert _current_index(w) < len(w._visible_keys_cache) - 1


def _v_grid_left_step(w: PhotoWindow, q) -> None:
    # Preparado sobre la tercera celda: ``h`` retrocede una celda.
    assert _current_index(w) == 1


def _v_grid_right_step(w: PhotoWindow, q) -> None:
    assert _current_index(w) == 1


def _v_grid_group_first(w: PhotoWindow, q) -> None:
    assert _current_name(w) == _may_visible(w)[0]


def _v_grid_group_last(w: PhotoWindow, q) -> None:
    assert _current_name(w) == _may_visible(w)[-1]


def _v_favorite_on(w: PhotoWindow, q) -> None:
    photo = w._current_photo()
    assert photo.favorite is True
    current = w.grid.currentItem()
    assert bool(current.data(FAVORITE_ROLE)) is True


def _v_quit_hint_message(w: PhotoWindow, q) -> None:
    assert "Ctrl+q" in w.status.message_label.text()


def _v_zoom_in(w: PhotoWindow, q) -> None:
    assert w.viewer.canvas.is_zoomed()


def _v_zoom_toggled_back(w: PhotoWindow, q) -> None:
    assert not w.viewer.canvas.is_zoomed()


def _v_zoom_out(w: PhotoWindow, q) -> None:
    # Sin API pública del nivel de zoom: se comprueba el estado interno del
    # lienzo (el zoom baja del 100 % y sigue por encima del ajuste).
    canvas = w.viewer.canvas
    assert canvas.is_zoomed()
    assert canvas._zoom is not None and canvas._zoom < 1.0


def _v_pan_left(w: PhotoWindow, q) -> None:
    # El paneo no expone API pública: se comprueba el offset del lienzo.
    assert w.viewer.canvas._pan.x() < 0


def _v_pan_right(w: PhotoWindow, q) -> None:
    assert w.viewer.canvas._pan.x() > 0


def _v_pan_up(w: PhotoWindow, q) -> None:
    assert w.viewer.canvas._pan.y() < 0


def _v_pan_down(w: PhotoWindow, q) -> None:
    assert w.viewer.canvas._pan.y() > 0


def _v_escape_resets_zoom_only(w: PhotoWindow, q) -> None:
    _v_viewer_open(w, q)
    assert not w.viewer.canvas.is_zoomed()


def _v_escape_closes_viewer(w: PhotoWindow, q) -> None:
    assert not w._viewer_visible
    _v_grid_focus(w, q)


def _v_escape_clears_search(w: PhotoWindow, q) -> None:
    assert not w.status.prompt_active
    assert w.search_text == ""
    assert w._visible_count() == len(w.photos)


def _v_prompt_close_without_execution(w: PhotoWindow, q) -> None:
    assert not w.status.prompt_active
    assert w._sort_key == "date"
    assert not w.help_overlay.isVisible()


def _v_prompt_enter_runs_help(w: PhotoWindow, q) -> None:
    _v_help_open(w, q)
    assert not w.status.prompt_active


def _v_prompt_search_committed(w: PhotoWindow, q) -> None:
    assert w.search_text == "junio"
    assert not w.status.prompt_active
    assert w._visible_count() == 5


def _v_prompt_history_up(w: PhotoWindow, q) -> None:
    assert w.status.prompt.text() == "sort camera"


def _v_prompt_history_down(w: PhotoWindow, q) -> None:
    assert w.status.prompt.text() == ""


def _v_prompt_tab_completes(w: PhotoWindow, q) -> None:
    assert w.status.prompt.text() == "sort date"


def _setup_pending_g_window(w: PhotoWindow, q) -> None:
    """Deja un prefijo ``g`` pendiente desde la ventana-fallback."""
    w.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key_G, Qt.KeyboardModifier.NoModifier)
    )
    q.processEvents()


def _setup_pending_g_viewer(w: PhotoWindow, q) -> None:
    """Abre el visor y deja un prefijo ``g`` pendiente en su lienzo."""
    w._open_viewer()
    q.processEvents()
    w.viewer.canvas.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key_G, Qt.KeyboardModifier.NoModifier)
    )
    q.processEvents()


# - La tabla ------------------------------------------------------------

GLOBAL_SHORTCUTS = (
    Shortcut("?", "window", Qt.Key_Question, verify=_v_help_open),
    Shortcut("F1", "window", Qt.Key_F1, verify=_v_help_open),
    Shortcut(":", "window", Qt.Key_Colon, verify=_v_command_open),
    Shortcut("/", "window", Qt.Key_Slash, verify=_v_search_open),
    Shortcut(
        "i", "window", Qt.Key_I, verify=_v_panel_visible_only
    ),
    # ``g m`` documentado como global: prefijo ``g`` + ``m``. La foto del
    # fixture no tiene GPS, así que la conducta real es el aviso de la barra.
    Shortcut(
        "m", "window", Qt.Key_M, setup=_setup_pending_g_window,
        verify=_v_map_without_gps,
    ),
    Shortcut(
        "Shift+Tab",
        "panel",
        Qt.Key_Backtab,
        setup=lambda w, q: (
            w.toggle_panel(),
            w.panel.focus_scroll(),
            q.processEvents(),
        ),
        verify=_v_grid_focus,
    ),
    # Esc: cierra overlay → restablece zoom → retrocede. Se ejercitan los tres
    # pasos con ventanas frescas.
    Shortcut(
        "Esc",
        "window",
        Qt.Key_Escape,
        setup=_setup_zoomed_viewer,
        verify=_v_escape_resets_zoom_only,
    ),
    Shortcut(
        "Esc",
        "window",
        Qt.Key_Escape,
        setup=lambda w, q: (w._open_viewer(), q.processEvents()),
        verify=_v_escape_closes_viewer,
    ),
    Shortcut(
        "Esc",
        "window",
        Qt.Key_Escape,
        setup=_setup_search_committed,
        verify=_v_escape_clears_search,
    ),
)

GRID_SHORTCUTS = (
    Shortcut("l", "grid", Qt.Key_L, verify=_v_grid_moved_forward),
    Shortcut(
        "h", "grid", Qt.Key_H, setup=_setup_grid_third, verify=_v_grid_left_step
    ),
    Shortcut("j", "grid", Qt.Key_J, verify=_v_grid_moved_forward),
    Shortcut(
        "k", "grid", Qt.Key_K, setup=_setup_grid_last, verify=_v_grid_moved_backward
    ),
    Shortcut("←", "grid", Qt.Key_Left, setup=_setup_grid_third, verify=_v_grid_left_step),
    Shortcut("→", "grid", Qt.Key_Right, verify=_v_grid_right_step),
    Shortcut("↓", "grid", Qt.Key_Down, verify=_v_grid_moved_forward),
    Shortcut("↑", "grid", Qt.Key_Up, setup=_setup_grid_last, verify=_v_grid_moved_backward),
    Shortcut(
        "0",
        "grid",
        Qt.Key_0,
        setup=_setup_may_group_grid,
        verify=_v_grid_group_first,
    ),
    Shortcut(
        "$",
        "grid",
        Qt.Key_Dollar,
        setup=_setup_may_group_grid,
        verify=_v_grid_group_last,
    ),
    # Secuencia documentada «g g»: el token documentado es ``g``.
    Shortcut(
        "g",
        "grid",
        Qt.Key_G,
        repeticiones=2,
        setup=_setup_grid_last,
        verify=_v_grid_first,
    ),
    Shortcut("G", "grid", Qt.Key_G, Qt.KeyboardModifier.ShiftModifier, verify=_v_grid_last),
    Shortcut(
        "Home", "grid", Qt.Key_Home, setup=_setup_grid_last, verify=_v_grid_first
    ),
    Shortcut("End", "grid", Qt.Key_End, verify=_v_grid_last),
    Shortcut("PgDn", "grid", Qt.Key_PageDown, verify=_v_grid_moved_forward),
    Shortcut(
        "PgUp", "grid", Qt.Key_PageUp, setup=_setup_grid_last, verify=_v_grid_moved_backward
    ),
    Shortcut(
        "Ctrl+d",
        "grid",
        Qt.Key_D,
        Qt.KeyboardModifier(CTRL_MASK),
        verify=_v_grid_moved_forward,
    ),
    Shortcut(
        "Ctrl+u",
        "grid",
        Qt.Key_U,
        Qt.KeyboardModifier(CTRL_MASK),
        setup=_setup_grid_last,
        verify=_v_grid_moved_backward,
    ),
    Shortcut("Enter", "grid", Qt.Key_Return, verify=_v_viewer_open),
    Shortcut("o", "grid", Qt.Key_O, verify=_v_viewer_open),
    Shortcut("f", "grid", Qt.Key_F, verify=_v_favorite_on),
    Shortcut("q", "grid", Qt.Key_Q, verify=_v_quit_hint_message),
    Shortcut(
        "Tab", "grid", Qt.Key_Tab, verify=_v_panel_visible_with_focus
    ),
)

VIEWER_SHORTCUTS = (
    Shortcut(
        "←", "viewer", Qt.Key_Left, setup=_setup_viewer_on_second, verify=_v_viewer_previous
    ),
    Shortcut("→", "viewer", Qt.Key_Right, verify=_v_viewer_next),
    Shortcut(
        "↑", "viewer", Qt.Key_Up, setup=_setup_viewer_on_second, verify=_v_viewer_previous
    ),
    Shortcut("↓", "viewer", Qt.Key_Down, verify=_v_viewer_next),
    Shortcut(
        "g",
        "viewer",
        Qt.Key_G,
        repeticiones=2,
        setup=_setup_viewer_on_last,
        verify=_v_viewer_previous,
    ),
    Shortcut(
        "G",
        "viewer",
        Qt.Key_G,
        Qt.KeyboardModifier.ShiftModifier,
        verify=_v_grid_last,
    ),
    Shortcut("Home", "viewer", Qt.Key_Home, verify=_v_grid_first),
    Shortcut(
        "End", "viewer", Qt.Key_End, setup=lambda w, q: None, verify=_v_grid_last
    ),
    Shortcut(
        "0",
        "viewer",
        Qt.Key_0,
        setup=_setup_may_group_viewer,
        verify=_v_grid_group_first,
    ),
    Shortcut(
        "$",
        "viewer",
        Qt.Key_Dollar,
        setup=_setup_may_group_viewer,
        verify=_v_grid_group_last,
    ),
    # En la app real el paneo de ``H J K L`` lo atiende la rejilla mientras
    # tiene ``viewer_keys``, no el lienzo directamente.
    Shortcut("H", "grid", Qt.Key_H, Qt.KeyboardModifier.ShiftModifier, setup=_setup_zoomed_viewer, verify=_v_pan_left),
    Shortcut("L", "grid", Qt.Key_L, Qt.KeyboardModifier.ShiftModifier, setup=_setup_zoomed_viewer, verify=_v_pan_right),
    Shortcut("J", "grid", Qt.Key_J, Qt.KeyboardModifier.ShiftModifier, setup=_setup_zoomed_viewer, verify=_v_pan_down),
    Shortcut("K", "grid", Qt.Key_K, Qt.KeyboardModifier.ShiftModifier, setup=_setup_zoomed_viewer, verify=_v_pan_up),
    Shortcut("+", "viewer", Qt.Key_Plus, setup=_setup_unzoomed_viewer_with_image, verify=_v_zoom_in),
    Shortcut(
        "-",
        "viewer",
        Qt.Key_Minus,
        setup=_setup_zoomed_viewer,
        verify=_v_zoom_out,
    ),
    Shortcut(
        "z",
        "viewer",
        Qt.Key_Z,
        repeticiones=2,
        setup=_setup_unzoomed_viewer_with_image,
        verify=_v_zoom_toggled_back,
    ),
    Shortcut("q", "viewer", Qt.Key_Q, verify=_v_escape_closes_viewer),
    Shortcut(
        "Esc",
        "viewer",
        Qt.Key_Escape,
        setup=_setup_zoomed_viewer,
        verify=_v_escape_resets_zoom_only,
    ),
    Shortcut("i", "viewer", Qt.Key_I, verify=_v_panel_visible_only),
    Shortcut(
        "f",
        "viewer",
        Qt.Key_F,
        setup=lambda w, q: None,
        verify=_v_favorite_on,
    ),
    Shortcut(
        "m", "viewer", Qt.Key_M, setup=_setup_pending_g_viewer,
        verify=_v_map_without_gps,
    ),
    Shortcut("?", "viewer", Qt.Key_Question, verify=_v_help_open),
    Shortcut(":", "viewer", Qt.Key_Colon, verify=_v_command_open),
    Shortcut("/", "viewer", Qt.Key_Slash, verify=_v_search_open),
    Shortcut(
        "Tab", "viewer", Qt.Key_Tab, verify=_v_panel_visible_with_focus
    ),
)

PROMPT_SHORTCUTS = (
    Shortcut(
        "Tab",
        "prompt",
        Qt.Key_Tab,
        setup=_setup_prompt_tab,
        verify=_v_prompt_tab_completes,
    ),
    Shortcut(
        "↑",
        "prompt",
        Qt.Key_Up,
        setup=_setup_prompt_history,
        verify=_v_prompt_history_up,
    ),
    Shortcut(
        "↓",
        "prompt",
        Qt.Key_Down,
        setup=lambda w, q: (
            _setup_prompt_history(w, q),
            w.status.prompt.keyPressEvent(
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Up, Qt.KeyboardModifier.NoModifier)
            ),
        ),
        verify=_v_prompt_history_down,
    ),
    Shortcut(
        "Enter",
        "prompt",
        Qt.Key_Return,
        setup=_setup_prompt_help_text,
        verify=_v_prompt_enter_runs_help,
    ),
    Shortcut(
        "Enter",
        "prompt",
        Qt.Key_Return,
        setup=_setup_prompt_search_text,
        verify=_v_prompt_search_committed,
    ),
    Shortcut(
        "Esc",
        "prompt",
        Qt.Key_Escape,
        setup=_setup_prompt_camera_text,
        verify=_v_prompt_close_without_execution,
    ),
)

SHORTCUTS = GLOBAL_SHORTCUTS + GRID_SHORTCUTS + VIEWER_SHORTCUTS + PROMPT_SHORTCUTS


@pytest.mark.parametrize(
    ("spec",),
    [(spec,) for spec in SHORTCUTS],
    ids=[f"{index}-{spec.tecla}-{spec.contexto}" for index, spec in enumerate(SHORTCUTS)],
)
def test_documented_shortcuts_have_real_behavior(qapp, spec):
    """Cada entrada de la tabla compone su tecla, preparación y efecto."""
    window = _make_window(qapp)
    if spec.setup is not None:
        spec.setup(window, qapp)
    for _ in range(spec.repeticiones):
        _press(window, spec, qapp)
    assert spec.verify is not None, f"falta verificación para {spec.tecla}"
    try:
        spec.verify(window, qapp)
    except AssertionError as exc:
        pytest.fail(
            f"atajo documentado {spec.tecla!r} (contexto {spec.contexto}): {exc}",
            pytrace=False,
        )


# Tokens documentados que no pasan por el dispatch de teclas del visor:
# salir lo porta una ``QAction`` con ``QKeySequence.Quit`` (Ctrl+q/⌘Q), y
# «flechas» es la manera de escribir la referencia a las flechas del cursor.
_KEY_ALIASES = {"flechas": ("←", "→", "↑", "↓")}
_KEY_EXCEPTIONS = ("Ctrl+q", "⌘Q")


def test_documented_keys_are_covered_by_the_behavior_table():
    """Tecla documentada sin entrada de comportamiento → el test falla."""
    table = {spec.tecla for spec in SHORTCUTS}
    faltan: list[str] = []
    for token in _documented_key_tokens():
        if token in table or token in _KEY_EXCEPTIONS:
            continue
        resueltos = _KEY_ALIASES.get(token, (token,))
        pendientes = [t for t in resueltos if t not in table]
        if pendientes:
            faltan.append(f"{token!r} → falta {pendientes}")
    assert not faltan, (
        f"teclas documentadas sin entrada de comportamiento: {faltan}; tabla: "
        f"{sorted(table)}"
    )
