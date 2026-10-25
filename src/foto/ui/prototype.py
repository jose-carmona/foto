"""Prototipo estático de la barra de estado y el panel de metadatos.

No lee discos, no usa exiftool y no toca la biblioteca: todos los datos son
falsos. Se ejecuta con:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m foto.ui.prototype

La app real arrancará con el panel oculto, como fija docs/diseno-ui.md. En el
prototipo arranca visible para poder evaluarlo sin pulsar nada.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

PHOTO_ROLE = Qt.UserRole + 1
RATING_ROLE = Qt.UserRole + 2
FAVORITE_ROLE = Qt.UserRole + 3

THUMB_W = 400
THUMB_H = 247
GRID_GAP = 16
ITEM_W = THUMB_W + GRID_GAP
ITEM_H = THUMB_H + GRID_GAP
PANEL_W = 320
BAR_H = 28


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
    ratio = 0.62 if background.lightness() > 128 else 0.72
    return QColor(
        round(background.red() + (foreground.red() - background.red()) * ratio),
        round(background.green() + (foreground.green() - background.green()) * ratio),
        round(background.blue() + (foreground.blue() - background.blue()) * ratio),
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


def _app_palette() -> QPalette:
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
    palette = _app_palette()
    palette.setColor(QPalette.WindowText, _muted_from_palette(palette))
    label.setPalette(palette)
    label.setForegroundRole(QPalette.WindowText)


def error_color(widget: QWidget) -> QColor:
    if widget.palette().color(QPalette.Window).lightness() > 128:
        return QColor(180, 45, 45)
    return QColor(255, 125, 125)


@dataclass(frozen=True)
class Photo:
    name: str
    taken: str
    camera: str
    lens: str
    focal: str
    aperture: str
    shutter: str
    iso: str
    folder: str
    fmt: str
    dimensions: str
    size: str
    location: str
    rating: int = 0
    favorite: bool = False
    path: Optional[Path] = None

    @property
    def key(self) -> str:
        """Identifica la foto aunque haya nombres repetidos en la biblioteca."""
        return str(self.path) if self.path is not None else self.name


def photo_accessible_name(photo: Photo) -> str:
    parts = [photo.name]
    if photo.favorite:
        parts.append("favorito")
    if photo.rating:
        parts.append(f"valoración {photo.rating} de 5")
    return ", ".join(parts)


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS = (
    Command("q", "salir de la aplicación"),
    Command("quit", "salir de la aplicación"),
    Command("open", "abrir una carpeta o biblioteca"),
    Command("rescan", "volver a indexar la biblioteca"),
    Command("sort date", "ordenar por fecha"),
    Command("sort name", "ordenar por nombre"),
    Command("sort camera", "ordenar por cámara"),
    Command("help", "mostrar la ayuda"),
)

CAMERAS = (
    ("Fujifilm X100V", "XF23mmF2 R WR", "23 mm"),
    ("Sony ILCE-7RM5", "FE 35mm F1.8", "35 mm"),
    ("Leica Q2", "Summilux 28mm f/1.7", "28 mm"),
    ("Nikon Zf", "NIKKOR Z 40mm f/2", "40 mm"),
)


def make_photos() -> list[Photo]:
    photos: list[Photo] = []
    for i in range(24):
        camera, lens, focal = CAMERAS[i % len(CAMERAS)]
        day = 24 - (i % 20)
        month = 10 if i < 12 else 9
        photos.append(
            Photo(
                name=f"DSCF{1001 + i}.{'RAF' if i % 3 else 'JPG'}",
                taken=f"2026-{month:02d}-{day:02d} {18 - (i % 8):02d}:{(i * 7) % 60:02d}",
                camera=camera,
                lens="" if i % 7 == 3 else lens,
                focal=focal,
                aperture=f"f/{(1.4, 2.0, 2.8, 4.0, 5.6)[i % 5]}",
                shutter=("1/125 s", "1/250 s", "1/500 s", "1/60 s", "2,5 s")[i % 5],
                iso=str((100, 200, 400, 800, 1600)[i % 5]),
                folder=f"2026/{month:02d}",
                fmt="RAW (RAF)" if i % 3 else "JPEG",
                dimensions=f"{6240 - (i % 3) * 240} × {4160 - (i % 3) * 160} px",
                size=f"{18 + (i % 11) * 2},{(i * 3) % 10} MB",
                location="" if i % 7 == 1 else f"41,{3800 + i} N · 2,{1600 + i} E",
                rating=(i * 3) % 6,
                favorite=i % 4 == 0,
            )
        )
    return photos


def make_thumbnail(index: int) -> QIcon:
    pixmap = QPixmap(QSize(THUMB_W, THUMB_H))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    hue = (index * 47 + 140) % 360
    sky = QColor.fromHsv(hue, 55, 205)
    ground = QColor.fromHsv((hue + 20) % 360, 85, 95)
    painter.fillRect(0, 0, THUMB_W, THUMB_H, sky)
    painter.fillRect(0, int(THUMB_H * 0.64), THUMB_W, THUMB_H, ground)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor.fromHsv((hue + 190) % 360, 100, 235))
    radius = 34 + (index % 4) * 9
    painter.drawEllipse(QPoint(THUMB_W - 92, 72), radius, radius)

    painter.setBrush(QColor(0, 0, 0, 35))
    painter.drawRect(0, int(THUMB_H * 0.64), THUMB_W, 2)
    painter.drawRect(48, int(THUMB_H * 0.42), 38, THUMB_H)
    painter.drawRect(96, int(THUMB_H * 0.34), 22, THUMB_H)

    painter.setPen(QColor(255, 255, 255, 150))
    painter.setFont(ui_font(13, QFont.DemiBold))
    painter.drawText(
        QRect(16, THUMB_H - 34, 160, 24),
        Qt.AlignLeft | Qt.AlignVCenter,
        f"{index + 1:02d}",
    )
    painter.end()
    return QIcon(pixmap)


def _paint_selection_shadow(
    painter: QPainter, palette: QPalette, rect: QRect
) -> None:
    """Dibuja una sombra sutil alrededor de la miniatura, sin cubrirla."""
    shadow = palette.color(QPalette.Shadow)
    painter.save()
    painter.setPen(Qt.NoPen)
    for spread, alpha in ((4, 8), (3, 14), (2, 22), (1, 32)):
        color = QColor(shadow)
        color.setAlpha(alpha)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(rect.adjusted(-spread, 1 - spread, spread, 1 + spread)),
            6 + spread,
            6 + spread,
        )
    painter.restore()


class ThumbnailDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        icon_size = option.decorationSize
        if not icon_size.isValid():
            icon_size = QSize(THUMB_W, THUMB_H)
        icon_rect = QRect(QPoint(0, 0), icon_size)
        icon_rect.moveCenter(option.rect.center())

        icon = index.data(Qt.DecorationRole)
        selected = bool(option.state & QStyle.State_Selected)
        focused = bool(option.state & QStyle.State_HasFocus)
        if selected:
            _paint_selection_shadow(painter, option.palette, icon_rect)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(icon_rect), 6, 6)
        painter.save()
        painter.setClipPath(clip)
        if icon and not icon.isNull():
            icon.paint(painter, icon_rect, Qt.AlignCenter)
        else:
            super().paint(painter, option, index)
        painter.restore()

        if selected:
            border = (
                option.palette.color(QPalette.Highlight)
                if focused
                else option.palette.color(QPalette.Mid)
            )
            inset = 2 if focused else 1
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(border, 2 if focused else 1))
            painter.drawRoundedRect(
                QRectF(icon_rect.adjusted(-inset, -inset, inset, inset)),
                6 + inset,
                6 + inset,
            )

        rating = int(index.data(RATING_ROLE) or 0)
        favorite = bool(index.data(FAVORITE_ROLE))
        if rating or favorite:
            parts: list[str] = []
            if favorite:
                parts.append("♥")
            if rating:
                parts.append(f"★{rating}")
            text = " ".join(parts)

            font = ui_font(11, QFont.DemiBold)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            badge_w = metrics.horizontalAdvance(text) + 16
            badge_h = metrics.height() + 8
            badge = QRect(
                icon_rect.right() - badge_w - 12,
                icon_rect.top() + 12,
                badge_w,
                badge_h,
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.drawRoundedRect(badge, 2, 2)
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(badge, Qt.AlignCenter, text)

        painter.restore()


class PhotoGrid(QListWidget):
    """Rejilla que traduce las teclas de vim a acciones de la ventana."""

    action_requested = Signal(str)
    focus_panel = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("rejilla de fotos")

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.setStyleSheet(self.styleSheet())
        self.setPalette(palette)
        self.viewport().update()

    def move_selection(self, action: str) -> bool:
        visible = [
            row for row in range(self.count()) if not self.item(row).isHidden()
        ]
        if not visible:
            return True
        try:
            position = visible.index(self.currentRow())
        except ValueError:
            position = 0

        if action == "home":
            target = 0
        elif action == "end":
            target = len(visible) - 1
        else:
            columns = max(1, self.viewport().width() // ITEM_W)
            page_rows = max(1, self.viewport().height() // ITEM_H)
            page = columns * page_rows
            step = {
                "j": columns,
                "k": -columns,
                "h": -1,
                "l": 1,
                "pagedown": page,
                "pageup": -page,
                "halfdown": max(1, page // 2),
                "halfup": -max(1, page // 2),
            }[action]
            target = max(0, min(len(visible) - 1, position + step))

        self.setCurrentItem(self.item(visible[target]))
        self.scrollToItem(self.currentItem(), QAbstractItemView.EnsureVisible)
        return target != position

    def keyPressEvent(self, event):
        key = event.key()

        if key in (Qt.Key_J, Qt.Key_K, Qt.Key_H, Qt.Key_L):
            self.action_requested.emit(
                {
                    Qt.Key_J: "j",
                    Qt.Key_K: "k",
                    Qt.Key_H: "h",
                    Qt.Key_L: "l",
                }[key]
            )
            return
        if key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            self.action_requested.emit(
                {
                    Qt.Key_Up: "k",
                    Qt.Key_Down: "j",
                    Qt.Key_Left: "h",
                    Qt.Key_Right: "l",
                }[key]
            )
            return
        if key == Qt.Key_Home:
            self.action_requested.emit("home")
            return
        if key == Qt.Key_End:
            self.action_requested.emit("end")
            return
        if key == Qt.Key_PageDown:
            self.action_requested.emit("pagedown")
            return
        if key == Qt.Key_PageUp:
            self.action_requested.emit("pageup")
            return
        if key == Qt.Key_D and event.modifiers() & Qt.ControlModifier:
            self.action_requested.emit("halfdown")
            return
        if key == Qt.Key_U and event.modifiers() & Qt.ControlModifier:
            self.action_requested.emit("halfup")
            return
        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            self.focus_panel.emit()
            return
        if key == Qt.Key_Colon:
            self.action_requested.emit(":")
            return
        if key == Qt.Key_Slash:
            self.action_requested.emit("/")
            return
        if key in (Qt.Key_Question, Qt.Key_F1):
            self.action_requested.emit("?")
            return
        if key == Qt.Key_I:
            self.action_requested.emit("i")
            return
        if key == Qt.Key_F:
            self.action_requested.emit("f")
            return
        if key == Qt.Key_Q:
            self.action_requested.emit("q")
            return
        if key == Qt.Key_Escape:
            self.action_requested.emit("esc")
            return
        if Qt.Key_0 <= key <= Qt.Key_5:
            self.action_requested.emit(str(key - Qt.Key_0))
            return

        super().keyPressEvent(event)


class PanelScroll(QScrollArea):
    focus_back = Signal()
    escape_pressed = Signal()
    action_requested = Signal(str)
    focus_changed = Signal(bool)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            self.focus_back.emit()
            return
        if key == Qt.Key_Escape:
            self.escape_pressed.emit()
            return
        bar = self.verticalScrollBar()
        if key in (Qt.Key_Up, Qt.Key_Down):
            delta = 32
            bar.setValue(bar.value() + (-delta if key == Qt.Key_Up else delta))
            return
        if key in (Qt.Key_PageUp, Qt.Key_PageDown):
            delta = bar.pageStep()
            bar.setValue(bar.value() + (-delta if key == Qt.Key_PageUp else delta))
            return
        if key == Qt.Key_Home:
            bar.setValue(bar.minimum())
            return
        if key == Qt.Key_End:
            bar.setValue(bar.maximum())
            return
        if key in (Qt.Key_Left, Qt.Key_Right):
            return
        if key in (Qt.Key_J, Qt.Key_K, Qt.Key_H, Qt.Key_L):
            self.action_requested.emit(
                {
                    Qt.Key_J: "j",
                    Qt.Key_K: "k",
                    Qt.Key_H: "h",
                    Qt.Key_L: "l",
                }[key]
            )
            return
        if key in (
            Qt.Key_Colon,
            Qt.Key_Slash,
            Qt.Key_Question,
            Qt.Key_F1,
            Qt.Key_I,
            Qt.Key_F,
            Qt.Key_Q,
        ):
            self.action_requested.emit(
                {
                    Qt.Key_Colon: ":",
                    Qt.Key_Slash: "/",
                    Qt.Key_Question: "?",
                    Qt.Key_F1: "?",
                    Qt.Key_I: "i",
                    Qt.Key_F: "f",
                    Qt.Key_Q: "q",
                }[key]
            )
            return
        if Qt.Key_0 <= key <= Qt.Key_5:
            self.action_requested.emit(str(key - Qt.Key_0))
            return
        super().keyPressEvent(event)


class MetadataPanel(QFrame):
    """Ficha de metadatos. Sin cabecera: el nombre vive en la barra de estado."""

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("panel de metadatos")
        self.setFrameShape(QFrame.NoFrame)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)

        self.scroll = PanelScroll(self)
        self.scroll.setAccessibleName("contenido del panel de metadatos")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setFocusPolicy(Qt.StrongFocus)
        scroll_palette = self.scroll.palette()
        scroll_palette.setColor(QPalette.Base, scroll_palette.color(QPalette.AlternateBase))
        self.scroll.setPalette(scroll_palette)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(
            """
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical { width: 8px; background: transparent; }
            QScrollBar::handle:vertical {
                background: palette(mid); min-height: 24px; border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            """
        )
        self.scroll.focus_changed.connect(lambda _: self.update())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

        self._scroll_value = 0
        self.show_empty("Sin selección")

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        scroll_palette = _app_palette()
        surface = scroll_palette.color(QPalette.AlternateBase)
        scroll_palette.setColor(QPalette.Window, surface)
        scroll_palette.setColor(QPalette.Base, surface)
        self.scroll.setStyleSheet(self.scroll.styleSheet())
        self.scroll.setPalette(scroll_palette)
        self.scroll.viewport().setAutoFillBackground(False)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if self.scroll.hasFocus():
            painter.setPen(self.palette().color(QPalette.Highlight))
            painter.drawLine(0, 0, 0, self.height())
            painter.drawLine(1, 0, 1, self.height())
        else:
            painter.setPen(self.palette().color(QPalette.Mid))
            painter.drawLine(0, 0, 0, self.height())

    def focus_scroll(self) -> None:
        self.scroll.setFocus()

    def _replace_content(self, content: QWidget) -> None:
        self._scroll_value = self.scroll.verticalScrollBar().value()
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(content)
        QTimer.singleShot(0, self._restore_scroll)

    def _restore_scroll(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(min(self._scroll_value, bar.maximum()))

    def show_empty(self, text: str) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._muted_label(text, 12))
        layout.addStretch(1)
        self._replace_content(content)

    def _muted_label(self, text: str, size: int = 12) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(size))
        set_muted(label)
        label.setWordWrap(True)
        return label

    def set_photo(self, photo: Optional[Photo]) -> None:
        if photo is None:
            self.show_empty("Sin selección")
            return

        sections = (
            (
                "Captura",
                (
                    ("Fecha", self._format_date(photo.taken)),
                    ("Cámara", photo.camera),
                    ("Objetivo", photo.lens),
                ),
            ),
            (
                "Exposición",
                (
                    ("Focal", photo.focal),
                    ("Apertura", photo.aperture),
                    ("Velocidad", photo.shutter),
                    ("ISO", photo.iso),
                ),
            ),
            (
                "Archivo",
                (
                    ("Carpeta", photo.folder),
                    ("Formato", photo.fmt),
                    ("Dimensiones", photo.dimensions),
                    ("Peso", photo.size),
                ),
            ),
            (
                "Ubicación",
                (("Coordenadas", photo.location),),
            ),
        )

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        rendered = False
        for title, rows in sections:
            visible_rows = [(label, value) for label, value in rows if value]
            if not visible_rows:
                continue
            rendered = True
            layout.addWidget(self._section(title, visible_rows))

        if not rendered:
            layout.addWidget(self._muted_label("Sin datos EXIF", 12))

        layout.addStretch(1)
        self._replace_content(content)

    def _section(self, title: str, rows: list[tuple[str, str]]) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setFont(ui_font(11, QFont.DemiBold))
        set_muted(title_label)
        layout.addWidget(title_label)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, 96)
        grid.setColumnStretch(1, 1)

        for row, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setFont(ui_font(11))
            set_muted(key)
            key.setWordWrap(True)
            key.setAlignment(Qt.AlignLeft | Qt.AlignTop)

            text = QLabel(value)
            value_font = ui_font(13)
            value_font.setFeature(QFont.Tag("tnum"), 1)
            text.setFont(value_font)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            text.setAlignment(Qt.AlignLeft | Qt.AlignTop)

            grid.addWidget(key, row, 0, Qt.AlignTop)
            grid.addWidget(text, row, 1)

        layout.addLayout(grid)
        return section

    @staticmethod
    def _format_date(value: str) -> str:
        if not value:
            return ""
        date, _, time = value.partition(" ")
        try:
            year, month, day = date.split("-")
            months = (
                "ene",
                "feb",
                "mar",
                "abr",
                "may",
                "jun",
                "jul",
                "ago",
                "sep",
                "oct",
                "nov",
                "dic",
            )
            return f"{int(day)} {months[int(month) - 1]} {year}, {time}"
        except (ValueError, IndexError):
            return value


class PromptEdit(QLineEdit):
    tab_pressed = Signal()
    up_pressed = Signal()
    down_pressed = Signal()
    enter_pressed = Signal()
    escape_pressed = Signal()

    def event(self, event):
        if event.type() == QEvent.KeyPress and event.key() in (
            Qt.Key_Tab,
            Qt.Key_Backtab,
        ):
            self.tab_pressed.emit()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            self.tab_pressed.emit()
            return
        if key == Qt.Key_Up:
            self.up_pressed.emit()
            return
        if key == Qt.Key_Down:
            self.down_pressed.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.enter_pressed.emit()
            return
        if key == Qt.Key_Escape:
            self.escape_pressed.emit()
            return
        super().keyPressEvent(event)


class CompletionPopup(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("completionPopup")
        self.setFrameShape(QFrame.NoFrame)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.Base))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QFrame#completionPopup {
                background: palette(base);
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
            """
        )
        self.setFixedWidth(400)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(0)
        self.hide()

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.Base))
        self.setStyleSheet(self.styleSheet())
        self.setPalette(palette)
        self.update()

    def set_matches(self, matches: list[tuple[str, str]], selected: int) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

        accent = self.palette().color(QPalette.Highlight).name()
        for index, (name, description) in enumerate(matches):
            row = QWidget()
            row.setFixedHeight(28)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 8, 0)
            row_layout.setSpacing(8)

            marker = QFrame()
            marker.setFixedWidth(2)
            marker.setAttribute(Qt.WA_StyledBackground, True)
            marker.setStyleSheet(
                f"background: {accent if index == selected else 'transparent'};"
            )
            row_layout.addWidget(marker)

            command = QLabel(name)
            command.setFont(ui_font(12, QFont.DemiBold))
            command.setMinimumWidth(120)

            desc = QLabel(description)
            desc.setFont(ui_font(11))
            set_muted(desc)

            row_layout.addWidget(command)
            row_layout.addWidget(desc, 1)
            if index == selected:
                row.setAttribute(Qt.WA_StyledBackground, True)
                row.setStyleSheet("background: palette(alternate-base);")
            self.layout.addWidget(row)
        self.setFixedHeight(len(matches) * 28 + 10)


