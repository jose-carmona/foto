"""Fidelidad de las plantillas QSS y estabilidad del re-theme (P1.7).

Las cinco plantillas de ``foto.ui.theme`` (``GRID_QSS``, ``PANEL_SCROLL_QSS``,
``COMPLETION_QSS``, ``PROMPT_QSS`` y ``HELP_BODY_QSS``) las aplican los
widgets en su constructor y las re-aplican vía ``reapply_style`` en
``retheme()``. Con la plataforma offscreen y las paletas clara y oscura, este
fichero fija que cada widget porta exactamente su plantilla (semántica CSS
normalizada, no el byte) y que re-tematizar no degenera ni vacía el sheet.
"""

from __future__ import annotations

import os
import re

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from foto.ui.grid import PhotoGrid
from foto.ui.help import HelpOverlay
from foto.ui.panels import CompletionPopup, MetadataPanel, StatusBar
from foto.ui.theme import (
    COMPLETION_QSS,
    GRID_QSS,
    HELP_BODY_QSS,
    PANEL_SCROLL_QSS,
    PROMPT_QSS,
    apply_dark_palette,
    apply_light_palette,
)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _same_sheet(widget_sheet: str, template: str) -> None:
    assert _norm(widget_sheet) == _norm(template)


@pytest.mark.parametrize("apply_palette", [apply_light_palette, apply_dark_palette])
def test_widgets_carry_their_theme_template(qapp, apply_palette):
    apply_palette(qapp)
    owner = QWidget()
    status = StatusBar()

    _same_sheet(PhotoGrid().styleSheet(), GRID_QSS)
    _same_sheet(MetadataPanel().scroll.styleSheet(), PANEL_SCROLL_QSS)
    _same_sheet(CompletionPopup(owner).styleSheet(), COMPLETION_QSS)
    _same_sheet(status.prompt.styleSheet(), PROMPT_QSS)
    _same_sheet(HelpOverlay(owner).body.styleSheet(), HELP_BODY_QSS)


@pytest.mark.parametrize("apply_palette", [apply_light_palette, apply_dark_palette])
def test_retheme_keeps_the_template_intact(qapp, apply_palette):
    apply_palette(qapp)
    owner = QWidget()
    grid = PhotoGrid()
    panel = MetadataPanel()
    popup = CompletionPopup(owner)
    overlay = HelpOverlay(owner)

    # Paleta opuesta a la recién aplicada: así el re-parse del sheet no puede
    # apoyarse en que la paleta no cambió.
    window = qapp.palette().color(qapp.palette().ColorRole.Window)
    opposite = apply_dark_palette if window.lightness() > 128 else apply_light_palette
    opposite(qapp)

    grid.retheme()
    panel.retheme()
    popup.retheme()
    overlay.retheme()

    _same_sheet(grid.styleSheet(), GRID_QSS)
    _same_sheet(panel.scroll.styleSheet(), PANEL_SCROLL_QSS)
    _same_sheet(popup.styleSheet(), COMPLETION_QSS)
    _same_sheet(overlay.body.styleSheet(), HELP_BODY_QSS)
