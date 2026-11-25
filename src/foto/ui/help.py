"""La ayuda integrada: lectura, render a HTML y overlay.

``_help_markdown`` lee el documento de atajos empaquetado
(``foto/resources/ayuda.md``), ``render_help`` lo convierte al subset de HTML
enriquecido del ``QTextBrowser`` y ``HelpOverlay`` lo muestra sobre la
ventana.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPalette,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from foto.ui.presentation import muted_rgb
from foto.ui.theme import (
    HELP_BODY_QSS,
    app_palette,
    help_chip_surface,
    reapply_style,
    set_muted,
    ui_font,
)

# Puntuación que ya aporta su propio aire: la ficha no añade relleno del lado
# que toca con ella, para no separar la tecla del signo que la acompaña.
_CHIP_TRAILING_PUNCT = ",.;:!?)]}»"
_CHIP_LEADING_PUNCT = "([{«"


@dataclass(frozen=True)
class HelpSection:
    """Una sección ``##`` del documento de ayuda: ancla estable y título."""

    anchor: str
    title: str


def help_sections(markdown: str) -> tuple[HelpSection, ...]:
    """Las secciones ``##`` del documento, en orden, con anclas ``sN``.

    El índice de la cabecera y el render comparten esta lista: las anclas
    ``s1``, ``s2``… son estables aunque cambien los títulos, y el
    ``# Ayuda`` inicial no se indexa. El título se recorta igual que hace el
    parser de ``render_help``, de modo que índice y anclas coinciden 1:1.
    """
    sections: list[HelpSection] = []
    for line in markdown.splitlines():
        bare = line.strip()
        if bare.startswith("##"):
            sections.append(
                HelpSection(anchor=f"s{len(sections) + 1}", title=bare[2:].strip())
            )
    return tuple(sections)


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
    """Título del documento y de sección, con su escala y regla (§ 2.4).

    ``h1``/``h2`` semánticos a 20/15 px con peso 600; la regla usa
    ``Midlight``, más discreta que el ``Mid`` anterior. El filete va con
    ``background-color``: el QSS del cuerpo (``color: palette(...)``) hace
    que Qt ignore el ``color`` inline de un ``<hr>`` y lo pinte negro.
    """
    size = 20 if level == 1 else 15
    tag = "h1" if level == 1 else "h2"
    return (
        f'<{tag} style="margin-top:{"0" if level == 1 else "18"}px; margin-bottom:6px;'
        f" font-size:{size}px; font-weight:600;"
        f' color:{_help_color(QPalette.WindowText)}">{label}</{tag}>'
        f'<hr style="background-color:{_help_color(QPalette.Midlight)};">'
    )


def _help_text_color() -> str:
    """Color del texto corriente, explícito en cada bloque.

    Los párrafos y celdas no pueden apoyarse en el QSS del cuerpo
    (``color: palette(window-text)``): Qt no reinterpreta las funciones
    ``palette(...)`` al cambiar de tema (ver ``reapply_style``) y tras un
    cambio oscuro→claro en vivo el texto se quedaba blanco sobre la
    superficie clara. Igual que encabezados y fichas, cada bloque lleva su
    color inline del tema vigente en el momento del render.
    """
    return _help_color(QPalette.WindowText)


