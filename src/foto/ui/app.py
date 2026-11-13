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
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import AppConfig, ConfigError, load_config, save_config
from foto.storage.library import (
    LibraryConfig,
    foto_dir,
    library_file,
    load_library,
    thumbs_dir,
)
from foto.storage.resolver import resolve_library_path, resolve_metadata_dir
from foto.storage.tags import Tag, connect_read_write
from foto.ui.grid import THUMB_H, THUMB_W
from foto.ui.models import Photo, PhotoTag
from foto.ui.theme import apply_theme, ui_font
from foto.ui.thumbnails import ThumbnailService
from foto.ui.window import (
    PhotoWindow,
)

logger = logging.getLogger(__name__)

_RAW_EXTENSIONS = {".arw", ".cr2", ".dng", ".nef", ".raf"}

# Mismas cadenas que los CLIs (ADR 0008): la UI comparte resolutores, y sus
# overrides de CLI no se persisten en config.toml.
UI_EPILOG = """\
precedencia de la biblioteca:
  1. --library PATH
  2. FOTO_LIBRARY
  3. [library].path de config.toml
  4. diálogo de primera ejecución (persiste [library].path)
precedencia de la metainformación (.foto, ADR 0008):
  1. --metadata PATH
  2. FOTO_METADATA
  3. [metadata].path de config.toml
  4. <fototeca>/.foto (por defecto)
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
    parser.add_argument("--library", metavar="PATH", type=Path, help="raíz de la biblioteca")
    parser.add_argument(
        "--metadata",
        metavar="PATH",
        type=Path,
        help="directorio de metainformación; por defecto: <fototeca>/.foto",
    )
    return parser


def _resolve_before_dialog(
    args: argparse.Namespace,
) -> tuple[AppConfig, Path | None]:
    """Config y biblioteca según la cadena compartida con los CLIs.

    El diálogo de primera ejecución queda como último nivel del llamador,
    nunca como paso del resolvedor. Los overrides de CLI no se persisten.
    """
    config = load_config(args.config)
    resolution = resolve_library_path(args.library, config_path=args.config)
    return config, resolution.library_path


def _resolve_metadata_dir(
    library_path: Path, args: argparse.Namespace
) -> Path:
    """Directorio de metainformación por la cadena del ADR 0008."""
    resolution = resolve_metadata_dir(
        library_path, args.metadata, config_path=args.config
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
        other_locations=photo.other_locations,
        photo_hash=photo.photo_hash,
    )


def _validated_library(library_path: Path, metadata_dir: Path | None = None) -> LibraryConfig:
    """Carga y valida el descriptor de la biblioteca sin crearlo (C04).

    El catálogo solo se abre una vez validado ``library.toml``: descriptor
    ausente, TOML corrupto, UUID inválido, versión futura o ``root`` que no
    coincide producen un error explícito, nunca la creación del descriptor
    desde la UI (ADR 0008).
    """
    resolved_dir = metadata_dir if metadata_dir is not None else foto_dir(library_path)
    if not library_file(resolved_dir).exists():
        raise RuntimeError(
            "La biblioteca no tiene descriptor (library.toml). Ejecuta "
            "'foto-index' en ella para prepararla."
        )
    try:
        return load_library(resolved_dir, library_path)
    except LibraryError as exc:
        raise RuntimeError(str(exc)) from exc


def _metadata_dir_for(library_path: Path, metadata_dir: Path | None) -> Path:
    """Directorio de metadatos efectivo (ADR 0008)."""
    return metadata_dir if metadata_dir is not None else foto_dir(library_path)


def load_photos(library_path: Path, metadata_dir: Path | None = None) -> list[Photo]:
    """Carga las fotos de una biblioteca validada y con índice existente."""
    resolved_dir = _metadata_dir_for(library_path, metadata_dir)
    _validated_library(library_path, resolved_dir)

    catalog_path = resolved_dir / CATALOG_FILE_NAME
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No se encontró el índice de la fototeca. Ejecuta 'foto-index' "
            "para crearlo en el directorio de metainformación."
        )

    try:
        catalog = Catalog.open_read_only(catalog_path)
    except CatalogError as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        catalog_photos = load_from_catalog(catalog, library_path)
    finally:
        catalog.close()

    return [_to_ui_photo(photo) for photo in catalog_photos]


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
            # C04: el modelo validado del descriptor se conserva explícitamente.
            self._library_config = _validated_library(library_path, resolved_dir)
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
        config, library_path = _resolve_before_dialog(args)
    except ConfigError as exc:
        QMessageBox.critical(None, "foto", str(exc))
        sys.exit(1)

    if library_path is None or not library_path.is_dir():
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

    try:
        metadata_dir = _resolve_metadata_dir(library_path, args)
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
