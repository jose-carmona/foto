"""Descriptor de biblioteca: ``<fototeca>/.foto/library.toml``."""

from __future__ import annotations

import os
import tempfile
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


def _serialize(config: LibraryConfig) -> str:
    return f"version = {config.version}\nid = \"{config.id}\"\n"


def _write_atomic_new(path: Path, content: str) -> bool:
    """Crea ``path`` sin sobrescribir; devuelve si lo creó este proceso."""
    directory = path.parent
    descriptor, temporary = tempfile.mkstemp(
        dir=directory, prefix=".library.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            return False
        except OSError:
            # exFAT, SMB y otros sistemas no admiten hardlinks. Se reserva el
            # nombre de forma exclusiva y se escribe el contenido ahí mismo.
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                return False
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return True


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
        created = _write_atomic_new(descriptor, _serialize(config))
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo crear library.toml: {reason}") from exc

    if not created:
        # Otro proceso se adelantó: se usa su descriptor, nunca se sobrescribe.
        return LibraryBootstrap(
            foto_dir=derived_dir,
            config=load_library(root),
            created=False,
        )

    return LibraryBootstrap(foto_dir=derived_dir, config=config, created=True)
