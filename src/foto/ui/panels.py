"""Panel de metadatos, barra de estado y prompts.

``MetadataPanel`` (con su ``PanelScroll``) muestra la ficha de metadatos;
``PromptEdit``, ``CompletionPopup`` y ``StatusBar`` (con ``BAR_H``) forman la
zona de estado: la línea de comandos ``:`` con historial y completado de
``COMMANDS``, la búsqueda ``/`` y los mensajes.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from foto.ui.commands import COMMANDS
from foto.ui.keybindings import PANEL, event_modifiers, key_action
from foto.ui.models import Photo
from foto.ui.theme import app_palette, error_color, set_muted, ui_font

BAR_H = 28


class PanelScroll(QScrollArea):
    action_requested = Signal(str)
    focus_changed = Signal(bool)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def keyPressEvent(self, event):
        # Solo traduce y emite: el dispatcher de la ventana decide qué hacer,
        # incluido el scroll local del propio panel (_panel_scroll).
        shift, ctrl, cmd = event_modifiers(event)
        action = key_action(PANEL, event.key(), shift=shift, ctrl=ctrl, cmd=cmd)
        if action is None:
            super().keyPressEvent(event)
            return
        self.action_requested.emit(action)


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
        palette = app_palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.AlternateBase))
        self.setPalette(palette)
        scroll_palette = app_palette()
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
        palette = app_palette()
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
        palette = app_palette()
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
