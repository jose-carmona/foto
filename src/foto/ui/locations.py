"""Panel de ubicaciones de la fototeca (ADR 0010, Decisión 7).

``LocationsOverlay`` muestra el estado de cada ubicación del catálogo
(nombre, ``tier``, ruta, estado y nº de fotos). Solo presenta: los datos
salen de la BD vía ``presentation.location_rows`` (puro, sin Qt) y el
estilo de la capa de tokens de ``theme.py``. Lo abre el banner de
ubicaciones no disponibles (clic) y el comando ``:locations``; se cierra
con ``Esc`` o ``q``, igual que la ayuda.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QFrame, QTextBrowser, QVBoxLayout, QWidget

from foto.ui.presentation import muted_rgb
from foto.ui.theme import app_palette, ui_font


def _panel_color(role: QPalette.ColorRole) -> str:
    return app_palette().color(role).name(QColor.HexArgb)


def _panel_muted() -> str:
    """Token ``fg.muted``: texto atenuado con contraste suficiente."""
    palette = app_palette()
    foreground = palette.color(QPalette.WindowText)
    background = palette.color(QPalette.Window)
    return QColor(
        *muted_rgb(
            (foreground.red(), foreground.green(), foreground.blue()),
            (background.red(), background.green(), background.blue()),
        )
    ).name(QColor.HexArgb)


def _fotos_text(count: int) -> str:
    noun = "foto" if count == 1 else "fotos"
    return f"{count} {noun}"


def render_locations(rows: Sequence[tuple[str, str, str, str, int]]) -> str:
    """HTML del panel a partir de las filas puras de ``location_rows``.

    Sin ubicaciones declaradas (demo, catálogo vacío) muestra una nota en
    lugar de una lista vacía.
    """
    muted = _panel_muted()
    border = _panel_color(QPalette.Mid)
    text = _panel_color(QPalette.WindowText)
    html = [
        (
            '<p style="margin-top:0; margin-bottom:6px; font-size:20px; '
            f'font-weight:600; color:{text}">Ubicaciones</p>'
            f'<hr style="color:{border};">'
        )
    ]
    if not rows:
        html.append(
            f'<p style="color:{muted}">El catálogo no declara ubicaciones.</p>'
        )
        return "".join(html)
    for name, tier, path, state, count in rows:
        html.append(
            f'<p style="margin-top:14px; margin-bottom:0;">'
            f'<b>{escape(name)}</b> '
            f'<span style="color:{muted}">· {escape(tier)}</span><br>'
            f'<span style="color:{muted}">{escape(state)} · {_fotos_text(count)}'
            f"<br>{escape(path)}</span></p>"
        )
    html.append(
        f'<p style="color:{muted}; margin-top:18px;">El estado físico se '
        "evaluó al abrir la ventana: para refrescarlo, reabre «foto» después "
        "de montar o desmontar un volumen.</p>"
    )
    return "".join(html)


class LocationsOverlay(QFrame):
    """Estado de las ubicaciones de la fototeca, en un overlay desplazable."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAccessibleName("panel de ubicaciones")
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
        layout.addWidget(body)
        self.body = body
        self.set_locations(())

    def set_locations(
        self, rows: Sequence[tuple[str, str, str, str, int]]
    ) -> None:
        """Pinta las filas del panel (las puras de ``location_rows``)."""
        self._rows = tuple(rows)
        self._render()

    def _render(self) -> None:
        document = self.body.document()
        document.setTextWidth(-1)
        self.body.setHtml(render_locations(self._rows))

    def retheme(self) -> None:
        palette = app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        scroll = self.body.verticalScrollBar().value()
        self._render()
        self.body.verticalScrollBar().setValue(scroll)
        self.update()

    def keyPressEvent(self, event) -> None:
        """Scroll con las flechas o ``j``/``k``; el resto cae a la ventana."""
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
