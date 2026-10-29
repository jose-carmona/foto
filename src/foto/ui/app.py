"""Aplicación Qt de foto."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from foto.core.library import list_images
from foto.storage.config import AppConfig, ConfigError, load_config, save_config
from foto.ui.prototype import (
    THUMB_H,
    THUMB_W,
    Photo,
    PrototypeWindow,
    apply_theme,
    ui_font,
)

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


def _thumbnail_icon(path: Path) -> tuple[QIcon, str]:
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return _placeholder_icon(path), ""

    width, height = pixmap.width(), pixmap.height()
    scaled = pixmap.scaled(
        THUMB_W,
        THUMB_H,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    x = max(0, (scaled.width() - THUMB_W) // 2)
    y = max(0, (scaled.height() - THUMB_H) // 2)
    cropped = scaled.copy(x, y, THUMB_W, THUMB_H)
    return QIcon(cropped), f"{width} × {height} px"


def _build_photo(path: Path, library_path: Path) -> tuple[Photo, QIcon]:
    icon, dimensions = _thumbnail_icon(path)

    try:
        size = _format_size(path.stat().st_size)
    except OSError:
        size = ""

    try:
        folder = str(path.parent.relative_to(library_path))
    except ValueError:
        folder = str(path.parent)

    photo = Photo(
        name=path.name,
        taken="",
        camera="",
        lens="",
        focal="",
        aperture="",
        shutter="",
        iso="",
        folder="." if folder == "." else folder,
        fmt=_format_name(path),
        dimensions=dimensions,
        size=size,
        location="",
        path=path,
    )
    return photo, icon


def load_photos(library_path: Path) -> tuple[list[Photo], dict[str, QIcon]]:
    """Convierte los ficheros de la biblioteca en datos y miniaturas para la UI."""
    photos: list[Photo] = []
    thumbnails: dict[str, QIcon] = {}
    for path in list_images(library_path):
        photo, icon = _build_photo(path, library_path)
        photos.append(photo)
        thumbnails[photo.key] = icon
    return photos, thumbnails


class MainWindow(PrototypeWindow):
    def __init__(self, library_path: Path, config: AppConfig) -> None:
        self.library_path = library_path
        self.config = config
        photos, thumbnails = load_photos(library_path)
        super().__init__(
            photos,
            thumbnails=thumbnails,
            title=self._window_title(),
            initial_panel_visible=False,
            on_open=self._open_library,
            on_rescan=self._rescan,
            grid_row=True,
        )

    def _window_title(self) -> str:
        return f"foto — {self.library_path.name}"

    def _reload_library(self) -> bool:
        if not self.library_path.is_dir():
            self.status.show_error("la fototeca no está disponible")
            return False
        photos, thumbnails = load_photos(self.library_path)
        self.setWindowTitle(self._window_title())
        self.set_photos(photos, thumbnails)
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
        self.status.show_message("reindexando…", 3000)
        if self._reload_library():
            self.status.show_message(f"{len(self.photos)} fotos indexadas", 3000)


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

    window = MainWindow(library_path, config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
