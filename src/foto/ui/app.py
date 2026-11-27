"""Aplicación Qt de foto."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from foto.core.errors import CatalogError, LibraryError
from foto.core.library import LibraryLoadCancelled, load_from_catalog
from foto.storage.availability import AVAILABLE, availability_state
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import (
    DEFAULT_LOCATION_NAME,
    DEFAULT_LOCATION_TIER,
    AppConfig,
    ConfigError,
    Location,
    load_config,
    save_config,
)
from foto.storage.library import foto_dir, thumbs_dir
from foto.storage.resolver import resolve_locations, resolve_metadata_dir
from foto.storage.tags import FAVORITE_TAG, Tag, connect_read_write
from foto.ui.grid import THUMB_H, THUMB_W
from foto.ui.models import LocationStatus, Photo
from foto.ui.presentation import find_tag_in_catalog
from foto.ui.theme import apply_theme, ui_font
from foto.ui.thumbnails import ThumbnailService
from foto.ui.window import (
    PhotoWindow,
)

logger = logging.getLogger(__name__)

_RAW_EXTENSIONS = {".arw", ".cr2", ".dng", ".nef", ".raf"}

# Mismas cadenas que los CLIs (ADR 0008 y ADR 0010): la UI comparte
# resolutores, y sus overrides de CLI no se persisten en config.toml.
UI_EPILOG = """\
ubicaciones de la fototeca (ADR 0010): se declaran solo en config.toml
  [[library.locations]] (version = 2) o [library].path (version = 1).
  No existe --library: si FOTO_LIBRARY está definida, error explícito.
  Sin declaración, el diálogo de primera ejecución persiste [library].path
  (solo en ficheros v1: la UI nunca escribe [[library.locations]]).
precedencia de la metainformación (.foto, ADR 0008):
  1. --metadata PATH
  2. FOTO_METADATA
  3. [metadata].path de config.toml
  4. <única ubicación declarada>/.foto (por defecto; con varias ubicaciones
     hay que concretarla: --metadata, FOTO_METADATA o [metadata].path)
