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

from foto.ui.window import _CTRL_MASK


def test_ctrl_mask_points_to_the_physical_control_key():
    if sys.platform == "darwin":
        assert _CTRL_MASK == Qt.MetaModifier
    else:
        assert _CTRL_MASK == Qt.ControlModifier


def test_ctrl_mask_is_not_the_command_key_on_mac():
    # ⌘ Command llega como ControlModifier en macOS; con el bug, ⌘d/⌘u
    # disparaban la media página en lugar de Ctrl+d/Ctrl+u.
    if sys.platform == "darwin":
        assert _CTRL_MASK != Qt.ControlModifier
    else:
        assert _CTRL_MASK == Qt.ControlModifier
