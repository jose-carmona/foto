"""Tabla de teclas unificada con contexto explícito (P1.5).

Sin Qt: los códigos de tecla y de modificador se duplican como enteros
(patrones de ``presentation.py``), ``tests/test_keybindings.py`` los prueba
sin la capa Qt y ``tests/test_ui_shortcut_modifiers.py`` los ancla a los
valores reales de Qt. ``window.py`` solo contiene los adaptadores que extraen
(key, shift, ctrl, cmd) del evento de teclado.

La misma tecla significa cosas distintas según el modo/widget, de modo que la
tabla es **con contexto explícito**: ``(contexto, tecla, modificadores) →
acción``. Los contextos son los cuatro traductores que existían dispersos en
``window.py``: la rejilla normal (``grid``), la rejilla con la fila del visor
abierta (``grid_viewer``, ``PhotoGrid.viewer_keys``), el panel de metadatos
(``panel``), el lienzo del visor (``viewer``) y la ventana-fallback
(``window``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from foto.ui.presentation import is_group_end, is_group_start

# ---------------------------------------------------------------------------
# Contextos
# ---------------------------------------------------------------------------
GRID = "grid"
GRID_VIEWER = "grid_viewer"
PANEL = "panel"
VIEWER = "viewer"
WINDOW = "window"

CONTEXTS = (GRID, GRID_VIEWER, PANEL, VIEWER, WINDOW)

# ---------------------------------------------------------------------------
# Teclas
# ---------------------------------------------------------------------------
# Códigos tal como los entrega QKeyEvent.key(), duplicados como enteros para
# no importar Qt desde la capa pura. Los visibles coinciden con ASCII.
# Qt.Key_Escape = 0x01000000, Key_Tab = 0x01000001, Key_Backtab = 0x01000002,
# Key_Return = 0x01000004, Key_Enter = 0x01000005, Key_Home = 0x01000010,
# Key_End = 0x01000011, Key_Left = 0x01000012, Key_Up = 0x01000013,
# Key_Right = 0x01000014, Key_Down = 0x01000015, Key_PageUp = 0x01000016,
# Key_PageDown = 0x01000017, Key_F1 = 0x01000030 (Qt::Key en qnamespace.h).
KEY_ESCAPE = 0x01000000
KEY_TAB = 0x01000001
KEY_BACKTAB = 0x01000002
KEY_RETURN = 0x01000004
KEY_ENTER = 0x01000005
KEY_F1 = 0x01000030
KEY_HOME = 0x01000010
KEY_END = 0x01000011
KEY_LEFT = 0x01000012
KEY_UP = 0x01000013
KEY_RIGHT = 0x01000014
KEY_DOWN = 0x01000015
KEY_PAGEUP = 0x01000016
KEY_PAGEDOWN = 0x01000017

# Símbolos: Qt.Key_Dollar = 0x24, Key_Plus = 0x2b, Key_Minus = 0x2d,
# Key_Slash = 0x2f, Key_Colon = 0x3a, Key_Equal = 0x3d, Key_Question = 0x3f.
KEY_DOLLAR = 0x24
KEY_PLUS = 0x2B
KEY_MINUS = 0x2D
KEY_SLASH = 0x2F
KEY_COLON = 0x3A
KEY_EQUAL = 0x3D
KEY_QUESTION = 0x3F

# Dígitos y letras coinciden con ASCII (Key_0 = 0x30, Key_A = 0x41).
KEY_0, KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9 = range(
    0x30, 0x3A
)
KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I, KEY_J = range(
    0x41, 0x4B
)
KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R, KEY_S, KEY_T = range(
    0x4B, 0x55
)
KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z = range(0x55, 0x5B)

# ---------------------------------------------------------------------------
# Modificadores
# ---------------------------------------------------------------------------
# Qt::KeyboardModifiers: Shift = 0x02000000, Control = 0x04000000,
# Meta = 0x10000000.
SHIFT_MODIFIER = 0x02000000
CONTROL_MODIFIER = 0x04000000
META_MODIFIER = 0x10000000

# Qt remapea los modificadores en macOS: ⌘ Command llega como ControlModifier
# y la tecla Ctrl física como MetaModifier (documentado en Qt::KeyboardModifier).
# La media página debe ser Ctrl+d/Ctrl+u con la tecla Ctrl física en todas las
# plataformas (docs/diseno-ui.md), así que la máscara depende del sistema.
CTRL_MASK = META_MODIFIER if sys.platform == "darwin" else CONTROL_MODIFIER


# ---------------------------------------------------------------------------
# Adaptador de evento → modificadores
# ---------------------------------------------------------------------------
def event_modifiers(event) -> tuple[bool, bool, bool]:
    """(shift, ctrl, cmd): ctrl es la Ctrl física; cmd es ⌘/Ctrl no física."""
    # La tabla guarda los códigos como enteros, se evalúan sobre el valor
    # numérico de los flags (Qt no mezcla KeyboardModifier con int).
    flag = event.modifiers().value
    shift = bool(flag & SHIFT_MODIFIER)
    ctrl = bool(flag & CTRL_MASK)
    any_ctrl = bool(flag & (CONTROL_MODIFIER | META_MODIFIER))
    return shift, ctrl, any_ctrl and not ctrl


# ---------------------------------------------------------------------------
# Tabla
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Binding:
    """Una fila de la tabla: (contexto, tecla, modificadores) → acción."""

    context: str  # uno de los contextos anteriores
    key: int  # código de tecla (entero, sin Qt)
    action: str  # nombre de acción del vocabulario unificado
    shift: bool | None = None  # None = indiferente; True/False = exigido
    ctrl: bool | None = None  # Ctrl física (según CTRL_MASK)
    cmd: bool | None = None  # Control/⌘ que no es la Ctrl física


def _build_table() -> tuple[Binding, ...]:
    rows: list[Binding] = []

    def bind(
        context: str,
        key: int,
        action: str,
        *,
        shift: bool | None = None,
        ctrl: bool | None = None,
        cmd: bool | None = None,
    ) -> None:
        rows.append(Binding(context, key, action, shift, ctrl, cmd))

    def bind_group_keys(
        context: str,
        *,
        start_action: str,
        end_action: str,
        ctrl: bool | None = None,
        cmd: bool | None = None,
    ) -> None:
        """Filas de borde de grupo, generadas con las teclas de presentation.

        ``is_group_start``/``is_group_end`` (``KEY_0``, ``KEY_DOLLAR`` y las
        variantes ``Shift+4``/``Shift+5``) describen las teclas de fila y
        aquí solo se vuelven filas de la tabla, una por combinación posible.
        """
        for key in (KEY_0, KEY_DOLLAR, KEY_4, KEY_5):
            for shift in (False, True):
                if is_group_start(key, shift):
                    bind(
                        context, key, start_action, shift=shift, ctrl=ctrl, cmd=cmd
                    )
                if is_group_end(key, shift):
                    bind(
                        context, key, end_action, shift=shift, ctrl=ctrl, cmd=cmd
                    )

    def bind_common(context: str) -> None:
        """Filas de navegación compartidas por rejilla, fila del visor y
        ventana-fallback.

        Es la misma vista en dos geometrías (mismo ``PhotoGrid``), y la
        ventana-fallback reutiliza las mismas filas desde R6, de modo que
        reaccionen las filas completas si ninguna widget-hija se queda la
        tecla. Los modificadores siguen la regla única del módulo (§7.3/R11):
        cualquier combinación con Ctrl/⌘ no declarada anula la tecla, así que
        aquí no se repite ``ctrl=False, cmd=False`` fila a fila; solo el
        emparejamiento declara la excepción: la media página exige la Ctrl
        física.
        """
        for key, action, shift in (
            (KEY_J, "j", None),
            (KEY_K, "k", None),
            (KEY_H, "h", None),
            (KEY_L, "l", None),
            (KEY_UP, "k", None),
            (KEY_DOWN, "j", None),
            (KEY_LEFT, "h", None),
            (KEY_RIGHT, "l", None),
            (KEY_HOME, "home", None),
            (KEY_END, "end", None),
        ):
            bind(context, key, action, shift=shift)
        bind_group_keys(context, start_action="0", end_action="$")
        for key in (KEY_RETURN, KEY_ENTER, KEY_O):
            bind(context, key, "open_viewer")
        bind(context, KEY_G, "g", shift=False)
        bind(context, KEY_G, "end", shift=True)
        for key, action in ((KEY_PAGEDOWN, "pagedown"), (KEY_PAGEUP, "pageup")):
            bind(context, key, action)
        # Solo la Ctrl física dispara la media página; ⌘d/⌘u no cuentan.
        bind(context, KEY_D, "halfdown", ctrl=True, cmd=False)
        bind(context, KEY_U, "halfup", ctrl=True, cmd=False)
        # Qt usa Tab para la cadena de foco; aquí las filas quedan como fuente
        # de verdad y cada adaptador decide cómo servirla.
        bind(context, KEY_TAB, "focus_panel")
        bind(context, KEY_BACKTAB, "focus_panel")
        for key, action in (
            (KEY_COLON, "command"),
            (KEY_SLASH, "search"),
            (KEY_QUESTION, "help"),
            (KEY_F1, "help"),
            (KEY_I, "panel"),
            (KEY_F, "favorite"),
            (KEY_Q, "close"),
            (KEY_ESCAPE, "escape"),
        ):
            bind(context, key, action)
        # Los dígitos 1–5 quedan fuera de servicio (R1): ninguna fila los
        # interpreta y caen a super(), sin efecto visible.

    def bind_panes_and_zoom(context: str) -> None:
        """Paneo y zoom de la foto grande (fila del visor y panel)."""
        for key, action in (
            (KEY_H, "pan_left"),
            (KEY_J, "pan_down"),
            (KEY_K, "pan_up"),
            (KEY_L, "pan_right"),
        ):
            bind(context, key, action, shift=True)
        for key, action in ((KEY_PLUS, "zoom_in"), (KEY_EQUAL, "zoom_in")):
            bind(context, key, action)
        bind(context, KEY_MINUS, "zoom_out")
        bind(context, KEY_Z, "toggle_zoom", shift=False)

    # Rejilla: sin visor y con la fila del visor abierta (viewer_keys).
    bind_common(GRID)
    bind_common(GRID_VIEWER)
    bind_panes_and_zoom(GRID_VIEWER)

    # Panel de metadatos: scroll local más acciones de rejilla. Desde la
    # fase 3 comparte con la rejilla los bordes de grupo (R2), exige ``z``
    # sin shift (R4) y ``Shift+G`` emite ``end`` (R3).
    for key in (KEY_TAB, KEY_BACKTAB):
        bind(PANEL, key, "focus_back")
    bind(PANEL, KEY_ESCAPE, "escape")
    for key, action in (
        (KEY_UP, "scroll_up"),
        (KEY_DOWN, "scroll_down"),
        (KEY_PAGEUP, "scroll_page_up"),
        (KEY_PAGEDOWN, "scroll_page_down"),
        (KEY_HOME, "scroll_top"),
        (KEY_END, "scroll_end"),
        (KEY_LEFT, "ignore"),
        (KEY_RIGHT, "ignore"),
    ):
        bind(PANEL, key, action)
    for key in (KEY_RETURN, KEY_ENTER, KEY_O):
        bind(PANEL, key, "open_viewer")
    bind_group_keys(PANEL, start_action="0", end_action="$")
    bind_panes_and_zoom(PANEL)
    bind(PANEL, KEY_G, "g", shift=False)
    bind(PANEL, KEY_G, "end", shift=True)
    for key, action in (
        (KEY_J, "j"),
        (KEY_K, "k"),
        (KEY_H, "h"),
        (KEY_L, "l"),
        (KEY_COLON, "command"),
        (KEY_SLASH, "search"),
        (KEY_QUESTION, "help"),
        (KEY_F1, "help"),
        (KEY_I, "panel"),
        (KEY_F, "favorite"),
        (KEY_Q, "close"),
    ):
        bind(PANEL, key, action)

    # Lienzo del visor: sin guardas fila a fila — la regla única del módulo
    # (§7.3/R11) ya anula cualquier tecla con Ctrl/⌘, incluida la fila de la
    # guarda histórica del lienzo.
    def bind_viewer(key: int, action: str, *, shift: bool | None = None) -> None:
        bind(VIEWER, key, action, shift=shift)

    bind_group_keys(
        VIEWER,
        start_action="group_first",
        end_action="group_last",
    )
    bind_viewer(KEY_J, "pan_down", shift=True)
    bind_viewer(KEY_J, "next", shift=False)
    bind_viewer(KEY_K, "pan_up", shift=True)
    bind_viewer(KEY_K, "previous", shift=False)
    bind_viewer(KEY_H, "pan_left", shift=False)
    bind_viewer(KEY_L, "pan_right", shift=False)
    bind_viewer(KEY_UP, "previous")
    bind_viewer(KEY_LEFT, "previous")
    bind_viewer(KEY_DOWN, "next")
    bind_viewer(KEY_RIGHT, "next")
    bind_viewer(KEY_PLUS, "zoom_in")
    bind_viewer(KEY_EQUAL, "zoom_in")
    bind_viewer(KEY_MINUS, "zoom_out")
    bind_viewer(KEY_Z, "toggle_zoom", shift=False)
    bind_viewer(KEY_G, "last", shift=True)
    bind_viewer(KEY_G, "g", shift=False)
    bind_viewer(KEY_HOME, "first")
    bind_viewer(KEY_END, "last")
    bind_viewer(KEY_Q, "close")
    bind_viewer(KEY_ESCAPE, "escape")
    bind_viewer(KEY_I, "panel")
    bind_viewer(KEY_F, "favorite")
    bind_viewer(KEY_QUESTION, "help")
    bind_viewer(KEY_F1, "help")
    bind_viewer(KEY_COLON, "command")
    bind_viewer(KEY_SLASH, "search")
    # R9: el canvas sirve Tab desde event() porque Qt lo consume para la
    # cadena de foco; la fila vive aquí como fuente de verdad del mapeo.
    bind_viewer(KEY_TAB, "focus_panel")
    bind_viewer(KEY_BACKTAB, "focus_panel")

    # Ventana-fallback: reutiliza las mismas filas que la rejilla (R6: antes
    # solo tenía letras y modos globales, y ``G`` con visor abierta llamaba
    # a _viewer_last, una divergencia con la semántica de la rejilla que se
    # unifica — la rejilla manda en navegación).
    bind_common(WINDOW)
    return tuple(rows)


TABLE: tuple[Binding, ...] = _build_table()

# Índice de búsqueda por (contexto, tecla), construido una vez.
_MATCHES: dict[tuple[str, int], list[Binding]] = {}
for _row in TABLE:
    _MATCHES.setdefault((_row.context, _row.key), []).append(_row)
_MATCHES: dict[tuple[str, int], tuple[Binding, ...]] = {
    slot: tuple(rows) for slot, rows in _MATCHES.items()
}


# Teclas que se emparejan con la Ctrl física (la única excepción de §7.3 a
# la regla «cualquier Ctrl/⌘ anula»); ``Ctrl+q`` vive en una ``QAction`` de Qt.
_CONTROL_KEYS = (KEY_D, KEY_U)


def key_action(
    context: str,
    key: int,
    *,
    shift: bool = False,
    ctrl: bool = False,
    cmd: bool = False,
) -> str | None:
    """Traduce (contexto, tecla, modificadores) → acción, o None.

    Regla única de modificadores (§7.3/R11): la única combinación con control
    es ``Ctrl+d``/``Ctrl+u`` —más ``Ctrl+q``, que es una ``QAction`` de Qt—,
    de modo que cualquier tecla que llegue con Ctrl/⌘ pulsados devuelve
    ``None``, salvo ``d``/``u`` cuando la pulsada es la Ctrl física. Una fila
    casa si cada modificador declarado (``True``/``False``) coincide con el
    evento y los ``None`` se ignoran. Si varias filas casan, gana la más
    específica (menos ``None``); el test de la tabla garantiza que no haya
    dos filas del mismo (contexto, tecla) con igual especificidad que
    compartan combinación.
    """
    if (ctrl or cmd) and key not in _CONTROL_KEYS:
        return None
    best: Binding | None = None
    specificity = -1
    for row in _MATCHES.get((context, key), ()):
        if row.shift is not None and row.shift is not shift:
            continue
        if row.ctrl is not None and row.ctrl is not ctrl:
            continue
        if row.cmd is not None and row.cmd is not cmd:
            continue
        specificity_of_row = (
            (row.shift is not None)
            + (row.ctrl is not None)
            + (row.cmd is not None)
        )
        if specificity_of_row > specificity:
            best = row
            specificity = specificity_of_row
    return best.action if best is not None else None
