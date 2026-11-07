"""Suite de la tabla de teclas de la UI, sin PySide6.

Ejercita ``foto.ui.keybindings`` (P1.5) sin importar Qt: la tabla debe cubrir
lo que cada adaptador mapea, no debe tener filas ambiguas y debe seguir
reutilizando las teclas de grupo de ``presentation``. Incluye el test guardián
que impide que el módulo importe Qt (patrón de ``tests/test_presentation.py``).
"""

from __future__ import annotations

import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest

from foto.ui.keybindings import (
    CONTEXTS,
    CONTROL_MODIFIER,
    CTRL_MASK,
    GRID,
    GRID_VIEWER,
    KEY_0,
    KEY_1,
    KEY_2,
    KEY_3,
    KEY_4,
    KEY_5,
    KEY_BACKTAB,
    KEY_COLON,
    KEY_D,
    KEY_DOLLAR,
    KEY_DOWN,
    KEY_END,
    KEY_ENTER,
    KEY_EQUAL,
    KEY_ESCAPE,
    KEY_F,
    KEY_F1,
    KEY_G,
    KEY_H,
    KEY_HOME,
    KEY_I,
    KEY_J,
    KEY_K,
    KEY_L,
    KEY_LEFT,
    KEY_MINUS,
    KEY_O,
    KEY_PAGEDOWN,
    KEY_PAGEUP,
    KEY_PLUS,
    KEY_Q,
    KEY_QUESTION,
    KEY_RETURN,
    KEY_RIGHT,
    KEY_SLASH,
    KEY_TAB,
    KEY_U,
    KEY_UP,
    KEY_Z,
    META_MODIFIER,
    PANEL,
    SHIFT_MODIFIER,
    TABLE,
    VIEWER,
    WINDOW,
    Binding,
    event_modifiers,
    key_action,
)
from foto.ui.presentation import is_group_end, is_group_start

# ----------------------------------------------------------------------
# Cobertura de la tabla por contexto (conducta final, fase 3)
# ----------------------------------------------------------------------
GRID_EXPECTATIONS = (
    (GRID, KEY_J, "", "j"),
    (GRID, KEY_J, "ctrl", None),  # R11: cualquier Ctrl/⌘ anula la tecla
    (GRID, KEY_J, "cmd", None),
    (GRID, KEY_K, "", "k"),
    (GRID, KEY_H, "", "h"),
    (GRID, KEY_L, "", "l"),
    (GRID, KEY_H, "shift", "h"),
    (GRID, KEY_UP, "", "k"),
    (GRID, KEY_DOWN, "", "j"),
    (GRID, KEY_LEFT, "", "h"),
    (GRID, KEY_RIGHT, "", "l"),
    (GRID, KEY_UP, "ctrl", None),
    (GRID, KEY_HOME, "", "home"),
    (GRID, KEY_END, "", "end"),
    (GRID, KEY_END, "cmd", None),
    (GRID, KEY_0, "", "0"),
    (GRID, KEY_0, "shift", None),
    (GRID, KEY_0, "ctrl", None),
    (GRID, KEY_DOLLAR, "", "$"),
    (GRID, KEY_4, "shift", "$"),
    (GRID, KEY_5, "shift", "$"),
    (GRID, KEY_RETURN, "", "open_viewer"),
    (GRID, KEY_ENTER, "", "open_viewer"),
    (GRID, KEY_O, "", "open_viewer"),
    (GRID, KEY_G, "", "g"),
    (GRID, KEY_G, "shift", "end"),
    (GRID, KEY_G, "ctrl", None),
    (GRID, KEY_PAGEDOWN, "", "pagedown"),
    (GRID, KEY_PAGEUP, "", "pageup"),
    (GRID, KEY_D, "ctrl", "halfdown"),
    (GRID, KEY_U, "ctrl", "halfup"),
    (GRID, KEY_D, "", None),
    (GRID, KEY_D, "cmd", None),  # ⌘d/⌘u no cuentan (Ctrl física, R8)
    (GRID, KEY_U, "cmd", None),
    (GRID, KEY_TAB, "", "focus_panel"),
    (GRID, KEY_BACKTAB, "shift", "focus_panel"),
    (GRID, KEY_COLON, "", "command"),
    (GRID, KEY_SLASH, "", "search"),
    (GRID, KEY_QUESTION, "", "help"),
    (GRID, KEY_F1, "", "help"),
    (GRID, KEY_I, "", "panel"),
    (GRID, KEY_F, "", "favorite"),
    (GRID, KEY_Q, "", "close"),
    (GRID, KEY_Q, "cmd", None),
    (GRID, KEY_ESCAPE, "", "escape"),
    # R1: los dígitos 1–5 están fuera de servicio.
    (GRID, KEY_1, "", None),
    (GRID, KEY_2, "", None),
    (GRID, KEY_3, "", None),
    (GRID, KEY_4, "", None),
    (GRID, KEY_5, "", None),
    (GRID, KEY_1, "shift", None),
)

