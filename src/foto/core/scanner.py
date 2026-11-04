"""Recorrido de la biblioteca con detección de altas y errores."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from foto.core.library import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileSignature:
    """Firma barata de un fichero: evita leerlo si no ha cambiado."""

    size: int
    mtime_ns: int
    inode: int | None
    device: int | None


@dataclass(frozen=True)
class ScannedFile:
    """Imagen encontrada durante el recorrido."""

    rel_path: str
    path: Path
    signature: FileSignature


@dataclass(frozen=True)
class ScanIssue:
    """Incidencia al clasificar o leer una entrada del sistema de ficheros."""

    rel_path: str
    message: str
    kind: Literal["file", "directory"]


ScanEvent = ScannedFile | ScanIssue


def _reason(exc: OSError) -> str:
    """Mensaje de error sin rutas absolutas."""
    return exc.strerror or type(exc).__name__


class LibraryScanner:
    """Recorre la raíz sin seguir symlinks e ignorando ``.foto/``."""

    def __init__(
        self,
        root: Path,
        extensions: Iterable[str] = IMAGE_EXTENSIONS,
    ) -> None:
        self.root = root
        self.extensions = {extension.lower() for extension in extensions}
        self.complete = True
        self.directory_errors = 0
        self.file_errors = 0

    def scan(self) -> Iterator[ScanEvent]:
        """Genera ficheros e incidencias en orden de recorrido.

        ``complete`` solo permanece verdadero si se pudieron listar todos los
        directorios. Un error de listado impide reconciliar ausencias.
        """
        pending: list[tuple[Path, str]] = [(self.root, "")]
        while pending:
            directory, rel_dir = pending.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = list(iterator)
            except OSError as exc:
                self.complete = False
                self.directory_errors += 1
                rel_path = rel_dir or "."
                logger.warning(
                    "No se pudo listar el directorio %s: %s", rel_path, _reason(exc)
                )
                yield ScanIssue(rel_path, _reason(exc), "directory")
                continue

            for entry in entries:
                rel_path = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                try:
                    if entry.is_symlink():
                        logger.debug("Se ignora el symlink %s", rel_path)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name == ".foto":
                            continue
                        pending.append((Path(entry.path), rel_path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError as exc:
                    # No se puede saber si era un directorio: se bloquea la
                    # reconciliación para no marcar ausencias por error.
                    self.complete = False
                    self.directory_errors += 1
                    logger.warning(
                        "No se pudo clasificar la entrada %s: %s", rel_path, _reason(exc)
                    )
                    yield ScanIssue(rel_path, _reason(exc), "directory")
                    continue

                suffix = Path(entry.name).suffix.lower()
                if suffix not in self.extensions:
                    continue

                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    self.file_errors += 1
                    logger.warning("No se pudo leer el stat de %s: %s", rel_path, _reason(exc))
                    yield ScanIssue(rel_path, _reason(exc), "file")
                    continue

                signature = FileSignature(
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    inode=getattr(stat, "st_ino", None),
                    device=getattr(stat, "st_dev", None),
                )
                yield ScannedFile(rel_path=rel_path, path=Path(entry.path), signature=signature)
