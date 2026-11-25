"""La ayuda integrada se renderiza desde el markdown empaquetado.

`ayuda.md` es la fuente única del contenido (`docs/diseno-ui.md`); el overlay
(`?`/`F1`/`:help`) lo convierte a HTML con el subset restringido del
documento: encabezados, tablas de dos columnas y teclas entre acentos.
"""

from __future__ import annotations

import os
import re

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPalette

from foto.ui.help import HelpOverlay, _help_markdown, render_help
from foto.ui.models import Photo
from foto.ui.theme import (
    _apply_system_font,
    _contrast_ratio,
    apply_dark_palette,
    apply_light_palette,
)
from foto.ui.window import PhotoWindow


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # Igual que el arranque real: sin una fuente de sistema resuelta, Qt
    # aborta al medir las fichas en plataformas sin tema (``offscreen``).
    _apply_system_font(app)
    return app


@pytest.fixture(autouse=True)
def _ensure_qapp(qapp):
    """Todo el módulo mide fuentes: sin ``QApplication`` Qt aborta."""
    return qapp


def test_help_markdown_is_packaged():
    texto = _help_markdown()

    assert "# Ayuda" in texto
    for section in ("## Globales", "## Rejilla", "## Visor", "## Comandos"):
        assert section in texto


def test_render_keeps_the_documented_shortcuts():
    html = render_help(_help_markdown())

    # Toda tecla del documento acaba en una ficha monoespaciada.
    assert "<span style=" in html
    for fragment in ("0", "$", "g g", "G", "Ctrl+d", ":sort", ":q", ":help"):
        assert fragment in html


def test_help_documents_the_tag_overlay():
    texto = _help_markdown()

    assert "| `t` |" in texto
    assert "tooltip" in texto
    html = render_help(texto)
    assert "\u00a0\u00a0t\u00a0\u00a0" in html
    assert "rectángulos de etiquetas con región" in html


def test_chips_are_not_duplicated():
    """Regresión: el texto de una ficha no debe repetirse dentro de ella."""
    html = render_help(_help_markdown())

    assert "\u00a0\u00a0?\u00a0\u00a0" in html
    assert "?\u00a0\u00a0?\u00a0\u00a0?" not in html


def test_keys_column_fits_the_widest_row():
    """La fila ``h j k l / flechas`` no debe partirse por falta de ancho."""
    html = render_help(_help_markdown())

    import re

    columns = {int(width) for width in re.findall(r'width="(\d+)"', html)}
    assert len(columns) == 1
    assert "/ flechas" in html


def test_rejected_syntax_stays_out_of_the_renderer():
    # El subset no interpreta listas ni negritas: solo encabezados, tablas y
    # fichas de código; lo demás entra como párrafo plano.
    html = render_help("# T\n\n- lista\n\n**negrita**")

    assert "T</p>" in html
    assert "lista" in html
    assert html.count("<table") == 0


# ----------------------------------------------------------------------
# Fase 1 — render y navegación P0
# ----------------------------------------------------------------------
_CHIP_STYLE = re.compile(
    r"background-color:(?P<background>#[0-9a-fA-F]{8}); "
    r"color:(?P<color>#[0-9a-fA-F]{8});"
)


@pytest.mark.parametrize("apply_palette", [apply_light_palette, apply_dark_palette])
def test_chips_stand_out_from_the_overlay_surface(qapp, apply_palette):
    """T1.1: la ficha se ve sobre el fondo del overlay y su texto contrasta."""
    apply_palette(qapp)
    match = _CHIP_STYLE.search(render_help("texto `Esc` siempre"))

    assert match is not None
    chip_background = QColor(match.group("background"))
    chip_text = QColor(match.group("color"))
    overlay_surface = qapp.palette().color(QPalette.AlternateBase)

    assert chip_background != overlay_surface
    assert _contrast_ratio(chip_background, overlay_surface) >= 1.1
    assert _contrast_ratio(chip_background, chip_text) >= 4.5


def test_consecutive_lines_render_as_one_paragraph():
    """T1.2: un párrafo escrito en varias líneas no se parte."""
    html = render_help("l1\nl2\n\nl3")

    assert "l1 l2</p>" in html
    assert "l3</p>" in html
    assert "l1</p><p" not in html


def test_real_help_paragraphs_render_complete():
    """T1.2: la introducción y «Ubicaciones» dejan de ser frases partidas."""
    html = render_help(_help_markdown())

    assert "no hay menús ni ratón necesario." in html
    assert "no hay menús ni ratón necesario.</p>" not in html
    assert "sale de la aplicación.</p>" in html
    assert "La app es teclado primero" in html
    assert "la miniatura sale de la caché si existe" in html


