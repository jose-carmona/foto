"""Generación asíncrona de miniaturas para la rejilla."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from foto.core.thumbnails import reader_scaled_size

logger = logging.getLogger(__name__)

MAX_ACTIVE_THUMBNAILS = 3


def read_thumbnail(path: Path, target: QSize) -> QImage:
    """Decodifica y recorta una miniatura sin usar el hilo de UI."""
    if target.isEmpty() or not target.isValid():
        raise ValueError("Tamaño de miniatura no válido")

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if not reader.canRead():
        raise ValueError(reader.errorString() or f"formato no soportado: {path.suffix}")

    source = reader.size()
    if source.isValid() and not source.isEmpty():
        rotate_90 = bool(
            reader.transformation()
            & QImageIOHandler.Transformation.TransformationRotate90
        )
        scaled_width, scaled_height = reader_scaled_size(
            source.width(),
            source.height(),
            target.width(),
            target.height(),
            rotate_90=rotate_90,
        )
        reader.setScaledSize(QSize(scaled_width, scaled_height))

    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString() or "imagen no válida")
    if image.width() != target.width() or image.height() != target.height():
        image = image.scaled(
            target,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
    left = max(0, (image.width() - target.width()) // 2)
    top = max(0, (image.height() - target.height()) // 2)
    return image.copy(left, top, target.width(), target.height())


class ThumbnailSignals(QObject):
    loaded = Signal(int, str, QImage)
    failed = Signal(int, str, str)


class ThumbnailTask(QRunnable):
    """Decodifica una miniatura fuera del hilo de UI."""

    def __init__(
        self,
        generation: int,
        key: str,
        path: Path,
        target: QSize,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.key = key
        self.path = path
        self.target = QSize(target)
        self.signals = ThumbnailSignals()

    def run(self) -> None:
        try:
            image = read_thumbnail(self.path, self.target)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - plugins de imagen
            self.signals.failed.emit(self.generation, self.key, str(exc))
            return
        self.signals.loaded.emit(self.generation, self.key, image)


class ThumbnailService(QObject):
    """Genera las miniaturas visibles con un pool acotado.

    La cola pendiente se sustituye cada vez que cambia el viewport: solo se
    trabaja sobre lo que el usuario puede ver. Las tareas ya iniciadas no se
    interrumpen; sus resultados se descartan al cambiar de generación.
    """

    loaded = Signal(str, QImage)
    failed = Signal(str, str)

    def __init__(self, target: QSize, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._target = QSize(target)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_ACTIVE_THUMBNAILS)
        self._pending: dict[str, Path] = {}
        self._order: deque[str] = deque()
        self._running: set[str] = set()
        self._done: set[str] = set()
        self._failed: set[str] = set()
        self._generation = 0

    def load_visible(self, items: Iterable[tuple[str, Path]]) -> None:
        """Sustituye la cola pendiente por las miniaturas visibles actuales."""
        self._pending.clear()
        self._order.clear()
        for key, path in items:
            if key in self._done or key in self._failed or key in self._running:
                continue
            if key in self._pending:
                continue
            self._pending[key] = path
            self._order.append(key)
        self._dispatch()

    def cancel_all(self) -> None:
        """Descarta la cola y los resultados en vuelo de la generación actual."""
        self._generation += 1
        self._pending.clear()
        self._order.clear()
        self._running.clear()
        self._done.clear()
        self._failed.clear()
        self._pool.clear()

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Descarta el trabajo pendiente y espera brevemente a las tareas activas."""
        self.cancel_all()
        if not self._pool.waitForDone(timeout_ms):
            logger.debug("Quedan miniaturas en curso al cerrar la ventana")

    def _dispatch(self) -> None:
        while self._order and len(self._running) < MAX_ACTIVE_THUMBNAILS:
            key = self._order.popleft()
            path = self._pending.pop(key, None)
            if path is None or key in self._done or key in self._failed:
                continue
            if key in self._running:
                continue
            self._running.add(key)
            task = ThumbnailTask(self._generation, key, path, self._target)
            task.signals.loaded.connect(self._on_loaded)
            task.signals.failed.connect(self._on_failed)
            self._pool.start(task)

    def _on_loaded(self, generation: int, key: str, image: QImage) -> None:
        if generation != self._generation:
            return
        self._running.discard(key)
        self._done.add(key)
        self.loaded.emit(key, image)
        self._dispatch()

    def _on_failed(self, generation: int, key: str, message: str) -> None:
        if generation != self._generation:
            return
        self._running.discard(key)
        self._failed.add(key)
        logger.debug("No se pudo generar la miniatura de %s: %s", key, message)
        self.failed.emit(key, message)
        self._dispatch()
