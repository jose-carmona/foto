"""Guardián del grafo de dependencias de la UI (P1.6, ampliado en el P1.7).

Sin Qt: lee los fuentes y comprueba que el monolito no se reconstruye. Los
módulos de widget (``theme``, ``grid``, ``canvas``, ``panels``, ``help``) no
importan la ventana (la flecha nunca apunta hacia ``window.py``), ``theme``
es la base de la pirámide, ``commands`` es puro y cada símbolo vive en su
módulo canónico —con las ausencias simétricas en ``window.py``—. Desde el
P1.7, además: ningún widget escribe QSS estructural ni literales de color
solo vive en ``theme.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

_UI = Path("src/foto/ui")

_THEME = _UI / "theme.py"
_WIDGETS = (
    _THEME,
    _UI / "chips.py",
    _UI / "grid.py",
    _UI / "canvas.py",
    _UI / "panels.py",
    _UI / "help.py",
)
_WINDOW = _UI / "window.py"
_COMMANDS = _UI / "commands.py"

# “Símbolo en el módulo canónico”: primer token de la definición en el
# fichero; las ausencias simétricas comprueban contra ``window.py``.
_CANONICAL = (
    ("grid.py", ("class PhotoGrid", "class ThumbnailDelegate")),
    ("canvas.py", ("class PhotoCanvas", "class PhotoViewer", "class ImageLoadTask")),
    ("panels.py", (
        "class StatusBar",
        "class MetadataPanel",
        "class PromptEdit",
        "class CompletionPopup",
        "class PanelScroll",
    )),
    ("help.py", ("def render_help", "class HelpOverlay")),
    ("theme.py", (
        "def apply_theme",
        "def error_color",
        "def reapply_style",
        "GRID_QSS",
        "PANEL_SCROLL_QSS",
        "COMPLETION_QSS",
        "PROMPT_QSS",
        "HELP_BODY_QSS",
    )),
    ("commands.py", ("class Command", "COMMANDS = (")),
    ("window.py", ("class PhotoWindow",)),
)

# En window.py no queda ninguna definición movida (ausencias simétricas).
_NOT_IN_WINDOW = (
    "class PhotoGrid",
    "class ThumbnailDelegate",
    "def default_thumbnail",
    "class PhotoCanvas",
    "class PhotoViewer",
    "class ImageLoadTask",
    "class StatusBar",
    "class MetadataPanel",
    "class PromptEdit",
    "class CompletionPopup",
    "class PanelScroll",
    "def render_help",
    "class HelpOverlay",
    "def apply_theme",
    "def error_color",
    "def reapply_style",
    "GRID_QSS = ",
    "PANEL_SCROLL_QSS = ",
    "COMPLETION_QSS = ",
    "PROMPT_QSS = ",
    "HELP_BODY_QSS = ",
    "def event_modifiers",
    "def _event_modifiers",
    "class Command",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").casefold()


def test_widget_modules_never_import_the_window():
    for module in _WIDGETS:
        source = _text(module)
        for token in ("foto.ui.window", "foto.ui.app", "foto.ui.prototype"):
            assert token not in source, (module, token)


def test_theme_is_the_base_of_the_pyramid():
    source = _text(_THEME)
    ui_imports = [line.strip() for line in source.splitlines()
                  if "from foto.ui." in line or "import foto.ui." in line]
    assert all("foto.ui.presentation" in line for line in ui_imports), ui_imports


def test_commands_is_pure_without_qt():
    source = _text(_COMMANDS)
    for token in ("pyside", "qtcore", "qtgui", "qtwidgets"):
        assert token not in source, token


def test_every_symbol_lives_in_its_canonical_module():
    for filename, definitions in _CANONICAL:
        source = _text(_UI / filename)
        for definition in definitions:
            assert definition.casefold() in source, (filename, definition)


def test_window_keeps_no_moved_definition():
    source = _text(_WINDOW)
    for definition in _NOT_IN_WINDOW:
        assert definition.casefold() not in source, definition


# Seletores QSS estructurales: tras el P1.7 solo ``theme.py`` contiene QSS.
# Los tokens se escriben con la llave del selector para no chocar con los
# ``import`` de los nombres (false positivo de ``QScrollArea``/``QTextBrowser``
# como clase importada en un widget); ``QScrollBar`` y
# ``QFrame#completionPopup`` no se importan en ningún widget y dan la cobertura
# estructural extra.
_QSS_TOKENS = (
    "qscrollbar",
    "qlistwidget {",
    "qscrollarea {",
    "qtextbrowser {",
    "qframe#completionpopup",
    "qlineedit {",
)

# El truco de re-parse vive una sola vez, en ``theme.reapply_style``: ningún
# widget vuelve a escribir ``w.setStyleSheet(w.styleSheet())``.
_TRICK = re.compile(r"setstylesheet\(\s*\w+\.stylesheet\(\)")


def test_widgets_write_no_qss():
    sources = {module: _text(module) for module in _WIDGETS}
    theme = sources[_THEME]
    for token in _QSS_TOKENS:
        assert token in theme, token
    for module, source in sources.items():
        if module == _THEME:
            assert _TRICK.search(source), "reapply_style debe vivir en theme.py"
            continue
        for token in _QSS_TOKENS:
            assert token not in source, (module, token)
        assert not _TRICK.search(source), module


# Ninguna fuente de widget contiene literales de color hexadecimal: solo la
# capa de tokens (``theme.py``) puede escribirlos.
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def test_widgets_write_no_color_literals():
    for module in _WIDGETS:
        source = (module).read_text(encoding="utf-8")
        matches = _HEX_COLOR.findall(source)
        if module == _THEME:
            assert matches, "theme.py debe seguir conteniendo los tokens"
            continue
        assert not matches, (module, matches)
