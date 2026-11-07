"""El visor de fotos: lienzo con zoom y paneo, y decodificación diferida.

``PhotoCanvas`` pinta la foto entera con zoom/paneo (no decodifica),
``PhotoViewer`` es su contenedor y ``ImageLoadTask`` decodifica el original
fuera del hilo de UI para alimentar la caché LRU de la ventana.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QRunnable, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QVBoxLayout, QWidget

from foto.ui.keybindings import VIEWER, event_modifiers, key_action
from foto.ui.theme import muted_color, ui_font

CANVAS_MARGIN = 8


class ImageLoaderSignals(QObject):
    loaded = Signal(str, QImage)
    failed = Signal(str, str)


class ImageLoadTask(QRunnable):
    """Decodifica una imagen original fuera del hilo de UI."""

    def __init__(self, key: str, path: Path) -> None:
        super().__init__()
        self.key = key
        self.path = path
        self.signals = ImageLoaderSignals()

    def run(self) -> None:
        try:
            reader = QImageReader(str(self.path))
            reader.setAutoTransform(True)
            if not reader.canRead():
                self.signals.failed.emit(
                    self.key, reader.errorString() or "formato no soportado"
                )
                return
            image = reader.read()
            if image.isNull():
                self.signals.failed.emit(
                    self.key, reader.errorString() or "imagen no válida"
                )
                return
            self.signals.loaded.emit(self.key, image)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - plugins de imagen
            self.signals.failed.emit(self.key, str(exc))


class PhotoCanvas(QWidget):
    """Lienzo neutro que ajusta la foto entera y permite zoom y paneo."""

    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("foto actual")
        self.setFocusPolicy(Qt.StrongFocus)
        self._pixmap: QPixmap | None = None
        self._preview = False
        self._zoom: float | None = None
        self._pan = QPointF(0, 0)
        self.focus_ring = True

    def set_image(self, pixmap: QPixmap | None, *, preview: bool = False) -> None:
        self._pixmap = pixmap
        self._preview = preview
        self._zoom = None
        self._pan = QPointF(0, 0)
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self._preview = False
        self._zoom = None
        self._pan = QPointF(0, 0)
        self.update()

    def has_image(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    def is_zoomed(self) -> bool:
        if self._zoom is None:
            return False
        return abs(self._zoom - self._fit_scale()) > 0.001

    def reset_zoom(self) -> None:
        self._zoom = None
        self._pan = QPointF(0, 0)
        self.update()

    def zoom_in(self) -> None:
        base = self._fit_scale() if self._zoom is None else self._zoom
        if base <= 0:
            return
        self._zoom = min(base * 1.25, 16.0)
        self._pan = QPointF(0, 0)
        self.update()

    def zoom_out(self) -> None:
        if self._zoom is None:
            return
        fit = self._fit_scale()
        zoom = max(self._zoom / 1.25, 0.05)
        if self._zoom > fit and zoom <= fit:
            self._zoom = None
            self._pan = QPointF(0, 0)
        else:
            self._zoom = zoom
            self._clamp_pan()
        self.update()

    def toggle_zoom(self) -> None:
        self._zoom = 1.0 if self._zoom is None else None
        self._pan = QPointF(0, 0)
        self.update()

    def pan_by(self, dx: float, dy: float) -> bool:
        if not self.is_zoomed():
            return False
        self._pan += QPointF(dx, dy)
        self._clamp_pan()
        self.update()
        return True

    def _logical_size(self) -> QSizeF:
        if self._pixmap is None or self._pixmap.isNull():
            return QSizeF(0, 0)
        ratio = self._pixmap.devicePixelRatio() or 1.0
        return QSizeF(self._pixmap.width() / ratio, self._pixmap.height() / ratio)

    def _fit_scale(self) -> float:
        size = self._logical_size()
        if size.isEmpty():
            return 0.0
        available_w = max(1, self.width() - 2 * CANVAS_MARGIN)
        available_h = max(1, self.height() - 2 * CANVAS_MARGIN)
        return min(available_w / size.width(), available_h / size.height())

    def _clamp_pan(self) -> None:
        if self._zoom is None or self._pixmap is None:
            self._pan = QPointF(0, 0)
            return
        size = self._logical_size()
        target_w = size.width() * self._zoom
        target_h = size.height() * self._zoom
        max_x = max(0.0, (target_w - self.width()) / 2)
        max_y = max(0.0, (target_h - self.height()) / 2)
        self._pan.setX(max(-max_x, min(max_x, self._pan.x())))
        self._pan.setY(max(-max_y, min(max_y, self._pan.y())))

    def _canvas_color(self) -> QColor:
        window = self.palette().color(QPalette.Window)
        if window.lightness() > 128:
            return window.darker(112)
        return window.darker(135)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._canvas_color())

        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(muted_color(self))
            painter.setFont(ui_font(13))
            painter.drawText(self.rect(), Qt.AlignCenter, "Sin foto")
            self._paint_focus_ring(painter)
            return

        scale = self._fit_scale() if self._zoom is None else self._zoom
        size = self._logical_size()
        target_w = size.width() * scale
        target_h = size.height() * scale
        x = (self.width() - target_w) / 2
        y = (self.height() - target_h) / 2
        if self._zoom is not None:
            x += self._pan.x()
            y += self._pan.y()
        target = QRectF(x, y, target_w, target_h)

        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        self._paint_focus_ring(painter)

    def _paint_focus_ring(self, painter: QPainter) -> None:
        if not self.focus_ring or not self.hasFocus():
            return
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self.palette().color(QPalette.Highlight), 2))
        painter.drawRect(self.rect().adjusted(2, 2, -3, -3))

    def wheelEvent(self, event) -> None:
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            delta = event.angleDelta().y()
            if delta > 0:
                self.action_requested.emit("zoom_in")
            elif delta < 0:
                self.action_requested.emit("zoom_out")
            event.accept()
            return
        event.ignore()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._clamp_pan()
        self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def event(self, event) -> bool:
        # Qt consume Tab para la cadena de foco antes de keyPressEvent.
        if event.type() == QEvent.KeyPress and event.key() in (
            Qt.Key_Tab,
            Qt.Key_Backtab,
        ):
            self.action_requested.emit("focus_panel")
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        shift, ctrl, cmd = event_modifiers(event)
        action = key_action(VIEWER, event.key(), shift=shift, ctrl=ctrl, cmd=cmd)
        if action is not None:
            self.action_requested.emit(action)
            return
        super().keyPressEvent(event)


class PhotoViewer(QWidget):
    """Vista individual: solo la foto grande.

    La fila de miniaturas la aporta la rejilla de la ventana cuando se abre
    con ``grid_row=True``; el visor no contiene una tira propia.
    """

    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("visor de fotos")
        self.canvas = PhotoCanvas(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas, 1)
        self.canvas.action_requested.connect(self.action_requested)

    def focus_canvas(self) -> None:
        self.canvas.setFocus()

    def retheme(self) -> None:
        self.canvas.update()