def _help_paragraph(lines: list[str]) -> str:
    """Un párrafo completo: las líneas consecutivas del markdown se unen."""
    return (
        f'<p style="margin-top:0; margin-bottom:12px; color:{_help_text_color()};">'
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
    color = _help_text_color()
    keys = _help_inline(cells[0]) if cells else ""
    description = _help_inline(cells[1]) if len(cells) > 1 else ""
    if header:
        # Cabecera de tabla: 12 px con peso 600 en fg.muted, para que no
        # compita con los títulos de sección (§ 2.4).
        style = f"font-size:12px; font-weight:600; color:{_help_muted()};"
        keys = f'<span style="{style}">{keys}</span>'
        description = f'<span style="{style}">{description}</span>'
    tag = "th" if header else "td"
    # Qt centra el contenido de un ``th``: la cabecera va alineada a la
    # izquierda, como las filas de datos.
    align = ' align="left"' if header else ""
    padding = "2px" if header else "8px"
    return (
        "<tr>"
        f'<{tag}{align} width="{keys_width}" style="vertical-align:top;">'
        f'<p style="margin-top:{padding}; margin-bottom:{padding};'
        f' color:{color};">{keys}</p></{tag}>'
        f'<{tag}{align} style="vertical-align:top;">'
        f"<p style=\"margin-top:{padding}; margin-bottom:{padding};"
        f" color:{color};\">"
        f"{description}</p></{tag}>"
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
    Las secciones ``##`` llevan antes del encabezado su ``<a name="sN">``,
    el mismo orden que devuelven ``help_sections`` para el índice.
    """
    sections = help_sections(markdown)
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
    section_index = 0
    for kind, data in blocks:
        if kind == "heading":
            label, level = data  # type: ignore[misc]
            if level == 2 and section_index < len(sections):
                html.append(f'<a name="{sections[section_index].anchor}"></a>')
                section_index += 1
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

        self.sections = help_sections(_help_markdown())
        self.current_section: str | None = None
        self._pending_section: HelpSection | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(6)

        layout.addWidget(self._build_header())
        self.rule = self._build_rule()
        layout.addWidget(self.rule)

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
        body.setAccessibleName("atajos de teclado")
        document = body.document()
        document.setDefaultFont(ui_font(13))
        document.setDocumentMargin(0)
        body.setStyleSheet(HELP_BODY_QSS)
        layout.addWidget(body)
        self.body = body
        self._render()

    def _build_header(self) -> QWidget:
        """Cabecera fija: índice de secciones y pista de cierre (§ 2.2).

        No scrollea —el cuerpo lo hace debajo— y no lleva botones: el
        cierre es solo por teclado, así que no hay «✕» ni ``QToolButton``.
        """
        header = QWidget(self)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self.index_label = QLabel()
        self.index_label.setFont(ui_font(12))
        # A 576 px el índice no cabe en una línea: se parte en dos.
        self.index_label.setWordWrap(True)
        self.index_label.setAccessibleName("índice de la ayuda")
        self.index_label.setText(self._index_text())

        self.hint_label = QLabel("Esc/q cerrar")
        self.hint_label.setFont(ui_font(12))

        row.addWidget(self.index_label, 1)
        row.addWidget(self.hint_label)
        self._apply_header_palette()
        return header

    def _index_text(self) -> str:
        return " · ".join(
            f"{number} {section.title}"
            for number, section in enumerate(self.sections, 1)
        )

    def _build_rule(self) -> QFrame:
        """Filete bajo la cabecera, con el ``Midlight`` del tema."""
        rule = QFrame(self)
        rule.setFrameShape(QFrame.NoFrame)
        rule.setFixedHeight(1)
        rule.setAutoFillBackground(True)
        self._apply_rule_palette(rule)
        return rule

    def _apply_header_palette(self) -> None:
        set_muted(self.index_label)
        set_muted(self.hint_label)

    def _apply_rule_palette(self, rule: QFrame) -> None:
        palette = rule.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.Midlight))
        rule.setPalette(palette)

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
        self._apply_line_height()

    def _apply_line_height(self) -> None:
        """Interlineado 1,4 en todos los bloques (§ 2.4).

        Qt rich text ignora ``line-height`` en el CSS: se aplica bloque a
        bloque con ``QTextBlockFormat`` después de ``setHtml``, incluidos los
        bloques de las celdas de tabla. Se reaplica en cada ``_render``.
        """
        fmt = QTextBlockFormat()
        fmt.setLineHeight(
            140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        cursor = QTextCursor(self.body.document())
        block = self.body.document().firstBlock()
        while block.isValid():
            cursor.setPosition(block.position())
            cursor.mergeBlockFormat(fmt)
            block = block.next()

    def show_section(self, title: str | None) -> None:
        """Abre el documento en la sección con ese título; ``None``: al inicio.

        Un título que no existe en ``ayuda.md`` cae también al inicio
        (§ 2.1). El salto queda pendiente para el siguiente pase de eventos:
        con el overlay recién mostrado, ``scrollToAnchor`` necesita la
        geometría ya calculada para posicionar el documento. Sin sección
        (inicio) se restablece el scroll, también al reabrir.
        """
        self._pending_section = None
        if not title:
            bar = self.body.verticalScrollBar()
            bar.setValue(bar.minimum())
            self.current_section = None
            return
        section = next((s for s in self.sections if s.title == title), None)
        if section is None:
            return
        self._pending_section = section
        QTimer.singleShot(0, self._apply_pending_section)

    def _apply_pending_section(self) -> None:
        section, self._pending_section = self._pending_section, None
        if section is None or not self.isVisible():
            return
        self.body.scrollToAnchor(section.anchor)
        self.current_section = section.title

    def retheme(self) -> None:
        palette = app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        self._apply_header_palette()
        self._apply_rule_palette(self.rule)
        # El QSS del cuerpo usa palette(window-text) y Qt no reinterpreta las
        # funciones palette(...) al cambiar la paleta: re-asignar la cadena
        # provoca el re-parse y el texto no se queda con el color del tema
        # anterior (blanco sobre claro tras un cambio oscuro→claro en vivo).
        reapply_style(self.body)
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
        """Navega la ayuda con las teclas de § 2.3.

        ``↑``/``↓`` y ``j``/``k`` avanzan una línea medida con la fuente;
        ``PgUp``/``PgDn``, ``Espacio``/``Shift+Espacio`` y ``Ctrl+d``/``Ctrl+u``
        mueven páginas o medias páginas con el paso de la barra,
        ``Home``/``End`` van a los extremos y ``1``–``9`` saltan a la sección
        con ese número del índice. El resto de teclas cae a la ventana, que
        cerrará el overlay cuando sea ``Esc`` o ``q``.
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
        elif Qt.Key_1 <= key <= Qt.Key_9 and not (
            ctrl
            or shift
            or bool(event.modifiers() & Qt.AltModifier)
            or bool(event.modifiers() & Qt.MetaModifier)
        ):
            # Salto a la sección N del índice; los dígitos sin sección (7–9
            # con seis secciones) no hacen nada.
            index = key - Qt.Key_1
            if index < len(self.sections):
                section = self.sections[index]
                self.body.scrollToAnchor(section.anchor)
                self.current_section = section.title
            event.accept()
            return
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