class StatusBar(QWidget):
    command_submitted = Signal(str)
    search_changed = Signal(str)
    search_committed = Signal(str)
    prompt_opened = Signal()
    prompt_cancelled = Signal(str)
    prompt_closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("barra de estado")
        self.setFixedHeight(BAR_H)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)

        self.prompt_active = False
        self.prompt_mode: Optional[str] = None
        self.error_text: Optional[str] = None
        self.help_mode = False
        self._history: list[str] = []
        self._history_index = 0
        self._matches: list[tuple[str, str]] = []
        self._selected_match = 0
        self._idle_right = ""
        self._search_right = ""
        self._message = ""
        self._help_saved_message = ""
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_message)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(4)

        self.mode_label = QLabel("NORMAL")
        self.mode_label.setFont(ui_font(11, QFont.DemiBold))
        self.mode_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.message_label = QLabel("")
        self.message_label.setFont(ui_font(12))
        self.message_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.prompt = PromptEdit()
        self.prompt.setAccessibleName("línea de comandos o búsqueda")
        self.prompt.setFont(ui_font(12))
        self.prompt.setFrame(False)
        self.prompt.setStyleSheet(
            """
            QLineEdit {
                background: transparent;
                border: none;
                padding: 0;
            }
            """
        )
        self.prompt.hide()
        self.prompt.textChanged.connect(self._on_text_changed)
        self.prompt.tab_pressed.connect(self._complete)
        self.prompt.up_pressed.connect(self._history_up)
        self.prompt.down_pressed.connect(self._history_down)
        self.prompt.enter_pressed.connect(self._enter)
        self.prompt.escape_pressed.connect(self._escape)

        self.right_label = QLabel("")
        right_font = ui_font(11)
        right_font.setFeature(QFont.Tag("tnum"), 1)
        self.right_label.setFont(right_font)
        set_muted(self.right_label)
        self.right_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self.mode_label)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.prompt, 1)
        layout.addWidget(self.right_label)

        self.popup = CompletionPopup(self.window())

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        self.prompt.setPalette(palette)
        self.popup.retheme()
        self.mode_label.setStyleSheet(
            "color: palette(highlight);" if self.prompt_active else ""
        )
        if self.error_text:
            self.message_label.setStyleSheet(f"color: {error_color(self).name()};")
        if self.prompt_mode == "command" and self._matches:
            self.popup.set_matches(self._matches, self._selected_match)
            self.position_popup()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.Mid))
        painter.drawLine(0, 0, self.width(), 0)

    def set_counts(self, visible: int, total: int, filename: str) -> None:
        suffix = f" · {filename}" if filename else ""
        self._idle_right = f"{visible} de {total}{suffix}"
        self._search_right = f"{visible} de {total}"
        self.refresh_right()

    def set_help_mode(self, enabled: bool) -> None:
        self.help_mode = enabled
        self.mode_label.setText("AYUDA" if enabled else "NORMAL")
        if enabled:
            self._help_saved_message = self.message_label.text()
            self.message_label.setText("")
            self.right_label.setText("Esc/q cerrar")
        else:
            if self.error_text or self._message:
                self.message_label.setText(self._help_saved_message)
            self.refresh_right()

    def open_command(self) -> None:
        self.prompt_active = True
        self.prompt_mode = "command"
        self.mode_label.setText(":")
        self.mode_label.setStyleSheet("color: palette(highlight);")
        self.message_label.hide()
        self.prompt.clear()
        self.prompt.show()
        self.prompt.setFocus()
        self._history_index = len(self._history)
        self._refresh_matches()
        self.refresh_right()
        self.prompt_opened.emit()

    def open_search(self, initial: str) -> None:
        self.prompt_active = True
        self.prompt_mode = "search"
        self.mode_label.setText("/")
        self.mode_label.setStyleSheet("color: palette(highlight);")
        self.message_label.hide()
        self.prompt.blockSignals(True)
        self.prompt.setText(initial)
        self.prompt.setCursorPosition(len(initial))
        self.prompt.blockSignals(False)
        self.prompt.show()
        self.prompt.setFocus()
        self.popup.hide()
        self.refresh_right()
        self.prompt_opened.emit()

    def close_prompt(self) -> None:
        self.prompt_active = False
        self.prompt_mode = None
        self.mode_label.setText("NORMAL")
        self.mode_label.setStyleSheet("")
        self.prompt.hide()
        self.prompt.clear()
        self.popup.hide()
        self._matches = []
        if not self.error_text and not self._message:
            self.message_label.setText("")
        self.message_label.show()
        self.refresh_right()
        self.prompt_closed.emit()

    def show_message(self, text: str, milliseconds: int = 3000) -> None:
        if self.error_text or self.help_mode:
            return
        self._message = text
        self.message_label.setText(text)
        self.message_label.setForegroundRole(QPalette.WindowText)
        self._message_timer.start(milliseconds)

    def show_error(self, text: str) -> None:
        self.error_text = text
        self._message_timer.stop()
        self._message = ""
        self.message_label.show()
        self.message_label.setText(f"error: {text}")
        self.message_label.setStyleSheet(f"color: {error_color(self).name()};")
        self.refresh_right()

    def clear_error(self) -> None:
        if not self.error_text:
            return
        self.error_text = None
        self._message = ""
        self.message_label.setText("")
        self.message_label.setStyleSheet("")
        self.refresh_right()

    def refresh_right(self) -> None:
        if self.prompt_active:
            if self.prompt_mode == "search":
                if self._search_right:
                    self.right_label.setText(f"{self._search_right} · Enter · Esc")
                else:
                    self.right_label.setText("Enter confirmar · Esc cancelar")
            else:
                self.right_label.setText("Tab completar · ↑ historial · Enter · Esc")
        elif self.error_text:
            self.right_label.setText("Esc descartar")
        elif self.help_mode:
            self.right_label.setText("Esc cerrar")
        else:
            self.right_label.setText(self._idle_right)

    def _clear_message(self) -> None:
        self._message = ""
        if not self.error_text:
            self.message_label.setText("")

    def _on_text_changed(self, text: str) -> None:
        if not self.prompt_active:
            return
        if self.prompt_mode == "search":
            self.search_changed.emit(text)
        else:
            self._refresh_matches()

    def _refresh_matches(self) -> None:
        text = self.prompt.text().strip().lower()
        self._matches = [
            (command.name, command.description)
            for command in COMMANDS
            if command.name.startswith(text)
        ][:8]
        self._selected_match = 0
        self._show_popup()

    def _show_popup(self) -> None:
        if self.prompt_mode != "command" or not self._matches:
            self.popup.hide()
            return
        self.popup.set_matches(self._matches, self._selected_match)
        self.position_popup()
        self.popup.show()
        self.popup.raise_()

    def position_popup(self) -> None:
        window = self.window()
        prompt_pos = self.prompt.mapTo(window, QPoint(0, 0))
        bar_pos = self.mapTo(window, QPoint(0, 0))
        x = max(8, min(prompt_pos.x(), window.width() - self.popup.width() - 8))
        y = max(8, bar_pos.y() - self.popup.height() - 4)
        self.popup.move(x, y)

    def _complete(self) -> None:
        if self.prompt_mode != "command" or not self._matches:
            return
        if self.prompt.text() != self._matches[self._selected_match][0]:
            self._selected_match = 0
        else:
            self._selected_match = (self._selected_match + 1) % len(self._matches)
        text = self._matches[self._selected_match][0]
        self.prompt.blockSignals(True)
        self.prompt.setText(text)
        self.prompt.setCursorPosition(len(text))
        self.prompt.blockSignals(False)
        self.popup.set_matches(self._matches, self._selected_match)
        self.position_popup()
        self.popup.show()

    def _history_up(self) -> None:
        if self.prompt_mode != "command" or not self._history:
            return
        if self._history_index > 0:
            self._history_index -= 1
        self._set_prompt_text(self._history[self._history_index])

    def _history_down(self) -> None:
        if self.prompt_mode != "command" or not self._history:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._set_prompt_text(self._history[self._history_index])
        else:
            self._history_index = len(self._history)
            self._set_prompt_text("")

    def _set_prompt_text(self, text: str) -> None:
        self.prompt.setText(text)
        self.prompt.setCursorPosition(len(text))

    def _enter(self) -> None:
        text = self.prompt.text()
        mode = self.prompt_mode
        if mode == "command" and text.strip():
            command = text.strip()
            if not self._history or self._history[-1] != command:
                self._history.append(command)
        self.close_prompt()
        if mode == "command":
            self.command_submitted.emit(text)
        elif mode == "search":
            self.search_committed.emit(text)

    def _escape(self) -> None:
        mode = self.prompt_mode or "command"
        self.close_prompt()
        self.prompt_cancelled.emit(mode)