GRID_VIEWER_EXPECTATIONS = (
    (GRID_VIEWER, KEY_H, "shift", "pan_left"),
    (GRID_VIEWER, KEY_J, "shift", "pan_down"),
    (GRID_VIEWER, KEY_K, "shift", "pan_up"),
    (GRID_VIEWER, KEY_L, "shift", "pan_right"),
    # Sin shift caen a las filas de letras (movimiento del cursor).
    (GRID_VIEWER, KEY_H, "", "h"),
    (GRID_VIEWER, KEY_J, "", "j"),
    (GRID_VIEWER, KEY_K, "", "k"),
    (GRID_VIEWER, KEY_L, "", "l"),
    # R11: con Ctrl/⌘, el paneo y las letras se anulan.
    (GRID_VIEWER, KEY_H, "shift ctrl", None),
    (GRID_VIEWER, KEY_J, "shift cmd", None),
    (GRID_VIEWER, KEY_PLUS, "", "zoom_in"),
    (GRID_VIEWER, KEY_PLUS, "shift", "zoom_in"),
    (GRID_VIEWER, KEY_PLUS, "ctrl", None),
    (GRID_VIEWER, KEY_MINUS, "", "zoom_out"),
    (GRID_VIEWER, KEY_MINUS, "cmd", None),
    (GRID_VIEWER, KEY_Z, "", "toggle_zoom"),
    (GRID_VIEWER, KEY_Z, "shift", None),
    (GRID_VIEWER, KEY_Z, "ctrl", None),
    # El resto de filas compartidas no cambia con el visor abierto.
    (GRID_VIEWER, KEY_RETURN, "", "open_viewer"),
    (GRID_VIEWER, KEY_G, "shift", "end"),
    (GRID_VIEWER, KEY_0, "", "0"),
    (GRID_VIEWER, KEY_DOLLAR, "", "$"),
    (GRID_VIEWER, KEY_TAB, "", "focus_panel"),
    (GRID_VIEWER, KEY_ESCAPE, "", "escape"),
)

PANEL_EXPECTATIONS = (
    (PANEL, KEY_TAB, "", "focus_back"),
    (PANEL, KEY_BACKTAB, "shift", "focus_back"),
    (PANEL, KEY_ESCAPE, "", "escape"),
    (PANEL, KEY_UP, "", "scroll_up"),
    (PANEL, KEY_DOWN, "", "scroll_down"),
    (PANEL, KEY_PAGEUP, "", "scroll_page_up"),
    (PANEL, KEY_PAGEDOWN, "", "scroll_page_down"),
    (PANEL, KEY_HOME, "", "scroll_top"),
    (PANEL, KEY_END, "", "scroll_end"),
    (PANEL, KEY_LEFT, "", "ignore"),
    (PANEL, KEY_RIGHT, "", "ignore"),
    (PANEL, KEY_LEFT, "ctrl", None),
    (PANEL, KEY_RETURN, "", "open_viewer"),
    (PANEL, KEY_O, "", "open_viewer"),
    # R2: el panel comparte las filas de borde de grupo con la rejilla.
    (PANEL, KEY_0, "", "0"),
    (PANEL, KEY_DOLLAR, "", "$"),
    (PANEL, KEY_4, "shift", "$"),
    (PANEL, KEY_5, "shift", "$"),
    (PANEL, KEY_PLUS, "", "zoom_in"),
    (PANEL, KEY_MINUS, "", "zoom_out"),
    # R4: el panel exige ``z`` sin shift, igual que rejilla y visor.
    (PANEL, KEY_Z, "", "toggle_zoom"),
    (PANEL, KEY_Z, "shift", None),
    (PANEL, KEY_G, "", "g"),
    # R3: Shift+G del panel emite ``end``, paridad con la rejilla.
    (PANEL, KEY_G, "shift", "end"),
    (PANEL, KEY_J, "", "j"),
    (PANEL, KEY_H, "", "h"),
    (PANEL, KEY_COLON, "", "command"),
    (PANEL, KEY_SLASH, "", "search"),
    (PANEL, KEY_QUESTION, "", "help"),
    (PANEL, KEY_F1, "", "help"),
    (PANEL, KEY_I, "", "panel"),
    (PANEL, KEY_F, "", "favorite"),
    (PANEL, KEY_Q, "", "close"),
    (PANEL, KEY_Q, "ctrl", None),
    # R1: los dígitos del panel quedan fuera de servicio (salvo el ``0``
    # anterior, que ahora llega por las filas de grupo).
    (PANEL, KEY_1, "", None),
    (PANEL, KEY_2, "cmd", None),
)

