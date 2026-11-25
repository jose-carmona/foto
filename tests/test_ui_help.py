"""La ayuda integrada se renderiza desde el markdown empaquetado.

`ayuda.md` es la fuente única del contenido (`docs/diseno-ui.md`); el overlay
(`?`/`F1`/`:help`) lo convierte a HTML con el subset restringido del
documento: encabezados, tablas de dos columnas y teclas entre acentos.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPalette,
    QTextBlockFormat,
)

from foto.ui.help import (
    HelpOverlay,
    _help_color,
    _help_markdown,
    _help_muted,
    help_sections,
    render_help,
)
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


def _make_window(qapp) -> PhotoWindow:
    """Ventana real con una foto: la rejilla tiene el foco al arrancar."""
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
    window.activateWindow()
    qapp.processEvents()
    return window


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

    assert ">T</h1>" in html
    assert "lista" in html
    assert html.count("<table") == 0


# ----------------------------------------------------------------------
# Fase 2 — índice, cabecera, responsive y contenido
# ----------------------------------------------------------------------
def test_help_sections_lists_the_titles_in_document_order():
    """T2.1: el índice comparte con el render el orden de los ``##``."""
    sections = help_sections(_help_markdown())

    assert [section.title for section in sections] == [
        "Globales",
        "Rejilla",
        "Visor",
        "Panel de metadatos",
        "Comandos",
        "Ubicaciones",
    ]


def test_help_sections_anchors_are_stable_and_skip_the_document_title():
    """T2.1: anclas ``s1``, ``s2``… y el ``# Ayuda`` inicial no se indexa."""
    sections = help_sections("# Ayuda\n\nintro\n\n## Una\n\n## Dos\n")

    assert [(s.anchor, s.title) for s in sections] == [("s1", "Una"), ("s2", "Dos")]


def test_render_inserts_one_anchor_per_section_before_its_heading():
    """T2.1: el HTML lleva las mismas anclas que devuelve ``help_sections``."""
    markdown = (
        "# Ayuda\n\nintro\n\n## Una\n\npárrafo\n\n## Dos\n\n"
        "| Tecla | Acción |\n| --- | --- |\n| `a` | b |\n"
    )
    sections = help_sections(markdown)

    html = render_help(markdown)

    for section in sections:
        anchor = html.index(f'<a name="{section.anchor}"></a>')
        heading = html.index(section.title)
        assert anchor < heading, section.anchor


def test_header_shows_the_section_index_and_the_close_hint(qapp, help_overlay):
    """T2.2: índice numerado siempre visible y pista de cierre."""
    text = help_overlay.index_label.text()

    for number, section in enumerate(help_overlay.sections, 1):
        assert f"{number} {section.title}" in text
    assert help_overlay.hint_label.text() == "Esc/q cerrar"


def test_header_has_no_close_button(qapp, help_overlay):
    """T2.2: sin botones ni «✕»: el cierre es solo por teclado."""
    from PySide6.QtWidgets import QPushButton, QToolButton

    assert help_overlay.findChildren(QToolButton) == []
    assert help_overlay.findChildren(QPushButton) == []


def test_header_index_has_an_accessible_name(qapp, help_overlay):
    """T2.2: el índice lleva nombre accesible."""
    assert help_overlay.index_label.accessibleName() == "índice de la ayuda"


def test_header_stays_above_the_body_and_outside_the_scroll(qapp, help_overlay):
    """T2.2: la cabecera no scrollea; el cuerpo queda debajo."""
    header_bottom = help_overlay.index_label.mapTo(help_overlay, QPoint(0, 0)).y()
    body_top = help_overlay.body.mapTo(help_overlay, QPoint(0, 0)).y()

    assert header_bottom < body_top


def test_digit_jumps_to_its_section(qapp, help_overlay):
    """T2.3: pulsar ``2`` salta a «Rejilla»; ``Home`` vuelve al inicio."""
    bar = help_overlay.body.verticalScrollBar()
    bar.setValue(bar.minimum())

    _press(qapp, help_overlay, Qt.Key_2)

    assert help_overlay.current_section == "Rejilla"
    assert bar.value() > bar.minimum()

    _press(qapp, help_overlay, Qt.Key_Home)
    assert bar.value() == bar.minimum()


@pytest.mark.parametrize("digit", range(1, 7))
def test_digits_reach_every_existing_section(qapp, help_overlay, digit):
    """T2.3: ``1``–``6`` cubren las seis secciones del documento real."""
    _press(qapp, help_overlay, Qt.Key_0 + digit)

    assert help_overlay.current_section == help_overlay.sections[digit - 1].title


@pytest.mark.parametrize("digit", range(7, 10))
def test_digits_beyond_the_sections_do_nothing(qapp, help_overlay, digit):
    """T2.3: ``7``–``9`` sin sección no hacen nada."""
    bar = help_overlay.body.verticalScrollBar()
    bar.setValue(bar.minimum())

    _press(qapp, help_overlay, Qt.Key_0 + digit)

    assert help_overlay.current_section is None
    assert bar.value() == bar.minimum()


