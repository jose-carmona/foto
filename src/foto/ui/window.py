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
queda siempre a la vista.

`Photo.path` y las miniaturas las aporta la app real, que consume el catálogo
y arranca con los paneles ocultos, como fija docs/diseno-ui.md. La demo con
datos falsos `python -m foto.ui.prototype` está aparte, en `prototype.py`.

El modelo `Photo` y las reglas puras (orden, grupos, búsqueda, navegación
vertical, teclas de grupo y el color atenuado) siguen en los módulos sin Qt
`models.py`, `presentation.py` y `keybindings.py`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
    QRect,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from foto.core.errors import CatalogError, TagError
from foto.core.thumbnails import eviction_candidates
from foto.storage.tags import FAVORITE_TAG, Tag, normalize_tag_name, toggle_favorite
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
    UNAVAILABLE_ROLE,
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
from foto.ui.locations import LocationsOverlay
from foto.ui.models import LocationStatus, Photo, map_url
from foto.ui.panels import MetadataPanel, StatusBar
from foto.ui.presentation import (
    DEFAULT_SORT,
    SORT_KEYS,
    PreparedPhotos,
    build_search_index,
    find_tag_in_catalog,
    help_section_for_context,
    location_banner_parts,
    location_banner_text,
    location_rows,
    photo_accessible_name,
    photo_group,
    photo_matches_tag_name,
    photo_search_text,
    sorted_photos,
    visible_tag_regions,
    with_favorite_tag,
)
from foto.ui.theme import (
    app_palette,
    apply_theme,
    set_muted,
    ui_font,
    warning_background,
    warning_color,
)

logger = logging.getLogger(__name__)

PANEL_W = 426
PAN_STEP = 48
IMAGE_CACHE_SIZE = 3
# Ventana de miniaturas en memoria. Cada icono de 400×247 ocupa ~395 KB; 160
# cubre de sobra un viewport 4K y limita la memoria a unos 63 MB.
THUMBNAIL_CACHE_SIZE = 160
# Populate por lotes (T2.4): por debajo del límite la inserción es la
# síncrona de siempre (demo, pruebas, bibliotecas pequeñas) y el
# comportamiento es idéntico al anterior; por encima, los ítems entran en
# lotes de este tamaño procesados con un QTimer de disparo cero para que el
# hilo de UI respire entre lotes.
POPULATE_SYNC_LIMIT = 1000
POPULATE_BATCH_SIZE = 500


@dataclass
class _PopulateState:
    """Sesión de populate por lotes (T2.4).

    El cálculo de orden se hace una vez antes del primer lote y el grupo
    de cada foto se calcula dentro de su lote, al insertarla (R9); los
    campos ``index``/``row``/``current_group`` conservan el avance
    entre lotes y ``headers`` guarda las cabeceras ya insertadas para
    poder desocultarlas cuando llegue su primera foto visible. La
    visibilidad de cada ítem se evalúa **en el momento de insertarlo**
    contra el filtro vigente (búsqueda ``/`` y ``:tag``), de modo que un
    cambio de filtro a medio populate también alcanza a los lotes
    siguientes; ``on_done`` es el trabajo post-populate que el llamador
    difirió.
    """

    ordered: list[Photo]
    index: int = 0
    row: int = 0
    current_group: str | None = None
    headers: dict[str, QListWidgetItem] = field(default_factory=dict)
    on_done: Callable[[], None] | None = None