VIEWER_EXPECTATIONS = (
    (VIEWER, KEY_J, "", "next"),
    (VIEWER, KEY_J, "shift", "pan_down"),
    (VIEWER, KEY_J, "ctrl", None),
    (VIEWER, KEY_J, "cmd", None),
    (VIEWER, KEY_K, "", "previous"),
    (VIEWER, KEY_K, "shift", "pan_up"),
    (VIEWER, KEY_H, "", "pan_left"),
    (VIEWER, KEY_H, "shift", None),
    (VIEWER, KEY_L, "", "pan_right"),
    (VIEWER, KEY_UP, "shift", "previous"),
    (VIEWER, KEY_LEFT, "", "previous"),
    (VIEWER, KEY_DOWN, "", "next"),
    (VIEWER, KEY_RIGHT, "", "next"),
    (VIEWER, KEY_PLUS, "", "zoom_in"),
    (VIEWER, KEY_EQUAL, "", "zoom_in"),
    (VIEWER, KEY_MINUS, "", "zoom_out"),
    (VIEWER, KEY_Z, "", "toggle_zoom"),
    (VIEWER, KEY_Z, "shift", None),
    (VIEWER, KEY_0, "", "group_first"),
    (VIEWER, KEY_0, "shift", None),
    (VIEWER, KEY_DOLLAR, "shift", "group_last"),
    (VIEWER, KEY_4, "shift", "group_last"),
    (VIEWER, KEY_5, "shift", "group_last"),
    (VIEWER, KEY_G, "", "g"),
    (VIEWER, KEY_G, "shift", "last"),
    (VIEWER, KEY_HOME, "", "first"),
    (VIEWER, KEY_END, "", "last"),
    (VIEWER, KEY_Q, "", "close"),
    (VIEWER, KEY_ESCAPE, "", "escape"),
    (VIEWER, KEY_I, "", "panel"),
    (VIEWER, KEY_F, "", "favorite"),
    (VIEWER, KEY_QUESTION, "", "help"),
    (VIEWER, KEY_F1, "", "help"),
    (VIEWER, KEY_COLON, "", "command"),
    (VIEWER, KEY_SLASH, "", "search"),
    # R9: el canvas sirve Tab desde event() porque Qt lo consume para la
    # cadena de foco; la fila vive en la tabla como fuente de verdad.
    (VIEWER, KEY_TAB, "", "focus_panel"),
    (VIEWER, KEY_BACKTAB, "shift", "focus_panel"),
)

WINDOW_EXPECTATIONS = (
    # R6: la ventana-fallback reutiliza las filas de la rejilla.
    (WINDOW, KEY_RETURN, "", "open_viewer"),
    (WINDOW, KEY_O, "", "open_viewer"),
    (WINDOW, KEY_G, "", "g"),
    (WINDOW, KEY_G, "shift", "end"),
    (WINDOW, KEY_G, "ctrl", None),
    (WINDOW, KEY_COLON, "", "command"),
    (WINDOW, KEY_SLASH, "", "search"),
    (WINDOW, KEY_QUESTION, "", "help"),
    (WINDOW, KEY_F1, "", "help"),
    (WINDOW, KEY_I, "cmd", None),
    (WINDOW, KEY_ESCAPE, "", "escape"),
    (WINDOW, KEY_F, "", "favorite"),
    (WINDOW, KEY_Q, "", "close"),
    (WINDOW, KEY_J, "", "j"),
    (WINDOW, KEY_J, "ctrl", None),
    (WINDOW, KEY_H, "", "h"),
    (WINDOW, KEY_0, "", "0"),
    (WINDOW, KEY_DOLLAR, "", "$"),
    (WINDOW, KEY_4, "shift", "$"),
    (WINDOW, KEY_HOME, "", "home"),
    (WINDOW, KEY_END, "", "end"),
    (WINDOW, KEY_PAGEUP, "", "pageup"),
    (WINDOW, KEY_PAGEDOWN, "", "pagedown"),
    (WINDOW, KEY_D, "ctrl", "halfdown"),
    (WINDOW, KEY_U, "ctrl", "halfup"),
    (WINDOW, KEY_TAB, "", "focus_panel"),
    # La ventana no tiene filas de paneo/zoom (esas van con la fila del visor).
    (WINDOW, KEY_PLUS, "", None),
    (WINDOW, KEY_MINUS, "", None),
    (WINDOW, KEY_Z, "", None),
    (WINDOW, KEY_H, "shift", "h"),
)

