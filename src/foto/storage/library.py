"""Descriptor de biblioteca: ``<fototeca>/.foto/library.toml``."""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore

from foto.core.errors import LibraryError

LIBRARY_VERSION = 1
FOTO_DIR_NAME = ".foto"
LIBRARY_FILE_NAME = "library.toml"
THUMBS_DIR_NAME = "thumbs"
CREATION_LOCK_NAME = ".library.toml.creating"
_CREATION_LOCK_POLL = 0.02
_CREATION_LOCK_ATTEMPTS = 30
_CREATION_LOCK_STALE_SECONDS = 30.0


@dataclass(frozen=True)
class LibraryConfig:
    """Contenido validado de ``library.toml``."""

    version: int
    id: str


@dataclass(frozen=True)
class LibraryBootstrap:
    """Resultado de asegurar el descriptor de la biblioteca."""

    foto_dir: Path
    config: LibraryConfig
    created: bool
    would_create: bool = False


def foto_dir(root: Path) -> Path:
    """Directorio derivado de la biblioteca."""
    return root / FOTO_DIR_NAME


def library_file(root: Path) -> Path:
    """Ruta del descriptor de la biblioteca."""
    return foto_dir(root) / LIBRARY_FILE_NAME


def thumbs_dir(root: Path) -> Path:
    """Directorio de la caché persistente de miniaturas."""
    return foto_dir(root) / THUMBS_DIR_NAME


def load_library(root: Path) -> LibraryConfig:
    """Carga y valida el descriptor existente."""
    path = library_file(root)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo leer library.toml: {reason}") from exc
    except Exception as exc:
        if tomllib is not None and isinstance(exc, tomllib.TOMLDecodeError):
            raise LibraryError("library.toml no es un TOML válido") from exc
        if tomllib is None:
            raise LibraryError("No hay soporte TOML disponible") from exc
        raise LibraryError("library.toml no es un TOML válido") from exc

    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise LibraryError("library.toml no contiene un campo 'version' entero")
    if version > LIBRARY_VERSION:
        raise LibraryError(
            f"library.toml usa la versión {version}, más nueva que la soportada "
            f"({LIBRARY_VERSION})"
        )
    if version < 1:
        raise LibraryError(f"library.toml usa una versión no válida: {version}")

    identifier = data.get("id")
    if not isinstance(identifier, str):
        raise LibraryError("library.toml no contiene un campo 'id' de texto")
    try:
        canonical_id = str(uuid.UUID(identifier))
    except ValueError as exc:
        raise LibraryError("library.toml contiene un 'id' que no es un UUID") from exc

    return LibraryConfig(version=version, id=canonical_id)


def _serialized(config: LibraryConfig) -> str:
    return f"version = {config.version}\nid = \"{config.id}\"\n"


def _write_content_temporary(directory: Path, content: str) -> Path:
    """Escribe el contenido completo en un temporal del directorio y lo devuelve.

    El destino nunca contiene contenido parcial: o está ausente, o está
    publicado de una pieza mediante un ``os.replace``/``os.link``.
    """
    descriptor, temporary = tempfile.mkstemp(
        dir=directory, prefix=".library.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary_path


def _publish(temporary_path: Path, path: Path) -> bool:
    """Publica el temporal completo en ``path`` sin sobrescribir nada.

    Debe llamarse con el lock de creación adquirido. Devuelve ``False`` si
    ``path`` ya existía (el descriptor de otro creador se respeta siempre).
    """
    if path.exists():
        return False
    try:
        os.link(temporary_path, path)
    except FileExistsError:
        return False
    except OSError:
        # exFAT, SMB y otros sistemas no admiten hardlinks. El temporal ya
        # está completo y sincronizado en disco, así que la publicación
        # sigue siendo atómica con un rename dentro del mismo directorio.
        os.replace(temporary_path, path)
    return True


def _creation_lock(descriptor: Path) -> Path:
    return descriptor.parent / CREATION_LOCK_NAME


def _creation_lock_is_stale(lock_path: Path) -> bool:
    try:
        return time.time() - lock_path.stat().st_mtime > _CREATION_LOCK_STALE_SECONDS
    except OSError:
        return False


def _create_descriptor(descriptor: Path, config: LibraryConfig) -> bool:
    """Crea el descriptor de forma atómica, con exclusión entre creadores.

    Devuelve ``True`` si este proceso lo publicó y ``False`` si otro se
    adelantó (y su descriptor debe adoptarse).
    """
    lock_path = _creation_lock(descriptor)
    for _ in range(_CREATION_LOCK_ATTEMPTS):
        try:
            reserved = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _creation_lock_is_stale(lock_path):
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            time.sleep(_CREATION_LOCK_POLL)
            continue
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise LibraryError(f"No se pudo crear library.toml: {reason}") from exc

        os.close(reserved)
        temporary_path: Path | None = None
        try:
            temporary_path = _write_content_temporary(descriptor.parent, _serialized(config))
            try:
                return _publish(temporary_path, descriptor)
            except OSError as exc:
                reason = exc.strerror or type(exc).__name__
                raise LibraryError(f"No se pudo crear library.toml: {reason}") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    raise LibraryError("Otro proceso está creando library.toml en esta biblioteca")



def ensure_library(root: Path, dry_run: bool = False) -> LibraryBootstrap:
    """Asegura ``.foto/library.toml`` de forma atómica e idempotente."""
    if not root.is_dir():
        raise LibraryError("La raíz de la biblioteca no existe o no es un directorio")

    derived_dir = foto_dir(root)
    descriptor = library_file(root)

    if descriptor.exists():
        return LibraryBootstrap(
            foto_dir=derived_dir,
            config=load_library(root),
            created=False,
        )

    config = LibraryConfig(version=LIBRARY_VERSION, id=str(uuid.uuid4()))
    if dry_run:
        return LibraryBootstrap(
            foto_dir=derived_dir,
            config=config,
            created=False,
            would_create=True,
        )

    if derived_dir.exists() and not derived_dir.is_dir():
        raise LibraryError(".foto existe y no es un directorio")

    try:
        derived_dir.mkdir(exist_ok=True)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo crear .foto: {reason}") from exc

    try:
        created = _create_descriptor(descriptor, config)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo crear library.toml: {reason}") from exc

    if not created:
        # Otro proceso se adelantó (o su descriptor ya existía): se usa su
        # descriptor, nunca se sobrescribe.
        return LibraryBootstrap(
            foto_dir=derived_dir,
            config=load_library(root),
            created=False,
        )

    return LibraryBootstrap(foto_dir=derived_dir, config=config, created=True)