class ContentArea(QWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class HelpOverlay(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAccessibleName("ayuda")
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Ayuda")
        title.setFont(ui_font(20, QFont.DemiBold))
        layout.addWidget(title)

        body = QLabel(
            "<b>Teclas</b><br>"
            "<code>:</code> comando · <code>/</code> búsqueda · "
            "<code>i</code> metadatos · <code>Ctrl+q</code> salir<br>"
            "<code>j k h l</code> o flechas mover · <code>PgUp/PgDn</code> página · "
            "<code>Ctrl+d/u</code> media página · <code>Home/End</code> extremos<br>"
            "<code>f</code> favorito · <code>1–5</code> valoración · "
            "<code>0</code> quitar · <code>Tab</code> foco del panel<br>"
            "<code>Esc</code> cerrar o limpiar la búsqueda<br><br>"
            "<b>Comandos</b><br>"
            "<code>:q</code>/<code>:quit</code> salir · <code>:open [ruta]</code> abrir · "
            "<code>:rescan</code> reindexar<br>"
            "<code>:sort date|name|camera</code> ordenar · "
            "<code>:help</code> ayuda"
        )
        body.setFont(ui_font(13))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(body)
        layout.addStretch(1)

        hint = QLabel("Esc o q para cerrar")
        hint.setFont(ui_font(11))
        set_muted(hint)
        layout.addWidget(hint)

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        self.update()

    def focusNextPrevChild(self, next: bool) -> bool:
        return True

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.Mid))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class PrototypeWindow(QMainWindow):
    def __init__(
        self,
        photos: list[Photo],
        *,
        thumbnails: Optional[dict[str, QIcon]] = None,
        title: Optional[str] = None,
        initial_panel_visible: bool = True,
        on_open: Optional[Callable[[str], None]] = None,
        on_rescan: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.photos = {photo.key: photo for photo in photos}
        self.thumbnails = dict(thumbnails or {})
        self._on_open = on_open
        self._on_rescan = on_rescan
        self.search_text = ""
        self._search_origin: Optional[str] = None
        self.panel_visible = initial_panel_visible
        self.panel_overlay = False

        self.setWindowTitle(title or "foto — prototipo de UI")
        self.resize(1280, 960)
        self.setMinimumSize(640, 480)

        self.content = ContentArea()
        self.content_blocker = QWidget(self.content)
        self.content_blocker.setFocusPolicy(Qt.NoFocus)
        self.content_blocker.hide()
        self.content.resized.connect(self._layout_content)
        self.grid = PhotoGrid(self.content)
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setFlow(QListWidget.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setSelectionMode(QListWidget.SingleSelection)
        self.grid.setSpacing(0)
        self.grid.setFrameShape(QListWidget.NoFrame)
        self.grid.setIconSize(QSize(THUMB_W, THUMB_H))
        self.grid.setUniformItemSizes(True)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.grid.setItemDelegate(ThumbnailDelegate(self.grid))
        self.grid.setStyleSheet(
            """
            QListWidget { background: palette(window); }
            QListWidget::item:selected { background: transparent; }
            QScrollBar:vertical { width: 8px; background: transparent; }
            QScrollBar::handle:vertical {
                background: palette(mid); min-height: 24px; border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            """
        )
        palette = self.grid.palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.grid.setPalette(palette)

        self.panel = MetadataPanel()
        self.panel.setParent(self.content)

        self.empty_label = QLabel("", self.content)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setFont(ui_font(13))
        set_muted(self.empty_label)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()

        self.status = StatusBar(self)

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(self.content, 1)
        outer_layout.addWidget(self.status, 0)
        self.setCentralWidget(outer)

        self.help_overlay = HelpOverlay(self)
        self.help_overlay.hide()

        self._populate_grid(photos)
        self._update_empty_state()
        self.grid.currentItemChanged.connect(self._on_current_changed)
        self.grid.action_requested.connect(self._on_action)
        self.grid.focus_panel.connect(self._focus_panel)
        self.panel.scroll.focus_back.connect(self._focus_grid)
        self.panel.scroll.escape_pressed.connect(self._handle_escape)
        self.panel.scroll.action_requested.connect(self._on_action)
        self.status.command_submitted.connect(self._run_command)
        self.status.search_changed.connect(self._apply_filter)
        self.status.search_committed.connect(self._commit_search)
        self.status.prompt_cancelled.connect(self._cancel_prompt)
        self.status.prompt_opened.connect(self._show_content_blocker)
        self.status.prompt_closed.connect(self._hide_content_blocker)
        self.status.prompt_closed.connect(self._focus_grid)

        quit_action = QAction(self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(QApplication.quit)
        self.addAction(quit_action)

        self.status.set_counts(len(photos), len(photos), photos[0].name if photos else "")
        if photos:
            self.grid.setCurrentRow(0)
        self._layout_content()
        QTimer.singleShot(0, self._focus_grid)
        QTimer.singleShot(
            400,
            lambda: self.status.show_message(
                "i panel · : comandos · / búsqueda · ? ayuda", 4000
            ),
        )
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            self._on_color_scheme_changed
        )

    def retheme(self) -> None:
        palette = _app_palette()
        self.setPalette(palette)
        central = self.centralWidget()
        if central is not None:
            central.setPalette(palette)
        self.content.setPalette(palette)
        self.grid.retheme()
        self.panel.retheme()
        self.status.retheme()
        self.help_overlay.retheme()
        for label in self.findChildren(QLabel):
            if label.property("fotoMuted"):
                set_muted(label)
        self.content.update()
        self.grid.viewport().update()
        self.update()

    def _on_color_scheme_changed(self, *_args) -> None:
        # Cocoa actualiza la paleta estándar justo después de la señal.
        QTimer.singleShot(0, self._apply_system_theme)

    def _apply_system_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app)
            self.retheme()

    def _populate_grid(self, photos: list[Photo]) -> None:
        self.grid.clear()
        for index, photo in enumerate(photos):
            item = QListWidgetItem()
            icon = self.thumbnails.get(photo.key)
            if icon is None or icon.isNull():
                icon = make_thumbnail(index)
            item.setIcon(icon)
            item.setData(PHOTO_ROLE, photo.key)
            item.setData(RATING_ROLE, photo.rating)
            item.setData(FAVORITE_ROLE, photo.favorite)
            item.setData(Qt.AccessibleTextRole, photo_accessible_name(photo))
            item.setSizeHint(QSize(ITEM_W, ITEM_H))
            item.setText("")
            self.grid.addItem(item)

    def set_photos(
        self,
        photos: list[Photo],
        thumbnails: Optional[dict[str, QIcon]] = None,
    ) -> None:
        """Sustituye la biblioteca visible conservando la foto actual si sigue."""
        current = self.grid.currentItem()
        current_key = current.data(PHOTO_ROLE) if current is not None else None
        self.photos = {photo.key: photo for photo in photos}
        if thumbnails is not None:
            self.thumbnails = dict(thumbnails)
        self.search_text = ""
        self._search_origin = None
        self.grid.blockSignals(True)
        try:
            self._populate_grid(photos)
            self.grid.setCurrentRow(-1)

            target = None
            for row in range(self.grid.count()):
                item = self.grid.item(row)
                if item.data(PHOTO_ROLE) == current_key:
                    target = item
                    break
            if target is None and self.grid.count():
                target = self.grid.item(0)
            if target is not None:
                self.grid.setCurrentItem(target)
                self.grid.scrollToItem(target, QAbstractItemView.EnsureVisible)
            else:
                self.panel.show_empty("Sin selección")
        finally:
            self.grid.blockSignals(False)

        self.status.clear_error()
        photo = self._current_photo()
        filename = photo.name if photo else ""
        self.status.set_counts(self._visible_count(), len(self.photos), filename)
        self._update_empty_state()
        self._on_current_changed(self.grid.currentItem(), None)
        self._focus_grid()

    def _layout_content(self) -> None:
        width = self.content.width()
        height = self.content.height()
        if self.panel_visible:
            panel_w = min(PANEL_W, max(280, width - 48))
            self.panel_overlay = width < 900
            if self.panel_overlay:
                grid_w = width
                self.grid.setGeometry(16, 16, max(0, width - 32), max(0, height - 32))
            else:
                grid_w = width - panel_w
                self.grid.setGeometry(16, 16, max(0, grid_w - 32), max(0, height - 32))
            self.panel.setGeometry(width - panel_w, 0, panel_w, height)
            self.panel.show()
            self.panel.raise_()
        else:
            self.panel_overlay = False
            self.grid.setGeometry(16, 16, max(0, width - 32), max(0, height - 32))
            self.panel.hide()
        self._position_empty_label()
        if self.panel_visible:
            self.panel.raise_()
        if self.content_blocker.isVisible():
            self.content_blocker.setGeometry(0, 0, width, height)
            self.content_blocker.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_content()
        self.status.position_popup()
        if self.help_overlay.isVisible():
            self._center_help()

    def _center_help(self) -> None:
        content_rect = self.content.geometry()
        width = min(560, max(320, content_rect.width() - 64))
        height = min(380, max(260, content_rect.height() - 64))
        x = content_rect.x() + (content_rect.width() - width) // 2
        y = content_rect.y() + (content_rect.height() - height) // 2
        self.help_overlay.setGeometry(x, y, width, height)

    def _show_content_blocker(self) -> None:
        self.content_blocker.setGeometry(0, 0, self.content.width(), self.content.height())
        self.content_blocker.show()
        self.content_blocker.raise_()

    def _hide_content_blocker(self) -> None:
        self.content_blocker.hide()

    def _open_search(self) -> None:
        photo = self._current_photo()
        self._search_origin = photo.key if photo else None
        self.status.open_search(self.search_text)

    def _focus_grid(self) -> None:
        self.grid.setFocus()

    def _focus_panel(self) -> None:
        if not self.panel_visible:
            self.panel_visible = True
            self._layout_content()
        self.panel.focus_scroll()

    def _current_photo(self) -> Optional[Photo]:
        item = self.grid.currentItem()
        if item is None:
            return None
        return self.photos.get(item.data(PHOTO_ROLE))

    def _on_current_changed(self, current, previous) -> None:
        photo = self._current_photo()
        self.panel.set_photo(photo)
        filename = photo.name if photo else ""
        self.status.set_counts(self._visible_count(), len(self.photos), filename)

    def _visible_count(self) -> int:
        return sum(
            1 for row in range(self.grid.count()) if not self.grid.item(row).isHidden()
        )

    def _position_empty_label(self) -> None:
        if not self.empty_label.isVisible():
            return
        panel_w = self.panel.width() if self.panel_visible and self.panel_overlay else 0
        visible_w = max(0, self.grid.width() - panel_w)
        label_w = min(420, max(200, visible_w - 64))
        label_h = 64
        self.empty_label.setGeometry(
            self.grid.x() + (visible_w - label_w) // 2,
            self.grid.y() + (self.grid.height() - label_h) // 2,
            label_w,
            label_h,
        )

    def _update_empty_state(self) -> None:
        if self._visible_count() == 0:
            if self.status.prompt_mode == "search":
                self.empty_label.setText("Sin coincidencias\nEsc cancela la búsqueda")
            elif self.search_text:
                self.empty_label.setText("Sin coincidencias\nEsc limpia la búsqueda")
            else:
                self.empty_label.setText(
                    "No hay fotos en esta biblioteca\n:open para abrir otra carpeta"
                )
            self.empty_label.show()
            self._position_empty_label()
            self.empty_label.raise_()
            if self.panel_visible:
                self.panel.raise_()
            if self.content_blocker.isVisible():
                self.content_blocker.raise_()
        else:
            self.empty_label.hide()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            photo = self.photos.get(item.data(PHOTO_ROLE))
            visible = needle in photo.name.lower() if photo else True
            item.setHidden(not visible)

        current = self.grid.currentItem()
        if current is None or current.isHidden():
            visible_items = [
                self.grid.item(row)
                for row in range(self.grid.count())
                if not self.grid.item(row).isHidden()
            ]
            if visible_items:
                self.grid.setCurrentItem(visible_items[0])
            else:
                self.grid.setCurrentRow(-1)
                self.panel.show_empty("Sin coincidencias")

        photo = self._current_photo()
        filename = photo.name if photo else ""
        self.status.set_counts(self._visible_count(), len(self.photos), filename)
        self._update_empty_state()

    def _commit_search(self, text: str) -> None:
        self.search_text = text.strip()
        self._search_origin = None
        self._apply_filter(self.search_text)
        if self.search_text:
            self.status.show_message(
                f"{self._visible_count()} coincidencias", 3000
            )
        self._focus_grid()

    def _cancel_prompt(self, mode: str) -> None:
        if mode == "search":
            self._apply_filter(self.search_text)
            self._restore_search_origin()
        self._focus_grid()

    def _restore_search_origin(self) -> None:
        key = self._search_origin
        self._search_origin = None
        if not key:
            return
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item.data(PHOTO_ROLE) == key and not item.isHidden():
                self.grid.setCurrentItem(item)
                self.grid.scrollToItem(item, QAbstractItemView.EnsureVisible)
                return

    def _run_command(self, text: str) -> None:
        command = text.strip()
        if not command:
            return
        if command in ("q", "quit"):
            QApplication.quit()
            return
        if command == "help":
            self._toggle_help()
            return
        if command == "rescan":
            if self._on_rescan is not None:
                self._on_rescan()
            else:
                self.status.show_message("reindexando…", 3000)
                QTimer.singleShot(
                    1200,
                    lambda: self.status.show_message(
                        f"{len(self.photos)} fotos indexadas", 3000
                    ),
                )
            return
        if command == "open" or command.startswith("open "):
            argument = command[5:].strip()
            if self._on_open is not None:
                self._on_open(argument)
            else:
                suffix = f": {argument}" if argument else ""
                self.status.show_message(
                    f"selector nativo no implementado en el prototipo{suffix}", 4000
                )
            return
        if command == "sort" or command.startswith("sort "):
            key = command[4:].strip()
            if key in ("date", "name", "camera"):
                self._sort_grid(key)
                return
            self.status.show_error("uso: :sort date | name | camera")
            return
        self.status.show_error(
            f"comando desconocido: {command} · :help para ver los comandos"
        )

    def _sort_grid(self, key: str) -> None:
        current = self.grid.currentItem()
        current_key = current.data(PHOTO_ROLE) if current else None

        self.grid.blockSignals(True)
        items = [self.grid.takeItem(0) for _ in range(self.grid.count())]

        def sort_key(item: QListWidgetItem):
            photo_key = item.data(PHOTO_ROLE)
            photo = self.photos[photo_key]
            if key == "date":
                return photo.taken
            if key == "camera":
                return (photo.camera.lower(), photo.name.lower())
            return photo.name.lower()

        items.sort(key=sort_key, reverse=(key == "date"))
        for item in items:
            self.grid.addItem(item)

        self.grid.setCurrentRow(-1)
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item.data(PHOTO_ROLE) == current_key:
                self.grid.setCurrentItem(item)
                break
        self.grid.blockSignals(False)

        self._apply_filter(self.search_text)
        self._on_current_changed(self.grid.currentItem(), None)
        self.status.show_message(f"ordenado por {key}", 3000)

    def _on_action(self, action: str) -> None:
        if self.help_overlay.isVisible():
            if action in ("esc", "?", "q"):
                self._toggle_help()
            return
        if self.status.prompt_active:
            if action == "esc":
                self._handle_escape()
            return
        if action in (
            "j",
            "k",
            "h",
            "l",
            "home",
            "end",
            "pagedown",
            "pageup",
            "halfdown",
            "halfup",
        ):
            self._move_cursor(action)
            return
        if action == ":":
            self.status.open_command()
            return
        if action == "/":
            self._open_search()
            return
        if action == "?":
            self._toggle_help()
            return
        if action == "i":
            self.toggle_panel()
            return
        if action == "esc":
            self._handle_escape()
            return
        if action == "q":
            self.status.show_message("usa Ctrl+q para salir", 3000)
            return
        if action == "f":
            self._toggle_favorite()
            return
        if action.isdigit():
            self._set_rating(int(action))

    def _move_cursor(self, action: str) -> None:
        if not self.grid.move_selection(action):
            self.status.show_message("fin de la lista", 1500)

    def _handle_escape(self) -> None:
        if self.status.prompt_active:
            self.status._escape()
            return
        if self.help_overlay.isVisible():
            self._toggle_help()
            return
        if self.status.error_text:
            self.status.clear_error()
            return
        if self.panel_visible and self.panel_overlay:
            self.toggle_panel()
            return
        if self.panel.scroll.hasFocus():
            self._focus_grid()
            return
        if self.search_text:
            self.search_text = ""
            self._apply_filter("")
            self.status.show_message("búsqueda limpiada", 2000)
        self._focus_grid()

    def toggle_panel(self) -> None:
        had_focus = self.panel.scroll.hasFocus()
        self.panel_visible = not self.panel_visible
        self._layout_content()
        if self.panel_visible:
            self.status.show_message("panel de metadatos", 2000)
        else:
            if had_focus:
                self._focus_grid()
            self.status.show_message("panel oculto", 2000)

    def _toggle_help(self) -> None:
        if self.help_overlay.isVisible():
            self.help_overlay.hide()
            self._hide_content_blocker()
            self.status.set_help_mode(False)
            self._focus_grid()
        else:
            self._center_help()
            self._show_content_blocker()
            self.help_overlay.show()
            self.help_overlay.raise_()
            self.help_overlay.setFocus()
            self.status.set_help_mode(True)

    def _toggle_favorite(self) -> None:
        photo = self._current_photo()
        item = self.grid.currentItem()
        if photo is None or item is None:
            return
        updated = replace(photo, favorite=not photo.favorite)
        self.photos[photo.key] = updated
        item.setData(FAVORITE_ROLE, updated.favorite)
        item.setData(Qt.AccessibleTextRole, photo_accessible_name(updated))
        self.grid.viewport().update()
        self.status.show_message(
            "favorito" if updated.favorite else "sin favorito", 2000
        )

    def _set_rating(self, rating: int) -> None:
        photo = self._current_photo()
        item = self.grid.currentItem()
        if photo is None or item is None:
            return
        updated = replace(photo, rating=rating)
        self.photos[photo.key] = updated
        item.setData(RATING_ROLE, updated.rating)
        item.setData(Qt.AccessibleTextRole, photo_accessible_name(updated))
        self.grid.viewport().update()
        self.status.show_message(
            f"valoración {rating}" if rating else "sin valoración", 2000
        )

    def keyPressEvent(self, event):
        if self.status.prompt_active:
            if event.key() == Qt.Key_Escape:
                self._handle_escape()
            else:
                super().keyPressEvent(event)
            return
        if self.help_overlay.isVisible():
            if event.key() in (Qt.Key_Escape, Qt.Key_F1, Qt.Key_Question, Qt.Key_Q):
                self._toggle_help()
            return

        key = event.key()
        if key == Qt.Key_Colon:
            self.status.open_command()
            return
        if key == Qt.Key_Slash:
            self._open_search()
            return
        if key in (Qt.Key_Question, Qt.Key_F1):
            self._toggle_help()
            return
        if key == Qt.Key_I:
            self.toggle_panel()
            return
        if key == Qt.Key_Escape:
            self._handle_escape()
            return
        if key == Qt.Key_F:
            self._toggle_favorite()
            return
        if key == Qt.Key_Q:
            self.status.show_message("usa Ctrl+q para salir", 3000)
            return
        if key in (Qt.Key_J, Qt.Key_K, Qt.Key_H, Qt.Key_L):
            self._move_cursor(
                {
                    Qt.Key_J: "j",
                    Qt.Key_K: "k",
                    Qt.Key_H: "h",
                    Qt.Key_L: "l",
                }[key]
            )
            return
        if Qt.Key_0 <= key <= Qt.Key_5:
            self._set_rating(key - Qt.Key_0)
            return
        super().keyPressEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    photos = make_photos()
    window = PrototypeWindow(photos)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
