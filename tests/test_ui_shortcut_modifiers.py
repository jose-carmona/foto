"""La media página (Ctrl+d/Ctrl+u) usa la tecla Ctrl física en cada plataforma.

Regresión del atajo en macOS: Qt remapea los modificadores (⌘ Command llega
como ControlModifier y la Ctrl física como MetaModifier, documentado en
``Qt::KeyboardModifier``), de modo que chequear ``ControlModifier`` hacía que
en Mac la media página respondiera a ⌘d/⌘u. docs/diseno-ui.md exige
``Ctrl+d``/``Ctrl+u`` con la Ctrl física en todas las plataformas (la única
variante ⌘ es la de salir, ``Ctrl+q``/``⌘Q``).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt

from foto.ui import keybindings


def test_ctrl_mask_points_to_the_physical_control_key():
    if sys.platform == "darwin":
        assert keybindings.CTRL_MASK == Qt.MetaModifier.value
    else:
        assert keybindings.CTRL_MASK == Qt.ControlModifier.value


def test_ctrl_mask_is_not_the_command_key_on_mac():
    # ⌘ Command llega como ControlModifier en macOS; con el bug, ⌘d/⌘u
    # disparaban la media página en lugar de Ctrl+d/Ctrl+u.
    if sys.platform == "darwin":
        assert keybindings.CTRL_MASK != Qt.ControlModifier.value
    else:
        assert keybindings.CTRL_MASK == Qt.ControlModifier.value


# ----------------------------------------------------------------------
# Anclaje de las constantes enteras del módulo puro (P1.5)
# ----------------------------------------------------------------------
# ``foto.ui.keybindings`` duplica los códigos Qt como enteros para poder
# probarse sin la capa Qt; este test fija cada constante contra los valores
# reales del enum de la versión instalada, de modo que un cambio de Qt no
# pase silenciosamente.
def test_keybindings_keys_match_qt():
    letter_names = {f"KEY_{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    digit_names = {f"KEY_{digit}" for digit in "0123456789"}
    symbol_names = {
        "KEY_DOLLAR": Qt.Key_Dollar,
        "KEY_PLUS": Qt.Key_Plus,
        "KEY_MINUS": Qt.Key_Minus,
        "KEY_SLASH": Qt.Key_Slash,
        "KEY_COLON": Qt.Key_Colon,
        "KEY_EQUAL": Qt.Key_Equal,
        "KEY_QUESTION": Qt.Key_Question,
        "KEY_ESCAPE": Qt.Key_Escape,
        "KEY_TAB": Qt.Key_Tab,
        "KEY_BACKTAB": Qt.Key_Backtab,
        "KEY_RETURN": Qt.Key_Return,
        "KEY_ENTER": Qt.Key_Enter,
        "KEY_F1": Qt.Key_F1,
        "KEY_HOME": Qt.Key_Home,
        "KEY_END": Qt.Key_End,
        "KEY_PAGEUP": Qt.Key_PageUp,
        "KEY_PAGEDOWN": Qt.Key_PageDown,
        "KEY_LEFT": Qt.Key_Left,
        "KEY_UP": Qt.Key_Up,
        "KEY_RIGHT": Qt.Key_Right,
        "KEY_DOWN": Qt.Key_Down,
    }

    constant_names = {name for name in dir(keybindings) if name.startswith("KEY_")}
    assert constant_names == letter_names | digit_names | set(symbol_names)

    for name in sorted(letter_names):
        letter = name.removeprefix("KEY_")
        assert getattr(keybindings, name) == int(getattr(Qt, f"Key_{letter}")), name

    for name in sorted(digit_names):
        digit = name.removeprefix("KEY_")
        assert getattr(keybindings, name) == int(getattr(Qt, f"Key_{digit}")), name

    for name, qt_key in sorted(symbol_names.items()):
        assert getattr(keybindings, name) == int(qt_key), name


def test_keybindings_modifiers_and_ctrl_mask_match_qt():
    assert keybindings.SHIFT_MODIFIER == Qt.KeyboardModifier.ShiftModifier.value
    assert keybindings.CONTROL_MODIFIER == Qt.KeyboardModifier.ControlModifier.value
    assert keybindings.META_MODIFIER == Qt.KeyboardModifier.MetaModifier.value

    if sys.platform == "darwin":
        assert keybindings.CTRL_MASK == Qt.KeyboardModifier.MetaModifier.value
    else:
        assert keybindings.CTRL_MASK == Qt.KeyboardModifier.ControlModifier.value
