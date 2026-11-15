"""Recorrido de la biblioteca con detección de altas y errores."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import Literal

from foto.core.errors import LibraryError
from foto.core.library import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

#: Sufijo del paquete de fototeca de Apple Photos (ADR 0009).
APPLE_LIBRARY_SUFFIX = ".photoslibrary"

#: Patrón de las variantes editadas ``<UUID>_<n>`` que el ADR 0009 excluye
#: de ``originals/`` como defensa frente a otras versiones del modelo de Apple.
APPLE_VARIANT_PATTERN: Pattern[str] = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12}_[0-9]+$"
)


def is_apple_photos_library(root: Path) -> bool:
    """Detecta un paquete de fototeca de Apple Photos por su sufijo.

    Único sitio de la detección (ADR 0009, § 2): lo consumen la política de
    escaneo, el resolvedor de metainformación y la CLI. Sin mayúsculas
    distinguidas, como el propio Finder.
    """
    return root.name.lower().endswith(APPLE_LIBRARY_SUFFIX)


@dataclass(frozen=True)
class ScanPolicy:
    """Política declarativa de escaneo (ADR 0009, § 1).

    Sin política (``None`` en ``LibraryScanner``) el recorrido es el de
    siempre sobre la raíz entera. Con política: ``include_roots`` restringe
    el recorrido a raíces relativas (vacío = raíz entera);
    ``excluded_dir_names`` excluye directorios por nombre dentro de lo
    incluido; ``excluded_file_patterns`` excluye ficheros por patrón del
    nombre base (sin extensión); ``extensions`` sustituye a las extensiones
    del scanner (``None`` = las del scanner).
    """

    include_roots: tuple[str, ...] = ()
    excluded_dir_names: frozenset[str] = frozenset()
    excluded_file_patterns: tuple[Pattern[str], ...] = ()
    extensions: frozenset[str] | None = None


def apple_photos_scan_policy() -> ScanPolicy:
    """Política de escaneo de una fototeca de Apple Photos (ADR 0009, § 1).

    Solo ``originals/``; dentro, se excluyen las variantes editadas
    ``<UUID>_<n>`` para preservar el invariante «un hash = una foto». Las
    extensiones quedan como las del scanner (sin vídeo, ADR 0009, § 5).
    """
    return ScanPolicy(
        include_roots=("originals",),
        excluded_file_patterns=(APPLE_VARIANT_PATTERN,),
    )


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


def _directory_error_reason(root: Path, rel_dir: str, exc: OSError) -> str:
    """Motivo de un fallo de listado, con causa explícita si es TCC (§ 3).

    Un permiso denegado sobre un paquete Apple es siempre *Full Disk
    Access*: el mensaje lo dice, con la acción sugerida, en lugar del
    «Operation not permitted» anónimo.
    """
    if isinstance(exc, PermissionError) and is_apple_photos_library(root):
        target = "el paquete de la fototeca" if not rel_dir else f"«{rel_dir}» del paquete"
        return (
            f"Acceso denegado por macOS (TCC) al listar {target}: concede "
            "«Acceso total al disco» (Full Disk Access) al proceso para "
            "indexar una fototeca de Apple Photos"
        )
    return _reason(exc)


def _excluded_by_policy(policy: ScanPolicy, name: str) -> bool:
    """¿Excluye la política el fichero por patrón de nombre base?"""
    stem = Path(name).stem
    return any(pattern.match(stem) for pattern in policy.excluded_file_patterns)


class LibraryScanner:
    """Recorre la raíz sin seguir symlinks e ignorando ``.foto/``.

    Con una ``ScanPolicy`` (ADR 0009) el recorrido se restringe a las raíces
    relativas incluidas y se aplican sus exclusiones; sin política el
    comportamiento es el de siempre, byte a byte.
    """

    def __init__(
        self,
        root: Path,
        extensions: Iterable[str] = IMAGE_EXTENSIONS,
        *,
        policy: ScanPolicy | None = None,
    ) -> None:
        self.root = root
        self.policy = policy
        if policy is not None and policy.extensions is not None:
            extensions = policy.extensions
        self.extensions = {extension.lower() for extension in extensions}
        self.complete = True
        self.directory_errors = 0
        self.file_errors = 0

    def _initial_pending(self) -> list[tuple[Path, str]]:
        """Directorios de arranque del recorrido según la política.

        Una raíz relativa incluida que no existe es un error explícito
        (paquete anómalo), nunca un escaneo vacío silencioso. Si no se puede
        saber por un error de acceso (p. ej. TCC), el error lo reportará el
        ``scandir`` del recorrido con su causa (recorrido parcial).
        """
        policy = self.policy
        if policy is None or not policy.include_roots:
            return [(self.root, "")]
        pending: list[tuple[Path, str]] = []
        for relative_root in policy.include_roots:
            directory = self.root / relative_root
            try:
                missing = not directory.is_dir()
            except OSError:
                missing = False  # el scandir del recorrido reportará la causa
            if missing:
                raise LibraryError(
                    "La política de escaneo incluye la raíz relativa "
                    f"«{relative_root}» y no existe o no es un directorio "
                    "en la raíz de la biblioteca"
                )
            pending.append((directory, relative_root))
        return pending

    def scan(self) -> Iterator[ScanEvent]:
        """Genera ficheros e incidencias en orden de recorrido.

        ``complete`` solo permanece verdadero si se pudieron listar todos los
        directorios. Un error de listado impide reconciliar ausencias.
        """
        pending: list[tuple[Path, str]] = self._initial_pending()
        policy = self.policy
        while pending:
            directory, rel_dir = pending.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = list(iterator)
            except OSError as exc:
                self.complete = False
                self.directory_errors += 1
                rel_path = rel_dir or "."
                reason = _directory_error_reason(self.root, rel_dir, exc)
                logger.warning(
                    "No se pudo listar el directorio %s: %s", rel_path, reason
                )
                yield ScanIssue(rel_path, reason, "directory")
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
                        if (
                            policy is not None
                            and entry.name in policy.excluded_dir_names
                        ):
                            logger.debug(
                                "Se ignora el directorio excluido por la "
                                "política %s",
                                rel_path,
                            )
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
                if policy is not None and _excluded_by_policy(policy, entry.name):
                    logger.debug(
                        "Se ignora el fichero excluido por la política %s", rel_path
                    )
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
