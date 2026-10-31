"""Lock exclusivo del indexador basado en ``fcntl.flock``."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType

try:
    from typing import Self
except ImportError:
    from typing import TypeVar

    _T = TypeVar("_T")
    Self = _T

from foto.core.errors import LockBusyError, LockError

LOCK_FILE_NAME = "index.lock"


class IndexerLock:
    """Lock de escritor único; mantiene el descriptor abierto."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise LockError(f"No se pudo abrir el lock: {reason}") from exc

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise LockBusyError("Otro proceso está indexando esta biblioteca") from exc
        except OSError as exc:
            os.close(descriptor)
            reason = exc.strerror or type(exc).__name__
            raise LockError(f"No se pudo adquirir el lock: {reason}") from exc

        self._descriptor = descriptor
        try:
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            # El contenido del lock es informativo: el flock ya está tomado.
            pass

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(descriptor)
        except OSError:
            pass

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
