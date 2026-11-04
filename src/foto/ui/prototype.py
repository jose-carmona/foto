"""UI de foto: rejilla, visor con la rejilla en una fila y panel de metadatos.

El prototipo usa datos falsos; las mismas clases sirven a la app real, que pasa
`Photo.path` y miniaturas reales. El visor decodifica el original en segundo
plano sin tocar la biblioteca. No usa exiftool.

Se ejecuta con:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m foto.ui.prototype

La app real arranca con el panel oculto, como fija docs/diseno-ui.md. En el
prototipo arranca visible para poder evaluarlo sin pulsar nada.

La ventana se compone de cuatro regiones: zona de rejilla (siempre visible),
zona de estado (barra inferior de 28 px, siempre visible), panel de metadatos
(columna derecha de ancho fijo, mostrable) y panel de foto (el visor, la misma
foto seleccionada, mostrable). Los paneles solo cambian la dimensión de la
zona de rejilla; su comportamiento —navegación, grupos, scroll, selección— es
idéntico en todos los estados, y la miniatura seleccionada queda siempre a la
vista. Esto lo fija prompts/refabricacion-visual-visor.md.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    QSizeF,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QImageReader,
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
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from foto.core.thumbnails import eviction_candidates

PHOTO_ROLE = Qt.UserRole + 1
FAVORITE_ROLE = Qt.UserRole + 2
GROUP_ROLE = Qt.UserRole + 3
HEADER_ROLE = Qt.UserRole + 4

# Qt remapea los modificadores en macOS: ⌘ Command llega como ControlModifier
# y la tecla Ctrl física como MetaModifier (documentado en Qt::KeyboardModifier).
# La media página debe ser Ctrl+d/Ctrl+u con la tecla Ctrl física en todas las
# plataformas (docs/diseno-ui.md), así que la máscara depende del sistema.
_CTRL_MASK = Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier

THUMB_W = 400
THUMB_H = 247
GRID_GAP = 16
GRID_MARGIN = 16
ITEM_W = THUMB_W + GRID_GAP
ITEM_H = THUMB_H + GRID_GAP
GROUP_HEADER_H = 52
PANEL_W = 426
BAR_H = 28
STRIP_THUMB_H = 96
STRIP_THUMB_W = round(STRIP_THUMB_H * THUMB_W / THUMB_H)
STRIP_GAP = 8
STRIP_VPAD = 8
STRIP_H = STRIP_THUMB_H + 2 * STRIP_VPAD + 8
CANVAS_MARGIN = 8
PAN_STEP = 48
IMAGE_CACHE_SIZE = 3
# Ventana de miniaturas en memoria. Cada icono de 400×247 ocupa ~395 KB; 160
# cubre de sobra un viewport 4K y limita la memoria a unos 63 MB.
THUMBNAIL_CACHE_SIZE = 160


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
    favorite: bool = False
    path: Path | None = None
    canonical_rel_path: str | None = None
    other_locations: tuple[str, ...] = ()
    photo_hash: str | None = None

    @property
    def key(self) -> str:
        """Identifica la foto aunque haya nombres repetidos en la biblioteca."""
        return str(self.path) if self.path is not None else self.name


def photo_accessible_name(photo: Photo) -> str:
    parts = [photo.name]
    if photo.favorite:
        parts.append("favorito")
    return ", ".join(parts)


def photo_search_text(photo: Photo) -> str:
    """Documento de búsqueda normalizado, una entrada por foto (V03).

    Examina nombre, ruta relativa (carpeta y ubicación canónica), réplicas,
    cámara, objetivo, tipo, fecha y el resto de campos indexados que el
    catálogo aporta al modelo de UI. El filtrado es en memoria, contra este
    documento, sin volver a recorrer el filesystem (ADR 0003).
    """
    parts = (
        photo.name,
        photo.taken,
        photo.camera,
        photo.lens,
        photo.focal,
        photo.aperture,
        photo.shutter,
        photo.iso,
        photo.folder,
        photo.fmt,
        photo.location,
        photo.canonical_rel_path or "",
        " ".join(photo.other_locations),
        photo.photo_hash or "",
    )
    return " ".join(part.casefold() for part in parts if part)


@dataclass(frozen=True)
class StripItem:
    """Una miniatura de la tira inferior del visor."""

    key: str
    icon: QIcon
    favorite: bool
    accessible_name: str


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS = (
    Command("q", "salir de la aplicación"),
    Command("quit", "salir de la aplicación"),
    Command("sort date", "ordenar por fecha"),
    Command("sort camera", "ordenar por cámara"),
    Command("help", "mostrar la ayuda"),
)

DEFAULT_SORT = "date"
SORT_KEYS = ("date", "camera")

MONTH_NAMES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def photo_sort_value(photo: Photo, key: str) -> tuple[bool, str]:
    """Clave de ordenación; las fotos sin dato quedan siempre al final."""
    if key == "camera":
        camera = photo.camera.strip().casefold()
        return (not camera, camera)
    taken = photo.taken.strip()
    # Con reverse=True, True ordena antes: las fotos con fecha van primero.
    return (bool(taken), taken)


def sorted_photos(photos: list[Photo], key: str) -> list[Photo]:
    """Ordena por fecha (más nueva primero) o cámara; desempata por nombre."""
    ordered = sorted(photos, key=lambda photo: photo.name.casefold())
    ordered.sort(
        key=lambda photo: photo_sort_value(photo, key),
        reverse=(key == "date"),
    )
    return ordered


def vertical_row_target(
    action: str,
    rows: Sequence[tuple[int, tuple[int, ...]]],
    *,
    row_index: int,
    step_top: int,
) -> int:
    """Índice de línea visual destino para un movimiento vertical.

    ``rows`` describe la rejilla renderizada: una entrada ``(top, centros_x)``
    por línea visual con fotos; las cabeceras de grupo no aparecen, de modo
    que ``j``/``k`` las saltan. Con la agrupación por fecha o cámara las
    líneas pueden ser parciales, así que el destino se deduce de la geometría
    (altura de línea) y no de un número fijo de elementos por línea.
    """
    if action == "j":
        return min(row_index + 1, len(rows) - 1)
    if action == "k":
        return max(row_index - 1, 0)
    if action in ("pagedown", "pageup", "halfdown", "halfup"):
        top = rows[row_index][0]
        if action in ("pagedown", "halfdown"):
            threshold = top + step_top
            for index in range(row_index + 1, len(rows)):
                if rows[index][0] >= threshold:
                    return index
            return len(rows) - 1
        threshold = top - step_top
        for index in range(row_index - 1, -1, -1):
            if rows[index][0] <= threshold:
                return index
        return 0
    raise ValueError(f"acción vertical desconocida: {action}")


def photo_group(photo: Photo, key: str) -> tuple[str, str]:
    """Devuelve la clave y la etiqueta del grupo de una foto."""
    if key == "camera":
        camera = photo.camera.strip()
        if not camera:
            return "", "Sin cámara"
        return camera.casefold(), camera

    taken = photo.taken.strip()
    if not taken:
        return "", "Sin fecha"
    date = taken.split(" ", 1)[0]
    parts = date.split("-")
    if len(parts) >= 2:
        try:
            year = int(parts[0])
            month = int(parts[1])
        except ValueError:
            return date, date
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}", f"{MONTH_NAMES[month - 1]} de {year}"
    return date, date


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


def is_group_start(key: int, shift: bool) -> bool:
    """Detecta ``0``: primera foto del grupo.

    Se saca ``0`` del rango de dígitos reservado para que no caiga como acción
    suelta sin consumidor.
    """
    return key == Qt.Key_0 and not shift


def is_group_end(key: int, shift: bool) -> bool:
    """Detecta ``$``: última foto del grupo.

    El ``$`` vive en su propia tecla en algunas distribuciones y en ``Shift+4``
    o ``Shift+5`` en otras; se aceptan ambas hasta que llegue el nuevo uso de
    esos dígitos.
    """
    return key == Qt.Key_Dollar or (shift and key in (Qt.Key_4, Qt.Key_5))


def viewer_key_action(event) -> str | None:
    """Traduce una tecla del visor a una acción, o None si no aplica."""
    key = event.key()
    modifiers = event.modifiers()
    shift = bool(modifiers & Qt.ShiftModifier)
    if modifiers & (Qt.ControlModifier | Qt.MetaModifier):
        return None

    if is_group_start(key, shift):
        return "group_first"
    if is_group_end(key, shift):
        return "group_last"
    if key == Qt.Key_J:
        return "pan_down" if shift else "next"
    if key == Qt.Key_K:
        return "pan_up" if shift else "previous"
    if key == Qt.Key_H and not shift:
        return "pan_left"
    if key == Qt.Key_L and not shift:
        return "pan_right"
    if key in (Qt.Key_Up, Qt.Key_Left):
        return "previous"
    if key in (Qt.Key_Down, Qt.Key_Right):
        return "next"
    if key in (Qt.Key_Plus, Qt.Key_Equal):
        return "zoom_in"
    if key == Qt.Key_Minus:
        return "zoom_out"
    if key == Qt.Key_Z and not shift:
        return "toggle_zoom"
    if key == Qt.Key_G:
        return "last" if shift else "g"
    if key == Qt.Key_Home:
        return "first"
    if key == Qt.Key_End:
        return "last"
    if key == Qt.Key_Q:
        return "close"
    if key == Qt.Key_Escape:
        return "escape"
    if key == Qt.Key_I:
        return "panel"
    if key == Qt.Key_F:
        return "favorite"
    if key in (Qt.Key_Question, Qt.Key_F1):
        return "help"
    if key == Qt.Key_Colon:
        return "command"
    if key == Qt.Key_Slash:
        return "search"
    if key in (Qt.Key_Tab, Qt.Key_Backtab):
        return "focus_panel"
    return None


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
        action = viewer_key_action(event)
        if action is not None:
            self.action_requested.emit(action)
            return
        super().keyPressEvent(event)


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

        if index.data(HEADER_ROLE):
            painter.setFont(ui_font(14, QFont.DemiBold))
            painter.setPen(option.palette.color(QPalette.WindowText))
            painter.drawText(
                option.rect.adjusted(8, 0, -8, 0),
                Qt.AlignLeft | Qt.AlignVCenter,
                str(index.data(Qt.DisplayRole) or ""),
            )
            painter.restore()
            return

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

        favorite = bool(index.data(FAVORITE_ROLE))
        if favorite:
            text = "♥"

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


class Filmstrip(QListWidget):
    """Tira horizontal histórica del visor.

    La app real y el prototipo usan la misma rejilla limitada a una fila. Esta
    tira se conserva como variante anterior para comparar comportamientos.
    """

    action_requested = Signal(str)
    photo_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("tira de miniaturas")
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setSpacing(0)
        self.setFrameShape(QListWidget.NoFrame)
        self.setIconSize(QSize(STRIP_THUMB_W, STRIP_THUMB_H))
        self.setUniformItemSizes(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(STRIP_H)
        self.setStyleSheet(
            """
            QListWidget { background: palette(window); border: none; }
            QListWidget::item:selected { background: transparent; }
            QScrollBar:horizontal { height: 8px; background: transparent; }
            QScrollBar::handle:horizontal {
                background: palette(mid); min-width: 24px; border-radius: 4px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            """
        )
        palette = self.palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.setPalette(palette)
        self._syncing = False
        self.currentItemChanged.connect(self._on_current_changed)

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.setPalette(palette)
        self.setStyleSheet(self.styleSheet())
        self.viewport().update()
        self.update()

    def set_items(self, items: list[StripItem], current_key: str | None) -> None:
        self._syncing = True
        try:
            self.clear()
            for item in items:
                row = QListWidgetItem()
                row.setIcon(item.icon)
                row.setData(PHOTO_ROLE, item.key)
                row.setData(FAVORITE_ROLE, item.favorite)
                row.setData(Qt.AccessibleTextRole, item.accessible_name)
                row.setSizeHint(
                    QSize(
                        STRIP_THUMB_W + STRIP_GAP,
                        STRIP_THUMB_H + 2 * STRIP_VPAD,
                    )
                )
                row.setText("")
                self.addItem(row)
        finally:
            self._syncing = False
        if current_key is not None:
            self.select_key(current_key)

    def _item_for_key(self, key: str) -> QListWidgetItem | None:
        for row in range(self.count()):
            item = self.item(row)
            if item.data(PHOTO_ROLE) == key:
                return item
        return None

    def select_key(self, key: str, *, center: bool = True) -> None:
        item = self._item_for_key(key)
        if item is None:
            return
        self._syncing = True
        try:
            self.setCurrentItem(item)
        finally:
            self._syncing = False
        if center:
            self.scrollToItem(item, QAbstractItemView.PositionAtCenter)

    def update_item(
        self, key: str, favorite: bool, accessible_name: str
    ) -> None:
        item = self._item_for_key(key)
        if item is None:
            return
        item.setData(FAVORITE_ROLE, favorite)
        item.setData(Qt.AccessibleTextRole, accessible_name)
        self.viewport().update()

    def update_icon(self, key: str, icon: QIcon) -> None:
        item = self._item_for_key(key)
        if item is None:
            return
        item.setIcon(icon)
        self.viewport().update()

    def _on_current_changed(self, current, previous) -> None:
        if self._syncing or current is None:
            return
        key = current.data(PHOTO_ROLE)
        if key:
            self.photo_selected.emit(str(key))

    def wheelEvent(self, event) -> None:
        bar = self.horizontalScrollBar()
        delta = event.angleDelta().y() or event.angleDelta().x()
        bar.setValue(bar.value() - delta)
        event.accept()

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
        action = viewer_key_action(event)
        if action is not None:
            self.action_requested.emit(action)
            return
        super().keyPressEvent(event)


class PhotoViewer(QWidget):
    """Vista individual: foto grande arriba y fila de miniaturas abajo.

    Con `with_strip=False`, la fila inferior la aporta la rejilla de la ventana:
    es el modo que usan la app real y el prototipo.
    """

    action_requested = Signal(str)
    photo_selected = Signal(str)

    def __init__(
        self, parent: QWidget | None = None, *, with_strip: bool = True
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName("visor de fotos")
        self.canvas = PhotoCanvas(self)
        self.strip = Filmstrip(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas, 1)
        if with_strip:
            layout.addWidget(self.strip, 0)
        else:
            self.strip.hide()

        self.canvas.action_requested.connect(self.action_requested)
        self.strip.action_requested.connect(self.action_requested)
        self.strip.photo_selected.connect(self.photo_selected)

    def set_items(self, items: list[StripItem], current_key: str | None) -> None:
        self.strip.set_items(items, current_key)

    def select_key(self, key: str) -> None:
        self.strip.select_key(key)

    def update_item(
        self, key: str, favorite: bool, accessible_name: str
    ) -> None:
        self.strip.update_item(key, favorite, accessible_name)

    def update_icon(self, key: str, icon: QIcon) -> None:
        self.strip.update_icon(key, icon)

    def focus_canvas(self) -> None:
        self.canvas.setFocus()

    def retheme(self) -> None:
        self.canvas.update()
        self.strip.retheme()


class PhotoGrid(QListWidget):
    """Rejilla que traduce las teclas de vim a acciones de la ventana."""

    action_requested = Signal(str)
    focus_panel = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("rejilla de fotos")
        # Con el panel de foto abierto, la rejilla aparta las teclas del visor
        # (H/J/K/L paneo, +/−/z zoom), porque el foco sigue en la rejilla. La
        # composición de la rejilla no cambia: es la misma vista, solo recortada.
        self.viewer_keys = False
        self._visible_rows: list[int] = []
        self._visible_positions: dict[int, int] = {}
        self._header_width = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_header_widths()

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and index.data(HEADER_ROLE):
            return
        super().mousePressEvent(event)

    def update_header_widths(self) -> None:
        """Hace que cada cabecera ocupe la fila completa y fuerce la ruptura."""
        width = max(1, self.viewport().width())
        if width == self._header_width:
            return
        self._header_width = width
        changed = False
        for row in range(self.count()):
            item = self.item(row)
            if item is not None and item.data(HEADER_ROLE):
                item.setSizeHint(QSize(width, GROUP_HEADER_H))
                changed = True
        if changed:
            self.doItemsLayout()

    def set_visible_rows(self, rows: list[int]) -> None:
        """Cachea las filas no ocultas para navegar sin recorrer toda la lista."""
        self._visible_rows = rows
        self._visible_positions = {row: index for index, row in enumerate(rows)}

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.setStyleSheet(self.styleSheet())
        self.setPalette(palette)
        self.viewport().update()

    def _visual_rows(self) -> list[list[int]] | None:
        """Agrupa las filas de fotos en líneas visuales usando la geometría real.

        Con agrupación por fecha o cámara, las cabeceras ocupan la fila
        completa y la última fila de cada grupo puede ser parcial: las filas
        ya no tienen todas el mismo número de fotos, así que el paso vertical
        no puede ser un número fijo de elementos. Devuelve ``None`` si la
        geometría aún no es fiable (sin layout).
        """
        rows: list[list[int]] = []
        row: list[int] = []
        row_top: int | None = None
        for visible_row in self._visible_rows:
            item = self.item(visible_row)
            if item is None:
                continue
            rect = self.visualItemRect(item)
            if rect.width() <= 1 and rect.height() <= 1:
                return None
            top = rect.top()
            if row and abs(top - row_top) > 2:
                rows.append(row)
                row = []
            if not row:
                row_top = top
            row.append(visible_row)
        if row:
            rows.append(row)
        return rows or None

    def _row_center_x(self, visible_row: int) -> int:
        item = self.item(visible_row)
        if item is None:
            return 0
        return int(self.visualItemRect(item).center().x())

    def move_selection(self, action: str) -> bool:
        if not self._visible_rows:
            return True
        position = self._visible_positions.get(self.currentRow(), 0)
        if action in ("0", "$"):
            return self._move_group(action)
        # h/l y Home/End se mueven por índice.
        if action in ("h", "l", "home", "end"):
            return self._move_indexed(action, position)
        return self._move_vertical(action, position)

    def _move_group(self, action: str) -> bool:
        """Salta a la primera (``0``) o última (``$``) foto del grupo actual.

        Los límites salen de los datos de grupo de las celdas visibles, no de
        la geometría: bastan para delimitar el grupo con filtros activos y
        sirven igual en la fila del visor, donde las cabeceras están ocultas
        pero cada celda conserva su grupo. Igual que el resto de movimientos,
        no hay salto circular: si el cursor ya está en el extremo del grupo se
        queda donde está y la ventana lo hace saber.
        """
        current_row = self.currentRow()
        item = self.item(current_row)
        if item is None:
            return True
        group_key = str(item.data(GROUP_ROLE) or "")
        members = [
            position
            for position, visible_row in enumerate(self._visible_rows)
            if (cell := self.item(visible_row)) is not None
            and str(cell.data(GROUP_ROLE) or "") == group_key
        ]
        if not members:
            return True
        target = members[0] if action == "0" else members[-1]
        self.setCurrentRow(self._visible_rows[target])
        self.scrollToItem(self.currentItem(), QAbstractItemView.EnsureVisible)
        return target != self._visible_positions.get(current_row, 0)

    def _move_indexed(self, action: str, position: int) -> bool:
        last = len(self._visible_rows) - 1
        if action == "home":
            target = 0
        elif action == "end":
            target = last
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
            target = max(0, min(last, position + step))

        self.setCurrentRow(self._visible_rows[target])
        self.scrollToItem(self.currentItem(), QAbstractItemView.EnsureVisible)
        return target != position

    def _move_vertical(self, action: str, position: int) -> bool:
        """Mueve el cursor verticalmente siguiendo la geometría renderizada.

        ``j``/``k`` pasan a la línea visual contigua (las cabeceras de grupo no
        cuentan: solo quedan en ``_visible_rows`` filas con fotos); el tamaño
        de página salta por altura de viewport. Como la línea destino puede
        tener menos fotos (cabecera que corta el flujo), la celda elegida es
        la de centro horizontal más próximo a la columna actual.
        """
        visual_rows = self._visual_rows()
        current_row = self._visible_rows[position]
        if visual_rows is None:
            return self._move_indexed(action, position)
        if self.item(current_row) is None:
            return False

        row_index = 0
        geometry: list[tuple[int, tuple[int, ...]]] = []
        for index, row in enumerate(visual_rows):
            if current_row in row:
                row_index = index
            geometry.append(
                (
                    self.visualItemRect(self.item(row[0])).top(),
                    tuple(self._row_center_x(visible_row) for visible_row in row),
                )
            )

        page_rows = max(1, self.viewport().height() // ITEM_H)
        step_top = page_rows * ITEM_H
        if action in ("halfdown", "halfup"):
            step_top = max(1, step_top // 2)

        target_index = vertical_row_target(action, geometry, row_index=row_index, step_top=step_top)
        # La línea destino puede tener menos fotos: elige la celda más
        # cercana a la columna actual.
        current_x = self._row_center_x(current_row)
        visible_row = min(
            visual_rows[target_index],
            key=lambda row: abs(self._row_center_x(row) - current_x),
        )
        target = self._visible_positions.get(visible_row, position)
        self.setCurrentRow(visible_row)
        self.scrollToItem(self.currentItem(), QAbstractItemView.EnsureVisible)
        return target != position

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        shift = bool(modifiers & Qt.ShiftModifier)
        control = bool(modifiers & (Qt.ControlModifier | Qt.MetaModifier))

        if self.viewer_keys:
            if not control and shift and key in (
                Qt.Key_H,
                Qt.Key_J,
                Qt.Key_K,
                Qt.Key_L,
            ):
                self.action_requested.emit(
                    {
                        Qt.Key_H: "pan_left",
                        Qt.Key_J: "pan_down",
                        Qt.Key_K: "pan_up",
                        Qt.Key_L: "pan_right",
                    }[key]
                )
                return
            if not control:
                if key in (Qt.Key_Plus, Qt.Key_Equal):
                    self.action_requested.emit("zoom_in")
                    return
                if key == Qt.Key_Minus:
                    self.action_requested.emit("zoom_out")
                    return
                if key == Qt.Key_Z and not shift:
                    self.action_requested.emit("toggle_zoom")
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
        if is_group_start(key, shift):
            self.action_requested.emit("0")
            return
        if is_group_end(key, shift):
            self.action_requested.emit("$")
            return
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_O):
            self.action_requested.emit("open_viewer")
            return
        if key == Qt.Key_G:
            if event.modifiers() & Qt.ShiftModifier:
                self.action_requested.emit("end")
            else:
                self.action_requested.emit("g")
            return
        if key == Qt.Key_PageDown:
            self.action_requested.emit("pagedown")
            return
        if key == Qt.Key_PageUp:
            self.action_requested.emit("pageup")
            return
        if key == Qt.Key_D and event.modifiers() & _CTRL_MASK:
            self.action_requested.emit("halfdown")
            return
        if key == Qt.Key_U and event.modifiers() & _CTRL_MASK:
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
        if not shift and Qt.Key_0 <= key <= Qt.Key_5:
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
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_O):
            self.action_requested.emit("open_viewer")
            return
        if key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus, Qt.Key_Z):
            self.action_requested.emit(
                {
                    Qt.Key_Plus: "zoom_in",
                    Qt.Key_Equal: "zoom_in",
                    Qt.Key_Minus: "zoom_out",
                    Qt.Key_Z: "toggle_zoom",
                }[key]
            )
            return
        if key == Qt.Key_G:
            self.action_requested.emit(
                "last" if event.modifiers() & Qt.ShiftModifier else "g"
            )
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

    def set_photo(self, photo: Photo | None) -> None:
        if photo is None:
            self.show_empty("Sin selección")
            return

        file_rows: list[tuple[str, str]] = []
        if photo.fmt:
            file_rows.append(("Formato", photo.fmt))
        if photo.dimensions:
            file_rows.append(("Dimensiones", photo.dimensions))
        if photo.size:
            file_rows.append(("Peso", photo.size))
        canonical = photo.canonical_rel_path or (str(photo.path) if photo.path else "")
        if canonical:
            file_rows.append(("Fichero", canonical))
        for other in photo.other_locations:
            if other and other != canonical:
                file_rows.append(("Fichero", other))

        sections = [
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
        ]
        if file_rows:
            sections.append(("Archivo", file_rows))
        if photo.location:
            sections.append(("Ubicación", (("Coordenadas", photo.location),)))

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("barra de estado")
        self.setFixedHeight(BAR_H)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)

        self.prompt_active = False
        self.prompt_mode: str | None = None
        self.error_text: str | None = None
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


def _help_markdown() -> str:
    """El documento de ayuda empaquetado con la aplicación."""
    from importlib import resources

    import foto

    return (resources.files(foto) / "resources" / "ayuda.md").read_text(
        encoding="utf-8"
    )


def _help_color(role: QPalette.ColorRole) -> str:
    palette = _app_palette()
    return palette.color(role).name(QColor.HexArgb)


def _help_muted() -> str:
    """Token fg.muted: texto atenuado con contraste suficiente."""
    palette = _app_palette()
    window = palette.color(QPalette.Window)
    text = palette.color(QPalette.WindowText)
    ratio = 0.62 if window.lightness() > 128 else 0.72
    return QColor(
        round(window.red() + (text.red() - window.red()) * ratio),
        round(window.green() + (text.green() - window.green()) * ratio),
        round(window.blue() + (text.blue() - window.blue()) * ratio),
    ).name(QColor.HexArgb)


def _help_chip_font() -> QFont:
    mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    mono.setPixelSize(12)
    return mono


def _help_cell_width(cell: str) -> int:
    """Ancho aproximado de una celda de teclas con sus fichas."""
    mono = QFontMetrics(_help_chip_font())
    text = QFontMetrics(ui_font(13))
    total = 0
    for index, chunk in enumerate(cell.split("`")):
        if not chunk:
            continue
        if index % 2:
            total += mono.horizontalAdvance(_help_chip_pad(chunk))
        else:
            total += text.horizontalAdvance(f"{chunk} ")
    return total


def _help_chip(text: str) -> str:
    """Ficha de tecla: monoespaciada, sobre la superficie secundaria."""
    mono = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
    return (
        f'<span style="background-color:{_help_color(QPalette.AlternateBase)};'
        f" color:{_help_color(QPalette.WindowText)};"
        f" font-family:'{mono}'; font-size:12px;"
        f'">{_help_chip_pad(text)}</span>'
    )


def _help_chip_pad(text: str) -> str:
    """Texto de la ficha con su margen interior, para medir con métricas."""
    return f"\u00a0\u00a0{text}\u00a0\u00a0"


def _help_inline(text: str) -> str:
    """Convierte ``teclas`` entre acentos en fichas; el resto queda igual."""
    pieces = []
    for index, chunk in enumerate(text.split("`")):
        if not chunk:
            continue
        pieces.append(_help_chip(chunk) if index % 2 else chunk)
    return " ".join(pieces)


def _help_heading(label: str, level: int) -> str:
    size = 26 if level == 1 else 20
    return (
        f'<p style="margin-top:{"0" if level == 1 else "18"}px; margin-bottom:6px;'
        f" font-size:{size}px; font-weight:600;"
        f' color:{_help_color(QPalette.WindowText)}">{label}</p>'
        f'<hr style="color:{_help_color(QPalette.Mid)};">'
    )


def _help_keys_width(tables: list[list[list[str]]]) -> int:
    """Ancho de la columna de teclas: el de la fila más ancha del documento.

    Con un ancho fijo la celda partía filas largas como ``h j k l / flechas``;
    con el natural de la fila más ancha todas las columnas de teclas quedan
    alineadas y nada cae de línea.
    """
    widths = [
        _help_cell_width(cells[0])
        for table in tables
        for cells in table
        if cells
    ]
    return (max(widths) + 12) if widths else 170


def _help_row(cells: list[str], header: bool, keys_width: int) -> str:
    keys = _help_inline(cells[0]) if cells else ""
    description = _help_inline(cells[1]) if len(cells) > 1 else ""
    if header:
        keys, description = f"<b>{keys}</b>", f"<b>{description}</b>"
    padding = "2px" if header else "8px"
    return (
        "<tr>"
        f'<td width="{keys_width}" style="vertical-align:top;">'
        f'<p style="margin-top:{padding}; margin-bottom:{padding};">{keys}</p></td>'
        "<td style=\"vertical-align:top;\">"
        f"<p style=\"margin-top:{padding}; margin-bottom:{padding};\">"
        f"{description}</p></td>"
        "</tr>"
    )


def _help_table(rows: list[list[str]], keys_width: int) -> str:
    """Tabla del markdown: filas con aire, sin líneas de separación."""
    chunks = [
        (
            '<table width="100%" cellspacing="0" cellpadding="0">'
            f"{_help_row(rows[0], header=True, keys_width=keys_width)}</table>"
        )
    ]
    for row in rows[1:]:
        chunks.append(
            '<table width="100%" cellspacing="0" cellpadding="0">'
            + _help_row(row, header=False, keys_width=keys_width)
            + "</table>"
        )
    return "".join(chunks)


def render_help(markdown: str) -> str:
    """Markdown restringido de ``ayuda.md`` → HTML enriquecido.

    ``setMarkdown`` de Qt pierde el formato de los spans de código y no se
    puede estilar, así que el overlay renderiza el subset que usa el
    documento: encabezados ``#``/``##``, tablas de dos columnas y teclas
    entre acentos. Todo lo que no encaje en ese subset no se renderiza.
    """
    blocks: list[tuple[str, object]] = []
    rows: list[list[str]] = []

    for line in markdown.splitlines():
        bare = line.strip()
        if bare.startswith("|"):
            cells = [cell.strip() for cell in bare.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue  # separador de cabecera
            rows.append(cells)
            continue
        if rows:
            blocks.append(("table", rows))
            rows = []
        if not bare:
            continue
        if bare.startswith("##"):
            blocks.append(("heading", (bare[2:].strip(), 2)))
        elif bare.startswith("#"):
            blocks.append(("heading", (bare[1:].strip(), 1)))
        else:
            blocks.append(("paragraph", bare))
    if rows:
        blocks.append(("table", rows))

    # La columna de teclas es única para todo el documento: todas las tablas
    # quedan alineadas y no hay filas partidas como ``h j k l / flechas``.
    width = _help_keys_width(
        [rows_ for kind, rows_ in blocks if kind == "table" and rows_]
    )

    html: list[str] = []
    for kind, data in blocks:
        if kind == "heading":
            label, level = data  # type: ignore[misc]
            html.append(_help_heading(label, level))
        elif kind == "table":
            html.append(_help_table(data, width))  # type: ignore[misc]
        else:
            html.append(f"<p>{_help_inline(data)}</p>")  # type: ignore[misc]
    return "".join(html)


class HelpOverlay(QFrame):
    """Ayuda de atajos, renderizada desde ``foto/resources/ayuda.md``."""

    BODY_STYLESHEET = """
        QTextBrowser {
            background: transparent;
            color: palette(window-text);
        }
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
        }
        QScrollBar::handle:vertical {
            background: palette(mid);
            min-height: 24px;
            border-radius: 4px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
    """

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
        layout.setSpacing(6)

        body = QTextBrowser()
        body.setFrameShape(QFrame.NoFrame)
        body.setOpenExternalLinks(False)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setLineWidth(0)
        body.setFont(ui_font(13))
        document = body.document()
        document.setDefaultFont(ui_font(13))
        document.setDocumentMargin(0)
        body.setStyleSheet(self.BODY_STYLESHEET)
        layout.addWidget(body)
        self.body = body
        self._render()

    def _render(self) -> None:
        """Renderiza el documento con la paleta del momento.

        ``setMarkdown`` no sirve aquí (pierde los spans de código y no admite
        estilos), así que el markdown restringido de ``ayuda.md`` se convierte
        a HTML propio; el navegador se queda con el documento desplazable en
        vertical y con reflujo según el ancho real del overlay.
        """
        document = self.body.document()
        document.setTextWidth(-1)  # el navegador decide con su ancho de vista
        self.body.setHtml(render_help(_help_markdown()))

    def retheme(self) -> None:
        palette = _app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        scroll = self.body.verticalScrollBar().value()
        self._render()
        self.body.verticalScrollBar().setValue(scroll)
        self.update()

    def keyPressEvent(self, event) -> None:
        """Mueve el scroll de la ayuda con las flechas o con ``j``/``k``.

        El resto de teclas cae a la ventana, que cerrará el overlay cuando
        sea ``Esc`` o ``q``.
        """
        bar = self.body.verticalScrollBar()
        key = event.key()
        step = 40
        if key in (Qt.Key_Down, Qt.Key_J):
            bar.setValue(bar.value() + step)
            event.accept()
            return
        if key in (Qt.Key_Up, Qt.Key_K):
            bar.setValue(bar.value() - step)
            event.accept()
            return
        super().keyPressEvent(event)

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
        thumbnails: dict[str, QIcon] | None = None,
        placeholder_icon: QIcon | None = None,
        thumbnail_loader: Callable[
            [list[tuple[str, Path, str | None]]], None
        ] | None = None,
        title: str | None = None,
        initial_panel_visible: bool = True,
        on_open: Callable[[str], None] | None = None,
        grid_row: bool = False,
    ) -> None:
        super().__init__()
        # True usa la rejilla como fila inferior del visor; False conserva el
        # filmstrip histórico, que solo usa la configuración del prototipo sin
        # rejilla (grid_row=False). La app real y el prototipo pasan True.
        self.grid_row = grid_row
        self.photos = {photo.key: photo for photo in photos}
        self._sort_key = DEFAULT_SORT
        self.thumbnails = dict(thumbnails or {})
        self._on_open = on_open
        self.search_text = ""
        self._search_origin: str | None = None
        self.panel_visible = initial_panel_visible
        self.panel_overlay = False
        self._viewer_visible = False
        self._image_cache: dict[str, QPixmap] = {}
        self._image_cache_order: list[str] = []
        self._loading_keys: set[str] = set()
        self._failed_images: set[str] = set()
        self._pending_image_key: str | None = None
        self._displayed_key: str | None = None
        self._g_pending = False
        self._g_timer = QTimer(self)
        self._g_timer.setSingleShot(True)
        self._g_timer.setInterval(500)
        self._g_timer.timeout.connect(self._clear_g_pending)
        self._loader_pool = QThreadPool(self)
        self._loader_pool.setMaxThreadCount(2)
        self._placeholder_icon = placeholder_icon
        self._thumbnail_loader = thumbnail_loader
        self._grid_items: dict[str, QListWidgetItem] = {}
        self._thumbnail_order: list[str] = list(self.thumbnails)
        self._visible_keys_cache: list[str] = []
        self._visible_positions_by_key: dict[str, int] = {}
        self._viewport_keys: set[str] = set()
        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(60)
        self._visible_timer.timeout.connect(self._request_visible_thumbnails)

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
        self.grid.setUniformItemSizes(False)
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

        self.viewer = PhotoViewer(self.content, with_strip=not grid_row)
        if grid_row:
            # La rejilla es la dueña del foco y de la navegación también en el visor.
            self.viewer.canvas.setFocusPolicy(Qt.NoFocus)
            self.viewer.canvas.focus_ring = False
        self.viewer.hide()

        self.panel = MetadataPanel()
        self.panel.setParent(self.content)

        self.empty_label = QLabel("", self.content)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setFont(ui_font(13))
        set_muted(self.empty_label)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()

        # Cabecera del grupo sobre la fila del visor: la misma rejilla
        # comprimida conserva el rótulo de grupo, igual que a pantalla
        # completa.
        self.row_header = QLabel("", self.content)
        self.row_header.setAccessibleName("grupo de la foto mostrada")
        self.row_header.setFont(ui_font(14, QFont.DemiBold))
        self.row_header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.row_header.setIndent(8)
        self.row_header.hide()

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
        self.grid.itemDoubleClicked.connect(self._on_grid_double_clicked)
        self.grid.action_requested.connect(self._on_action)
        self.grid.focus_panel.connect(self._focus_panel)
        self.grid.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnails)
        self.grid.horizontalScrollBar().valueChanged.connect(
            self._schedule_visible_thumbnails
        )
        self.viewer.action_requested.connect(self._on_viewer_action)
        self.viewer.photo_selected.connect(self._on_strip_photo_selected)
        self.panel.scroll.focus_back.connect(self._focus_current_view)
        self.panel.scroll.escape_pressed.connect(self._handle_escape)
        self.panel.scroll.action_requested.connect(self._on_action)
        self.status.command_submitted.connect(self._run_command)
        self.status.search_changed.connect(self._apply_filter)
        self.status.search_committed.connect(self._commit_search)
        self.status.prompt_cancelled.connect(self._cancel_prompt)
        self.status.prompt_opened.connect(self._show_content_blocker)
        self.status.prompt_closed.connect(self._hide_content_blocker)
        self.status.prompt_closed.connect(self._focus_current_view)

        quit_action = QAction(self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(QApplication.quit)
        self.addAction(quit_action)

        self.status.set_counts(len(photos), len(photos), photos[0].name if photos else "")
        if photos and self._visible_keys_cache:
            first = self._grid_items.get(self._visible_keys_cache[0])
            if first is not None:
                self.grid.setCurrentItem(first)
        self._layout_content()
        QTimer.singleShot(0, self._focus_current_view)
        QTimer.singleShot(
            400,
            lambda: self.status.show_message(
                "Enter visor · i panel · : comandos · ? ayuda", 4000
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
        self.viewer.retheme()
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

    def _add_group_header(self, group_key: str, label: str) -> None:
        item = QListWidgetItem(label)
        item.setData(HEADER_ROLE, True)
        item.setData(GROUP_ROLE, group_key)
        item.setData(Qt.AccessibleTextRole, f"grupo {label}")
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(max(1, self.grid.viewport().width()), GROUP_HEADER_H))
        self.grid.addItem(item)

    def _populate_grid(self, photos: list[Photo]) -> None:
        self.grid.clear()
        self._grid_items.clear()
        current_group: str | None = None
        for index, photo in enumerate(sorted_photos(photos, self._sort_key)):
            group_key, group_label = photo_group(photo, self._sort_key)
            if group_key != current_group:
                self._add_group_header(group_key, group_label)
                current_group = group_key
            item = QListWidgetItem()
            icon = self.thumbnails.get(photo.key)
            if icon is None or icon.isNull():
                if self._placeholder_icon is not None:
                    icon = self._placeholder_icon
                else:
                    icon = make_thumbnail(index)
            item.setIcon(icon)
            item.setData(PHOTO_ROLE, photo.key)
            item.setData(FAVORITE_ROLE, photo.favorite)
            item.setData(GROUP_ROLE, group_key)
            item.setData(Qt.AccessibleTextRole, photo_accessible_name(photo))
            item.setSizeHint(QSize(ITEM_W, ITEM_H))
            item.setText("")
            self.grid.addItem(item)
            self._grid_items[photo.key] = item
        self.grid.update_header_widths()
        self._update_group_headers()
        self._refresh_visible_cache()
        self._rebuild_strip()
        self._schedule_visible_thumbnails()

    def _update_group_headers(self) -> None:
        """Oculta las cabeceras de grupos sin fotos visibles."""
        visible_groups: set[str] = set()
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is None or item.data(HEADER_ROLE) or item.isHidden():
                continue
            visible_groups.add(str(item.data(GROUP_ROLE) or ""))
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is None or not item.data(HEADER_ROLE):
                continue
            group_key = str(item.data(GROUP_ROLE) or "")
            item.setHidden(group_key not in visible_groups)

    def _update_row_header(self) -> None:
        """En el visor muestra sobre la fila el grupo de la foto actual.

        La fila es la rejilla comprimida: mientras navega por ella (``h``,
        ``l``, ``0``/``$``…), el rótulo cambia al grupo de la miniatura que
        tiene el cursor, exactamente como lo hace la cabecera al recorrer la
        rejilla a pantalla completa. Si no hay grupo que mostrar, se oculta.
        """
        if not (self.grid_row and self._viewer_visible):
            self.row_header.hide()
            return
        photo = self._current_photo()
        label = photo_group(photo, self._sort_key)[1] if photo is not None else ""
        self.row_header.setText(label)
        self.row_header.setVisible(bool(label))

    def set_photos(
        self,
        photos: list[Photo],
        thumbnails: dict[str, QIcon] | None = None,
    ) -> None:
        """Sustituye la biblioteca visible conservando la foto actual si sigue."""
        viewer_was_visible = self._viewer_visible
        self._image_cache.clear()
        self._image_cache_order.clear()
        self._failed_images.clear()
        self._pending_image_key = None
        self._displayed_key = None
        self.viewer.canvas.clear()
        current = self.grid.currentItem()
        current_key = current.data(PHOTO_ROLE) if current is not None else None
        self.photos = {photo.key: photo for photo in photos}
        if thumbnails is not None:
            self.thumbnails = dict(thumbnails)
            self._thumbnail_order = list(self.thumbnails)
        self.search_text = ""
        self._search_origin = None
        self.grid.blockSignals(True)
        try:
            self._populate_grid(photos)
            self.grid.setCurrentRow(-1)

            target = self._grid_items.get(current_key) if current_key else None
            if target is None and self._visible_keys_cache:
                target = self._grid_items.get(self._visible_keys_cache[0])
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
        if viewer_was_visible and not self.photos:
            self._close_viewer()
        elif viewer_was_visible:
            self._layout_content()
        self._focus_current_view()

    def _layout_content(self) -> None:
        """Compone las cuatro regiones de la ventana.

        Regiones: zona de rejilla, zona de estado (barra inferior, fuera de
        este área de contenido), panel de metadatos (columna derecha, siempre
        por encima de la zona de estado) y panel de foto. La rejilla y la
        barra de estado están siempre visibles; mostrar u ocultar los paneles
        solo cambia la dimensión de la zona de rejilla, nunca su
        comportamiento.
        """
        width = self.content.width()
        height = self.content.height()
        if self.panel_visible:
            # Panel de metadatos: ancho fijo, ocupa el lateral derecho por
            # encima de la zona de estado (esta barra vive fuera del área de
            # contenido). En ventanas estrechas se muestra como capa temporal.
            panel_w = min(PANEL_W, max(280, width - 48))
            self.panel_overlay = width < 900
            content_w = width if self.panel_overlay else width - panel_w
            self.panel.setGeometry(width - panel_w, 0, panel_w, height)
            self.panel.show()
        else:
            self.panel_overlay = False
            content_w = width
            self.panel.hide()

        if self._viewer_visible:
            if self.grid_row:
                # Panel de foto, encima la zona de rejilla recortada a un
                # título de grupo y una única fila de celdas. Es la misma
                # rejilla de siempre: solo cambia su dimensión.
                header_h = min(GROUP_HEADER_H, max(0, height))
                row_h = min(ITEM_H, max(0, height - header_h))
                viewer_h = max(0, height - header_h - row_h)
                self.viewer.setGeometry(0, 0, max(0, content_w), viewer_h)
                self.viewer.show()
                self.viewer.raise_()
                self.grid.show()
                self.grid.setGeometry(
                    0, viewer_h + header_h, max(0, content_w), row_h
                )
                self.grid.raise_()
                self.row_header.setGeometry(0, viewer_h, max(0, content_w), header_h)
                self.row_header.raise_()
            else:
                self.grid.hide()
                self.viewer.setGeometry(0, 0, max(0, content_w), height)
                self.viewer.show()
                self.viewer.raise_()
        else:
            self.viewer.hide()
            self.grid.show()
            self.grid.setGeometry(
                GRID_MARGIN,
                GRID_MARGIN,
                max(0, content_w - 2 * GRID_MARGIN),
                max(0, height - 2 * GRID_MARGIN),
            )

        self._position_empty_label()
        if self.panel_visible:
            self.panel.raise_()
        self._update_row_header()
        if self.content_blocker.isVisible():
            self.content_blocker.setGeometry(0, 0, width, height)
            self.content_blocker.raise_()
        self._schedule_visible_thumbnails()
        # Invariante: la miniatura seleccionada queda siempre a la vista tras
        # cualquier cambio de geometría (mostrar/ocultar paneles, redimensionar
        # la ventana), con el desplazamiento mínimo (EnsureVisible).
        current = self.grid.currentItem()
        if current is not None:
            self.grid.scrollToItem(current, QAbstractItemView.EnsureVisible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_content()
        self.status.position_popup()
        if self.help_overlay.isVisible():
            self._center_help()

    def _center_help(self) -> None:
        content_rect = self.content.geometry()
        width = min(760, max(320, content_rect.width() - 64))
        height = min(760, max(260, content_rect.height() - 64))
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

    def _focus_current_view(self) -> None:
        if self._viewer_visible:
            if self.grid_row:
                self.grid.setFocus()
            else:
                self.viewer.focus_canvas()
        else:
            self._focus_grid()

    def _focus_panel(self) -> None:
        if not self.panel_visible:
            self.panel_visible = True
            self._layout_content()
        self.panel.focus_scroll()

    def _current_photo(self) -> Photo | None:
        item = self.grid.currentItem()
        if item is None:
            return None
        return self.photos.get(item.data(PHOTO_ROLE))

    def _grid_item(self, key: str) -> QListWidgetItem | None:
        return self._grid_items.get(key)

    def _refresh_visible_cache(self) -> None:
        """Recalcula las fotos no ocultas y su posición para navegación O(1)."""
        rows: list[int] = []
        keys: list[str] = []
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is None or item.isHidden():
                continue
            key = item.data(PHOTO_ROLE)
            if not key:
                continue
            rows.append(row)
            keys.append(str(key))
        self.grid.set_visible_rows(rows)
        self._visible_keys_cache = keys
        self._visible_positions_by_key = {key: index for index, key in enumerate(keys)}

    def _remember_thumbnail(self, key: str, icon: QIcon) -> None:
        if key in self.thumbnails:
            try:
                self._thumbnail_order.remove(key)
            except ValueError:
                pass
        self.thumbnails[key] = icon
        self._thumbnail_order.append(key)
        self._trim_thumbnails()

    def _trim_thumbnails(self) -> None:
        """Descarta las miniaturas más antiguas fuera del viewport actual."""
        keep = set(self._viewport_keys)
        photo = self._current_photo()
        if photo is not None:
            keep.add(photo.key)

        evicted = eviction_candidates(
            self._thumbnail_order,
            keep,
            THUMBNAIL_CACHE_SIZE,
        )
        if not evicted:
            return

        for key in evicted:
            try:
                self._thumbnail_order.remove(key)
            except ValueError:
                pass
            self.thumbnails.pop(key, None)
            item = self._grid_items.get(key)
            if item is not None:
                item.setIcon(self._placeholder_icon or QIcon())

    def _schedule_visible_thumbnails(self, *_args) -> None:
        if self._thumbnail_loader is None:
            return
        self._visible_timer.start()

    def _visible_photo_keys(self) -> list[str]:
        if self.grid.count() == 0:
            return []
        viewport = self.grid.viewport().rect()
        first = self.grid.indexAt(QPoint(viewport.left() + 1, viewport.top() + 1))
        if not first.isValid():
            first = self.grid.indexAt(QPoint(viewport.center().x(), viewport.top() + 1))
        if not first.isValid():
            current = self._current_photo()
            return [current.key] if current is not None else []

        # ``indexAt`` devuelve un elemento concreto de la fila visual; retrocede
        # hasta la primera celda de esa fila para no perder las de su izquierda.
        start = first.row()

        first_top = self.grid.visualItemRect(self.grid.item(start)).top()
        while start > 0:
            previous = self.grid.item(start - 1)
            if previous is None or self.grid.visualItemRect(previous).top() != first_top:
                break
            start -= 1

        keys = []
        row = start
        while row < self.grid.count():
            item = self.grid.item(row)
            if item is None:
                break
            rect = self.grid.visualItemRect(item)
            if rect.top() > viewport.bottom():
                break
            if not item.isHidden() and rect.bottom() >= viewport.top():
                key = item.data(PHOTO_ROLE)
                if key:
                    keys.append(str(key))
            row += 1
        return keys

    def _request_visible_thumbnails(self) -> None:
        if self._thumbnail_loader is None:
            return
        keys = self._visible_photo_keys()
        self._viewport_keys = set(keys)
        requests: list[tuple[str, Path, str | None]] = []
        for key in keys:
            photo = self.photos.get(key)
            if photo is None or photo.path is None or key in self.thumbnails:
                continue
            requests.append((key, photo.path, photo.photo_hash))
        self._thumbnail_loader(requests)
        self._trim_thumbnails()

    def set_thumbnail(self, key: str, icon: QIcon) -> None:
        """Actualiza una miniatura cuando termina su carga en segundo plano."""
        if icon.isNull() or key not in self.photos:
            return
        self._remember_thumbnail(key, icon)
        item = self._grid_items.get(key)
        # Si la ventana lo descartó por no estar visible, no dejamos el icono
        # pegado al item: se volverá a pedir al acercarse.
        if item is not None and key in self.thumbnails:
            item.setIcon(icon)
            self.grid.viewport().update()
        if not self.grid_row:
            self.viewer.update_icon(key, icon)
        if (
            self._viewer_visible
            and self._displayed_key == key
            and self._pending_image_key == key
        ):
            self.viewer.canvas.set_image(
                icon.pixmap(QSize(THUMB_W, THUMB_H)),
                preview=True,
            )

    def _rebuild_strip(self) -> None:
        if self.grid_row:
            return
        items: list[StripItem] = []
        for key in self._visible_keys_cache:
            item = self._grid_items.get(key)
            photo = self.photos.get(key)
            if item is None or photo is None:
                continue
            items.append(
                StripItem(
                    key=photo.key,
                    icon=item.icon(),
                    favorite=photo.favorite,
                    accessible_name=photo_accessible_name(photo),
                )
            )
        current = self._current_photo()
        self.viewer.set_items(items, current.key if current else None)

    def _visible_keys(self) -> list[str]:
        return self._visible_keys_cache

    def _select_key(self, key: str) -> None:
        item = self._grid_item(key)
        if item is None:
            return
        if self.grid.currentItem() is item:
            self._on_current_changed(item, None)
        else:
            self.grid.setCurrentItem(item)
        self.grid.scrollToItem(item, QAbstractItemView.EnsureVisible)

    def _viewer_navigate(self, delta: int) -> None:
        keys = self._visible_keys()
        if not keys:
            return
        photo = self._current_photo()
        index = (
            self._visible_positions_by_key.get(photo.key, 0)
            if photo is not None
            else 0
        )
        target = max(0, min(len(keys) - 1, index + delta))
        if target == index:
            self.status.show_message("fin de la lista", 1500)
            return
        self._select_key(keys[target])

    def _viewer_first(self) -> None:
        keys = self._visible_keys()
        if keys:
            self._select_key(keys[0])

    def _viewer_last(self) -> None:
        keys = self._visible_keys()
        if keys:
            self._select_key(keys[-1])

    def _viewer_group_edge(self, first: bool) -> None:
        """Salta a la primera o última foto del grupo actual en el visor.

        Como el visor solo muestra fotos (sin cabeceras), el grupo se deduce
        del criterio de ordenación activo sobre las claves visibles.
        """
        keys = self._visible_keys()
        photo = self._current_photo()
        if not keys or photo is None:
            return
        group_key = photo_group(photo, self._sort_key)[0]
        members = [
            key
            for key in keys
            if photo_group(self.photos[key], self._sort_key)[0] == group_key
        ]
        if not members:
            return
        self._select_key(members[0] if first else members[-1])

    def _handle_g(self) -> None:
        if self._g_pending:
            self._g_pending = False
            self._g_timer.stop()
            self._viewer_first()
        else:
            self._g_pending = True
            self._g_timer.start()

    def _clear_g_pending(self) -> None:
        self._g_pending = False

    def _set_viewer_keys(self, enabled: bool) -> None:
        """Aparta las teclas del panel de foto mientras la rejilla tiene foco.

        La composición de la rejilla no cambia en ningún estado: sigue siendo
        la misma vista con wrapping y scroll vertical, y lo único que varía es
        su geometría (ver ``_layout_content``). Aquí solo se indica a la
        rejilla que, con el panel de foto abierto, las teclas ``H J K L``, ``+
        -`` y ``z`` corresponden al paneo y al zoom de la foto grande en lugar
        de a sus navegadores.
        """
        self.grid.viewer_keys = enabled

    def _open_viewer(self) -> None:
        if self._viewer_visible:
            return
        photo = self._current_photo()
        if photo is None:
            self.status.show_message("no hay fotos", 2000)
            return
        self._viewer_visible = True
        if self.grid_row:
            self._set_viewer_keys(True)
        self._layout_content()
        if not self.grid_row:
            self.viewer.select_key(photo.key)
        self._show_viewer_photo(photo)
        self._focus_current_view()
        self.status.show_message("visor · h/j/k/l mover · q volver", 2500)

    def _close_viewer(self) -> None:
        if not self._viewer_visible:
            return
        self._viewer_visible = False
        self.viewer.canvas.reset_zoom()
        self.viewer.hide()
        if self.grid_row:
            self._set_viewer_keys(False)
        self._layout_content()
        if not self.status.prompt_active:
            self._focus_grid()
            self.status.show_message("rejilla", 1200)

    def _on_strip_photo_selected(self, key: str) -> None:
        if self._viewer_visible:
            self._select_key(key)

    def _on_grid_double_clicked(self, item: QListWidgetItem) -> None:
        if self._viewer_visible or item is None or item.data(HEADER_ROLE):
            return
        self.grid.setCurrentItem(item)
        self._open_viewer()

    def _on_current_changed(self, current, previous) -> None:
        if current is not None and current.data(HEADER_ROLE):
            return
        photo = self._current_photo()
        self._update_row_header()
        self.panel.set_photo(photo)
        filename = photo.name if photo else ""
        self.status.set_counts(self._visible_count(), len(self.photos), filename)
        if self._viewer_visible:
            if photo is None:
                self._close_viewer()
            else:
                if self.grid_row:
                    item = self.grid.currentItem()
                    if item is not None:
                        self.grid.scrollToItem(
                            item, QAbstractItemView.EnsureVisible
                        )
                else:
                    self.viewer.select_key(photo.key)
                self._show_viewer_photo(photo)
        self._trim_thumbnails()

    def _fallback_pixmap(self, photo: Photo) -> QPixmap | None:
        icon = self.thumbnails.get(photo.key)
        if icon is None or icon.isNull():
            item = self._grid_item(photo.key)
            if item is not None:
                icon = item.icon()
        if icon is None or icon.isNull():
            return None
        return icon.pixmap(QSize(THUMB_W, THUMB_H))

    def _show_viewer_photo(self, photo: Photo) -> None:
        if not self._viewer_visible:
            return
        if photo.key == self._displayed_key and self.viewer.canvas.has_image():
            return
        cached = self._image_cache.get(photo.key)
        if cached is not None:
            self.viewer.canvas.set_image(cached)
            self._displayed_key = photo.key
            self._prefetch_neighbors(photo)
            return
        fallback = self._fallback_pixmap(photo)
        if fallback is not None:
            self.viewer.canvas.set_image(fallback, preview=True)
        else:
            self.viewer.canvas.clear()
        self._displayed_key = photo.key
        self._pending_image_key = photo.key
        self._ensure_image_loaded(photo)

    def _ensure_image_loaded(self, photo: Photo) -> None:
        if photo.path is None:
            return
        key = photo.key
        if (
            key in self._image_cache
            or key in self._loading_keys
            or key in self._failed_images
        ):
            return
        self._loading_keys.add(key)
        task = ImageLoadTask(key, photo.path)
        task.signals.loaded.connect(self._on_image_loaded)
        task.signals.failed.connect(self._on_image_failed)
        self._loader_pool.start(task)

    def _on_image_loaded(self, key: str, image: QImage) -> None:
        self._loading_keys.discard(key)
        if key not in self.photos:
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._on_image_failed(key, "imagen no válida")
            return
        self._cache_image(key, pixmap)
        photo = self._current_photo()
        if self._viewer_visible and photo is not None and photo.key == key:
            self.viewer.canvas.set_image(pixmap)
            self._displayed_key = key
            if self._pending_image_key == key:
                self._pending_image_key = None
            self._prefetch_neighbors(photo)

    def _on_image_failed(self, key: str, message: str) -> None:
        self._loading_keys.discard(key)
        self._failed_images.add(key)
        photo = self._current_photo()
        if self._viewer_visible and photo is not None and photo.key == key:
            if self._pending_image_key == key:
                self._pending_image_key = None
            self.status.show_message(
                "no se pudo cargar el original; se muestra la miniatura", 4000
            )

    def _cache_image(self, key: str, pixmap: QPixmap) -> None:
        if key in self._image_cache:
            self._image_cache_order.remove(key)
        self._image_cache[key] = pixmap
        self._image_cache_order.append(key)
        while len(self._image_cache_order) > IMAGE_CACHE_SIZE:
            oldest = self._image_cache_order.pop(0)
            self._image_cache.pop(oldest, None)

    def _prefetch_neighbors(self, photo: Photo) -> None:
        keys = self._visible_keys()
        index = self._visible_positions_by_key.get(photo.key)
        if index is None:
            return
        for offset in (-1, 1):
            neighbor_index = index + offset
            if 0 <= neighbor_index < len(keys):
                neighbor = self.photos.get(keys[neighbor_index])
                if neighbor is not None:
                    self._ensure_image_loaded(neighbor)

    def _visible_count(self) -> int:
        return len(self._visible_keys_cache)

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
                    "No hay fotos en el índice\nEjecuta 'foto-index' o :open para abrir otra carpeta"
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
        needle = text.strip().casefold()
        visible_items: list[QListWidgetItem] = []
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is None or item.data(HEADER_ROLE):
                continue
            photo = self.photos.get(item.data(PHOTO_ROLE))
            visible = photo is None or needle in photo_search_text(photo)
            item.setHidden(not visible)
            if visible:
                visible_items.append(item)

        self._update_group_headers()
        self._refresh_visible_cache()

        current = self.grid.currentItem()
        if current is not None and (current.data(HEADER_ROLE) or current.isHidden()):
            current = None
        if current is None:
            if visible_items:
                self.grid.setCurrentItem(visible_items[0])
            else:
                self.grid.setCurrentRow(-1)
                self.panel.show_empty("Sin coincidencias")

        self._rebuild_strip()
        if self._viewer_visible and self._visible_count() == 0:
            self._close_viewer()

        photo = self._current_photo()
        filename = photo.name if photo else ""
        self.status.set_counts(self._visible_count(), len(self.photos), filename)
        self._update_empty_state()
        self._schedule_visible_thumbnails()

    def _commit_search(self, text: str) -> None:
        self.search_text = text.strip()
        self._search_origin = None
        self._apply_filter(self.search_text)
        if self.search_text:
            self.status.show_message(
                f"{self._visible_count()} coincidencias", 3000
            )
        self._focus_current_view()

    def _cancel_prompt(self, mode: str) -> None:
        if mode == "search":
            self._apply_filter(self.search_text)
            self._restore_search_origin()
        self._focus_current_view()

    def _restore_search_origin(self) -> None:
        key = self._search_origin
        self._search_origin = None
        if not key:
            return
        item = self._grid_items.get(key)
        if item is not None and not item.isHidden():
            self.grid.setCurrentItem(item)
            self.grid.scrollToItem(item, QAbstractItemView.EnsureVisible)

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
        if command == "sort" or command.startswith("sort "):
            key = command[4:].strip()
            if key in SORT_KEYS:
                self._sort_grid(key)
                return
            self.status.show_error("uso: :sort date | camera")
            return
        self.status.show_error(
            f"comando desconocido: {command} · :help para ver los comandos"
        )

    def _sort_grid(self, key: str) -> None:
        current = self.grid.currentItem()
        current_key = current.data(PHOTO_ROLE) if current else None
        if current is not None and current.data(HEADER_ROLE):
            current_key = None

        self._sort_key = key
        self.grid.blockSignals(True)
        try:
            self._populate_grid(list(self.photos.values()))
            self.grid.setCurrentRow(-1)
            target = self._grid_items.get(current_key) if current_key else None
            if target is not None:
                self.grid.setCurrentItem(target)
        finally:
            self.grid.blockSignals(False)

        self._apply_filter(self.search_text)
        self._on_current_changed(self.grid.currentItem(), None)
        self.status.show_message(f"ordenado por {key}", 3000)

    def _on_action(self, action: str) -> None:
        if action != "g" and self._g_pending:
            self._g_pending = False
            self._g_timer.stop()
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
            "0",
            "$",
            "pagedown",
            "pageup",
            "halfdown",
            "halfup",
        ):
            if self._viewer_visible and not self.grid_row:
                if action == "j":
                    self._viewer_navigate(1)
                elif action == "k":
                    self._viewer_navigate(-1)
                elif action == "h":
                    self._on_viewer_action("pan_left")
                elif action == "l":
                    self._on_viewer_action("pan_right")
                elif action in ("0", "$"):
                    self._viewer_group_edge(action == "0")
                elif action == "home":
                    self._viewer_first()
                elif action == "end":
                    self._viewer_last()
                return
            self._move_cursor(action)
            return
        if action == "open_viewer":
            self._open_viewer()
            return
        if action == "g":
            self._handle_g()
            return
        if action in ("zoom_in", "zoom_out", "toggle_zoom"):
            if self._viewer_visible:
                self._on_viewer_action(action)
            return
        if action.startswith("pan_") and self._viewer_visible:
            self._on_viewer_action(action)
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
            if self._viewer_visible:
                self._close_viewer()
            else:
                self.status.show_message("usa Ctrl+q para salir", 3000)
            return
        if action == "f":
            self._toggle_favorite()
            return

    def _on_viewer_action(self, action: str) -> None:
        if action != "g" and self._g_pending:
            self._g_pending = False
            self._g_timer.stop()
        if action == "next":
            self._viewer_navigate(1)
            return
        if action == "previous":
            self._viewer_navigate(-1)
            return
        if action == "first":
            self._viewer_first()
            return
        if action == "last":
            self._viewer_last()
            return
        if action == "group_first":
            self._viewer_group_edge(first=True)
            return
        if action == "group_last":
            self._viewer_group_edge(first=False)
            return
        if action == "g":
            self._handle_g()
            return
        if action == "close":
            self._close_viewer()
            return
        if action == "escape":
            self._handle_escape()
            return
        if action == "panel":
            self.toggle_panel()
            return
        if action == "favorite":
            self._toggle_favorite()
            return
        if action == "help":
            self._toggle_help()
            return
        if action == "command":
            self.status.open_command()
            return
        if action == "search":
            self._open_search()
            return
        if action == "focus_panel":
            self._focus_panel()
            return
        if action == "zoom_in":
            self.viewer.canvas.zoom_in()
            return
        if action == "zoom_out":
            self.viewer.canvas.zoom_out()
            return
        if action == "toggle_zoom":
            was_fit = not self.viewer.canvas.is_zoomed()
            self.viewer.canvas.toggle_zoom()
            self.status.show_message("100 %" if was_fit else "ajustado", 1200)
            return
        if action.startswith("pan_"):
            dx, dy = {
                "pan_left": (-PAN_STEP, 0),
                "pan_right": (PAN_STEP, 0),
                "pan_up": (0, -PAN_STEP),
                "pan_down": (0, PAN_STEP),
            }[action]
            if not self.viewer.canvas.pan_by(dx, dy):
                self.status.show_message("sin zoom · + para acercar", 1200)

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
            self._focus_current_view()
            return
        if self._viewer_visible:
            if self.viewer.canvas.is_zoomed():
                self.viewer.canvas.reset_zoom()
                self.status.show_message("ajustado", 1200)
            else:
                self._close_viewer()
            return
        if self.search_text:
            self.search_text = ""
            self._apply_filter("")
            self.status.show_message("búsqueda limpiada", 2000)
        self._focus_current_view()

    def toggle_panel(self) -> None:
        had_focus = self.panel.scroll.hasFocus()
        self.panel_visible = not self.panel_visible
        self._layout_content()
        if self.panel_visible:
            self.status.show_message("panel de metadatos", 2000)
        else:
            if had_focus:
                self._focus_current_view()
            self.status.show_message("panel oculto", 2000)

    def _toggle_help(self) -> None:
        if self.help_overlay.isVisible():
            self.help_overlay.hide()
            self._hide_content_blocker()
            self.status.set_help_mode(False)
            self._focus_current_view()
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
        if not self.grid_row:
            self.viewer.update_item(
                updated.key,
                updated.favorite,
                photo_accessible_name(updated),
            )
        self.status.show_message(
            "favorito" if updated.favorite else "sin favorito", 2000
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
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_O):
            self._open_viewer()
            return
        if key == Qt.Key_G:
            if event.modifiers() & Qt.ShiftModifier:
                if self._viewer_visible:
                    self._viewer_last()
                else:
                    self._move_cursor("end")
            else:
                self._handle_g()
            return
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
        super().keyPressEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app)
    photos = make_photos()
    window = PrototypeWindow(photos, grid_row=True)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