def test_chips_do_not_duplicate_spaces():
    """T1.3: la ficha ya lleva su aire; la unión no añade otro espacio."""
    html = render_help("texto `Esc` siempre")

    assert "Esc\u00a0\u00a0  " not in html


def test_chip_padding_steps_aside_from_punctuation():
    """T1.3: sin nbsp contra comas y paréntesis."""
    html = render_help("texto `config.toml`, ADR y (`Esc`)")

    assert "\u00a0</span>," not in html
    assert "config.toml</span>," in html
    assert ">Esc</span>)" in html


@pytest.fixture
def help_overlay(qapp):
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    parent.resize(900, 700)
    parent.show()
    parent.activateWindow()
    overlay = HelpOverlay(parent)
    overlay.resize(900, 700)
    overlay.show()
    overlay.setFocus()
    qapp.processEvents()
    yield overlay
    overlay.close()
    parent.close()


def _press(qapp, widget, key, modifiers=Qt.NoModifier):
    qapp.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, modifiers))


def test_arrows_and_jk_move_one_line(qapp, help_overlay):
    """T1.4: el paso de línea sale de la fuente, no de 40 px fijos."""
    bar = help_overlay.body.verticalScrollBar()
    step = QFontMetrics(help_overlay.body.document().defaultFont()).lineSpacing()

    assert bar.maximum() > step
    for key in (Qt.Key_Down, Qt.Key_J):
        bar.setValue(bar.minimum())
        _press(qapp, help_overlay, key)
        assert bar.value() == step
    for key in (Qt.Key_Up, Qt.Key_K):
        bar.setValue(bar.minimum() + step)
        _press(qapp, help_overlay, key)
        assert bar.value() == bar.minimum()


@pytest.mark.parametrize(
    ("key", "modifiers", "use_half"),
    [
        (Qt.Key_PageDown, Qt.NoModifier, False),
        (Qt.Key_Space, Qt.NoModifier, False),
        (Qt.Key_D, Qt.ControlModifier, True),
    ],
)
def test_page_keys_scroll_down(qapp, help_overlay, key, modifiers, use_half):
    bar = help_overlay.body.verticalScrollBar()
    step = bar.pageStep() // 2 if use_half else bar.pageStep()

    bar.setValue(bar.minimum())
    _press(qapp, help_overlay, key, modifiers)

    assert bar.value() == min(bar.minimum() + step, bar.maximum())


@pytest.mark.parametrize(
    ("key", "modifiers", "use_half"),
    [
        (Qt.Key_PageUp, Qt.NoModifier, False),
        (Qt.Key_Space, Qt.ShiftModifier, False),
        (Qt.Key_U, Qt.ControlModifier, True),
    ],
)
def test_page_keys_scroll_up(qapp, help_overlay, key, modifiers, use_half):
    bar = help_overlay.body.verticalScrollBar()
    step = bar.pageStep() // 2 if use_half else bar.pageStep()

    bar.setValue(bar.maximum())
    _press(qapp, help_overlay, key, modifiers)

    assert bar.value() == max(bar.maximum() - step, bar.minimum())


def test_home_and_end_reach_the_extremes(qapp, help_overlay):
    bar = help_overlay.body.verticalScrollBar()

    bar.setValue(bar.minimum())
    _press(qapp, help_overlay, Qt.Key_End)
    assert bar.value() == bar.maximum()

    _press(qapp, help_overlay, Qt.Key_Home)
    assert bar.value() == bar.minimum()


def test_body_never_takes_focus_and_click_keeps_selection(qapp, help_overlay):
    """T1.4: un clic selecciona texto sin devolver el foco al navegador."""
    body = help_overlay.body

    assert body.focusPolicy() == Qt.NoFocus
    help_overlay.setFocus()
    qapp.processEvents()
    assert help_overlay.hasFocus()

    viewport = body.viewport()
    position = QPointF(20, 20)
    qapp.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.MouseButtonPress,
            position,
            position,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ),
    )
    qapp.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.MouseButtonRelease,
            position,
            position,
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ),
    )

    assert not body.hasFocus()
    assert help_overlay.hasFocus()


def test_overlay_toggles_from_the_window(qapp):
    photo = Photo(
        name="a.jpg",
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
        photo_hash="sha256:a",
    )
    window = PhotoWindow(
        [photo],
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
    )
    window.show()  # isVisible() incluye a los ancestros: la ventana tiene que estar expuesta

    window._toggle_help()
    assert window.help_overlay.isVisible()

    html = window.help_overlay.body.toHtml()
    assert "Globales" in html
    assert ":help" in html

    window._toggle_help()
    assert not window.help_overlay.isVisible()
