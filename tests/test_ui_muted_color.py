"""Equivalencia del color atenuado tras extraer la aritmética pura.

``_muted_from_palette`` y ``_help_muted`` (``window.py``) delegan ahora en
``muted_rgb`` (``foto.ui.presentation``). Con la plataforma offscreen y las
paletas clara y oscura de la ventana, este fichero comprueba que el color
resultante no cambia ni un canal: el adaptador coincide con la aritmética
pura aplicada a la misma paleta, y la ayuda usa exactamente ese color.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from foto.ui.presentation import muted_rgb
from foto.ui.window import (
    _help_muted,
    _muted_from_palette,
    apply_dark_palette,
    apply_light_palette,
)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _rgb(color: QColor) -> tuple[int, int, int]:
    return (color.red(), color.green(), color.blue())


def _expected_muted(palette: QPalette) -> QColor:
    foreground = palette.color(QPalette.WindowText)
    background = palette.color(QPalette.Window)
    return QColor(*muted_rgb(_rgb(foreground), _rgb(background)))


@pytest.mark.parametrize("apply_palette", [apply_light_palette, apply_dark_palette])
def test_muted_from_palette_matches_the_pure_arithmetic(qapp, apply_palette):
    apply_palette(qapp)
    palette = qapp.palette()

    muted = _muted_from_palette(palette)
    expected = _expected_muted(palette)

    assert _rgb(muted) == _rgb(expected)


@pytest.mark.parametrize("apply_palette", [apply_light_palette, apply_dark_palette])
def test_help_muted_matches_muted_from_palette(qapp, apply_palette):
    apply_palette(qapp)

    help_muted = _help_muted()
    direct = _muted_from_palette(qapp.palette())

    assert help_muted == direct.name(QColor.HexArgb)
