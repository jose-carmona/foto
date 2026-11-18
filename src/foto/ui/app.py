"""Aplicación Qt de foto."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from foto.core.errors import CatalogError, LibraryError
from foto.core.library import CatalogPhoto, load_from_catalog
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
from foto.storage.tags import Tag, connect_read_write
from foto.ui.grid import THUMB_H, THUMB_W
from foto.ui.models import LocationStatus, Photo, PhotoTag
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


def _to_ui_photo(photo: CatalogPhoto) -> Photo:
    return Photo(
        name=photo.name,
        taken=photo.taken,
        camera=photo.camera,
        lens=photo.lens,
        focal=photo.focal,
        aperture=photo.aperture,
        shutter=photo.shutter,
        iso=photo.iso,
        folder=photo.folder,
        fmt=photo.fmt,
        dimensions=photo.dimensions,
        size=photo.size,
        location=photo.location,
        gps_latitude=photo.gps_latitude,
        gps_longitude=photo.gps_longitude,
        place_country=photo.place_country,
        place_country_code=photo.place_country_code,
        place_state=photo.place_state,
        place_city=photo.place_city,
        place_municipality=photo.place_municipality,
        place_display_name=photo.place_display_name,
        place_address_json=photo.place_address_json,
        tags=tuple(
            PhotoTag(
                name=assignment.tag_name,
                kind=assignment.kind,
                status=assignment.status,
                source=assignment.source,
            )
            for assignment in photo.tags
        ),
        path=photo.path,
        canonical_rel_path=photo.canonical_rel_path,
        canonical_location=photo.canonical_location,
        available=photo.available,
        other_locations=photo.other_locations,
        photo_hash=photo.photo_hash,
    )


def _metadata_dir_for(library_path: Path, metadata_dir: Path | None) -> Path:
    """Directorio de metadatos efectivo (ADR 0008)."""
    return metadata_dir if metadata_dir is not None else foto_dir(library_path)


def load_photos(library_path: Path, metadata_dir: Path | None = None) -> list[Photo]:
    """Carga las fotos del catálogo de la biblioteca (fuente de verdad, ADR 0010).

    La UI no resuelve ubicaciones ni las lista: abre el catálogo por la
    cadena de metainformación y de la BD salen las rutas absolutas. Sin
    catálogo no hay sesión: error explícito pidiendo ``foto-index``, sin
    crear nada.
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
        catalog_photos = load_from_catalog(catalog)
    finally:
        catalog.close()

    return [_to_ui_photo(photo) for photo in catalog_photos]


def load_locations(metadata_dir: Path) -> tuple[LocationStatus, ...]:
    """Estado de las ubicaciones desde el catálogo (fuente de verdad, ADR 0010).

    La UI no lee la declaración de la config ni la lista: la tabla
    ``locations`` de la BD da nombre, ``tier``, ruta y estado; el nº de
    fotos sale de ``files`` y la disponibilidad la evalúa la función pura
    compartida de ``storage/`` al cargar —no en cada acceso a fichero.
    Activas primero, dadas de baja al final; alfabético dentro de cada grupo.
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
        counts = catalog.present_counts_by_location()
        named = catalog.list_named_locations()
    finally:
        catalog.close()

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


def load_tags(library_path: Path, metadata_dir: Path | None = None) -> list[Tag]:
    """Carga el vocabulario de etiquetas del catálogo (ADR 0006).

    Solo lectura, igual que ``load_photos``: el visor exige un catálogo ya
    creado y migrado por ``foto-index``.
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
        return catalog.tag_list()
    finally:
        catalog.close()


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
        try:
            photos = load_photos(library_path, resolved_dir)
            tags = load_tags(library_path, resolved_dir)
            # El estado de las ubicaciones también sale del catálogo (T4.2):
            # la disponibilidad se evalúa aquí, al cargar, con la función
            # pura de storage/ y nunca en cada acceso a fichero.
            locations = load_locations(resolved_dir)
        except (OSError, RuntimeError) as exc:
            QMessageBox.critical(None, "foto", str(exc))
            sys.exit(1)

        try:
            # Conexión RW dedicada del visor para los favoritos (ADR 0006,
            # Decisión 10): no toma index.lock y WAL serializa con el
            # indexador. Sin ella los favoritos no persisten, así que un
            # fallo al abrirla es un error de arranque, no un aviso.
            tag_connection = connect_read_write(resolved_dir / CATALOG_FILE_NAME)
        except (CatalogError, OSError) as exc:
            QMessageBox.critical(None, "foto", str(exc))
            sys.exit(1)

        service = ThumbnailService(
            QSize(THUMB_W, THUMB_H),
            cache_dir=thumbs_dir(resolved_dir),
        )
        super().__init__(
            photos,
            thumbnails={},
            placeholder_icon=_placeholder_icon(Path()),
            thumbnail_loader=service.load_visible,
            title=self._window_title(),
            initial_panel_visible=False,
            grid_row=True,
            tag_connection=tag_connection,
            tag_catalog=tags,
            locations=locations,
        )
        self._thumbnail_service = service
        service.setParent(self)
        service.loaded.connect(self._on_thumbnail_loaded)
        service.failed.connect(self._on_thumbnail_failed)

    def _window_title(self) -> str:
        return f"foto — {self.library_path.name}"

    def closeEvent(self, event):
        self._thumbnail_service.shutdown()
        super().closeEvent(event)

    def _on_thumbnail_loaded(self, key: str, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self.set_thumbnail(key, QIcon(pixmap))

    def _on_thumbnail_failed(self, key: str, message: str) -> None:
        logger.debug("Miniatura fallida para %s: %s", key, message)


def main() -> None:
    args = _build_ui_parser().parse_args(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName("foto")
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
        window.raise_()
        window.activateWindow()
        QApplication.processEvents()
        try:
            pid = os.getpid()
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            logger.debug("no se pudo activar la ventana")
        def _focus():
            try:
                window.raise_()
                window.activateWindow()
                pid = os.getpid()
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true',
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except OSError:
                logger.debug("no se pudo activar la ventana")
        QTimer.singleShot(50, _focus)
    else:
        window.show()
        window.raise_()
        window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
