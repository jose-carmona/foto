"""Aplicación Qt mínima."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
)

from foto.core.library import list_images
from foto.storage.config import AppConfig, load_config, save_config


class MainWindow(QMainWindow):
    def __init__(self, library_path: Path):
        super().__init__()
        self.library_path = library_path
        self.setWindowTitle(f"foto — {library_path.name}")
        self.resize(1280, 960)
        self.setMinimumSize(640, 480)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setFlow(QListWidget.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setSpacing(16)
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.setIconSize(QSize(400, 400))
        self.list.setUniformItemSizes(True)
        self.setCentralWidget(self.list)

        self.load_thumbnails()

    def load_thumbnails(self):
        images = list_images(self.library_path)
        for img in images:
            item = QListWidgetItem()
            try:
                pixmap = QPixmap(str(img))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        392,
                        392,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                    item.setIcon(QIcon(scaled))
                    item.setText("")
                else:
                    item.setText(img.name)
            except Exception:
                item.setText(img.name)
            self.list.addItem(item)

        if not images:
            item = QListWidgetItem("No se encontraron imágenes en esta biblioteca")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self.list.addItem(item)


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