@pytest.mark.parametrize(
    "modifiers",
    [
        Qt.ControlModifier,
        Qt.AltModifier,
        Qt.MetaModifier,
        Qt.ShiftModifier,
    ],
)
def test_digits_with_modifiers_do_not_jump(qapp, help_overlay, modifiers):
    """T2.3: el salto exige el dígito sin modificadores."""
    _press(qapp, help_overlay, Qt.Key_1, modifiers)

    assert help_overlay.current_section is None


def test_show_section_scrolls_to_the_requested_section(qapp, help_overlay):
    """T2.4: ``show_section`` sitúa el documento en la sección pedida."""
    bar = help_overlay.body.verticalScrollBar()
    bar.setValue(bar.minimum())

    help_overlay.show_section("Ubicaciones")
    qapp.processEvents()

    assert help_overlay.current_section == "Ubicaciones"
    assert bar.value() > bar.minimum()


def test_show_section_with_an_unknown_title_falls_back_to_the_start(
    qapp, help_overlay
):
    """T2.4: un título inexistente (o ``None``) abre en el inicio."""
    bar = help_overlay.body.verticalScrollBar()
    bar.setValue(bar.minimum())

    help_overlay.show_section("No existe")
    qapp.processEvents()

    assert help_overlay.current_section is None
    assert bar.value() == bar.minimum()

    help_overlay.show_section(None)
    qapp.processEvents()
    assert help_overlay.current_section is None


def test_typographic_scale_uses_the_documented_sizes(qapp):
    """T2.7: ``h1`` 20 px, ``h2`` 15 px y cabecera de tabla en fg.muted."""
    html = render_help(_help_markdown())

    assert "font-size:20px" in html
    assert "font-size:15px" in html
    assert "<th" in html
    assert f"color:{_help_muted()}" in html


def test_section_rules_use_midlight(qapp):
    """T2.7: las reglas de sección usan ``Midlight``, no ``Mid``.

    El filete va con ``background-color``: el QSS del cuerpo hace que Qt
    ignore el ``color`` inline de un ``<hr>`` y lo pinte con el texto.
    """
    html = render_help(_help_markdown())

    assert f'<hr style="background-color:{_help_color(QPalette.Midlight)};">' in html
    assert f'<hr style="color:{_help_color(QPalette.Mid)};">' not in html
    assert f'<hr style="color:{_help_color(QPalette.Midlight)};">' not in html


def test_body_blocks_have_proportional_line_height(qapp, help_overlay):
    """T2.8: el interlineado del documento es 1,4 proporcional."""
    fmt = help_overlay.body.document().firstBlock().blockFormat()

    assert fmt.lineHeight() == 140
    assert (
        fmt.lineHeightType()
        == QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
    )


def test_headings_are_semantic(qapp):
    """T2.10: ``h1``/``h2`` reales y cabecera de tabla con ``th``."""
    html = render_help(_help_markdown())

    assert "<h1" in html
    assert "<h2" in html
    assert "<th" in html


def test_body_has_an_accessible_name(qapp, help_overlay):
    """T2.10: el cuerpo lleva su nombre accesible."""
    assert help_overlay.body.accessibleName() == "atajos de teclado"


def test_every_rendered_block_carries_an_explicit_color(qapp):
    """T2.13: ningún bloque depende del QSS para su color.

    Qt no reinterpreta ``palette(window-text)`` del QSS al cambiar de tema:
    tras un cambio oscuro→claro en vivo, el texto corriente se quedaba
    blanco sobre la superficie clara. Igual que encabezados y fichas, cada
    bloque lleva su color inline del tema vigente en el render.
    """
    html = render_help(_help_markdown())

    openings = re.findall(r"<(?:p|h1|h2)[^>]*>", html)
    assert openings
    for opening in openings:
        assert "color:" in opening, opening


def test_retheme_reapplies_the_body_stylesheet(qapp, help_overlay, monkeypatch):
    """T2.13: ``retheme`` re-parsea el QSS del cuerpo (y su scrollbar)."""
    seen: list[object] = []
    monkeypatch.setattr(
        "foto.ui.help.reapply_style", lambda widget: seen.append(widget)
    )

    help_overlay.retheme()

    assert help_overlay.body in seen


def test_new_help_keys_are_documented_in_both_sources():
    """T2.9: las teclas de la ayuda están en ``ayuda.md`` y en la guía."""
    guia = Path("docs/diseno-ui.md").read_text(encoding="utf-8")
    ayuda = _help_markdown()

    for token in (
        "PgUp",
        "PgDn",
        "Home",
        "End",
        "Espacio",
        "Ctrl+d",
        "Ctrl+u",
        "`1`–`9`",
    ):
        assert token in ayuda, token
        assert token in guia, token

    assert "`q` también cierra esta" in ayuda
    assert "también cierra esta ayuda.</p>" in render_help(ayuda)
    # Cabecera fija, apertura contextual y máximo de 960 px, en la guía.
    assert "Esc/q cerrar" in guia
    assert "abre en la sección de la vista" in guia
    assert "960×820" in guia


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
    """T1.2/T2.9: la introducción, el panel y «Ubicaciones» se leen completos."""
    html = render_help(_help_markdown())

    assert "no hay menús ni ratón necesario." in html
    assert "no hay menús ni ratón necesario.</p>" not in html
    assert "también cierra esta ayuda.</p>" in html
    assert "sección con ese número del índice.</p>" in html
    assert "solo las flechas desplazan el panel." in html
    assert "favoritar o filtrar por etiqueta sigue funcionando" in html
    assert "para refrescar el estado.</p>" in html


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
    window = _make_window(qapp)

    window._toggle_help()
    assert window.help_overlay.isVisible()

    html = window.help_overlay.body.toHtml()
    assert "Globales" in html
    assert ":help" in html

    window._toggle_help()
    assert not window.help_overlay.isVisible()


