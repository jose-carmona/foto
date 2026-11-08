"""La rejilla de fotos y su contrato de datos.

``PhotoGrid`` es el ``QListWidget`` que sirve tanto de rejilla como de fila
del visor, junto con sus roles de ítem, su geometría (``THUMB_*``, ``ITEM_*``,
``GROUP_HEADER_H``), el delegado que pinta las miniaturas y los iconos de
reserva de la demo (``default_thumbnail``).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from foto.ui.keybindings import (
    GRID,
    GRID_VIEWER,
    event_modifiers,
    key_action,
)
from foto.ui.presentation import vertical_row_target
from foto.ui.theme import GRID_QSS, app_palette, reapply_style, ui_font

PHOTO_ROLE = Qt.UserRole + 1
FAVORITE_ROLE = Qt.UserRole + 2
GROUP_ROLE = Qt.UserRole + 3
HEADER_ROLE = Qt.UserRole + 4

THUMB_W = 400
THUMB_H = 247
GRID_GAP = 16
GRID_MARGIN = 16
ITEM_W = THUMB_W + GRID_GAP
ITEM_H = THUMB_H + GRID_GAP
GROUP_HEADER_H = 52



def default_thumbnail(index: int) -> QIcon:
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


class PhotoGrid(QListWidget):
    """Rejilla que traduce las teclas de vim a acciones de la ventana."""

    action_requested = Signal(str)

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
        self.setStyleSheet(GRID_QSS)
        palette = self.palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.setPalette(palette)

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
        palette = app_palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        reapply_style(self)
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
        shift, ctrl, cmd = event_modifiers(event)
        context = GRID_VIEWER if self.viewer_keys else GRID
        action = key_action(context, event.key(), shift=shift, ctrl=ctrl, cmd=cmd)
        if action is None:
            super().keyPressEvent(event)
            return
        self.action_requested.emit(action)