ALL_EXPECTATIONS = (
    GRID_EXPECTATIONS
    + GRID_VIEWER_EXPECTATIONS
    + PANEL_EXPECTATIONS
    + VIEWER_EXPECTATIONS
    + WINDOW_EXPECTATIONS
)

_ALL_EXPECTATION_IDS = [
    f"{context}-{hex(key)}-{spec or 'sin-mods'}-{action}"
    for (context, key, spec, action) in ALL_EXPECTATIONS
]


def _events(spec: str) -> dict[str, bool]:
    """Convierte ``"shift ctrl"`` en kwargs de modificadores activados."""
    return {name: True for name in spec.split()}


ALL_EXPECTATIONS: tuple[tuple[str, int, dict[str, bool], str | None], ...] = tuple(
    (context, key, _events(spec), action)
    for (context, key, spec, action) in ALL_EXPECTATIONS
)


@pytest.mark.parametrize(
    ("context", "key", "modifiers", "expected"),
    ALL_EXPECTATIONS,
    ids=_ALL_EXPECTATION_IDS,
)
def test_table_action_per_combination(
    context: str, key: int, modifiers: dict[str, bool], expected: str | None
):
    result = key_action(context, key, **modifiers)

    assert result == expected


def test_table_only_has_the_five_contexts():
    assert tuple(sorted({row.context for row in TABLE})) == tuple(sorted(CONTEXTS))


# ----------------------------------------------------------------------
# Política unificada de modificadores (§7.3/R11)
# ----------------------------------------------------------------------
@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("key", (KEY_J, KEY_TAB, KEY_ESCAPE, KEY_PLUS, KEY_G))
def test_control_and_command_annul_undeclared_keys(context, key):
    """Solo Ctrl+d/Ctrl+u (y Ctrl+q, que es QAction) admiten modificadores."""
    assert key_action(context, key, ctrl=True) is None
    assert key_action(context, key, cmd=True) is None


def test_ctrl_d_and_ctrl_u_are_the_only_control_combination():
    # La excepción declarada: media página con la Ctrl física.
    for context in (GRID, GRID_VIEWER, WINDOW):
        assert key_action(context, KEY_D, ctrl=True) == "halfdown"
        assert key_action(context, KEY_U, ctrl=True) == "halfup"
    # ⌘d/⌘u no cuentan (regresión de macOS).
    assert key_action(GRID, KEY_D, cmd=True) is None
    assert key_action(GRID, KEY_U, cmd=True) is None


# ----------------------------------------------------------------------
# Teclas de grupo e integración con presentation
# ----------------------------------------------------------------------
def test_group_keys_mirror_is_group_start():
    for key in (KEY_0, KEY_DOLLAR, KEY_4, KEY_5):
        for shift in (False, True):
            if is_group_start(key, shift):
                assert key_action(GRID, key, shift=shift) == "0"


def test_group_keys_mirror_is_group_end():
    for key in (KEY_0, KEY_DOLLAR, KEY_4, KEY_5):
        for shift in (False, True):
            if is_group_end(key, shift) and not is_group_start(key, shift):
                assert key_action(GRID, key, shift=shift) == "$"


def test_group_keys_map_to_viewer_edges():
    assert key_action(VIEWER, KEY_0) == "group_first"
    assert key_action(VIEWER, KEY_DOLLAR) == "group_last"
    assert key_action(VIEWER, KEY_5, shift=True) == "group_last"
    # R2: el panel comparte los bordes de grupo con la rejilla.
    assert key_action(PANEL, KEY_0) == "0"
    assert key_action(PANEL, KEY_DOLLAR) == "$"


