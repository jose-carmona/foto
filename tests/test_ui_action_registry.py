"""Contrato del registro de acciones del dispatcher único (P1.5, fase 2).

Propiedades que mantienen coherentes la tabla de teclas (``keybindings.py``)
y el registro de la ventana:

1. toda acción producible por ``TABLE`` tiene un consumidor;
2. toda acción registrada es alcanzable (por la tabla o por ``:help``), así
   que el registro no contiene acciones huérfanas;
3. todo consumidor es invocable (nunca ``None``).
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from foto.ui.keybindings import TABLE
from foto.ui.window import PhotoWindow

# Únicas acciones que llegan al dispatcher sin producirse por una tecla:
# ``:help`` desde la línea de comandos. ``COMMAND_REACHABLE`` debe ampliarse
# si se documentan otras rutas no-teclado (p. e. ratón) que reusen el registro.
COMMAND_REACHABLE = {"help"}


def _photo(name: str, taken: str) -> object:
    from foto.ui.models import Photo

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
        photo_hash=f"sha256:{name}",
    )


PHOTOS = [_photo(f"un-{index}.jpg", f"2024-06-{index + 1:02d} 08:00") for index in range(4)]


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def window(qapp):
    photo_window = PhotoWindow(
        PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="registro",
        initial_panel_visible=False,
        grid_row=False,
    )
    yield photo_window


def producible_actions() -> set[str]:
    return {row.action for row in TABLE}


def test_every_producible_action_is_registered(window):
    producible = producible_actions()
    missing = producible - set(window._actions)
    assert not missing, sorted(missing)


def test_every_registered_action_is_reachable(window):
    unreachable = set(window._actions) - producible_actions() - COMMAND_REACHABLE
    assert not unreachable, sorted(unreachable)


def test_every_registered_handler_is_callable(window):
    for action, handler in window._actions.items():
        assert callable(handler), action


def test_panel_ignore_action_is_the_only_registered_no_op(window):
    # Tras la fase 3 solo queda ``ignore`` (flechas laterales del panel):
    # los dígitos 1–5 y ``dead_last`` desaparecieron con R1/R3.
    assert "ignore" in window._actions
    assert callable(window._actions["ignore"])
    for gone in ("1", "2", "3", "4", "5", "dead_last", "end_window"):
        assert gone not in window._actions


def test_help_command_still_reaches_the_registry(window):
    assert "help" in window._actions
