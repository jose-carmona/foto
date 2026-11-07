"""La ayuda integrada se renderiza desde el markdown empaquetado.

`ayuda.md` es la fuente única del contenido (`docs/diseno-ui.md`); el overlay
(`?`/`F1`/`:help`) lo convierte a HTML con el subset restringido del
documento: encabezados, tablas de dos columnas y teclas entre acentos.
"""

from __future__ import annotations

import os

import pytest

from foto.ui.help import _help_markdown, render_help
from foto.ui.models import Photo
from foto.ui.window import PhotoWindow


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


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