class ContentArea(QWidget):
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class LocationBanner(QFrame):
    """Banner de ubicaciones no disponibles (ADR 0010, Decisión 6).

    Tarjeta de alerta que encabeza la zona de rejilla, solo cuando una
    ubicación activa no está a mano: icono de advertencia, titular corto y
    detalle (ruta y recuento de fotos degradadas) como texto secundario. La
    app es teclado primero: el aviso se cierra con ``Esc``, con la «✕» para
    quien use el ratón, y se autocierra a los 30 segundos; el panel de
    ubicaciones sigue a un ``:locations`` de distancia (el clic en la
    tarjeta hace lo mismo). Los textos son puros
    (``presentation.location_banner_text`` y
    ``presentation.location_banner_parts``).
    """

    opened = Signal()
    #: El aviso se descarta (Esc, «✕» o autocierre): la ventana rehace el
    #: layout y puede avisar en la barra de estado.
    dismissed = Signal()
    geometry_changed = Signal()

    _AUTOHIDE_MS = 30_000
    _PAD_V = 10
    _PAD_H = 14
    _ICON = 22
    _TEXT_GAP = 10
    _CLOSE_SIZE = 24

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAccessibleName("banner de ubicaciones no disponibles")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._title = ""
        self._detail = ""
        self._full_text = ""
        self._dismissed = False
        self._background = QColor()
        self._border = QColor()

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(self._ICON, self._ICON)
        self.icon_label.setFocusPolicy(Qt.NoFocus)
        # Decorativo: el estado lo nombra el titular, no el icono.
        self.icon_label.setAccessibleName("")
        warning = self.style().standardIcon(QStyle.SP_MessageBoxWarning)
        self.icon_label.setPixmap(warning.pixmap(self._ICON, self._ICON))

        self.title_label = QLabel(self)
        self.title_label.setFocusPolicy(Qt.NoFocus)
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.detail_label = QLabel(self)
        self.detail_label.setFont(ui_font(12))
        set_muted(self.detail_label)
        self.detail_label.setFocusPolicy(Qt.NoFocus)
        self.detail_label.setTextInteractionFlags(Qt.NoTextInteraction)

        self.close_button = QToolButton(self)
        self.close_button.setText("✕")
        self.close_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.close_button.setAutoRaise(True)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setFont(ui_font(12))
        self.close_button.setAccessibleName("cerrar el aviso")
        self.close_button.setToolTip("Cerrar el aviso (Esc)")
        self.close_button.clicked.connect(self.dismiss)

        # Autocierre: el aviso no es permanente; no le roba la rejilla.
        self._autohide = QTimer(self)
        self._autohide.setSingleShot(True)
        self._autohide.setInterval(self._AUTOHIDE_MS)
        self._autohide.timeout.connect(self.dismiss)

        self._apply_palette()
        self.hide()

    # --- Contenido ---------------------------------------------------------
    def set_contents(self, title: str, detail: str, full_text: str) -> None:
        """Texto del aviso y arranque del autocierre de 30 segundos."""
        self._title = title
        self._detail = detail
        self._full_text = full_text
        self.setToolTip(full_text)
        self.setAccessibleDescription(full_text)
        self._relayout()
        self._autohide.start()

    def text(self) -> str:
        """Texto completo con el formato del ADR (para pruebas y lectores)."""
        return self._full_text

    def is_dismissed(self) -> bool:
        return self._dismissed

    def dismiss(self) -> None:
        """Cierra el aviso definitivamente (Esc, «✕» o autocierre)."""
        if self._dismissed:
            return
        self._dismissed = True
        self._autohide.stop()
        self.hide()
        self.geometry_changed.emit()
        self.dismissed.emit()

    # --- Presentación ------------------------------------------------------
    def _apply_palette(self) -> None:
        warning = warning_color(self)
        self._background = warning_background(self)
        self._border = QColor(warning)
        self._border.setAlpha(150)
        self.title_label.setFont(ui_font(13, QFont.DemiBold))

    def retheme(self) -> None:
        self._apply_palette()
        set_muted(self.detail_label)
        self._relayout()
        self.update()

    def heightForWidth(self, width: int) -> int:
        title_h = QFontMetrics(self.title_label.font()).height()
        detail_h = QFontMetrics(self.detail_label.font()).height()
        return self._PAD_V * 2 + title_h + 2 + detail_h

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_children()

    def mousePressEvent(self, event) -> None:
        # El clic abre el panel de ubicaciones; la «✕» se queda su clic.
        self.opened.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._background)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

    def _relayout(self) -> None:
        height = self.heightForWidth(max(1, self.width()))
        if height != self.height() and self.isVisible():
            self.resize(self.width(), height)
        self._layout_children()

    def _layout_children(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        right = width - self._PAD_H
        close_rect = QRect(
            right - self._CLOSE_SIZE,
            (height - self._CLOSE_SIZE) // 2,
            self._CLOSE_SIZE,
            self._CLOSE_SIZE,
        )
        self.close_button.setGeometry(close_rect)
        icon_x = self._PAD_H + 2
        self.icon_label.setGeometry(
            icon_x, (height - self._ICON) // 2, self._ICON, self._ICON
        )
        text_x = icon_x + self._ICON + self._TEXT_GAP
        text_right = close_rect.left() - self._TEXT_GAP
        self.title_label.setFont(ui_font(13, QFont.DemiBold))
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title_label.setGeometry(
            text_x,
            self._PAD_V,
            max(1, text_right - text_x),
            QFontMetrics(self.title_label.font()).height(),
        )
        self.title_label.setText(self._title)
        self.title_label.show()
        detail_font_metrics = QFontMetrics(self.detail_label.font())
        self.detail_label.setGeometry(
            text_x,
            self._PAD_V + self.title_label.height() + 2,
            max(1, text_right - text_x),
            detail_font_metrics.height(),
        )
        self.detail_label.setText(
            detail_font_metrics.elidedText(
                self._detail, Qt.ElideMiddle, max(1, text_right - text_x)
            )
        )
        self.detail_label.show()


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
        tag_connection: sqlite3.Connection | None = None,
        tag_catalog: Sequence[Tag] = (),
        locations: Sequence[LocationStatus] = (),
    ) -> None:
        super().__init__()
        # True usa la rejilla como fila inferior del visor; False muestra el
        # visor a pantalla completa con la rejilla oculta. La app real y la
        # demo usan True.
        self.grid_row = grid_row
        # Conexión RW dedicada del visor para escribir favoritos en el
        # catálogo (ADR 0006, Decisión 10) y el vocabulario de etiquetas con
        # el que ``:tag`` resuelve nombres. Sin conexión (demo, pruebas) el
        # conmutado de ``f`` queda solo en memoria.
        self.tag_connection = tag_connection
        self.tag_catalog = tuple(tag_catalog)
        self._tag_filter: str | None = None
        self.photos = {photo.key: photo for photo in photos}
        # Índice de búsqueda precalculado (T1.5): el documento por foto se
        # construye una vez aquí y se revalida al cambiar la biblioteca
        # (``set_photos``) o las etiquetas de una foto (``_toggle_favorite``).
        # ``_apply_filter`` consulta el dict en vez de reconstruir 25k
        # documentos en cada pulsación.
        # El arranque progresivo construye la ventana vacía: ni siquiera
        # vale la pena construir un índice vacío en el hilo de UI (Fase B).
        self._search_index = build_search_index(photos) if photos else {}
        self._sort_key = DEFAULT_SORT
        self.thumbnails = dict(thumbnails or {})
        # ``search_text`` es solo la consulta confirmada con Enter; el filtro
        # efectivo (vivo o confirmado) vive en ``_filter_text`` (A3).
        self.search_text = ""
        self._filter_text = ""
        self._search_origin: str | None = None
        self.panel_visible = initial_panel_visible
        self.panel_overlay = False
        self._viewer_visible = False
        # Interruptor de sesión del overlay de etiquetas: ni config.toml ni
        # QSettings (ADR 0001). Sobrevive al cambio de foto y al cierre del
        # visor; se pierde al reiniciar la aplicación.
        self._tag_overlay_visible = False
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
        # Populate por lotes (T2.4): un QTimer de disparo cero reparte la
        # inserción de ítems y deja respirar el hilo de UI entre lotes. La
        # sesión en curso vive en ``_populate_state``; ``_populate_active``
        # la marca para que los llamadores difieran su trabajo post-populate.
        self._library_loading = False
        self._populate_active = False
        self._populate_state: _PopulateState | None = None
        # Fase B (B.4): instantáneo del filtro efectivo al abrir cada sesión
        # de populate y flag que marca si cambió mientras corría. Si no
        # cambió, ``_finish_populate`` se ahorra la pasada final completa.
        self._populate_filter_snapshot: tuple[str, str | None] = ("", None)
        self._filter_changed_during_populate = False
        # Arranque en dos fases (A.3): total de fotos presentes conocido desde
        # la fase 1, fotos de la fase 2 esperando a que acabe el populate en
        # curso y cierre diferido del estado de carga tras el append.
        self._total_count: int | None = None
        self._pending_append: list[Photo] = []
        # Fase B: la fase 2 preparada por el worker espera junto al append
        # diferido para no recalcular índice ni orden en el hilo de UI.
        self._pending_prepared: PreparedPhotos | None = None
        self._append_on_done: Callable[[], None] | None = None
        # El append de la fase 2 inserta al final, así que el orden de la
        # rejilla deja de ser el canónico hasta que se recompone (bugfix A.3):
        # se hace solo cuando el filtro ``fav`` se quita o se pide ``:sort``,
        # para no pagar el repopulado en el arranque ni mientras ``fav`` siga
        # activo, donde la vista es idéntica.
        self._order_needs_restore = False
        self._canonical_order_dirty = False
        self._closing = False
        self._populate_timer = QTimer(self)
        self._populate_timer.setSingleShot(True)
        self._populate_timer.setInterval(0)
        self._populate_timer.timeout.connect(self._populate_next_batch)

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

        # Fase B (B.5b): los overlays de ayuda y ubicaciones arrancan ocultos
        # y no se construyen hasta que el usuario los abre. Las propiedades
        # ``help_overlay``/``locations_overlay`` mantienen la API de siempre
        # y los crean bajo demanda con el tema y los datos vigentes.
        self._help_overlay: HelpOverlay | None = None
        self._locations_overlay: LocationsOverlay | None = None

        # Panel de ubicaciones (ADR 0010, Decisión 7): los datos salen del
        # catálogo vía la app real; en demo y pruebas, vacío. Se guardan aquí
        # para que el overlay, si llega a crearse, nazca ya con las filas
        # correctas sin pagar el coste durante el arranque.
        self._locations = tuple(locations)

        # Banner de ubicaciones no disponibles (T4.2): sus textos son puros
        # (presentation.location_banner_text y location_banner_parts) y solo
        # aparece si algo falta. No es permanente: se autocierra a los 30 s,
        # y Esc (o la «✕») lo cierra antes; ``:locations`` reabre el panel.
        self.banner = LocationBanner(self.content)
        self.banner.opened.connect(self._toggle_locations)
        self.banner.geometry_changed.connect(self._layout_content)
        self.banner.dismissed.connect(self._banner_dismissed)
        self._banner_text = location_banner_text(self._locations, photos)
        banner_parts = location_banner_parts(self._locations, photos)
        if self._banner_text and banner_parts is not None:
            self.banner.set_contents(*banner_parts, self._banner_text)
            self.banner.show()
        else:
            self.banner.hide()

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

        self._update_counts()
        if self._populate_grid(photos):
            # Populate por lotes (T2.4): la primera selección y los recuentos
            # esperan al último lote.
            self._populate_state.on_done = self._select_first_visible
        else:
            self._select_first_visible()
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

    @property
    def help_overlay(self) -> HelpOverlay:
        """Overlay de ayuda, creado la primera vez que se consulta (Fase B)."""
        if self._help_overlay is None:
            self._ensure_help_overlay()
        return self._help_overlay

    @property
    def locations_overlay(self) -> LocationsOverlay:
        """Panel de ubicaciones, creado la primera vez que se consulta."""
        if self._locations_overlay is None:
            self._ensure_locations_overlay()
        return self._locations_overlay

    def _ensure_help_overlay(self) -> None:
        """Construye el overlay de ayuda oculto, con el tema ya vigente."""
        if self._help_overlay is None:
            self._help_overlay = HelpOverlay(self)
            self._help_overlay.hide()

    def _ensure_locations_overlay(self) -> None:
        """Construye el panel de ubicaciones con los datos ya guardados."""
        if self._locations_overlay is None:
            self._locations_overlay = LocationsOverlay(self)
            self._locations_overlay.set_locations(location_rows(self._locations))
            self._locations_overlay.hide()

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
        # B.5b: un retema antes de abrir los overlays no debe construirlos;
        # si algún día existen, el creado después ya recibe el tema vigente.
        if self._help_overlay is not None:
            self._help_overlay.retheme()
        if self._locations_overlay is not None:
            self._locations_overlay.retheme()
        self.banner.retheme()
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

    def _add_group_header(
        self, group_key: str, label: str, *, hidden: bool = False
    ) -> QListWidgetItem:
        item = QListWidgetItem(label)
        item.setData(HEADER_ROLE, True)
        item.setData(GROUP_ROLE, group_key)
        item.setData(Qt.AccessibleTextRole, f"grupo {label}")
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(max(1, self.grid.viewport().width()), GROUP_HEADER_H))
        item.setHidden(hidden)
        self.grid.addItem(item)
        return item

    def _populate_grid(
        self, photos: list[Photo], *, prepared: PreparedPhotos | None = None
    ) -> bool:
        """Ordena una vez e inserta la rejilla: síncrona o por lotes (T2.4).

        Por debajo de ``POPULATE_SYNC_LIMIT`` (demo, pruebas y bibliotecas
        pequeñas) hace la inserción monolítica de siempre y los pases
        finales en la misma llamada, con el comportamiento intacto. Por
        encima, los ítems entran en lotes de ``POPULATE_BATCH_SIZE``
        procesados con un ``QTimer`` de disparo cero para que el hilo de
        UI respire: tras el primer lote la rejilla ya es visible y
        navegable y los siguientes se insertan en fondo. El orden se
        calcula una vez antes del primer lote y el grupo de cada foto
        dentro de su lote, al insertarla (R9): el primer lote navegable
        no espera al materializado de todos los grupos; cada ítem
        nace ya oculto o visible según el filtro vigente en el momento
        de insertarlo, de modo que búsqueda, favoritos y navegación
        funcionan a medio populate, y ``_grid_items`` y la caché de
        visibles se mantienen coherentes lote a lote.

        ``prepared`` (Fase B) trae el orden ya calculado por el worker; se
        usa tal cual durante el arranque. Con ``None`` (demo, tests y
        acciones del usuario) se ordena aquí como siempre.

        Devuelve ``True`` si el populate quedó aplazado en lotes: el
        llamador debe diferir su trabajo post-populate asignándolo a
        ``self._populate_state.on_done`` (``_finish_populate`` lo ejecuta
        al terminar el último lote).
        """
        self._cancel_populate()
        self._snapshot_populate_filter()
        self.grid.clear()
        self._grid_items.clear()
        # Una sola pasada de orden antes del primer lote; los grupos se
        # calculan por foto dentro de cada lote (R9). La visibilidad se
        # evalúa por ítem al insertarlo, contra el filtro vigente en ese
        # momento. Fase B: durante el arranque el orden ya viene del worker.
        if prepared is not None:
            ordered = prepared.ordered
        elif not photos:
            ordered = []
        else:
            ordered = sorted_photos(photos, self._sort_key)
        state = _PopulateState(ordered=ordered)
        self._populate_state = state
        self._populate_active = True
        if len(ordered) <= POPULATE_SYNC_LIMIT:
            self._populate_range(len(ordered))
            self._finish_populate()
            return False
        self._populate_timer.start()
        return True

    def append_photos(
        self, photos: list[Photo], *, prepared: PreparedPhotos | None = None
    ) -> None:
        """Funde la fase 2 en la rejilla sin repoblarla (A.3/A.4).

        Añade las fotos al final, reutilizando ``_populate_range`` con un
        ``_PopulateState`` en modo append: ``grid.clear()`` no se llama, las
        cachés de visibles y el índice de búsqueda se amplían —nunca se
        reconstruyen—, y cada ítem nace oculto o visible según el filtro
        vigente en el momento de insertarlo. La selección y el scroll se
        guardan antes y se restauran al terminar. Si el append supera
        ``POPULATE_SYNC_LIMIT`` sigue por lotes con ``_populate_timer`` y el
        llamador puede diferir su trabajo final en ``_append_on_done``,
        que ``_finish_populate`` ejecuta al terminar la última tanda.

        ``prepared`` (Fase B) trae el índice y el orden del resto ya
        calculados en el worker: el índice solo crece con él y el estado
        inicial del populate es su lista ordenada. Con ``None`` se calculan
        aquí, como antes.

        Nota de diseño: al insertar al final, el orden tras la fase 2 es
        «favoritas primero, resto después». Con ``:tag fav`` la vista es
        idéntica a la de la fase 1; ``:sort <clave>`` vuelve al orden
        canónico completo cuando se pide.
        """
        if not photos:
            return
        self._snapshot_populate_filter()
        current_item = self.grid.currentItem()
        current_key = (
            current_item.data(PHOTO_ROLE) if current_item is not None else None
        )
        scroll_value = self.grid.verticalScrollBar().value()

        self.photos.update({photo.key: photo for photo in photos})
        # El índice de búsqueda solo crece: reconstruirlo sobre la biblioteca
        # completa costaría lo que la fase 1 se ahorró. ``/`` encuentra una
        # foto no favorita nada más llegar la fase 2.
        self._search_index.update(
            prepared.search_index
            if prepared is not None
            else build_search_index(photos)
        )

        # Las cabeceras ya insertadas se reutilizan para no duplicar la del
        # grupo con el que continúa el append.
        headers: dict[str, QListWidgetItem] = {}
        current_group: str | None = None
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is None:
                continue
            if item.data(HEADER_ROLE):
                headers[str(item.data(GROUP_ROLE))] = item
            else:
                group = item.data(GROUP_ROLE)
                if group is not None:
                    current_group = str(group)

        state = _PopulateState(
            ordered=(
                prepared.ordered
                if prepared is not None
                else sorted_photos(photos, self._sort_key)
            ),
            index=0,
            row=self.grid.count(),
            current_group=current_group,
            headers=headers,
        )
        self._populate_state = state
        self._populate_active = True
        self._order_needs_restore = True

        def _restore_and_finalize() -> None:
            self._restore_after_populate(current_key)
            self.grid.verticalScrollBar().setValue(scroll_value)
            if self._append_on_done is not None:
                self._append_on_done()
            self._after_append_inserted()

        state.on_done = _restore_and_finalize

        if len(state.ordered) <= POPULATE_SYNC_LIMIT:
            self._populate_range(len(state.ordered))
            self._finish_populate()
            return
        self._populate_timer.start()

    def _after_append_inserted(self) -> None:
        """Marca el orden no canónico que deja la fase 2 (bugfix A.3).

        Con ``fav`` activo la vista es idéntica, así que solo se apunta el
        trabajo pendiente; si el filtro ya se quitó (o cambió) mientras el
        append corría, se recompone ahora mismo.
        """
        self._order_needs_restore = False
        self._canonical_order_dirty = True
        if self._tag_filter != FAVORITE_TAG:
            self._restore_canonical_order()

    def _restore_canonical_order(self) -> None:
        """Reordena la rejilla por ``_sort_key`` sin perder selección ni scroll.

        El append de la fase 2 inserta al final para no repoblar ni vaciar la
        rejilla; eso deja las favoritas delante del resto. Al quitar ``fav``
        (o al ordenar explícitamente) hay que reconstruir el orden canónico
        sobre ``self.photos``, que ya contiene la biblioteca completa. Se
        reutiliza el populate por lotes, con las señales bloqueadas y el
        ``on_done`` restaurando la selección y el scroll que había antes.
        """
        if not (self._canonical_order_dirty or self._order_needs_restore):
            return
        self._canonical_order_dirty = False
        self._order_needs_restore = False
        # Si la fase 2 aún estaba en curso, su append queda sustituido por
        # esta recomposición (que ya incluye todas las fotos en memoria).
        self._pending_append = []
        self._pending_prepared = None
        finalizar = self._append_on_done
        self._append_on_done = None
        item = self.grid.currentItem()
        current_key = item.data(PHOTO_ROLE) if item is not None else None
        scroll_value = self.grid.verticalScrollBar().value()

        def _after() -> None:
            self._restore_after_populate(current_key)
            self.grid.verticalScrollBar().setValue(scroll_value)
            if finalizar is not None:
                finalizar()

        self.grid.blockSignals(True)
        try:
            self._populate_grid(list(self.photos.values()))
            if self._populate_active:
                self._populate_state.on_done = _after
            else:
                _after()
        finally:
            self.grid.blockSignals(False)

    def _populate_range(self, count: int) -> None:
        """Inserta los siguientes ``count`` ítems de la sesión en curso (T2.4).

        Cada lote deja ``_grid_items`` y la caché de visibles al día (y a la
        rejilla, con ``append_visible_rows``), de modo que búsqueda,
        favoritos y navegación son coherentes también a medio populate. Las
        filas visibles de este lote se registran incrementalmente: la
        pasada única del filtro normaliza el resto al terminar, en
        ``_finish_populate``.
        """
        state = self._populate_state
        if state is None:
            return
        end = min(state.index + count, len(state.ordered))
        # El filtro efectivo se lee al empezar el lote: solo puede cambiar
        # entre lotes (el bucle de eventos no corre en medio de este bucle).
        # ``_filter_text`` incluye la búsqueda viva escrita entre lotes (A3).
        needle = self._filter_text.strip().casefold()
        tag_filter = self._tag_filter
        rows: list[int] = []
        keys: list[str] = []
        while state.index < end:
            index = state.index
            photo = state.ordered[index]
            # El grupo de cada foto se calcula una vez, al insertarla
            # (R9): ``_sort_key`` no puede cambiar en mitad de un
            # populate (``_sort_grid`` cancela y repuebla).
            group_key, group_label = photo_group(photo, self._sort_key)
            visible = self._matches_filter(photo, needle, tag_filter)
            if group_key != state.current_group:
                header = self._add_group_header(group_key, group_label, hidden=True)
                state.headers[group_key] = header
                state.current_group = group_key
                state.row += 1
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
            item.setData(UNAVAILABLE_ROLE, not photo.available)
            item.setData(Qt.AccessibleTextRole, photo_accessible_name(photo))
            item.setSizeHint(QSize(ITEM_W, ITEM_H))
            item.setText("")
            item.setHidden(not visible)
            self.grid.addItem(item)
            self._grid_items[photo.key] = item
            state.row += 1
            if visible:
                # La cabecera del grupo nace oculta y se desoculta con su
                # primera foto visible; una pasada de filtro a medio
                # populate puede haberla vuelto a ocultar si aún no tenía
                # ninguna (el pase final normaliza el resto).
                header = state.headers.get(group_key)
                if header is not None and header.isHidden():
                    header.setHidden(False)
                rows.append(state.row - 1)
                keys.append(photo.key)
            state.index += 1
        if rows:
            self.grid.append_visible_rows(rows)
            base = len(self._visible_keys_cache)
            self._visible_keys_cache.extend(keys)
            for offset, key in enumerate(keys):
                self._visible_positions_by_key[key] = base + offset

    def _populate_next_batch(self) -> None:
        """Lote del populate progresivo (T2.4)."""
        if self._closing:
            return
        state = self._populate_state
        if state is None or not self._populate_active:
            return
        self._populate_range(POPULATE_BATCH_SIZE)
        self.grid.update_header_widths()
        self._update_empty_state()
        # Recuentos al día lote a lote: la barra refleja lo visible durante
        # el populate (T2.4) y, en la fase 2, el total de la fase 1 (A.3).
        self._update_counts()
        if state.index >= len(state.ordered):
            self._finish_populate()
            return
        # Los ítems del viewport empiezan a pedir miniatura también a medio
        # populate (T2.5): el debounce existente agrupa las peticiones y solo
        # se piden los ítems ya insertados, que son los únicos que existen.
        self._schedule_visible_thumbnails()
        self._populate_timer.start()

    def _finish_populate(self) -> None:
        """Pases finales del populate, síncrono o por lotes (T2.4, A.3)."""
        state = self._populate_state
        self._populate_timer.stop()
        self._populate_state = None
        self._populate_active = False
        # Con la rejilla completa ya no hay nada que cargar: el estado de
        # carga del arranque progresivo acaba aquí (T2.2).
        self._library_loading = False
        self.grid.update_header_widths()
        # Normalización final del filtro (T2.4): cada ítem nace con la
        # visibilidad del filtro de su lote. B.4: si el filtro no cambió
        # mientras corría la sesión, los lotes ya dejaron cabeceras, caché de
        # visibles y filas coherentes, así que solo se actualizan recuentos y
        # estado vacío; si cambió, se ejecuta la pasada íntegra de siempre.
        # Se usa el filtro efectivo para no descartar la búsqueda viva (A3).
        if self._filter_changed_during_populate:
            self._apply_filter(self._filter_text)
        else:
            self._update_counts()
        self._filter_changed_during_populate = False
        self._schedule_visible_thumbnails()
        self._update_empty_state()
        if state is not None and state.on_done is not None:
            state.on_done()
        # A.3: si la fase 2 llegó mientras aún corría el populate de la fase 1,
        # se aplica aquí —después del ``on_done`` que restauró la selección—,
        # sin solapar dos sesiones y sin perder scroll ni filtro. El cierre
        # del estado de carga se difiere al final de este append si vuelve a
        # quedar populate por lotes.
        pending = self._pending_append
        if pending:
            self._pending_append = []
            prepared = self._pending_prepared
            self._pending_prepared = None
            self.append_photos(pending, prepared=prepared)
            if self._populate_active:
                self._library_loading = True

    def _cancel_populate(self) -> None:
        """Cancela cualquier populate por lotes en curso (T2.4, T2.6).

        El ``on_done`` de la sesión cancelada no llega a ejecutarse: la
        nueva sesión lo sustituye (o el cierre de la ventana lo hace
        innecesario).
        """
        self._populate_timer.stop()
        self._populate_state = None
        self._populate_active = False
        self._visible_keys_cache = []
        self._visible_positions_by_key = {}
        self.grid.set_visible_rows([])

    def _snapshot_populate_filter(self) -> None:
        """Toma el filtro efectivo vigente al abrir una sesión de populate."""
        self._populate_filter_snapshot = (
            self._filter_text.strip().casefold(),
            self._tag_filter,
        )
        self._filter_changed_during_populate = False

    def _note_filter_change(self, text: str | None = None) -> None:
        """Marca si el filtro cambió mientras hay un populate en curso.

        ``text`` permite a ``_apply_filter`` comprobar la consulta entrante
        antes de asignarla. Fuera de una sesión el flag no se toca: no hay
        pasada final que omitir.
        """
        if not self._populate_active:
            return
        needle = (self._filter_text if text is None else text).strip().casefold()
        if (needle, self._tag_filter) != self._populate_filter_snapshot:
            self._filter_changed_during_populate = True

    def _matches_filter(
        self, photo: Photo, needle: str, tag_filter: str | None
    ) -> bool:
        """Visibilidad de una foto con el filtro vigente.

        La búsqueda ``/`` y el filtro ``:tag`` se componen en AND: la foto
        debe cumplir ambos. El documento sale del índice precalculado
        (T1.5), no de ``photo_search_text`` por foto. La consulta única de
        ``_apply_filter`` y la precomputación del populate por lotes (T2.4)
        comparten esta regla para que ambas sean idénticas.
        """
        return (
            not needle or needle in self._search_index.get(photo.key, "")
        ) and (tag_filter is None or photo_matches_tag_name(photo, tag_filter))

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

    def set_library_loading(self, loading: bool) -> None:
        """Estado de carga del catálogo (T2.2, arranque progresivo).

        Mientras no hay nada en la rejilla, el estado vacío muestra el
        texto discreto «Cargando el catálogo…» (sin spinner ni animación,
        conforme a docs/diseno-ui.md). En cuanto el primer lote deja ítems
        visibles, el texto se retira y la rejilla manda.
        """
        self._library_loading = loading
        self._update_empty_state()

    def _refresh_location_banner(self, photos: list[Photo]) -> None:
        """Recompone el banner de ubicaciones ausentes (ADR 0010, Decisión 6).

        Usa las mismas funciones puras que ``set_library``. Tras la fase 2
        hay que repetirlo con la biblioteca completa: con solo las favoritas
        de la fase 1 el recuento de «fotos no visibles» saldría corto.
        """
        self._banner_text = location_banner_text(self._locations, photos)
        banner_parts = location_banner_parts(self._locations, photos)
        if self._banner_text and banner_parts is not None:
            self.banner.set_contents(*banner_parts, self._banner_text)
            self.banner.show()
        else:
            self.banner.hide()

    def set_library(
        self,
        photos: list[Photo],
        *,
        tags: Sequence[Tag] = (),
        locations: Sequence[LocationStatus] = (),
        initial_tag_filter: str | None = None,
        total_count: int | None = None,
        prepared: PreparedPhotos | None = None,
    ) -> None:
        """Sustituye la biblioteca cargada por el worker (T2.3, ADR 0011).

        Recibe de una vez el vocabulario de etiquetas y el estado de
        ubicaciones del catálogo, recompone el banner de ubicaciones no
        disponibles (ADR 0010, Decisión 6) con las mismas funciones puras
        de presentación y el panel de ubicaciones, y delega el populate en
        ``set_photos``. Es la vía del arranque progresivo: durante la
        carga la ventana solo tuvo el estado de carga (T2.2).

        ``initial_tag_filter`` activa un filtro ``:tag`` antes de poblar:
        la aplicación real abre con ``fav`` (historia de usuario) y la
        rejilla nace con las no favoritas ya ocultas, así que la carga es
        más ligera. La llamada decide si la etiqueta es válida; aquí entra
        tal cual, como el filtro guardado de ``:tag``. ``prepared`` (Fase B)
        se reenvía a ``set_photos`` para que la UI no recalcule índice ni
        orden durante el arranque.
        """
        self.tag_catalog = tuple(tags)
        # Fase B: la fase 1 solo actualiza los datos y el banner; el panel
        # de ubicaciones se construye bajo demanda con estos mismos datos.
        self._locations = tuple(locations)
        self._refresh_location_banner(photos)
        self._layout_content()
        self.set_photos(
            photos,
            tag_filter=initial_tag_filter,
            total_count=total_count,
            prepared=prepared,
        )

    def set_photos(
        self,
        photos: list[Photo],
        thumbnails: dict[str, QIcon] | None = None,
        tag_filter: str | None = None,
        *,
        total_count: int | None = None,
        prepared: PreparedPhotos | None = None,
    ) -> None:
        """Sustituye la biblioteca visible conservando la foto actual si sigue.

        ``tag_filter`` fija el filtro ``:tag`` que rige el populate (por
        defecto, ``None``: sin filtro de etiqueta, como siempre). Debe
        entrar aquí y no después: cada ítem nace ya oculto o visible según
        el filtro vigente al insertarlo (T2.4), de modo que un arranque con
        filtro no paga la rejilla completa. Sin argumento el filtro queda
        retirado, igual que antes.

        ``total_count`` (A.3) es el total de fotos presentes del catálogo,
        conocido en la fase 1 aunque entonces solo existan las favoritas en
        memoria. La barra muestra «4 de 29567» desde el primer lote. Con
        ``None`` se limpia y se vuelve a ``len(photos)`` (demo y tests).

        ``prepared`` (Fase B) sustituye el índice y el orden calculados en la
        UI por los que ya entregó el worker. Con ``None`` se mantienen
        ``build_search_index`` y ``sorted_photos`` aquí, igual que antes; el
        resto de ``set_photos`` (cachés de imágenes, pinned, selección…) no
        cambia.
        """
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
        # La biblioteca cambió: el índice de búsqueda se reconstruye (T1.5),
        # salvo que el worker ya lo haya entregado preparado (Fase B).
        self._search_index = (
            prepared.search_index
            if prepared is not None
            else build_search_index(photos)
        )
        if thumbnails is not None:
            self.thumbnails = dict(thumbnails)
            self._thumbnail_order = list(self.thumbnails)
        self.search_text = ""
        self._filter_text = ""
        self._search_origin = None
        self._tag_filter = tag_filter
        self._total_count = total_count
        self.status.set_pinned(f"etiqueta: {tag_filter}" if tag_filter else "")
        self.grid.blockSignals(True)
        try:
            self._populate_grid(photos, prepared=prepared)
            if self._populate_active:
                # Populate por lotes (T2.4): la restauración de la selección
                # y el resto del trabajo post-populate esperan al último
                # lote (los ejecuta ``_finish_populate`` vía ``on_done``).
                self._populate_state.on_done = (
                    lambda: self._after_set_photos(current_key, viewer_was_visible)
                )
            else:
                self.grid.setCurrentRow(-1)
                self._restore_after_populate(current_key)
        finally:
            self.grid.blockSignals(False)
        if not self._populate_active:
            self._after_set_photos_tail(viewer_was_visible)

    def _select_first_visible(self) -> None:
        """Primera foto visible con cursor y recuentos al día (T2.4).

        Sirve tanto al populate síncrono inicial (comportamiento de siempre)
        como al por lotes, donde la ejecuta ``_finish_populate`` al terminar.
        """
        if self.photos and self._visible_keys_cache:
            first = self._grid_items.get(self._visible_keys_cache[0])
            if first is not None:
                self.grid.setCurrentItem(first)
        self._update_counts()

    def _restore_after_populate(self, current_key: str | None) -> None:
        """Restaura la foto actual tras un populate, conservándola si sigue."""
        target = self._grid_items.get(current_key) if current_key else None
        if target is None and self._visible_keys_cache:
            target = self._grid_items.get(self._visible_keys_cache[0])
        if target is not None:
            self.grid.setCurrentItem(target)
            self.grid.scrollToItem(target, QAbstractItemView.EnsureVisible)
        elif self.panel_visible:
            # El panel oculto no se reconstruye (R4): al abrirlo,
            # ``set_photo`` deja el estado correcto.
            self.panel.show_empty("Sin selección")

    def _after_set_photos(
        self, current_key: str | None, viewer_was_visible: bool
    ) -> None:
        """Selección y cola de ``set_photos`` al terminar el populate (T2.4)."""
        self._restore_after_populate(current_key)
        self._after_set_photos_tail(viewer_was_visible)

    def _after_set_photos_tail(self, viewer_was_visible: bool) -> None:
        """Trabajo post-populate de ``set_photos`` (idéntico al de siempre)."""
        self.status.clear_error()
        self._update_counts()
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

        # El banner de ubicaciones ausentes encabeza la zona de rejilla como
        # una tarjeta con margen: su altura se descuenta de la rejilla y del
        # visor, nunca del panel. Se decide por estado (_banner_text y si no
        # está descartado), no por ``isVisible()``: la visibilidad efectiva
        # de un hijo es falsa hasta que la ventana se muestra, y la pasada de
        # layout del ``show()`` —la única con el tamaño real— dejaría el
        # aviso sin componer.
        top = 0
        if self._banner_text and not self.banner.is_dismissed():
            banner_w = max(1, content_w - 2 * GRID_MARGIN)
            banner_h = max(28, self.banner.heightForWidth(banner_w))
            self.banner.setGeometry(GRID_MARGIN, 4, banner_w, banner_h)
            self.banner.raise_()
            top = banner_h + 8

        if self._viewer_visible:
            if self.grid_row:
                # Panel de foto, encima la zona de rejilla recortada a un
                # título de grupo y una única fila de celdas. Es la misma
                # rejilla de siempre: solo cambia su dimensión.
                header_h = min(GROUP_HEADER_H, max(0, height - top))
                row_h = min(ITEM_H, max(0, height - top - header_h))
                viewer_h = max(0, height - top - header_h - row_h)
                self.viewer.setGeometry(0, top, max(0, content_w), viewer_h)
                self.viewer.show()
                self.viewer.raise_()
                self.grid.show()
                self.grid.setGeometry(
                    0, top + viewer_h + header_h, max(0, content_w), row_h
                )
                self.grid.raise_()
                self.row_header.setGeometry(
                    0, top + viewer_h, max(0, content_w), header_h
                )
                self.row_header.raise_()
            else:
                self.grid.hide()
                self.viewer.setGeometry(
                    0, top, max(0, content_w), max(0, height - top)
                )
                self.viewer.show()
                self.viewer.raise_()
        else:
            self.viewer.hide()
            self.grid.show()
            self.grid.setGeometry(
                GRID_MARGIN,
                GRID_MARGIN + top,
                max(0, content_w - 2 * GRID_MARGIN),
                max(0, height - top - 2 * GRID_MARGIN),
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
        # Consultas que no deben construir un overlay que aún no existe.
        if self._help_overlay is not None and self._help_overlay.isVisible():
            self._center_overlay(self._help_overlay, max_width=960, max_height=820)
        if self._locations_overlay is not None and self._locations_overlay.isVisible():
            self._center_overlay(self._locations_overlay)

    def _center_overlay(
        self, overlay: QWidget, *, max_width: int = 760, max_height: int = 760
    ) -> None:
        """Centra un overlay informativo (ayuda, ubicaciones) sobre el contenido.

        La ayuda usa ``max_width=960`` y ``max_height=820`` (§ 2.4 del plan de
        ayuda); el panel de ubicaciones conserva su máximo de 760. Los
        márgenes de 64 px y los mínimos 320×260 son comunes.
        """
        content_rect = self.content.geometry()
        width = min(max_width, max(320, content_rect.width() - 64))
        height = min(max_height, max(260, content_rect.height() - 64))
        x = content_rect.x() + (content_rect.width() - width) // 2
        y = content_rect.y() + (content_rect.height() - height) // 2
        overlay.setGeometry(x, y, width, height)

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
            # El panel oculto no se refresca en cada selección (R4): al abrirlo
            # por Tab muestra la foto actual, igual que ``toggle_panel``.
            self.panel.set_photo(self._current_photo())
        self.panel.focus_scroll()

    def _current_photo(self) -> Photo | None:
        item = self.grid.currentItem()
        if item is None:
            return None
        return self.photos.get(item.data(PHOTO_ROLE))

    def _grid_item(self, key: str) -> QListWidgetItem | None:
        return self._grid_items.get(key)

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
        if self._closing or self._thumbnail_loader is None:
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
        if self._closing or self._thumbnail_loader is None:
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
        if self._closing:
            return
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
        if not self._open_if_available(photo):
            return
        self._viewer_visible = True
        if self.grid_row:
            self._set_viewer_keys(True)
        self._layout_content()
        self._show_viewer_photo(photo)
        self._focus_current_view()
        self.status.show_message("visor · h/j/k/l mover · q volver", 2500)

    def _open_if_available(self, photo: Photo) -> bool:
        """Guardia T4.3: las acciones que exigen el fichero no abren nada.

        Las fotos fantasma (ubicación desmontada) se muestran con sus datos
        del catálogo, pero ampliarlas a tamaño real exige el fichero: mensaje
        claro en la barra y sin visor.
        """
        if photo.available:
            return True
        where = (
            f" · ubicación «{photo.canonical_location}»"
            if photo.canonical_location
            else ""
        )
        self.status.show_message(f"el fichero no está disponible{where}", 3500)
        return False

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
        if self.panel_visible:
            # El panel oculto no se reconstruye en cada selección (R4): al
            # abrirlo, ``toggle_panel`` y ``_focus_panel`` lo rellenan con la
            # foto actual.
            self.panel.set_photo(photo)
        self._update_counts()
        if self._viewer_visible:
            if photo is None:
                self._close_viewer()
            elif not photo.available:
                # El cursor cayó en una foto fantasma: el visor exige el
                # fichero (T4.3); se vuelve a la rejilla con el mensaje claro.
                self._close_viewer()
                self._open_if_available(photo)
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

    def _sync_tag_regions(self, photo: Photo) -> None:
        """Entrega al lienzo las regiones visibles de la foto actual."""
        self.viewer.canvas.set_tag_regions(visible_tag_regions(photo))

    def _show_viewer_photo(self, photo: Photo) -> None:
        if not self._viewer_visible:
            return
        # Antes del retorno anticipado: la misma foto con imagen ya cargada
        # también debe llevar sus regiones al lienzo.
        self._sync_tag_regions(photo)
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
        self.viewer.canvas.set_favorite(photo.favorite)
        self._displayed_key = photo.key
        self._pending_image_key = photo.key
        self._ensure_image_loaded(photo)

    def _ensure_image_loaded(self, photo: Photo) -> None:
        if photo.path is None or not photo.available:
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

    def _update_counts(self) -> None:
        """Recuentos de la barra, con el total conocido de la fase 1 (A.3).

        Todas las puntadas que actualizan la barra pasan por aquí: con
        ``_total_count`` (el ``catalog.count_present_photos()`` que llega con
        la fase 1) el total es la biblioteca completa aunque solo existan las
        favoritas en memoria; sin él —demo y tests actuales— se mantiene
        ``len(self.photos)``, es decir, el comportamiento de siempre.
        """
        total = self._total_count if self._total_count is not None else len(self.photos)
        photo = self._current_photo()
        self.status.set_counts(
            self._visible_count(), total, photo.name if photo else ""
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
        if self._library_loading:
            # Estado de carga del arranque progresivo (T2.2): texto
            # discreto en la rejilla mientras no hay nada que mostrar.
            # Con el primer lote visible la rejilla manda y el texto cesa.
            if self._visible_count() == 0:
                self.empty_label.setText("Cargando el catálogo…")
                self.empty_label.show()
                self._position_empty_label()
                self.empty_label.raise_()
                if self.panel_visible:
                    self.panel.raise_()
                if self.content_blocker.isVisible():
                    self.content_blocker.raise_()
            else:
                self.empty_label.hide()
            return
        if self._visible_count() == 0:
            if self.status.prompt_mode == "search":
                self.empty_label.setText("Sin coincidencias\nEsc cancela la búsqueda")
            elif self._tag_filter:
                self.empty_label.setText(
                    "Sin coincidencias de etiqueta\n:tag sin nombre quita el filtro"
                )
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
        """Filtra la rejilla en una sola pasada (R2).

        El texto recibido es la fuente de esta pasada y queda registrado
        como filtro efectivo para los caminos que no son el filtro
        inmediato (populate, sort y filtro de etiqueta; A3). El único
        recorrido de la rejilla calcula a la vez la visibilidad de cada
        foto, las filas/claves/posiciones visibles, los grupos con fotos
        visibles y la primera foto visible para la selección de respaldo;
        ``setHidden`` solo se llama cuando el valor cambia.
        """
        self._note_filter_change(text)
        self._filter_text = text
        needle = text.strip().casefold()
        tag_filter = self._tag_filter
        first_visible: QListWidgetItem | None = None
        rows: list[int] = []
        keys: list[str] = []
        positions: dict[str, int] = {}
        visible_groups: set[str] = set()
        headers: list[QListWidgetItem] = []

        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is None:
                continue
            if item.data(HEADER_ROLE):
                headers.append(item)
                continue
            photo = self.photos.get(item.data(PHOTO_ROLE))
            if photo is None:
                visible = True
            else:
                # La búsqueda ``/`` y el filtro ``:tag`` se componen en AND:
                # la foto debe cumplir ambos. El documento sale del índice
                # precalculado (T1.5), no de ``photo_search_text`` por foto;
                # la regla es la misma que aplica el populate por lotes
                # (T2.4) al crear cada ítem.
                visible = self._matches_filter(photo, needle, tag_filter)
            hidden = not visible
            if item.isHidden() != hidden:
                item.setHidden(hidden)
            if not visible:
                continue
            if first_visible is None:
                first_visible = item
            group_key = str(item.data(GROUP_ROLE) or "")
            visible_groups.add(group_key)
            key = item.data(PHOTO_ROLE)
            if key:
                positions[str(key)] = len(keys)
                keys.append(str(key))
                rows.append(row)

        for header in headers:
            hidden = str(header.data(GROUP_ROLE) or "") not in visible_groups
            if header.isHidden() != hidden:
                header.setHidden(hidden)

        self.grid.set_visible_rows(rows)
        self._visible_keys_cache = keys
        self._visible_positions_by_key = positions

        current = self.grid.currentItem()
        if current is not None and (current.data(HEADER_ROLE) or current.isHidden()):
            current = None
        if current is None:
            if first_visible is not None:
                self.grid.setCurrentItem(first_visible)
            else:
                self.grid.setCurrentRow(-1)
                if self.panel_visible:
                    # El panel oculto no se reconstruye (R4): al abrirlo,
                    # ``set_photo`` deja el estado correcto.
                    self.panel.show_empty("Sin coincidencias")

        if self._viewer_visible and self._visible_count() == 0:
            self._close_viewer()

        self._update_counts()
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
            # Sin contexto de vista: el prompt era el contexto y al cerrarse
            # el foco vuelve a la rejilla. La ayuda abre al inicio (Globales).
            self._toggle_help(contextual=False)
            return
        if command == "locations":
            # Estado de las ubicaciones de la fototeca (ADR 0010, Decisión 7).
            self._toggle_locations()
            return
        if command == "sort" or command.startswith("sort "):
            key = command[4:].strip()
            if key in SORT_KEYS:
                self._sort_grid(key)
                return
            self.status.show_error("uso: :sort date | camera | location")
            return
        if command == "tag" or command.startswith("tag "):
            self._handle_tag_command(command[3:].strip())
            return
        self.status.show_error(
            f"comando desconocido: {command} · :help para ver los comandos"
        )

    def _handle_tag_command(self, argument: str) -> None:
        """``:tag <nombre>`` filtra la rejilla por etiqueta (ADR 0006).

        Sin argumento —o repitiendo la etiqueta ya activa— desactiva el
        filtro; un nombre fuera del vocabulario del catálogo es un error y
        no crea etiquetas. Taxonomía plana: no hay subárboles que expandir.
        """
        if not argument:
            if self._tag_filter is None:
                self.status.show_error("uso: :tag <nombre>")
                return
            self._deactivate_tag_filter()
            return
        if self._tag_filter is not None:
            try:
                repeat = normalize_tag_name(argument) == self._tag_filter
            except TagError:
                repeat = False
            if repeat:
                self._deactivate_tag_filter()
                return
        if self.tag_catalog:
            tag = find_tag_in_catalog(argument, self.tag_catalog)
            if tag is None:
                self.status.show_error(f"etiqueta desconocida: {argument}")
                return
            name = normalize_tag_name(tag.name)
        else:
            # Sin vocabulario (demo y pruebas): cualquier nombre normalizado.
            try:
                name = normalize_tag_name(argument)
            except TagError as exc:
                self.status.show_error(str(exc))
                return
        self._set_tag_filter(name)

    def _deactivate_tag_filter(self) -> None:
        self._set_tag_filter(None)
        self.status.show_message("filtro de etiqueta desactivado", 2000)

    def _set_tag_filter(self, name: str | None) -> None:
        """Aplica o retira el filtro de etiqueta y fija el indicador.

        El filtro activo permanece en la barra de estado («etiqueta:
        nombre») mientras dure: los mensajes temporales lo tapan y al
        expirar vuelve a verse.
        """
        self._tag_filter = name
        self._note_filter_change()
        # Un intento previo fallido (p. ej. «uso: :tag <nombre>») deja de
        # ser relevante: el filtro aplicado o retirado es la respuesta.
        self.status.clear_error()
        self.status.set_pinned(f"etiqueta: {name}" if name else "")
        # Bugfix A.3: al retirar/cambiar ``fav`` la rejilla debe pasar al
        # orden canónico; con ``fav`` activo la vista ya coincide.
        if name != FAVORITE_TAG:
            self._restore_canonical_order()
        self._apply_filter(self._filter_text)

    def _sort_grid(self, key: str) -> None:
        # Repetir la misma clave no repuebla: la rejilla ya está en ese
        # orden (R10). Salvo que la fase 2 haya dejado el orden no canónico:
        # entonces ``:sort date`` basta por sí solo, sin el doble salto
        # por otra clave que hacía falta antes (bugfix A.3).
        if key == self._sort_key and not (
            self._canonical_order_dirty or self._order_needs_restore
        ):
            self.status.show_message(f"ordenado por {key}", 3000)
            return
        self._canonical_order_dirty = False
        self._order_needs_restore = False

        current = self.grid.currentItem()
        current_key = current.data(PHOTO_ROLE) if current else None
        if current is not None and current.data(HEADER_ROLE):
            current_key = None

        def _after_sort() -> None:
            self.grid.setCurrentRow(-1)
            target = self._grid_items.get(current_key) if current_key else None
            if target is not None:
                self.grid.setCurrentItem(target)
            self._apply_filter(self._filter_text)
            self._on_current_changed(self.grid.currentItem(), None)
            self.status.show_message(f"ordenado por {key}", 3000)

        self._sort_key = key
        self.grid.blockSignals(True)
        try:
            self._populate_grid(list(self.photos.values()))
            if self._populate_active:
                # Populate por lotes (T2.4): el filtro y la restauración del
                # cursor esperan al último lote.
                self._populate_state.on_done = _after_sort
            else:
                _after_sort()
        finally:
            self.grid.blockSignals(False)

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
        registry["toggle_tag_overlay"] = self._toggle_tag_overlay
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
        if self._help_overlay is not None and self._help_overlay.isVisible():
            if action in ("escape", "help", "close"):
                self._toggle_help()
            return
        if self._locations_overlay is not None and self._locations_overlay.isVisible():
            if action in ("escape", "close"):
                self._toggle_locations()
                return
            if action != "help":
                return
            # ``?`` cierra ubicaciones y sigue al handler de ayuda (A4).
            self._toggle_locations()
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
        if self._help_overlay is not None and self._help_overlay.isVisible():
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
        if self._banner_text and not self.banner.is_dismissed():
            self.banner.dismiss()
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
            # El panel oculto no se refresca en cada selección (A1): al
            # abrirlo se rellena con la foto actual, no con la de antes.
            self.panel.set_photo(self._current_photo())
            self.status.show_message("panel de metadatos", 2000)
        else:
            if had_focus:
                self._focus_current_view()
            self.status.show_message("panel oculto", 2000)

    def _toggle_help(self, contextual: bool = True) -> None:
        # B.5b: consultar el backing evita construir el overlay solo para
        # preguntar si está visible; los caminos que sí lo abren usan la
        # propiedad, que lo crea en ese momento.
        if self._help_overlay is not None and self._help_overlay.isVisible():
            self._help_overlay.hide()
            self._hide_content_blocker()
            self.status.set_help_mode(False)
            self._focus_current_view()
        else:
            # Un solo overlay abierto a la vez (docs/diseno-ui.md).
            if (
                self._locations_overlay is not None
                and self._locations_overlay.isVisible()
            ):
                self._locations_overlay.hide()
            # La sección se deduce antes de mover el foco al overlay: es el
            # foco y la vista del momento los que deciden (§ 2.1). ``:help``
            # pide contextual=False: viene de la línea de comandos y, al
            # cerrarse el prompt, Qt devuelve el foco a la rejilla —un foco
            # heredado, no un contexto de vista—, así que abre al inicio.
            section = (
                help_section_for_context(
                    viewer_visible=self._viewer_visible,
                    panel_focused=self.panel.scroll.hasFocus(),
                    grid_focused=self.grid.hasFocus(),
                )
                if contextual
                else None
            )
            self._center_overlay(self.help_overlay, max_width=960, max_height=820)
            self._show_content_blocker()
            self.help_overlay.show()
            self.help_overlay.raise_()
            self.help_overlay.setFocus()
            self.help_overlay.show_section(section)
            self.status.set_help_mode(True, sections=len(self.help_overlay.sections))

    def _banner_dismissed(self) -> None:
        """El aviso se cerró (Esc, «✕» o autocierre de 30 s)."""
        self.status.show_message(
            "aviso de ubicaciones cerrado · :locations lo reabre", 3000
        )

    def _toggle_locations(self) -> None:
        """Panel de ubicaciones: clic en el banner o comando ``:locations``."""
        # B.5b: mismo criterio que en la ayuda: solo se crea al abrirlo.
        if self._locations_overlay is not None and self._locations_overlay.isVisible():
            self._locations_overlay.hide()
            self._hide_content_blocker()
            self._focus_current_view()
        else:
            if self._help_overlay is not None and self._help_overlay.isVisible():
                self._toggle_help()
            self._center_overlay(self.locations_overlay)
            self._show_content_blocker()
            self.locations_overlay.show()
            self.locations_overlay.raise_()
            self.locations_overlay.setFocus()

    def _apply_photo_update(self, photo: Photo) -> None:
        """Aplica una ``Photo`` a todas sus proyecciones (A1).

        Única ruta para que la foto de ``photos``, el índice de búsqueda, el
        ítem de la rejilla, el panel visible, el lienzo del visor y el
        filtro activo cuenten lo mismo. La usan el cambio optimista, la
        corrección al estado persistido y el rollback.
        """
        self.photos[photo.key] = photo
        self._search_index[photo.key] = photo_search_text(photo)

        item = self._grid_items.get(photo.key)
        if item is not None:
            item.setData(FAVORITE_ROLE, photo.favorite)
            item.setData(Qt.AccessibleTextRole, photo_accessible_name(photo))

        if self.panel_visible:
            self.panel.set_photo(photo)
        current = self._current_photo()
        if self._viewer_visible and current is not None and current.key == photo.key:
            self.viewer.canvas.set_favorite(photo.favorite)
            self._sync_tag_regions(photo)
        if self._filter_text or self._tag_filter:
            self._apply_filter(self._filter_text)
        else:
            self.grid.viewport().update()

    def _toggle_tag_overlay(self) -> None:
        """``t``: conmuta el borde de las etiquetas con región, sin escribir.

        Solo actúa con el visor abierto: la tabla de ``WINDOW`` no distingue
        estados y el panel/fallback también despachan esta acción. Sin
        regiones el interruptor queda activo y el aviso es efímero.
        """
        if not self._viewer_visible:
            return
        photo = self._current_photo()
        if photo is None:
            return
        self._tag_overlay_visible = not self._tag_overlay_visible
        regions = visible_tag_regions(photo)
        self.viewer.canvas.set_tag_regions(regions)
        self.viewer.canvas.set_tag_overlay_visible(self._tag_overlay_visible)
        if not self._tag_overlay_visible:
            message = "etiquetas ocultas"
        elif regions:
            message = "etiquetas sobre la foto"
        else:
            message = "sin etiquetas con región en esta foto"
        self.status.show_message(message, 3000)

    def _toggle_favorite(self) -> None:
        photo = self._current_photo()
        item = self.grid.currentItem()
        if photo is None or item is None:
            return
        updated = with_favorite_tag(photo, not photo.favorite)
        self._apply_photo_update(updated)
        if self.tag_connection is None or photo.photo_hash is None:
            # Demo y pruebas: el conmutado queda solo en memoria.
            message = "favorito" if updated.favorite else "sin favorito"
            self.status.show_message(message, 2000)
            return
        try:
            persisted = toggle_favorite(
                self.tag_connection,
                photo.photo_hash,
                now=datetime.now(UTC).isoformat(),
            )
        except CatalogError as exc:
            # Escritura fallida: se revierte el cambio optimista por la
            # misma ruta y el error permanece en la barra hasta que Esc lo
            # descarta.
            self._apply_photo_update(photo)
            self.status.show_error(str(exc))
            return
        final = updated
        if persisted != updated.favorite:
            # Otro escritor cambió la BD entre la carga y el conmutado: la
            # UI sigue el estado que devolvió el catálogo.
            final = with_favorite_tag(photo, persisted)
            self._apply_photo_update(final)
        message = "favorito" if final.favorite else "sin favorito"
        self.status.show_message(message, 2000)

    def shutdown_background_work(self) -> None:
        """Detiene el trabajo en fondo de la ventana (T2.6, cierre limpio).

        Idempotente. ``MainWindow`` lo cuelga también de ``aboutToQuit``:
        ``Ctrl+q``, ``:q`` y ⌘Q salen del bucle de eventos sin pasar por el
        ``closeEvent``, y los timers del populate por lotes y el pool de
        decodificación del visor deben quedar muertos en toda vía de
        salida. ``_closing`` es terminal y bloquea el trabajo que llegue
        tarde; el ``waitForDone`` va acotado y registra si expira.
        """
        self._closing = True
        self._visible_timer.stop()
        self._g_timer.stop()
        self._cancel_populate()
        self._loader_pool.clear()
        if not self._loader_pool.waitForDone(2000):
            logger.warning("quedan decodificaciones del visor al cerrar")

    def closeEvent(self, event):
        # Trabajo en fondo (T2.6): sin timers de populate ni tareas del
        # visor vivas al cerrar. ``MainWindow`` repite la llamada en
        # ``aboutToQuit`` para las salidas que no pasan por este evento.
        self.shutdown_background_work()
        # La conexión RW de etiquetas es propiedad de la ventana: la app real
        # la crea en MainWindow y aquí se cierra al salir.
        if self.tag_connection is not None:
            self.tag_connection.close()
            self.tag_connection = None
        super().closeEvent(event)

    def keyPressEvent(self, event):
        # Sin guardas de modo: las de _dispatch son las mismas y únicas.
        shift, ctrl, cmd = event_modifiers(event)
        action = key_action(WINDOW, event.key(), shift=shift, ctrl=ctrl, cmd=cmd)
        if action is None:
            super().keyPressEvent(event)
            return
        self._dispatch(action)
