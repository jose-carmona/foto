"""Generación asíncrona y caché persistente de miniaturas."""

from __future__ import annotations

import logging
import os
import tempfile
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from foto.core.thumbnails import CACHE_VERSION, cache_filename, reader_scaled_size

logger = logging.getLogger(__name__)

MAX_ACTIVE_THUMBNAILS = 3
CACHE_JPEG_QUALITY = 90


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


class ThumbnailCache:
    """Caché en disco de miniaturas ya orientadas y recortadas.

    El nombre combina el hash del contenido con el tamaño y la versión del
    formato, de modo que mover o renombrar una foto no invalida su miniatura.
    """

    def __init__(
        self,
        directory: Path,
        target: QSize,
        *,
        version: int = CACHE_VERSION,
    ) -> None:
        self._directory = Path(directory)
        self._target = QSize(target)
        self._version = version

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, photo_hash: str) -> Path:
        return self._directory / cache_filename(
            photo_hash,
            self._target.width(),
            self._target.height(),
            version=self._version,
        )

    def read(self, photo_hash: str) -> QImage | None:
        """Devuelve la miniatura cacheada o ``None`` si no es válida."""
        path = self.path_for(photo_hash)
        try:
            if path.stat().st_size <= 0:
                return None
        except OSError:
            return None
        image = QImage(str(path))
        if image.isNull() or image.size() != self._target:
            return None
        return image

    def write(self, photo_hash: str, image: QImage) -> None:
        """Guarda la miniatura de forma atómica; un fallo no es fatal."""
        path = self.path_for(photo_hash)
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{path.stem}-",
                suffix=".tmp",
            )
            temporary = Path(name)
            os.close(descriptor)
            if not image.save(str(temporary), "JPEG", CACHE_JPEG_QUALITY):
                raise OSError("Qt no pudo escribir la miniatura")
            os.replace(temporary, path)
        except OSError as exc:
            logger.debug("No se pudo guardar la miniatura en caché: %s", exc)
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass


def load_thumbnail(
    path: Path,
    target: QSize,
    *,
    photo_hash: str | None = None,
    cache: ThumbnailCache | None = None,
) -> QImage:
    """Devuelve la miniatura desde la caché o la genera y la guarda."""
    if cache is not None and photo_hash:
        cached = cache.read(photo_hash)
        if cached is not None:
            return cached
    image = read_thumbnail(path, target)
    if cache is not None and photo_hash:
        cache.write(photo_hash, image)
    return image


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
        *,
        photo_hash: str | None = None,
        cache: ThumbnailCache | None = None,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.key = key
        self.path = path
        self.target = QSize(target)
        self.photo_hash = photo_hash
        self.cache = cache
        self.signals = ThumbnailSignals()

    def run(self) -> None:
        try:
            image = load_thumbnail(
                self.path,
                self.target,
                photo_hash=self.photo_hash,
                cache=self.cache,
            )
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

    def __init__(
        self,
        target: QSize,
        parent: QObject | None = None,
        *,
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._target = QSize(target)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_ACTIVE_THUMBNAILS)
        self._cache: ThumbnailCache | None = None
        self.set_cache_dir(cache_dir)
        self._pending: dict[str, tuple[Path, str | None]] = {}
        self._order: deque[str] = deque()
        self._running: set[str] = set()
        self._done: set[str] = set()
        self._failed: set[str] = set()
        self._generation = 0

    def set_cache_dir(self, cache_dir: Path | None) -> None:
        """Cambia la caché persistente al abrir otra fototeca."""
        self._cache = (
            ThumbnailCache(cache_dir, self._target)
            if cache_dir is not None
            else None
        )

    def load_visible(self, items: Iterable[tuple[str, Path, str | None]]) -> None:
        """Sustituye la cola pendiente por las miniaturas visibles actuales."""
        self._pending.clear()
        self._order.clear()
        for key, path, photo_hash in items:
            if key in self._done or key in self._failed or key in self._running:
                continue
            if key in self._pending:
                continue
            self._pending[key] = (path, photo_hash)
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
            entry = self._pending.pop(key, None)
            if entry is None or key in self._done or key in self._failed:
                continue
            if key in self._running:
                continue
            path, photo_hash = entry
            self._running.add(key)
            task = ThumbnailTask(
                self._generation,
                key,
                path,
                self._target,
                photo_hash=photo_hash,
                cache=self._cache,
            )
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
