"""Chips de etiqueta: la forma de etiqueta colgante del ADR 0006.

Dibuja el chip con forma de etiqueta —rectángulo redondeado con la punta a
la izquierda y un agujero transparente en medio de la punta— para la
insignia de favorito de la rejilla (``grid.py``) y del visor (``canvas.py``),
y el flujo de chips con los nombres de las etiquetas del panel de metadatos
(``panels.py``). Los colores son tokens de ``theme.py``, iguales en ambos
temas porque se pintan sobre la foto; aquí solo vive la geometría y el trazo.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from foto.ui.theme import (
    CHIP_CONTENT_COLOR,
    CHIP_FAVORITE_COLOR,
    CHIP_TAG_COLOR,
    ui_font,
)

#: Altura del chip del panel de metadatos.
PANEL_CHIP_H = 30

# Proporciones del chip sobre su altura, tomadas de las capturas de diseño:
# la punta entra ~0,52 h, el agujero es ~0,13 h y el radio de esquina ~0,21 h.
_TIP = 0.52
_HOLE = 0.13
#: Centro del agujero desde el borde izquierdo: dentro del triángulo de la
#: punta, algo hacia su base.
_HOLE_CX = 0.34
_CORNER = 0.21
_TEXT_GAP = 0.24  # margen entre la base del triángulo y el contenido
_PAD = 0.45  # margen derecho tras el contenido


def favorite_chip_size(height: int) -> QSize:
    """Tamaño del chip de favorito: solo corazón, sin texto."""
    font = ui_font(max(9, round(height * 0.56)), QFont.DemiBold)
    return QSize(
        math.ceil(_content_x(height) + QFontMetrics(font).horizontalAdvance("♥") + _PAD * height),
        height,
    )


def text_chip_size(height: int, text: str) -> QSize:
    """Tamaño del chip de una etiqueta con su nombre."""
    font = ui_font(max(9, round(height * 0.5)), QFont.DemiBold)
    return QSize(
        math.ceil(_content_x(height) + QFontMetrics(font).horizontalAdvance(text) + _PAD * height),
        height,
    )


def _content_x(height: float) -> float:
    """X donde empieza el contenido: la base del triángulo y su margen."""
    return _TIP * height + _TEXT_GAP * height


def draw_tag_chip(
    painter: QPainter,
    rect,
    *,
    text: str = "",
    favorite: bool = False,
) -> None:
    """Pinta un chip de etiqueta dentro de ``rect``.

    Con ``favorite=True`` el chip es carmesí y muestra un corazón (el texto
    se ignora); con ``text`` muestra el nombre en un chip negro. El agujero
    es un anillo blanco que deja ver el color del chip.
    """
    height = rect.height()
    color = CHIP_FAVORITE_COLOR if favorite else CHIP_TAG_COLOR
    body = _chip_path(QRectF(rect))

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)

    # El agujero se resta del cuerpo: es transparente de verdad, sin borde,
    # y se ve a través de él la foto o el fondo del panel. Va en medio del
    # triángulo de la punta.
    hole = QPainterPath()
    hole.addEllipse(
        QPointF(rect.left() + _HOLE_CX * height, rect.center().y()),
        _HOLE * height,
        _HOLE * height,
    )
    painter.drawPath(body.subtracted(hole))

    content = QRectF(
        rect.left() + _content_x(height),
        rect.top(),
        rect.width() - _content_x(height) - _PAD * height,
        height,
    )
    painter.setPen(CHIP_CONTENT_COLOR)
    if favorite or not text:
        painter.setFont(ui_font(max(9, round(height * 0.56)), QFont.DemiBold))
        painter.drawText(content, Qt.AlignCenter, "♥")
    else:
        painter.setFont(ui_font(max(9, round(height * 0.5)), QFont.DemiBold))
        painter.drawText(content, Qt.AlignLeft | Qt.AlignVCenter, text)
    painter.restore()


def _chip_path(rect: QRectF) -> QPainterPath:
    """Polígono del chip con las esquinas suavizadas y la punta afilada.

    El radio respeta la disciplina visual (nunca mayor de 6 px).
    """
    h = rect.height()
    tip = _TIP * h
    radius = min(6.0, _CORNER * h)
    points = (
        QPointF(rect.left(), rect.center().y()),
        QPointF(rect.left() + tip, rect.top()),
        QPointF(rect.right(), rect.top()),
        QPointF(rect.right(), rect.bottom()),
        QPointF(rect.left() + tip, rect.bottom()),
    )
    path = QPainterPath()
    for index, point in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        if index == 0:
            # La punta no se redondea.
            path.moveTo(point)
            continue
        to_previous = point - previous
        to_following = following - point
        cut = min(0.5, radius / max(1.0, to_previous.manhattanLength()))
        path.lineTo(point - to_previous * cut)
        path.quadTo(point, point + to_following * cut)
    path.closeSubpath()
    return path


class TagChipFlow(QWidget):
    """Flujo de chips de etiqueta para la sección «Etiquetas» del panel.

    Recibe la lista que produce ``presentation.tag_chips`` —tercias
    ``(texto, tooltip, es_favorito)`` donde el texto vacío pinta el chip
    carmesí con corazón— y la compone en filas según el ancho disponible.
    El detalle de cada etiqueta (sugerida, machine learning) se ve en el
    tooltip del chip.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("etiquetas de la foto")
        self.setMouseTracking(True)
        self._chips: list[tuple[str, str, bool]] = []
        self._rects: list[QRectF] = []

    def set_chips(self, chips) -> None:
        self._chips = list(chips)
        self._relayout()
        self.update()

    def labels(self) -> tuple[str, ...]:
        """Textos de los chips, para pruebas y accesibilidad."""
        return tuple(text for text, _tooltip, _favorite in self._chips)

    def heightForWidth(self, width: int) -> int:
        return self._rows_height(max(1, width))

    def hasHeightForWidth(self) -> bool:
        return True

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self._rows_height(max(1, self.width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for chip, rect in zip(self._chips, self._rects):
            text, _tooltip, favorite = chip
            draw_tag_chip(painter, rect, text=text, favorite=favorite)

    def mouseMoveEvent(self, event) -> None:
        tooltip = ""
        for (text, tooltip_text, _favorite), rect in zip(self._chips, self._rects):
            if rect.contains(event.position()):
                tooltip = tooltip_text
                break
        if tooltip != self.toolTip():
            self.setToolTip(tooltip)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.setToolTip("")
        super().leaveEvent(event)

    def _relayout(self) -> None:
        self._rects = []
        x = 2.0  # respiro para la punta antialiada
        y = 0.0
        width = max(1, self.width())
        for chip in self._chips:
            size = self._chip_size(chip)
            if x > 2.0 and x + size.width() > width:
                x = 2.0
                y += PANEL_CHIP_H + 8
            self._rects.append(QRectF(x, y, size.width(), PANEL_CHIP_H))
            x += size.width() + 8
        self.setMinimumHeight(self._rows_height(width))

    @staticmethod
    def _chip_size(chip: tuple[str, str, bool]) -> QSize:
        text, _tooltip, favorite = chip
        if favorite or not text:
            return favorite_chip_size(PANEL_CHIP_H)
        return text_chip_size(PANEL_CHIP_H, text)

    def _rows_height(self, width: int) -> int:
        if not self._chips:
            return 0
        x = 2.0
        rows = 1
        for chip in self._chips:
            size = self._chip_size(chip)
            if x > 2.0 and x + size.width() > width:
                x = 2.0
                rows += 1
            x += size.width() + 8
        return rows * PANEL_CHIP_H + (rows - 1) * 8
