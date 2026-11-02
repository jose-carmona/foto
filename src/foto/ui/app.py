"""Aplicación Qt de foto."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from foto.core.library import CatalogPhoto, load_from_catalog
from foto.storage.catalog import Catalog
from foto.storage.config import AppConfig, ConfigError, load_config, save_config
from foto.storage.library import thumbs_dir
from foto.ui.prototype import (
    THUMB_H,
    THUMB_W,
    Photo,
    PrototypeWindow,
    apply_theme,
    ui_font,
)
from foto.ui.thumbnails import ThumbnailService

logger = logging.getLogger(__name__)

_RAW_EXTENSIONS = {".arw", ".cr2", ".dng", ".nef", ".raf"}


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
        rating=photo.rating,
        favorite=photo.favorite,
        path=photo.path,
        canonical_rel_path=photo.canonical_rel_path,
        other_locations=photo.other_locations,
        photo_hash=photo.photo_hash,
    )


def load_photos(library_path: Path) -> list[Photo]:
    """Carga las fotos desde el catálogo SQLite. Requiere un índice existente."""
    catalog_path = library_path / ".foto" / "catalog.sqlite"
    if not catalog_path.exists():
        raise FileNotFoundError(
            "No se encontró el índice de la fototeca. Ejecuta 'foto-index' "
            "para crear <fototeca>/.foto/catalog.sqlite."
        )

    try:
        catalog = Catalog.open(catalog_path)
    except Exception as exc:
        raise RuntimeError(f"No se pudo abrir el catálogo: {exc}") from exc

    try:
        catalog_photos = load_from_catalog(catalog, library_path)
    finally:
        catalog.close()

    return [_to_ui_photo(photo) for photo in catalog_photos]


class MainWindow(PrototypeWindow):
    def __init__(self, library_path: Path, config: AppConfig) -> None:
        self.library_path = library_path
        self.config = config
        try:
            photos = load_photos(library_path)
        except Exception as exc:
            QMessageBox.critical(None, "foto", str(exc))
            sys.exit(1)

        service = ThumbnailService(
            QSize(THUMB_W, THUMB_H),
            cache_dir=thumbs_dir(library_path),
        )
        super().__init__(
            photos,
            thumbnails={},
            placeholder_icon=_placeholder_icon(Path()),
            thumbnail_loader=service.load_visible,
            title=self._window_title(),
            initial_panel_visible=False,
            on_open=self._open_library,
            on_rescan=self._rescan,
            grid_row=True,
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

    def _reload_library(self) -> bool:
        if not self.library_path.is_dir():
            self.status.show_error("la fototeca no está disponible")
            return False
        try:
            photos = load_photos(self.library_path)
        except Exception as exc:
            self.status.show_error(str(exc))
            return False
        self._thumbnail_service.cancel_all()
        self._thumbnail_service.set_cache_dir(thumbs_dir(self.library_path))
        self.setWindowTitle(self._window_title())
        self.set_photos(photos, thumbnails={})
        return True

    def _open_library(self, argument: str) -> None:
        if argument:
            target = Path(argument).expanduser()
            if not target.is_absolute():
                target = Path.cwd() / target
            target = target.resolve()
            if not target.is_dir():
                self.status.show_error(f"no es una carpeta: {argument}")
                return
        else:
            directory = QFileDialog.getExistingDirectory(
                self,
                "Seleccionar fototeca",
                str(self.library_path),
            )
            if not directory:
                self.status.show_message("selección cancelada", 2000)
                return
            target = Path(directory).resolve()

        new_config = AppConfig(
            config_path=self.config.config_path,
            library_path=target,
            version=self.config.version,
        )
        try:
            save_config(new_config)
        except OSError as exc:
            self.status.show_error(f"no se pudo guardar la configuración: {exc}")
            return

        self.config = new_config
        self.library_path = target
        if self._reload_library():
            self.status.show_message(f"biblioteca abierta: {target.name}", 3000)

    def _rescan(self) -> None:
        self.status.show_message("recargando desde el índice…", 3000)
        if self._reload_library():
            self.status.show_message(f"{len(self.photos)} fotos cargadas", 3000)
        else:
            self.status.show_message("ejecuta 'foto-index' para generar/actualizar el índice", 4000)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("foto")
    apply_theme(app)

    try:
        config = load_config()
    except ConfigError as exc:
        QMessageBox.critical(None, "foto", str(exc))
        sys.exit(1)

    library_path = config.library_path
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
        except OSError as exc:
            QMessageBox.critical(
                None,
                "foto",
                f"No se pudo guardar la configuración:\n{exc}",
            )
            sys.exit(1)

    try:
        window = MainWindow(library_path, config)
    except SystemExit:
        raise
    except Exception as exc:
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
            )
        except Exception:
            pass
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
                )
            except Exception:
                pass
        QTimer.singleShot(50, _focus)
    else:
        window.show()
        window.raise_()
        window.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
