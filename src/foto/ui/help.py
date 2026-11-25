"""La ayuda integrada: lectura, render a HTML y overlay.

``_help_markdown`` lee el documento de atajos empaquetado
(``foto/resources/ayuda.md``), ``render_help`` lo convierte al subset de HTML
enriquecido del ``QTextBrowser`` y ``HelpOverlay`` lo muestra sobre la
ventana.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import QFrame, QTextBrowser, QVBoxLayout, QWidget

from foto.ui.presentation import muted_rgb
from foto.ui.theme import HELP_BODY_QSS, app_palette, help_chip_surface, ui_font

# Puntuación que ya aporta su propio aire: la ficha no añade relleno del lado
# que toca con ella, para no separar la tecla del signo que la acompaña.
_CHIP_TRAILING_PUNCT = ",.;:!?)]}»"
_CHIP_LEADING_PUNCT = "([{«"


def _help_markdown() -> str:
    """El documento de ayuda empaquetado con la aplicación."""
    from importlib import resources

    import foto

    return (resources.files(foto) / "resources" / "ayuda.md").read_text(
        encoding="utf-8"
    )


def _help_color(role: QPalette.ColorRole) -> str:
    palette = app_palette()
    return palette.color(role).name(QColor.HexArgb)


def _help_muted() -> str:
    """Token fg.muted: texto atenuado con contraste suficiente."""
    palette = app_palette()
    foreground = palette.color(QPalette.WindowText)
    background = palette.color(QPalette.Window)
    return QColor(
        *muted_rgb(
            (foreground.red(), foreground.green(), foreground.blue()),
            (background.red(), background.green(), background.blue()),
        )
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


def _help_chip(text: str, *, pad_left: bool = True, pad_right: bool = True) -> str:
    """Ficha de tecla: monoespaciada, sobre la superficie derivada del tema."""
    mono = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
    return (
        f'<span style="background-color:{help_chip_surface().name(QColor.HexArgb)};'
        f" color:{_help_color(QPalette.WindowText)};"
        f" font-family:'{mono}'; font-size:12px;"
        f'">{_help_chip_pad(text, left=pad_left, right=pad_right)}</span>'
    )


def _help_chip_pad(text: str, *, left: bool = True, right: bool = True) -> str:
    """Texto de la ficha con su margen interior, para medir con métricas."""
    padding = "\u00a0\u00a0"
    return f"{padding if left else ''}{text}{padding if right else ''}"


def _help_inline(text: str) -> str:
    """Convierte ``teclas`` entre acentos en fichas; el resto queda igual.

    Las fichas ya llevan su propio relleno, así que los trozos se unen sin
    espacios añadidos (``" ".join`` duplicaba los que el texto ya tenía). El
    relleno se recorta en el lado que toca con puntuación que no lo necesita:
    una ficha entre paréntesis se lee ``(Esc)``.
    """
    chunks = text.split("`")
    pieces: list[str] = []
    for index, chunk in enumerate(chunks):
        if not chunk:
            continue
        if index % 2 == 0:
            pieces.append(chunk)
            continue
        before = chunks[index - 1][-1:] if index > 0 else ""
        after = chunks[index + 1][:1] if index + 1 < len(chunks) else ""
        pieces.append(
            _help_chip(
                chunk,
                pad_left=not before or before not in _CHIP_LEADING_PUNCT,
                pad_right=not after or after not in _CHIP_TRAILING_PUNCT,
            )
        )
    return "".join(pieces)


def _help_heading(label: str, level: int) -> str:
    size = 26 if level == 1 else 20
    return (
        f'<p style="margin-top:{"0" if level == 1 else "18"}px; margin-bottom:6px;'
        f" font-size:{size}px; font-weight:600;"
        f' color:{_help_color(QPalette.WindowText)}">{label}</p>'
        f'<hr style="color:{_help_color(QPalette.Mid)};">'
    )


def _help_paragraph(lines: list[str]) -> str:
    """Un párrafo completo: las líneas consecutivas del markdown se unen."""
    return (
        '<p style="margin-top:0; margin-bottom:12px;">'
        f"{_help_inline(' '.join(lines))}</p>"
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
    lines: list[str] = []

    def flush_paragraph() -> None:
        if lines:
            blocks.append(("paragraph", list(lines)))
            lines.clear()

    for line in markdown.splitlines():
        bare = line.strip()
        if bare.startswith("|"):
            flush_paragraph()
            cells = [cell.strip() for cell in bare.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue  # separador de cabecera
            rows.append(cells)
            continue
        if rows:
            blocks.append(("table", rows))
            rows = []
        if not bare:
            flush_paragraph()
            continue
        if bare.startswith("##"):
            flush_paragraph()
            blocks.append(("heading", (bare[2:].strip(), 2)))
        elif bare.startswith("#"):
            flush_paragraph()
            blocks.append(("heading", (bare[1:].strip(), 1)))
        else:
            lines.append(bare)
    flush_paragraph()
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
            html.append(_help_paragraph(data))  # type: ignore[misc]
    return "".join(html)


class HelpOverlay(QFrame):
    """Ayuda de atajos, renderizada desde ``foto/resources/ayuda.md``."""

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
        # El foco se queda en el overlay: si un clic se lo llevara al
        # navegador, las teclas dejarían de llegar a ``keyPressEvent``. Con
        # ``NoFocus`` la selección con ratón sigue funcionando.
        body.setFocusPolicy(Qt.NoFocus)
        body.setLineWidth(0)
        body.setFont(ui_font(13))
        document = body.document()
        document.setDefaultFont(ui_font(13))
        document.setDocumentMargin(0)
        body.setStyleSheet(HELP_BODY_QSS)
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
        palette = app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        scroll = self.body.verticalScrollBar().value()
        self._render()
        self.body.verticalScrollBar().setValue(scroll)
        self.update()

    def _scroll_by(self, delta: int) -> None:
        bar = self.body.verticalScrollBar()
        bar.setValue(bar.value() + delta)

    def _line_step(self) -> int:
        """Paso de una línea: las métricas de la fuente del documento."""
        metrics = QFontMetrics(self.body.document().defaultFont())
        return max(1, metrics.lineSpacing())

    def keyPressEvent(self, event) -> None:
        """Navega el scroll de la ayuda con las teclas de § 2.3.

        ``↑``/``↓`` y ``j``/``k`` avanzan una línea medida con la fuente;
        ``PgUp``/``PgDn``, ``Espacio``/``Shift+Espacio`` y ``Ctrl+d``/``Ctrl+u``
        mueven páginas o medias páginas con el paso de la barra, y
        ``Home``/``End`` van a los extremos. El resto de teclas cae a la
        ventana, que cerrará el overlay cuando sea ``Esc`` o ``q``.
        """
        bar = self.body.verticalScrollBar()
        key = event.key()
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        if key in (Qt.Key_Down, Qt.Key_J):
            self._scroll_by(self._line_step())
        elif key in (Qt.Key_Up, Qt.Key_K):
            self._scroll_by(-self._line_step())
        elif key == Qt.Key_PageDown or (key == Qt.Key_Space and not shift):
            self._scroll_by(bar.pageStep())
        elif key == Qt.Key_PageUp or (key == Qt.Key_Space and shift):
            self._scroll_by(-bar.pageStep())
        elif key == Qt.Key_Home:
            bar.setValue(bar.minimum())
        elif key == Qt.Key_End:
            bar.setValue(bar.maximum())
        elif key == Qt.Key_D and ctrl:
            self._scroll_by(max(1, bar.pageStep() // 2))
        elif key == Qt.Key_U and ctrl:
            self._scroll_by(-max(1, bar.pageStep() // 2))
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusNextPrevChild(self, next: bool) -> bool:
        return True

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.Mid))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
