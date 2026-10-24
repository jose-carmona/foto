"""Aplicación Qt mínima."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from foto.core.library import list_images
from foto.storage.config import AppConfig, load_config, save_config


class ThumbnailLabel(QLabel):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(200, 200)
        self.setStyleSheet("border: 1px solid gray;")
        self._load_thumbnail()

    def _load_thumbnail(self):
        try:
            pixmap = QPixmap(str(self.path))
            if pixmap.isNull():
                self.setText(self.path.name)
                return
            scaled = pixmap.scaled(
                196,
                196,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.setPixmap(scaled)
            self.setText("")
        except Exception:
            self.setText(self.path.name)


class MainWindow(QMainWindow):
    def __init__(self, library_path: Path):
        super().__init__()
        self.library_path = library_path
        self.setWindowTitle(f"foto — {library_path.name}")
        self.setMinimumSize(640, 480)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.grid = QGridLayout(container)
        self.grid.setSpacing(8)
        scroll.setWidget(container)
        self.setCentralWidget(scroll)

        self.load_thumbnails()

    def load_thumbnails(self):
        images = list_images(self.library_path)
        row = col = 0
        cols = 4
        for img in images:
            label = ThumbnailLabel(img)
            self.grid.addWidget(label, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

        if not images:
            empty = QLabel("No se encontraron imágenes en esta biblioteca")
            empty.setAlignment(Qt.AlignCenter)
            self.grid.addWidget(empty, 0, 0)


def main():
    app = QApplication(sys.argv)
    
    # Cargar configuración
    config = load_config()
    library_path = config.library_path
    
    if library_path is None or not library_path.exists():
        # Mostrar diálogo pidiendo ruta
        msg = QMessageBox()
        msg.setWindowTitle("Configurar fototeca")
        msg.setText("No se ha configurado ninguna fototeca.\n\nSelecciona la carpeta que contiene tus fotos.")
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()
        
        directory = QFileDialog.getExistingDirectory(
            None,
            "Seleccionar fototeca",
            str(Path.home()),
        )
        if not directory:
            sys.exit(0)
        
        library_path = Path(directory).resolve()
        # Guardar configuración
        new_config = AppConfig(config_path=config.config_path, library_path=library_path, version=config.version)
        save_config(new_config)
    
    window = MainWindow(library_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
