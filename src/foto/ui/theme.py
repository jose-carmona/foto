"""Capa de tokens, paletas y estilos QSS de la UI.

Reúne, en un único punto de ``foto.ui``, dos cosas: los tokens sobre
``QPalette`` —las paletas clara y oscura, los colores derivados (texto
atenuado, error, acento y superficie de las fichas de ayuda) y la tipografía
común (``ui_font``)— y las plantillas
QSS de los widgets (``GRID_QSS``, ``PANEL_SCROLL_QSS``, ``COMPLETION_QSS``,
``PROMPT_QSS`` y ``HELP_BODY_QSS``, compuestas sobre ``_SCROLLBAR_QSS``), que
los widgets aplican en su constructor y re-aplican vía ``reapply_style`` al
cambiar de tema. Los widgets no escriben colores literales ni reglas de
estilo: consumen esta capa.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from foto.ui.presentation import muted_rgb


def ui_font(size: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont()
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def _apply_system_font(app: QApplication) -> None:
    """Fuente del sistema real como fuente de la aplicación (T2.8).

    Qt arranca con «Sans Serif» como fuente por defecto cuando la plataforma
    no le impone la suya (plataformas sin tema, como la ``offscreen`` de
    pruebas): es una familia inexistente, y su primera resolución dispara el
    calentamiento de la tabla de alias de fuentes (el propio Qt lo avisa:
    «Replace uses of missing font family "Sans Serif"»). Si la fuente
    vigente no resuelve a sí misma, se fija la familia que la plataforma
    resuelve para ella —la «tipografía del sistema» del diseño, sin
    empaquetar familias ni imponer un tema—; se conservan el resto de
    propiedades (tamaño, peso) de la fuente que había. En las plataformas
    que ya fijan su fuente (cocoa: ``.AppleSystemUIFont``) la comprobación
    es un no-op de pocos ms. Idempotente: una segunda pasada no encuentra
    sustitución.
    """
    current = app.font()
    resolved = QFontInfo(current).family()
    if not resolved or resolved == current.family():
        return
    fixed = QFont(current)
    fixed.setFamily(resolved)
    app.setFont(fixed)


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast_ratio(first: QColor, second: QColor) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _muted_from_palette(palette: QPalette) -> QColor:
    foreground = palette.color(QPalette.WindowText)
    background = palette.color(QPalette.Window)
    return QColor(
        *muted_rgb(
            (foreground.red(), foreground.green(), foreground.blue()),
            (background.red(), background.green(), background.blue()),
        )
    )


def muted_color(widget: QWidget) -> QColor:
    """Token fg.muted: texto atenuado con contraste suficiente."""
    return _muted_from_palette(widget.palette())


def help_chip_surface(palette: QPalette | None = None) -> QColor:
    """Token ``help.chip.surface``: fondo de las fichas de tecla de la ayuda.

    Deriva de la superficie del overlay —``AlternateBase``, que
    ``HelpOverlay`` usa como ``Window``— y no de la ventana: en un tema claro
    la oscurece y en uno oscuro la aclara. Si el resultado perdiera el
    contraste mínimo de 4,5:1 con ``WindowText``, se acerca al fondo en
    pasos pequeños hasta recuperarlo; con las paletas de la aplicación la
    primera derivación ya lo cumple.
    """
    palette = app_palette() if palette is None else palette
    surface = palette.color(QPalette.AlternateBase)
    text = palette.color(QPalette.WindowText)
    light = surface.lightness() > 128
    chip = surface.darker(112) if light else surface.lighter(135)
    for _ in range(20):
        if _contrast_ratio(chip, text) >= 4.5:
            break
        chip = chip.lighter(106) if light else chip.darker(106)
    return chip


def _accent_for(palette: QPalette, background: QColor) -> QColor:
    candidates = (palette.color(QPalette.Link), QColor("#5c9cf5"))
    for candidate in candidates:
        if _contrast_ratio(candidate, background) >= 3.0:
            return candidate
    return candidates[-1]


def accent_color(widget: QWidget, background: QColor) -> QColor:
    """Token ``accent`` sobre un fondo concreto, con contraste suficiente.

    Envuelve ``_accent_for`` para que los widgets no necesiten la paleta ni
    decisiones de tema: el lienzo pasa su propio fondo y recibe el acento.
    """
    return _accent_for(widget.palette(), background)


def app_palette() -> QPalette:
    app = QApplication.instance()
    return app.palette() if app is not None else QPalette()


def apply_light_palette(app: QApplication) -> None:
    """Aplica las superficies claras de la aplicación."""
    palette = app.style().standardPalette()
    palette.setColor(QPalette.Window, QColor("#ffffff"))
    palette.setColor(QPalette.WindowText, QColor("#1a1a1a"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#fafafa"))
    palette.setColor(QPalette.Text, QColor("#1a1a1a"))
    palette.setColor(QPalette.Button, QColor("#fafafa"))
    palette.setColor(QPalette.ButtonText, QColor("#1a1a1a"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Light, QColor("#ffffff"))
    palette.setColor(QPalette.Midlight, QColor("#e8e8e8"))
    palette.setColor(QPalette.Mid, QColor("#b8b8b8"))
    palette.setColor(QPalette.Dark, QColor("#8a8a8a"))
    palette.setColor(QPalette.PlaceholderText, QColor("#707070"))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor("#1a1a1a"))
    palette.setColor(QPalette.Link, QColor("#3b7dd8"))
    palette.setColor(QPalette.LinkVisited, QColor("#7b5bb6"))
    palette.setColor(QPalette.Highlight, QColor("#3b7dd8"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def apply_dark_palette(app: QApplication) -> None:
    """Conserva el fondo oscuro del sistema y deriva superficies oscuras."""
    palette = app.style().standardPalette()
    if palette.color(QPalette.Window).lightness() > 128:
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#323232"))
        palette.setColor(QPalette.WindowText, QColor("#ffffff"))
        palette.setColor(QPalette.Base, QColor("#171717"))
        palette.setColor(QPalette.AlternateBase, QColor("#383838"))
        palette.setColor(QPalette.Text, QColor("#ffffff"))
        palette.setColor(QPalette.Button, QColor("#383838"))
        palette.setColor(QPalette.ButtonText, QColor("#ffffff"))
        palette.setColor(QPalette.BrightText, QColor("#ffffff"))
        palette.setColor(QPalette.Light, QColor("#4b4b4b"))
        palette.setColor(QPalette.Midlight, QColor("#434343"))
        palette.setColor(QPalette.Mid, QColor("#555555"))
        palette.setColor(QPalette.Dark, QColor("#6e6e6e"))
        palette.setColor(QPalette.Shadow, QColor("#000000"))
        palette.setColor(QPalette.Highlight, QColor("#5c9cf5"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.Link, QColor("#5c9cf5"))
        palette.setColor(QPalette.LinkVisited, QColor("#b38ff4"))
        palette.setColor(QPalette.PlaceholderText, QColor("#a0a0a0"))
        palette.setColor(QPalette.ToolTipBase, QColor("#383838"))
        palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))

    window = palette.color(QPalette.Window)
    surface = window.lighter(112)
    palette.setColor(QPalette.AlternateBase, surface)
    palette.setColor(QPalette.Button, surface)
    palette.setColor(QPalette.ToolTipBase, surface)
    palette.setColor(QPalette.ToolTipText, palette.color(QPalette.WindowText))
    palette.setColor(QPalette.ButtonText, palette.color(QPalette.WindowText))
    palette.setColor(QPalette.Mid, window.lighter(170))
    palette.setColor(QPalette.Midlight, window.lighter(135))
    palette.setColor(QPalette.Light, window.lighter(150))
    palette.setColor(QPalette.Dark, window.lighter(220))
    palette.setColor(QPalette.PlaceholderText, window.lighter(180))
    palette.setColor(QPalette.Highlight, _accent_for(palette, surface))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def apply_theme(app: QApplication) -> None:
    # La fuente del sistema va primero: la resolución de familias que hace
    # ``QFontInfo`` es el mismo calentamiento que pagaría el primer texto
    # pintado; hacerlo aquí lo deja resuelto para todos los widgets.
    _apply_system_font(app)
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        apply_dark_palette(app)
    elif scheme == Qt.ColorScheme.Light or (
        app.style().standardPalette().color(QPalette.Window).lightness() > 128
    ):
        apply_light_palette(app)
    else:
        apply_dark_palette(app)


def set_muted(label: QLabel) -> None:
    label.setProperty("fotoMuted", True)
    palette = app_palette()
    palette.setColor(QPalette.WindowText, _muted_from_palette(palette))
    label.setPalette(palette)
    label.setForegroundRole(QPalette.WindowText)


def error_color(widget: QWidget) -> QColor:
    if widget.palette().color(QPalette.Window).lightness() > 128:
        return QColor(180, 45, 45)
    return QColor(255, 125, 125)


def warning_color(widget: QWidget) -> QColor:
    """Token estado.advertencia: ámbar legible sobre la superficie actual.

    La zona de advertencia (banner de ubicaciones ausentes, ADR 0010) no es
    un error: es un estado degradado, y usa su propio tono en ambos temas.
    """
    if widget.palette().color(QPalette.Window).lightness() > 128:
        return QColor(196, 128, 0)
    return QColor(240, 190, 90)


def warning_background(widget: QWidget, amount: float = 0.12) -> QColor:
    """Superficie del banner: la ventana ligeramente teñida de advertencia."""
    base = widget.palette().color(QPalette.Window)
    warning = warning_color(widget)
    return QColor(
        round(base.red() * (1 - amount) + warning.red() * amount),
        round(base.green() * (1 - amount) + warning.green() * amount),
        round(base.blue() * (1 - amount) + warning.blue() * amount),
    )


# Chips de etiqueta (ADR 0006): colores fijos, iguales en ambos temas, porque
# se pintan sobre la foto (miniatura, visor) y sobre el panel. El contenido
# (corazón y texto) es blanco sobre cualquiera de los dos; el agujero de la
# etiqueta es transparente.
CHIP_FAVORITE_COLOR = QColor("#b03d5d")
CHIP_TAG_COLOR = QColor("#222226")
CHIP_CONTENT_COLOR = QColor("#ffffff")


# --- Plantillas QSS de los widgets -----------------------------------------
# Todo el QSS estructural de la UI vive aquí: los widgets consumen su
# plantilla en el constructor y la re-aplican vía ``reapply_style`` al cambiar
# de tema. Los ``palette(...)`` de cada regla se resuelven contra la paleta
# del widget que la recibe.

# Bloque de scrollbar compartido: la usan la rejilla (GRID_QSS), el scroll del
# panel de metadatos (PANEL_SCROLL_QSS) y el cuerpo de la ayuda
# (HELP_BODY_QSS).
_SCROLLBAR_QSS = """
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical {
    background: palette(mid); min-height: 24px; border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

# Fondo y selección de la rejilla. Consumidor: ``PhotoGrid`` (grid.py).
GRID_QSS = """
QListWidget { background: palette(window); }
QListWidget::item:selected { background: transparent; }
""" + _SCROLLBAR_QSS

# Superficie transparente del scroll del panel de metadatos. Consumidor:
# ``MetadataPanel`` sobre su ``self.scroll`` (panels.py).
PANEL_SCROLL_QSS = """
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
""" + _SCROLLBAR_QSS

# Ventana flotante de completado. Consumidor: ``CompletionPopup`` (panels.py).
COMPLETION_QSS = """
QFrame#completionPopup {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 4px;
}
"""

# Línea de comandos/búsqueda sin marco. Consumidor: ``StatusBar`` sobre su
# ``self.prompt`` (panels.py).
PROMPT_QSS = """
QLineEdit {
    background: transparent;
    border: none;
    padding: 0;
}
"""

# Cuerpo de la ayuda. Consumidor: ``HelpOverlay`` sobre ``body`` (help.py).
HELP_BODY_QSS = """
QTextBrowser { background: transparent; color: palette(window-text); }
""" + _SCROLLBAR_QSS


def reapply_style(widget: QWidget) -> None:
    """Fuerza a Qt a re-resolver el QSS actual del widget.

    Qt no reinterpreta las funciones ``palette(...)`` del QSS cuando la
    paleta cambia: re-asignar la cadena provoca el re-parse mientras el
    estilo vive en ``theme``. Llamada desde ``retheme()``.
    """
    widget.setStyleSheet(widget.styleSheet())


