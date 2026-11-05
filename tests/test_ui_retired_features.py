"""Las funciones retiradas no deben reaparecer en código ni documentación.

Decisión de producto: se eliminan el comando ``:open``, el atajo ``F11`` y la
tira de miniaturas histórica. La documentación de producto y la UI no deben
volver a anunciarlos ni conservar implementaciones a medias.
"""

from __future__ import annotations

from pathlib import Path

_UI_SOURCES = tuple(Path("src/foto/ui").glob("*.py"))
_PRODUCT_DOCS = (
    Path("docs/diseno-ui.md"),
    Path("src/foto/resources/ayuda.md"),
)
_DOCUMENTED_SOURCES = _UI_SOURCES + _PRODUCT_DOCS


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").casefold()


def test_open_command_has_no_trace():
    for source in _DOCUMENTED_SOURCES:
        assert ":open" not in _text(source), source


def test_fullscreen_shortcut_has_no_trace():
    for source in _DOCUMENTED_SOURCES:
        assert "f11" not in _text(source), source


def test_filmstrip_has_no_trace():
    for source in _DOCUMENTED_SOURCES:
        text = _text(source)
        for token in ("filmstrip", "stripitem", "with_strip", "_rebuild_strip"):
            assert token not in text, (source, token)