def test_help_opens_in_the_grid_section_from_the_grid(qapp):
    """T2.4: con foco en la rejilla y sin visor, la ayuda abre en «Rejilla»."""
    window = _make_window(qapp)

    window._toggle_help()
    qapp.processEvents()

    assert window.help_overlay.current_section == "Rejilla"


def test_help_command_opens_at_the_start(qapp):
    """T2.12: ``:help`` abre al inicio (Globales), no en la sección de la rejilla.

    El prompt cierra antes de ejecutar el comando y el foco vuelve a la
    rejilla: es un foco heredado, no un contexto de vista, así que la ayuda
    abre sin ancla.
    """
    window = _make_window(qapp)

    # El flujo real de ``:help``: prompt abierto, texto y Enter.
    window.status.open_command()
    qapp.processEvents()
    prompt = window.status.prompt
    for ch in "help":
        prompt.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, ord(ch.upper()), Qt.NoModifier, ch)
        )
    prompt.keyPressEvent(
        QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    )
    qapp.processEvents()

    assert window.help_overlay.isVisible()
    assert window.help_overlay.current_section is None
    bar = window.help_overlay.body.verticalScrollBar()
    assert bar.value() == bar.minimum()


def test_help_command_ignores_the_focus_returned_by_the_prompt(qapp):
    """T2.12: ``:help`` abre al inicio aunque la rejilla haya recobrado el foco.

    En cocoa, al ocultarse el prompt el foco vuelve a la rejilla antes de
    que ``_run_command`` ejecute: el test simula ese estado para no depender
    de la restauración de foco de cada plataforma.
    """
    window = _make_window(qapp)

    window.status.open_command()
    qapp.processEvents()
    window.grid.setFocus()
    qapp.processEvents()
    window._run_command("help")
    qapp.processEvents()

    assert window.help_overlay.isVisible()
    assert window.help_overlay.current_section is None


def test_help_opens_in_the_panel_section_when_the_panel_is_focused(qapp):
    """T2.4: con el foco en el panel de metadatos, abre en «Panel de metadatos»."""
    window = _make_window(qapp)
    window.toggle_panel()
    window.panel.focus_scroll()
    qapp.processEvents()

    window._toggle_help()
    qapp.processEvents()

    assert window.help_overlay.current_section == "Panel de metadatos"


def test_help_opens_in_the_viewer_section_with_the_viewer_open(qapp):
    """T2.4: con el visor abierto, la ayuda abre en «Visor»."""
    window = _make_window(qapp)
    window._open_viewer()
    qapp.processEvents()

    window._toggle_help()
    qapp.processEvents()

    assert window.help_overlay.current_section == "Visor"


def test_digits_jump_with_the_help_open_from_the_window(qapp):
    """T2.3: el salto por dígito funciona con la ayuda abierta desde la ventana."""
    window = _make_window(qapp)

    window._toggle_help()
    qapp.processEvents()
    _press(qapp, window.help_overlay, Qt.Key_2)

    assert window.help_overlay.current_section == "Rejilla"


def test_help_overlay_widens_to_960_on_large_windows(qapp):
    """T2.6: a 1280×800 la ayuda mide 960 de ancho y no supera 820 de alto."""
    window = _make_window(qapp)
    window.resize(1280, 800)
    qapp.processEvents()

    window._toggle_help()
    qapp.processEvents()

    assert window.help_overlay.width() == min(
        960, window.content.geometry().width() - 64
    )
    assert window.help_overlay.width() == 960
    assert window.help_overlay.height() <= 820


def test_help_overlay_shrinks_on_small_windows(qapp):
    """T2.6: a 640×480 la ayuda ocupa 576 de ancho (contenido − 64)."""
    window = _make_window(qapp)
    window.resize(640, 480)
    qapp.processEvents()

    window._toggle_help()
    qapp.processEvents()

    assert window.help_overlay.width() == 576


def test_locations_overlay_keeps_its_760_maximum(qapp):
    """T2.6: el panel de ubicaciones conserva su máximo de 760 px."""
    window = _make_window(qapp)
    window.resize(1280, 800)
    qapp.processEvents()

    window._toggle_locations()
    qapp.processEvents()

    assert window.locations_overlay.width() == min(
        760, window.content.geometry().width() - 64
    )
    assert window.locations_overlay.width() == 760
