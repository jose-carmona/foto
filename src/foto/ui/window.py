"""Ventana principal de la UI de `foto`: el orquestador.

`PhotoWindow` monta y conecta los widgets de los módulos enfocados —la rejilla
(``grid.py``), el visor (`canvas.py`), los paneles de metadatos y de estado
(``panels.py``) y la ayuda integrada (`help.py`)—, aplica el tema de la capa
de tokens (`theme.py`) y ejecuta el vocabulario de comandos (`commands.py`).
Aquí vive solo la orquestación: el estado de la ventana, el repartidor único
`_dispatch` con su registro de acciones, la caché LRU de imágenes, la
planificación de miniaturas visibles y el layout de las cuatro regiones.

Las invariantes de la ventana no cambian: cuatro regiones —zona de rejilla
(siempre visible), zona de estado (barra inferior de 28 px, siempre visible),
panel de metadatos y panel de foto—; los paneles solo cambian la dimensión de
la zona de rejilla; su comportamiento —navegación, grupos, scroll,
selección— es idéntico en todos los estados, y la miniatura seleccionada
queda siempre a la vista. Esto lo fija prompts/refabricacion-visual-visor.md.

`Photo.path` y las miniaturas las aporta la app real, que consume el catálogo
y arranca con los paneles ocultos, como fija docs/diseno-ui.md. La demo con
datos falsos `python -m foto.ui.prototype` está aparte, en `prototype.py`.

El modelo `Photo` y las reglas puras (orden, grupos, búsqueda, navegación
vertical, teclas de grupo y el color atenuado) siguen en los módulos sin Qt
`models.py`, `presentation.py` y `keybindings.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from foto.core.thumbnails import eviction_candidates
from foto.ui.canvas import ImageLoadTask, PhotoViewer
from foto.ui.grid import (
    FAVORITE_ROLE,
    GRID_MARGIN,
    GROUP_HEADER_H,
    GROUP_ROLE,
    HEADER_ROLE,
    ITEM_H,
    ITEM_W,
    PHOTO_ROLE,
    THUMB_H,
    THUMB_W,
    PhotoGrid,
    ThumbnailDelegate,
    default_thumbnail,
)
from foto.ui.help import HelpOverlay
from foto.ui.keybindings import (
    WINDOW,
    event_modifiers,
    key_action,
)
from foto.ui.models import Photo, map_url
from foto.ui.panels import MetadataPanel, StatusBar
from foto.ui.presentation import (
    DEFAULT_SORT,
    SORT_KEYS,
    photo_accessible_name,
    photo_group,
    photo_search_text,
    sorted_photos,
)
from foto.ui.theme import (
    app_palette,
    apply_theme,
    set_muted,
    ui_font,
)

PANEL_W = 426
PAN_STEP = 48
IMAGE_CACHE_SIZE = 3
# Ventana de miniaturas en memoria. Cada icono de 400×247 ocupa ~395 KB; 160
# cubre de sobra un viewport 4K y limita la memoria a unos 63 MB.
THUMBNAIL_CACHE_SIZE = 160


class ContentArea(QWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class PhotoWindow(QMainWindow):
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
        grid_row: bool = False,
    ) -> None:
        super().__init__()
        # True usa la rejilla como fila inferior del visor; False muestra el
        # visor a pantalla completa con la rejilla oculta. La app real y la
        # demo usan True.
        self.grid_row = grid_row
        self.photos = {photo.key: photo for photo in photos}
        self._sort_key = DEFAULT_SORT
        self.thumbnails = dict(thumbnails or {})
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

        self.setWindowTitle(title or "foto")
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

        self.viewer = PhotoViewer(self.content)
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
        self.grid.action_requested.connect(self._dispatch)
        self.grid.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnails)
        self.grid.horizontalScrollBar().valueChanged.connect(
            self._schedule_visible_thumbnails
        )
        self.viewer.action_requested.connect(self._dispatch)
        self.panel.scroll.action_requested.connect(self._dispatch)
        self.status.command_submitted.connect(self._run_command)
        self.status.search_changed.connect(self._apply_filter)
        self.status.search_committed.connect(self._commit_search)
        self.status.prompt_cancelled.connect(self._cancel_prompt)
        self.status.prompt_opened.connect(self._show_content_blocker)
        self.status.prompt_closed.connect(self._hide_content_blocker)
        self.status.prompt_closed.connect(self._focus_current_view)

        # Registro de acciones: toda acción producible por la tabla debe
        # tener consumidor; _dispatch falla de forma ruidosa si no.
        self._actions = self._build_action_registry()

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
        palette = app_palette()
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
                    icon = default_thumbnail(index)
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
        if (
            self._viewer_visible
            and self._displayed_key == key
            and self._pending_image_key == key
        ):
            self.viewer.canvas.set_image(
                icon.pixmap(QSize(THUMB_W, THUMB_H)),
                preview=True,
            )

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
            self._cancel_g_pending()
            self._viewer_first()
        else:
            self._g_pending = True
            self._g_timer.start()

    def _clear_g_pending(self) -> None:
        self._g_pending = False

    def _cancel_g_pending(self) -> None:
        """Cierra el prefijo ``g`` de una secuencia (``g g`` o ``g m``)."""
        self._g_pending = False
        self._g_timer.stop()

    def _handle_m(self) -> None:
        """Segunda tecla de ``g m``: abre la ubicación en el navegador.

        Sin un ``g`` pendiente, ``m`` queda reservada y no hace nada,
        igual que ``g`` antes de su segunda tecla.
        """
        if not self._g_pending:
            return
        self._cancel_g_pending()
        self._open_map()

    def _open_map(self) -> None:
        """Abre la ubicación GPS de la foto actual en el navegador.

        ``QDesktopServices.openUrl`` delega en el sistema —``open`/
        NSWorkspace en macOS, portal y ``xdg-open`` en Omarchy/Wayland—,
        así que no hay código por plataforma. Es una salida a la web
        explícita: solo ocurre cuando el usuario pulsa ``g m`` (§3 de
        reglas no negociables: sin red por defecto).
        """
        photo = self._current_photo()
        if photo is None:
            self.status.show_message("no hay fotos", 2000)
            return
        if photo.gps_latitude is None or photo.gps_longitude is None:
            self.status.show_message("la foto no tiene ubicación GPS", 3000)
            return
        url = QUrl(map_url(photo.gps_latitude, photo.gps_longitude))
        if not QDesktopServices.openUrl(url):
            self.status.show_message("no se pudo abrir el navegador", 3000)
            return
        where = f" · {photo.location}" if photo.location else ""
        self.status.show_message(f"mapa{where}", 2500)

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
                    "No hay fotos en el índice\nEjecuta 'foto-index' para crear el catálogo"
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

    # ------------------------------------------------------------------
    # Registro de acciones y dispatcher único (P1.5, fase 2)
    # ------------------------------------------------------------------
    def _build_action_registry(self) -> dict[str, Callable[..., None]]:
        """Una entrada por cada acción producible por la tabla de teclas.

        ``ignore`` (flechas laterales del panel) tiene handler explícito para
        que ninguna acción emitida quede sin consumidor. ``help`` también es
        alcanzable por ``:help`` desde la línea de comandos.
        """
        registry: dict[str, Callable[..., None]] = {}
        for action in (
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
            registry[action] = (lambda a=action: self._move_grid(a))
        registry["g"] = self._handle_g
        registry["m"] = self._handle_m
        registry["open_viewer"] = self._open_viewer
        for action in ("zoom_in", "zoom_out", "toggle_zoom"):
            registry[action] = (lambda a=action: self._on_zoom(a))
        for action in ("pan_left", "pan_right", "pan_up", "pan_down"):
            registry[action] = (lambda a=action: self._on_pan(a))
        registry["close"] = self._on_close
        registry["escape"] = self._handle_escape
        registry["help"] = self._toggle_help
        registry["command"] = self.status.open_command
        registry["search"] = self._open_search
        registry["panel"] = self.toggle_panel
        registry["favorite"] = self._toggle_favorite
        registry["focus_panel"] = self._focus_panel
        registry["focus_back"] = self._focus_current_view
        for action in (
            "scroll_up",
            "scroll_down",
            "scroll_page_up",
            "scroll_page_down",
            "scroll_top",
            "scroll_end",
        ):
            registry[action] = (lambda a=action: self._panel_scroll(a))
        registry["next"] = lambda: self._viewer_navigate(1)
        registry["previous"] = lambda: self._viewer_navigate(-1)
        registry["first"] = self._viewer_first
        registry["last"] = self._viewer_last
        registry["group_first"] = lambda: self._viewer_group_edge(True)
        registry["group_last"] = lambda: self._viewer_group_edge(False)
        registry["ignore"] = self._no_op
        return registry

    def _dispatch(self, action: str) -> None:
        """Único repartidor de acciones de teclado de la ventana.

        1. cualquier acción distinta de ``g`` y ``m`` cancela un prefijo
           ``g`` pendiente (``g g`` o ``g m``); ``m`` solo actúa con ese
           prefijo y ``g`` siempre lo administra;
        2. con la ayuda visible, solo las teclas de cierre la ocultan;
        3. con el prompt activo, solo Esc lo cancela;
        4. en cualquier otro caso, el handler registrado ejecuta la acción
           (error ruidoso si la acción no está registrada: nunca debe ocurrir).
        """
        if action not in ("g", "m") and self._g_pending:
            self._cancel_g_pending()
        if self.help_overlay.isVisible():
            if action in ("escape", "help", "close"):
                self._toggle_help()
            return
        if self.status.prompt_active:
            if action == "escape":
                self._handle_escape()
            return
        handler = self._actions.get(action)
        if handler is None:
            raise RuntimeError(f"acción de teclado no registrada: {action}")
        handler()

    def _move_grid(self, action: str) -> None:
        """Navegación del cursor: con el visor a pantalla completa reproduce
        la rama del visor; con la fila del visor o sin él, mueve el cursor."""
        if self._viewer_visible and not self.grid_row:
            if action == "j":
                self._viewer_navigate(1)
                return
            if action == "k":
                self._viewer_navigate(-1)
                return
            if action in ("h", "l"):
                self._pan_canvas("pan_left" if action == "h" else "pan_right")
                return
            if action in ("0", "$"):
                self._viewer_group_edge(action == "0")
                return
            if action == "home":
                self._viewer_first()
                return
            if action == "end":
                self._viewer_last()
                return
        self._move_cursor(action)

    def _pan_canvas(self, action: str) -> None:
        dx, dy = {
            "pan_left": (-PAN_STEP, 0),
            "pan_right": (PAN_STEP, 0),
            "pan_up": (0, -PAN_STEP),
            "pan_down": (0, PAN_STEP),
        }[action]
        if not self.viewer.canvas.pan_by(dx, dy):
            self.status.show_message("sin zoom · + para acercar", 1200)

    def _on_pan(self, action: str) -> None:
        if self._viewer_visible:
            self._pan_canvas(action)

    def _on_zoom(self, action: str) -> None:
        if not self._viewer_visible:
            return
        if action == "zoom_in":
            self.viewer.canvas.zoom_in()
            return
        if action == "zoom_out":
            self.viewer.canvas.zoom_out()
            return
        was_fit = not self.viewer.canvas.is_zoomed()
        self.viewer.canvas.toggle_zoom()
        self.status.show_message("100 %" if was_fit else "ajustado", 1200)

    def _on_close(self) -> None:
        if self._viewer_visible:
            self._close_viewer()
        else:
            self.status.show_message("usa Ctrl+q para salir", 3000)

    def _panel_scroll(self, action: str) -> None:
        bar = self.panel.scroll.verticalScrollBar()
        if action == "scroll_up" or action == "scroll_down":
            delta = 32
            bar.setValue(bar.value() + (-delta if action == "scroll_up" else delta))
        elif action == "scroll_page_up" or action == "scroll_page_down":
            delta = bar.pageStep()
            bar.setValue(
                bar.value() + (-delta if action == "scroll_page_up" else delta)
            )
        elif action == "scroll_top":
            bar.setValue(bar.minimum())
        else:
            bar.setValue(bar.maximum())

    def _no_op(self) -> None:
        """Handler de ``ignore`` (flechas laterales del panel): no-op claro."""

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
        self.status.show_message(
            "favorito" if updated.favorite else "sin favorito", 2000
        )

    def keyPressEvent(self, event):
        # Sin guardas de modo: las de _dispatch son las mismas y únicas.
        shift, ctrl, cmd = event_modifiers(event)
        action = key_action(WINDOW, event.key(), shift=shift, ctrl=ctrl, cmd=cmd)
        if action is None:
            super().keyPressEvent(event)
            return
        self._dispatch(action)