# ----------------------------------------------------------------------
# Sanidad de la tabla: sin ambigüedads
# ----------------------------------------------------------------------
def _domains(row: Binding) -> tuple[set[bool], set[bool], set[bool]]:
    """Conjunto de combinaciones que la fila aceptaría por dimensión."""
    shift = {row.shift} if row.shift is not None else {True, False}
    ctrl = {row.ctrl} if row.ctrl is not None else {True, False}
    cmd = {row.cmd} if row.cmd is not None else {True, False}
    return (shift, ctrl, cmd)


def test_no_ambiguous_rows_for_the_same_key():
    """Filas del mismo (contexto, tecla): disjuntas o en cadena de precedencia.

    La consulta elige la fila más específica (menos ``None``), de modo que
    solaparse solo es aceptable cuando una fila es estrictamente más concreta
    que la otra (el paneo cede ante nada, las letras lo admiten todo). Si dos
    filas con la misma especificidad comparten una combinación, la tabla es
    ambigua y el test falla.
    """
    by_slot: dict[tuple[str, int], list[Binding]] = {}
    for row in TABLE:
        by_slot.setdefault((row.context, row.key), []).append(row)

    for (context, key), rows in by_slot.items():
        for first, second in combinations(rows, 2):
            first_domain = _domains(first)
            second_domain = _domains(second)
            overlap = all(
                first_set & second_set
                for first_set, second_set in zip(first_domain, second_domain)
            )
            if not overlap:
                continue
            first_specificity = sum(len(dim) == 1 for dim in first_domain)
            second_specificity = sum(len(dim) == 1 for dim in second_domain)
            same_specificity = first_specificity == second_specificity
            if same_specificity:
                assert first_domain == second_domain and (
                    first.action == second.action
                ), (
                    f"filas ambiguas en {context} para la tecla {hex(key)}: "
                    f"{first} vs {second}"
                )


# ----------------------------------------------------------------------
# Adaptador del keyPressEvent: (shift, ctrl, cmd) desde el evento (P1.6)
# ----------------------------------------------------------------------
class _FakeModifiers:
    def __init__(self, value: int) -> None:
        self.value = value


class _FakeEvent:
    """Evento de teclado falso: solo ``modifiers().value``, sin Qt."""

    def __init__(self, value: int) -> None:
        self._value = value

    def modifiers(self) -> _FakeModifiers:
        return _FakeModifiers(self._value)


# El control que no es la Ctrl física de la plataforma (⌘ en macOS, Ctrl
# física en el resto), según la máscara del módulo.
_OTHER_CONTROL = (CONTROL_MODIFIER | META_MODIFIER) ^ CTRL_MASK


def test_event_modifiers_without_modifiers():
    assert event_modifiers(_FakeEvent(0)) == (False, False, False)


def test_event_modifiers_extracts_shift():
    assert event_modifiers(_FakeEvent(SHIFT_MODIFIER)) == (True, False, False)
    assert event_modifiers(_FakeEvent(SHIFT_MODIFIER | _OTHER_CONTROL)) == (
        True,
        False,
        True,
    )


def test_event_modifiers_physical_ctrl_is_ctrl():
    # La Ctrl física depende de la plataforma (CTRL_MASK: ⌘ en macOS).
    assert event_modifiers(_FakeEvent(CTRL_MASK)) == (False, True, False)
    assert event_modifiers(_FakeEvent(CTRL_MASK | SHIFT_MODIFIER)) == (
        True,
        True,
        False,
    )


def test_event_modifiers_any_ctrl_and_not_ctrl_is_cmd():
    assert event_modifiers(_FakeEvent(_OTHER_CONTROL)) == (False, False, True)
    # Con la Ctrl física presente, el segundo control ya no cuenta como cmd.
    assert event_modifiers(_FakeEvent(CTRL_MASK | _OTHER_CONTROL)) == (
        False,
        True,
        False,
    )


# ----------------------------------------------------------------------
# Guardián de pureza: nada de Qt ni de ventana en el módulo puro
# ----------------------------------------------------------------------
def test_keybindings_imports_without_pyside6():
    code = (
        "import sys\n"
        "import foto.ui.keybindings\n"
        "leaked = [name for name in sys.modules if name.startswith('PySide6')]\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_keybindings_never_mentions_qt_or_the_window():
    source = Path("src/foto/ui/keybindings.py").read_text(encoding="utf-8").casefold()
    for token in (
        "pyside",
        "qtcore",
        "qtgui",
        "qtwidgets",
        "foto.ui.window",
        "foto.ui.thumbnails",
        "foto.ui.app",
    ):
        assert token not in source, token
