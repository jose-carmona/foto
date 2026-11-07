"""Capa de tokens y tipografía de la UI.

Sobre ``QPalette``: las paletas clara y oscura, los colores derivados
(texto atenuado, error, acento) y la tipografía común (``ui_font``). Los
widgets no escriben colores literales: consumen esta capa.

La centralización del ``retheme`` (cada panel sigue re-aplicando su propio
``styleSheet``) queda pendiente como P1.7 del informe de UI.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from foto.ui.presentation import muted_rgb


def ui_font(size: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont()
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


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


def _accent_for(palette: QPalette, background: QColor) -> QColor:
    candidates = (palette.color(QPalette.Link), QColor("#5c9cf5"))
    for candidate in candidates:
        if _contrast_ratio(candidate, background) >= 3.0:
            return candidate
    return candidates[-1]


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