Para el fichero de configuración: --config PATH, FOTO_CONFIG o platformdirs.
"""


def _build_ui_parser() -> argparse.ArgumentParser:
    """Parser de ``foto``: se ejecuta antes de construir nada de Qt."""
    parser = argparse.ArgumentParser(
        prog="foto",
        description="Visor de la fototeca local.",
        epilog=UI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", metavar="PATH", type=Path, help="configuración global")
    parser.add_argument(
        "--metadata",
        metavar="PATH",
        type=Path,
        help="directorio de metainformación; por defecto: <única ubicación "
        "declarada>/.foto",
    )
    return parser


def _resolve_before_dialog(
    args: argparse.Namespace,
) -> tuple[AppConfig, tuple[Location, ...]]:
    """Config y ubicaciones declaradas según la cadena compartida (ADR 0010).

    El diálogo de primera ejecución queda como último nivel del llamador,
    nunca como paso del resolvedor. Los overrides de CLI no se persisten.
    """
    config = load_config(args.config)
    locations = resolve_locations(config_path=args.config)
    return config, locations


def _resolve_metadata_dir(
    locations: tuple[Location, ...], args: argparse.Namespace
) -> Path:
    """Directorio de metainformación por la cadena del ADR 0008."""
    resolution = resolve_metadata_dir(
        locations, args.metadata, config_path=args.config
    )
    assert resolution.metadata_dir is not None
    return resolution.metadata_dir


def _format_name(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "JPEG"
    if suffix in {".tif", ".tiff"}:
        return "TIFF"
    if suffix in _RAW_EXTENSIONS:
        return f"RAW ({suffix[1:].upper()})"
    return suffix[1:].upper() or "—"


def _format_size(size: int) -> str:
    if size <= 0:
        return ""
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= factor:
            value = f"{size / factor:.1f}".rstrip("0").rstrip(".")
            return f"{value.replace('.', ',')} {unit}"
    return f"{size} B"


def _app_icon() -> QIcon:
    """Icono de la aplicación empaquetado con ella (``foto/resources``).

    Se carga en memoria (``read_bytes`` + ``QPixmap.loadFromData``) en vez
    de pasarse una ruta a ``QIcon``: el recurso viaja dentro del paquete y
    no hay que materializar un fichero temporal para que Qt lo lea.
    """
    from importlib import resources

    import foto

    data = (resources.files(foto) / "resources" / "icono.png").read_bytes()
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        return QIcon()
    return QIcon(pixmap)


def _placeholder_icon(path: Path) -> QIcon:
    pixmap = QPixmap(THUMB_W, THUMB_H)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    app = QApplication.instance()
    palette = app.palette() if app is not None else QPalette()
    painter.fillRect(pixmap.rect(), palette.color(QPalette.AlternateBase))
    painter.setPen(palette.color(QPalette.Mid))
    painter.drawRect(pixmap.rect().adjusted(0, 0, -1, -1))

    painter.setPen(palette.color(QPalette.WindowText))
    painter.setFont(ui_font(15, QFont.DemiBold))
    painter.drawText(
        pixmap.rect().adjusted(24, 0, -24, -28),
        Qt.AlignCenter,
        "Sin vista previa",
    )
    painter.setPen(palette.color(QPalette.Mid))
    painter.setFont(ui_font(12))
    painter.drawText(
        pixmap.rect().adjusted(24, 28, -24, 0),
        Qt.AlignCenter,
        path.suffix.lstrip(".").upper() or "imagen",
    )
    painter.end()
    return QIcon(pixmap)


def _metadata_dir_for(library_path: Path, metadata_dir: Path | None) -> Path:
    """Directorio de metadatos efectivo (ADR 0008)."""
    return metadata_dir if metadata_dir is not None else foto_dir(library_path)


def load_photos(library_path: Path, metadata_dir: Path | None = None) -> list[Photo]:
    """Carga las fotos del catálogo de la biblioteca (fuente de verdad, ADR 0010).

    La UI no resuelve ubicaciones ni las lista: abre el catálogo por la
    cadena de metainformación y de la BD salen las rutas absolutas. Sin
    catálogo no hay sesión: error explícito pidiendo ``foto-index``, sin
    crear nada. Es la variante de una sola carga (abre y cierra su
    conexión); el arranque real usa ``load_library``, que comparte una
    única conexión solo-lectura (T1.3).
    """
    resolved_dir = _metadata_dir_for(library_path, metadata_dir)

    catalog_path = resolved_dir / CATALOG_FILE_NAME
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No hay catálogo en la metainformación de la fototeca. Ejecuta "
            "'foto-index' para crearlo."
        )

    try:
        catalog = Catalog.open_read_only(catalog_path)
    except CatalogError as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        return _photos_from(catalog)
    finally:
        catalog.close()


def _photos_from(
    catalog: Catalog,
    should_continue: Callable[[], bool] | None = None,
    *,
    system_tag: str | None = None,
) -> list[Photo]:
    """Fotos desde un ``Catalog`` ya abierto (T1.3): sin abrir conexiones.

    Los objetos se materializan una sola vez (T1.4): la fábrica de
    ``load_from_catalog`` es ``Photo.from_catalog``, no hay copia campo a
    campo posterior. ``should_continue`` (T2.3) reenvía la guardia de
    cancelación del arranque progresivo (ADR 0011). ``system_tag`` (A.1/A.2)
    se reenvía al filtro en SQL para materializar solo las favoritas en la
    fase 1 del arranque en dos fases (A.3).
    """
    return load_from_catalog(
        catalog,
        factory=Photo.from_catalog,
        should_continue=should_continue,
        system_tag=system_tag,
    )


def _tags_from(catalog: Catalog) -> list[Tag]:
    """Vocabulario de etiquetas desde un ``Catalog`` ya abierto (T1.3)."""
    return catalog.tag_list()


def _locations_from(catalog: Catalog) -> tuple[LocationStatus, ...]:
    """Estado de las ubicaciones desde un ``Catalog`` ya abierto (T1.3).

    La disponibilidad se evalúa aquí, al cargar, con la función pura de
    ``storage/`` —nunca en cada acceso a fichero.
    """
    counts = catalog.present_counts_by_location()
    named = catalog.list_named_locations()

    statuses = [
        LocationStatus(
            name=row.name,
            tier=row.tier,
            path=row.path,
            state=row.state,
            available=availability_state(row.path) == AVAILABLE,
            photo_count=counts.get(row.id, 0),
            last_scanned_at=row.last_scanned_at,
        )
        for row in named
    ]
    active = sorted(
        (status for status in statuses if status.state != "deleted"),
        key=lambda status: status.name,
    )
    deleted = sorted(
        (status for status in statuses if status.state == "deleted"),
        key=lambda status: status.name,
    )
    return (*active, *deleted)


def _library_snapshot(
    catalog: Catalog,
    should_continue: Callable[[], bool] | None = None,
    system_tag: str | None = None,
) -> tuple[list[Photo], list[Tag], tuple[LocationStatus, ...]]:
    """Fotos, etiquetas y ubicaciones desde un catálogo ya abierto (A.3).

    Extraído de ``load_library`` para que esa función y el arranque en dos
    fases compartan exactamente la misma lectura. No abre ni cierra ninguna
    conexión: el llamador es dueño del catálogo. ``system_tag`` se reenvía a
    ``_photos_from`` para materializar solo las fotos con esa etiqueta de
    sistema; ``should_continue`` se comprueba también entre fotos y antes de
    leer etiquetas y ubicaciones, igual que en ``load_library``.
    """
    photos = _photos_from(catalog, should_continue, system_tag=system_tag)
    if should_continue is not None and not should_continue():
        raise LibraryLoadCancelled
    return photos, _tags_from(catalog), _locations_from(catalog)


def load_library(
    library_path: Path,
    metadata_dir: Path | None = None,
    *,
    should_continue: Callable[[], bool] | None = None,
) -> tuple[list[Photo], list[Tag], tuple[LocationStatus, ...]]:
    """Carga en una sola conexión solo-lectura fotos, tags y ubicaciones (T1.3).

    El arranque del visor abre el catálogo una única vez (``mode=ro``) y
    lee de él las tres cosas; antes eran tres aperturas secuenciales con
    el mismo contenido. La conexión de escritura de favoritos
    (``connect_read_write``) sigue aparte: es el escritor único del visor
    (ADR 0006, Decisión 10) y su semántica no cambia. El catálogo de
    entrada es el mismo fichero; no hay lock de escritor (modelo del
    visor, ADR 0003).

    ``should_continue`` (T2.3, ADR 0011): guardia de cancelación
    cooperativa del arranque progresivo. Se comprueba entre la fase de
    fotos y la de etiquetas y se reenvía a ``load_from_catalog``, que la
    comprueba por foto; al cortar se lanza ``LibraryLoadCancelled`` y la
    conexión se cierra igualmente en el ``finally``.
    """
    resolved_dir = _metadata_dir_for(library_path, metadata_dir)

    catalog_path = resolved_dir / CATALOG_FILE_NAME
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No hay catálogo en la metainformación de la fototeca. Ejecuta "
            "'foto-index' para crearlo."
        )

    try:
        catalog = Catalog.open_read_only(catalog_path)
    except CatalogError as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        return _library_snapshot(catalog, should_continue)
    finally:
        catalog.close()


@dataclass(frozen=True)
class LibraryFirstPhase:
    """Primera entrega del arranque en dos fases (A.3).

    Con favoritas, ``photos`` contiene solo ellas y ``has_more`` es ``True``:
    la ventana puede poblar la rejilla con esos ítems y dejar el resto para la
    fase 2. Sin favoritas, ``photos`` es ya la biblioteca completa y
    ``has_more`` es ``False``, así que no hay segunda entrega. ``total`` es el
    recuento de fotos presentes del catálogo, no el de ítems cargados, para
    que la barra muestre «4 de 29567» desde el primer momento.
    """

    photos: list[Photo]
    tags: list[Tag]
    locations: tuple[LocationStatus, ...]
    total: int
    has_more: bool


@dataclass(frozen=True)
class LibraryLoadPhases:
    """Resultado completo del arranque en dos fases (A.3).

    ``remaining`` contiene solo las fotos **no** incluidas en
    ``first.photos``: la unión de ambas listas es la biblioteca completa sin
    claves repetidas.
    """

    first: LibraryFirstPhase
    remaining: list[Photo]


def load_library_phases(
    library_path: Path,
    metadata_dir: Path | None = None,
    *,
    should_continue: Callable[[], bool] | None = None,
    on_first_phase: Callable[[LibraryFirstPhase], None] | None = None,
) -> LibraryLoadPhases:
    """Carga la biblioteca en dos fases sobre una única conexión solo-lectura.

    Convención del contrato de señales del worker (A.3): este función abre el
    catálogo una sola vez, lee primero etiquetas y ubicaciones —que hacen
    falta para el banner y para resolver ``:tag`` antes del primer lote— y
    calcula el total de fotos presentes. Después materializa la fase 1 (solo
    favoritas cuando las hay, completa cuando no) y llama a
    ``on_first_phase`` **antes** de empezar la fase 2; el worker aprovecha
    ese callback para emitir la primera fase por señal encolada hacia la UI.
    Por último materializa la biblioteca completa y entrega en ``remaining``
    solo las fotos que no estaban en la primera fase.

    ``should_continue`` se reenvía a ambas materializaciones y se comprueba
    también entre fases: al cortar se lanza ``LibraryLoadCancelled`` y no se
    entregan resultados parciales. La conexión se cierra siempre en ``finally``.
    No se crea ningún objeto Qt, no se escribe nada y no se abre la BD dos
    veces (ADR 0011, Decisiones 3 y 4).
    """
    resolved_dir = _metadata_dir_for(library_path, metadata_dir)
    catalog_path = resolved_dir / CATALOG_FILE_NAME
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No hay catálogo en la metainformación de la fototeca. Ejecuta "
            "'foto-index' para crearlo."
        )

    try:
        catalog = Catalog.open_read_only(catalog_path)
    except CatalogError as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        # El banner y el filtro ``fav`` hacen falta antes del primer lote.
        tags = _tags_from(catalog)
        locations = _locations_from(catalog)
        total = catalog.count_present_photos()

        use_fav = (
            find_tag_in_catalog(FAVORITE_TAG, tags) is not None
            and catalog.has_system_tag(FAVORITE_TAG)
        )
        if should_continue is not None and not should_continue():
            raise LibraryLoadCancelled
        first_photos, _, _ = _library_snapshot(
            catalog,
            should_continue,
            FAVORITE_TAG if use_fav else None,
        )
        if should_continue is not None and not should_continue():
            raise LibraryLoadCancelled

        first = LibraryFirstPhase(
            photos=first_photos,
            tags=tags,
            locations=locations,
            total=total,
            has_more=use_fav,
        )
        if on_first_phase is not None:
            on_first_phase(first)

        remaining: list[Photo] = []
        if first.has_more:
            if should_continue is not None and not should_continue():
                raise LibraryLoadCancelled
            first_keys = {photo.key for photo in first.photos}
            remaining = [
                photo
                for photo in _library_snapshot(catalog, should_continue)[0]
                if photo.key not in first_keys
            ]
        return LibraryLoadPhases(first=first, remaining=remaining)
    finally:
        catalog.close()


def load_locations(metadata_dir: Path) -> tuple[LocationStatus, ...]:
    """Estado de las ubicaciones desde el catálogo (fuente de verdad, ADR 0010).

    La UI no lee la declaración de la config ni la lista: la tabla
    ``locations`` de la BD da nombre, ``tier``, ruta y estado; el nº de
    fotos sale de ``files`` y la disponibilidad la evalúa la función pura
    compartida de ``storage/`` al cargar —no en cada acceso a fichero.
    Activas primero, dadas de baja al final; alfabético dentro de cada grupo.
    Variante de una sola carga (T1.3): el arranque real usa ``load_library``.
    """
    catalog_path = metadata_dir / CATALOG_FILE_NAME
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No hay catálogo en la metainformación de la fototeca. Ejecuta "
            "'foto-index' para crearlo."
        )

    try:
        catalog = Catalog.open_read_only(catalog_path)
    except CatalogError as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        return _locations_from(catalog)
    finally:
        catalog.close()


def load_tags(library_path: Path, metadata_dir: Path | None = None) -> list[Tag]:
    """Carga el vocabulario de etiquetas del catálogo (ADR 0006).

    Solo lectura, igual que ``load_photos``: el visor exige un catálogo ya
    creado y migrado por ``foto-index``. Variante de una sola carga (T1.3):
    el arranque real usa ``load_library``.
    """
    catalog_path = _metadata_dir_for(library_path, metadata_dir) / CATALOG_FILE_NAME
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No se encontró el índice de la fototeca. Ejecuta 'foto-index' "
            "para crear <fototeca>/.foto/catalog.sqlite."
        )

    try:
        catalog = Catalog.open_read_only(catalog_path)
    except CatalogError as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        return _tags_from(catalog)
    finally:
        catalog.close()


class LibraryLoadWorker(QObject):
    """Carga del catálogo fuera del hilo de UI (T2.3, ADR 0011).

    El worker vive en la capa de UI (``core/`` y ``storage/`` siguen sin
    Qt) y entrega el resultado por señales: solo datos puros cruzan el
    hilo (`Photo`, `Tag`, `LocationStatus`), nunca objetos Qt. La conexión
    solo-lectura del catálogo la abre y la cierra el propio worker dentro
    de su hilo (modelo del visor, ADR 0003); la RW de favoritos queda en
    el hilo de UI, como dueño la ventana (ADR 0006).

    Cancelación (T2.6): ``cancel()`` marca la carga como descartada; la
    guardia ``should_continue`` corta el recorrido con
    ``LibraryLoadCancelled`` en la foto siguiente y el worker vuelve sin
    entregar fotos. Al ser **un único worker** el que abre la conexión y
    emite las dos fases (A.3), ``cancel()`` cubre ambas: cerrar la ventana a
    mitad no deja resultados parciales ni escrituras pendientes (la lectura
    es ``mode=ro``).

    Contrato de señales (A.3): el worker abre la BD una sola vez y entrega
    dos emisiones desde el mismo hilo. ``loaded`` llega con la fase 1 y el
    total de fotos presentes; ``library_complete`` llega una sola vez al
    terminar, con las fotos restantes (``[]`` si no hay fase 2 o si se
    canceló). Es esta última señal —y no ``loaded``— la que detiene el hilo.
    """

    #: ``(fotos, etiquetas, ubicaciones, total, has_more)``: fase 1, con el
    #: recuento de la biblioteca completa para la barra de estado.
    loaded = Signal(object, object, object, int, bool)
    #: ``(fotos restantes)``: única emisión final, incluso al cancelar.
    library_complete = Signal(object)
    #: Mensaje de error fatal de arranque (mismo guard que antes del show).
    failed = Signal(str)

    def __init__(self, library_path: Path, metadata_dir: Path) -> None:
        super().__init__()
        self._library_path = library_path
        self._metadata_dir = metadata_dir
        self._cancelled = False

    def cancel(self) -> None:
        """Descarta el resultado de la carga en curso (T2.6)."""
        self._cancelled = True

    @Slot(object)
    def _emit_first(self, first: LibraryFirstPhase) -> None:
        """Entrega la fase 1 (datos puros + total) en el hilo de UI por señal."""
        self.loaded.emit(
            first.photos, first.tags, first.locations, first.total, first.has_more
        )

    @Slot()
    def load(self) -> None:
        """Carga las dos fases en el mismo hilo y entrega ambas por señal."""
        emitted = False
        try:
            try:
                phases = load_library_phases(
                    self._library_path,
                    self._metadata_dir,
                    should_continue=lambda: not self._cancelled,
                    on_first_phase=self._emit_first,
                )
            except LibraryLoadCancelled:
                return
            except sqlite3.Error as exc:
                if not self._cancelled:
                    self.failed.emit(str(exc))
                return
            except (OSError, RuntimeError) as exc:
                if not self._cancelled:
                    self.failed.emit(str(exc))
                return
            except Exception as exc:  # límite del worker: ninguna excepción escapa
                # Ninguna excepción puede escapar de un hilo de fondo: si lo
                # hiciera, el slot moriría y la ventana quedaría en «Cargando…»
                # para siempre. Se registra y se entrega por ``failed``.
                logger.exception("fallo inesperado al cargar el catálogo")
                if not self._cancelled:
                    self.failed.emit(f"Fallo inesperado al cargar el catálogo: {exc}")
                return
            if self._cancelled:
                return
            self.library_complete.emit(phases.remaining)
            emitted = True
        finally:
            # Una sola emisión final por llamada: también en cancelación, en
            # fallo y cuando no hay fase 2. Es la señal que conecta a
            # ``thread.quit``, así que el hilo muere siempre al terminar.
            if not emitted:
                self.library_complete.emit([])


class MainWindow(PhotoWindow):
    def __init__(
        self,
        library_path: Path,
        config: AppConfig,
        metadata_dir: Path | None = None,
    ) -> None:
        self.library_path = library_path
        self.config = config
        resolved_dir = _metadata_dir_for(library_path, metadata_dir)
        self._catalog_path = resolved_dir / CATALOG_FILE_NAME

        service = ThumbnailService(
            QSize(THUMB_W, THUMB_H),
            cache_dir=thumbs_dir(resolved_dir),
        )
        # Arranque progresivo (T2.2, ADR 0011): la ventana nace vacía con su
        # estado de carga y aparece antes de tocar el catálogo; fotos,
        # etiquetas y ubicaciones llegan después por señal desde el worker.
        # La conexión RW de favoritos también se abre al llegar la carga
        # (``_on_library_loaded``): sin catálogo no hay sesión y todos los
        # errores fatales comparten la misma vía de aviso y cierre, con el
        # mensaje del guard de ``load_library`` (ahora tras el show).
        super().__init__(
            photos=[],
            thumbnails={},
            placeholder_icon=_placeholder_icon(Path()),
            thumbnail_loader=service.load_visible,
            title=self._window_title(),
            initial_panel_visible=False,
            grid_row=True,
            tag_catalog=(),
            locations=(),
        )
        self._thumbnail_service = service
        service.setParent(self)
        service.loaded.connect(self._on_thumbnail_loaded)
        service.failed.connect(self._on_thumbnail_failed)
        # Estado de carga (T2.2): texto discreto en la rejilla y aviso
        # persistente en la barra; ``set_photos`` retira el fijado al
        # entrar los datos del worker.
        self.set_library_loading(True)
        self.status.set_pinned("cargando el catálogo…")
        self._load_cancelled = False
        # Arranque en dos fases (A.3): el total de fotos presentes llega con
        # la fase 1 y la barra lo muestra antes de que exista la fase 2;
        # ``_pending_append`` guarda el resto si la fase 2 llega mientras el
        # populate de la fase 1 aún corre por lotes.
        self._total_count: int | None = None
        self._has_more = False
        self._pending_append: list[Photo] = []
        self._start_library_load(library_path, resolved_dir)

    def _start_library_load(self, library_path: Path, metadata_dir: Path) -> None:
        """Carga del catálogo en un hilo worker propio (T2.3, ADR 0011)."""
        self._loader = LibraryLoadWorker(library_path, metadata_dir)
        self._loader_thread = QThread(self)
        self._loader.moveToThread(self._loader_thread)
        self._loader.loaded.connect(self._on_library_loaded)
        self._loader.library_complete.connect(self._on_library_complete)
        self._loader.failed.connect(self._on_library_failed)
        # El hilo es corto: termina al entregar el resultado final, en éxito
        # y en fallo (``quit`` es seguro e idempotente). A.3: antes ``loaded``
        # detenía el hilo, lo que mataría al worker al entregar la fase 1 y
        # dejaría sin cargar el resto; ahora solo lo hace ``library_complete``.
        self._loader.library_complete.connect(self._loader_thread.quit)
        self._loader.failed.connect(self._loader_thread.quit)
        self._loader_thread.started.connect(self._loader.load)
        self._loader_thread.finished.connect(self._loader.deleteLater)
        app = QApplication.instance()
        if app is not None:
            # Salida limpia en toda vía de cierre (T2.6): Ctrl+q, ``:q`` y
            # ⌘Q salen del bucle de eventos sin pasar por el ``closeEvent``
            # de la ventana, así que el apagado del trabajo en fondo se
            # cuelga también de ``aboutToQuit``, que Qt emite en toda
            # salida del ``exec()``. Sin ello, el ``QThread`` moría en plena
            # carga al destruirse los widgets («Destroyed while thread is
            # still running»).
            app.aboutToQuit.connect(self._shutdown_background_work)
        self._loader_thread.start()

    def _shutdown_background_work(self) -> None:
        """Cancela y espera todo el trabajo en fondo, en toda vía de salida.

        Idempotente: ``closeEvent`` y ``aboutToQuit`` pueden invocarlo en
        cualquier orden. Cubre el worker de carga (cancelación cooperativa
        con espera acotada), el servicio de miniaturas (descarta la cola y
        espera a las tareas activas) y el trabajo en fondo de la ventana
        (timers del populate, pool de decodificación del visor).

        Arranque en dos fases (A.3): hay **un único worker** que abre la
        conexión de solo lectura y emite ambas fases, así que ``cancel()``
        cubre la fase 1 y la fase 2 con la misma guardia; no hace falta
        cancelar dos hilos ni descartar dos resultados por separado.

        Antes solo cubría el worker de carga y el servicio de miniaturas
        quedaba solo en el ``closeEvent``: al salir con ``Ctrl+q``
        mientras se cargaban miniaturas, las tareas en vuelo emitían hacia
        objetos ya destruidos («RuntimeError: Signal source has been
        deleted»). Las esperas van acotadas y los emits residuales quedan
        bajo el guardia de cada tarea.
        """
        self._load_cancelled = True
        loader = getattr(self, "_loader", None)
        thread = getattr(self, "_loader_thread", None)
        if loader is not None:
            loader.cancel()
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(10_000):
                logger.warning("el worker de carga no terminó en 10 s")
        service = getattr(self, "_thumbnail_service", None)
        if service is not None:
            service.shutdown()
        super().shutdown_background_work()

    def _on_library_loaded(self, photos, tags, locations, total, has_more) -> None:
        """Fase 1 del arranque (A.3): favoritas, total y estado de carga.

        Con ``has_more`` la fase 1 ya es solo la biblioteca filtrada en SQL;
        el filtro ``fav`` se activa igual que antes, pero sin crear ítems para
        las fotos que faltan. La barra muestra desde aquí «4 de 29567» y el
        aviso «cargando el resto de la biblioteca…», que ``set_photos``
        sobrescribiría con «etiqueta: fav» si se fijara antes.
        """
        if self._load_cancelled:
            return
        try:
            # Conexión RW dedicada del visor para los favoritos (ADR 0006,
            # Decisión 10): no toma index.lock y WAL serializa con el
            # indexador. Se abre aquí, en el hilo de UI y solo si hubo
            # catálogo que leer: sin ella los favoritos no persisten, así
            # que un fallo al abrirla sigue siendo un error de arranque,
            # con el mismo aviso y cierre que antes.
            tag_connection = connect_read_write(self._catalog_path)
        except (CatalogError, OSError) as exc:
            QMessageBox.critical(None, "foto", str(exc))
            self.close()
            QApplication.exit(1)
            return
        self.tag_connection = tag_connection
        # Con ``has_more`` la lista ya trae solo favoritas, así que basta
        # con que el vocabulario conozca ``fav``. Sin fase 2 la fase 1 es la
        # biblioteca completa y se mantiene el comportamiento de siempre:
        # no se activa ningún filtro.
        initial_tag_filter = (
            FAVORITE_TAG
            if has_more and find_tag_in_catalog(FAVORITE_TAG, tags)
            else None
        )
        self._total_count = total
        self._has_more = has_more
        self.set_library(
            photos,
            tags=tags,
            locations=locations,
            initial_tag_filter=initial_tag_filter,
            total_count=total,
        )
        if has_more:
            self.set_library_loading(True)
            self.status.set_pinned("cargando el resto de la biblioteca…")

    def _on_library_complete(self, remaining: list[Photo]) -> None:
        """Fase 2 del arranque (A.3): funde el resto sin repoblar la rejilla.

        Sin favoritas (``has_more`` era ``False``) la fase 1 ya era la
        biblioteca completa y aquí no se toca nada. Con cancelación tampoco.
        Si el populate de la fase 1 sigue en curso, el append se deja a
        ``_finish_populate`` para no solapar dos sesiones y no perder la
        selección que restaura su ``on_done``.
        """
        if self._load_cancelled or not self._has_more:
            return
        self._has_more = False
        self.status.set_pinned(
            f"etiqueta: {self._tag_filter}" if self._tag_filter else ""
        )
        self._append_on_done = self._finish_two_phase_startup
        if self._populate_active:
            self._pending_append = list(remaining)
            return
        self.append_photos(remaining)
        if not self._populate_active:
            self._finish_two_phase_startup()

    def _finish_two_phase_startup(self) -> None:
        """Cierra el estado de carga cuando el append de la fase 2 terminó.

        Se difiere al ``on_done`` del populate por lotes para que la barra
        siga diciendo «cargando el resto…» mientras quedan ítems por
        insertar. El banner se recalcula con la biblioteca ya completa: con
        solo las favoritas de la fase 1 su recuento de fotos no visibles
        estaría incompleto (ADR 0010).
        """
        self._append_on_done = None
        self._refresh_location_banner(list(self.photos.values()))
        self.set_library_loading(False)
        self._update_counts()

    def _on_library_failed(self, message: str) -> None:
        """Error fatal de arranque tras el show (T2.2, ADR 0011).

        Mismo mensaje y cierre con código 1 que el guard previo al show de
        antes; ahora llega por señal, ya con la ventana visible.
        """
        if self._load_cancelled:
            return
        self.set_library_loading(False)
        QMessageBox.critical(None, "foto", message)
        self.close()
        QApplication.exit(1)

    def _window_title(self) -> str:
        return f"foto — {self.library_path.name}"

    def closeEvent(self, event):
        # Cierre a mitad de carga o con miniaturas en vuelo (T2.6): mismo
        # apagado del trabajo en fondo que ``aboutToQuit`` —``Ctrl+q``,
        # ``:q`` y ⌘Q salen del bucle sin pasar por este evento—.
        self._shutdown_background_work()
        super().closeEvent(event)

    def _on_thumbnail_loaded(self, key: str, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self.set_thumbnail(key, QIcon(pixmap))

    def _on_thumbnail_failed(self, key: str, message: str) -> None:
        logger.debug("Miniatura fallida para %s: %s", key, message)


def _activate_window(window, *, macos: bool) -> None:
    """Trata de llevar la ventana al frente, sin bloquear el hilo de UI (R6).

    ``raise_``/``activateWindow`` son locales y baratos; en macOS se añade
    una petición de activación vía ``osascript`` con ``QProcess.startDetached``
    —asíncrona, sin espera ni zombis— porque Cocoa no siempre atiende al
    ``activateWindow`` propio en el arranque. Se llama una sola vez tras
    ``show()``; fuera de macOS no se lanza ningún proceso.
    """
    window.raise_()
    window.activateWindow()
    if not macos:
        return
    QProcess.startDetached(
        "osascript",
        [
            "-e",
            (
                'tell application "System Events" to set frontmost of '
                f'(first process whose unix id is {os.getpid()}) to true'
            ),
        ],
    )


def main() -> None:
    args = _build_ui_parser().parse_args(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName("foto")
    # Icono de la app (dock de macOS incluido): se fija en la aplicación
    # antes de crear cualquier ventana, para que todas lo hereden.
    app.setWindowIcon(_app_icon())
    apply_theme(app)

    try:
        config, locations = _resolve_before_dialog(args)
    except ConfigError as exc:
        QMessageBox.critical(None, "foto", str(exc))
        sys.exit(1)

    if not locations:
        # Sin declaración: el diálogo de primera ejecución solo actúa sin
        # config o con ficheros v1 (persiste [library].path); una v2 no
        # admite [library].path y la lista se edita a mano (ADR 0010).
        if config.version >= 2 and config.config_path.exists():
            QMessageBox.critical(
                None,
                "foto",
                "La configuración (version = 2) no declara ubicaciones y la "
                "interfaz no puede escribirlas: decláralas a mano en "
                "'[[library.locations]]' en config.toml.",
            )
            sys.exit(1)

        directory = QFileDialog.getExistingDirectory(
            None,
            "Seleccionar fototeca",
            str(Path.home()),
        )
        if not directory:
            sys.exit(0)

        library_path = Path(directory).resolve()
        config = AppConfig(
            config_path=config.config_path,
            library_path=library_path,
            version=config.version,
        )
        try:
            save_config(config)
        except (OSError, ConfigError) as exc:
            QMessageBox.critical(
                None,
                "foto",
                f"No se pudo guardar la configuración:\n{exc}",
            )
            sys.exit(1)

        locations = (
            Location(
                name=DEFAULT_LOCATION_NAME,
                path=library_path,
                tier=DEFAULT_LOCATION_TIER,
            ),
        )

    library_path = locations[0].path

    try:
        metadata_dir = _resolve_metadata_dir(locations, args)
    except (ConfigError, LibraryError) as exc:
        QMessageBox.critical(None, "foto", f"Metainformación inválida: {exc}")
        sys.exit(1)

    try:
        window = MainWindow(library_path, config, metadata_dir)
    except SystemExit:
        raise
    except (OSError, RuntimeError) as exc:
        QMessageBox.critical(None, "foto", str(exc))
        sys.exit(1)
    if sys.platform == "darwin":
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            window.setGeometry(screen.availableGeometry())
        window.show()
        QApplication.processEvents()
        _activate_window(window, macos=True)
    else:
        window.show()
        _activate_window(window, macos=False)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
