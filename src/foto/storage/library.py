"""Descriptor de biblioteca: ``library.toml`` en el directorio de metadatos.

Con el ADR 0008 el directorio de metadatos puede vivir fuera de la fototeca
(``--metadata``/``FOTO_METADATA``/``[metadata].path``); los helpers operan
sobre el directorio resuelto y ``foto_dir(root)`` queda como derivación por
defecto del nivel 4 (``<fototeca>/.foto``).
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
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
ROOT_KEY = "root"
CREATION_LOCK_NAME = ".library.toml.creating"
_CREATION_LOCK_POLL = 0.02
_CREATION_LOCK_ATTEMPTS = 30
_CREATION_LOCK_STALE_SECONDS = 30.0


@dataclass(frozen=True)
class LibraryConfig:
    """Contenido validado de ``library.toml``.

    ``root`` (ADR 0008) es la fototeca a la que pertenece el descriptor:
    opcional, presente en los creados por la app y en los adoptados; su
    no coincidencia con la raíz resuelta es un error explícito.
    """

    version: int
    id: str
    root: Path | None = None


@dataclass(frozen=True)
class LibraryBootstrap:
    """Resultado de asegurar el descriptor de la biblioteca."""

    metadata_dir: Path
    config: LibraryConfig
    created: bool
    would_create: bool = False


def foto_dir(root: Path) -> Path:
    """Directorio de metadatos derivado de la biblioteca (nivel 4 por defecto)."""
    return root / FOTO_DIR_NAME


def library_file(metadata_dir: Path) -> Path:
    """Ruta del descriptor dentro del directorio de metadatos resuelto."""
    return metadata_dir / LIBRARY_FILE_NAME


def thumbs_dir(metadata_dir: Path) -> Path:
    """Directorio de la caché de miniaturas en el directorio de metadatos."""
    return metadata_dir / THUMBS_DIR_NAME


def _descriptor_root(data: dict[str, object]) -> Path | None:
    """Valida el campo opcional ``root`` y lo devuelve como ruta absoluta."""
    value = data.get(ROOT_KEY)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LibraryError(
            f"library.toml contiene un campo '{ROOT_KEY}' que no es una ruta de texto"
        )
    return Path(value).expanduser().resolve()


def _check_root_coherence(descriptor_root: Path | None, root: Path | None) -> None:
    """Compara el ``root`` del descriptor con la raíz resuelta (ADR 0008).

    La garantía física del emplazamiento se pierde con metainformación
    externa: la coherencia lógica se valida al abrir, nunca se mezclan
    catálogos en silencio.
    """
    if descriptor_root is None or root is None:
        return
    if descriptor_root != root:
        raise LibraryError(
            f"library.toml pertenece a la fototeca {descriptor_root}, no a "
            f"{root}. Relocaliza el descriptor a su fototeca o regenera la "
            "metainformación (por ejemplo, copiando .foto o volviendo a "
            "indexar en un directorio vacío)."
        )


def load_library(metadata_dir: Path, root: Path | None = None) -> LibraryConfig:
    """Carga y valida el descriptor existente en el directorio de metadatos.

    Si se pasa ``root`` (la raíz resuelta de la fototeca) y el descriptor
    declara un ``root`` distinto, produce ``LibraryError`` con la acción
    sugerida (ADR 0008). Descriptores sin ``root`` se siguen aceptando.
    """
    path = metadata_dir / LIBRARY_FILE_NAME
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

    descriptor_root = _descriptor_root(data)
    _check_root_coherence(descriptor_root, root)

    return LibraryConfig(version=version, id=canonical_id, root=descriptor_root)


def _serialized(config: LibraryConfig) -> str:
    lines = [f"version = {config.version}", f"id = \"{config.id}\""]
    if config.root is not None:
        lines.append(f"{ROOT_KEY} = \"{config.root}\"")
    return "\n".join(lines) + "\n"


def _insert_root_line(text: str, root: Path) -> str:
    """Devuelve el texto del descriptor con la línea ``root`` añadida.

    Conservador byte a byte salvo la línea añadida: se inserta tras la del
    ``id`` (o al final, si no se localiza) sin tocar nada más.
    """
    new_line = f"{ROOT_KEY} = \"{root}\"\n"
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r"^\s*id\s*=", line):
            # Si el id está en la última línea sin salto final, lo completamos:
            # es la única modificación necesaria para poder añadir la nuestra.
            if not line.endswith("\n"):
                lines[index] = line + "\n"
            lines.insert(index + 1, new_line)
            return "".join(lines)

    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    lines.append(new_line)
    return "".join(lines)


def _adopt_root(descriptor: Path, root: Path) -> None:
    """Añade ``root`` a un descriptor legacy con escritura atómica."""
    try:
        text = descriptor.read_text(encoding="utf-8")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo leer library.toml: {reason}") from exc

    temporary_path = _write_content_temporary(descriptor.parent, _insert_root_line(text, root))
    try:
        os.replace(temporary_path, descriptor)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo actualizar library.toml: {reason}") from exc
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


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



def ensure_library(
    root: Path,
    metadata_dir: Path | None = None,
    *,
    dry_run: bool = False,
) -> LibraryBootstrap:
    """Asegura ``library.toml`` de forma atómica e idempotente.

    Opera sobre el directorio de metadatos resuelto; ``None`` deriva
    ``<fototeca>/.foto`` de la raíz (nivel 4, compatibilidad con el
    comportamiento anterior). Al crear y al adoptar un descriptor legacy,
    ``library.toml`` nace/gana el campo ``root`` con la raíz resuelta; un
    descriptor con ``root`` distinto produce ``LibraryError`` (ADR 0008).
    """
    if not root.is_dir():
        raise LibraryError("La raíz de la biblioteca no existe o no es un directorio")

    resolved_dir = metadata_dir if metadata_dir is not None else foto_dir(root)
    descriptor = library_file(resolved_dir)

    if descriptor.exists():
        config = load_library(resolved_dir, root)
        if config.root is None and not dry_run:
            # Descriptor legacy sin root: se añade conservadoramente, sin
            # tocar el resto del contenido (ADR 0008, § 3.3).
            _adopt_root(descriptor, root)
            config = replace(config, root=root)
        return LibraryBootstrap(
            metadata_dir=resolved_dir,
            config=config,
            created=False,
        )

    config = LibraryConfig(version=LIBRARY_VERSION, id=str(uuid.uuid4()), root=root)
    if dry_run:
        return LibraryBootstrap(
            metadata_dir=resolved_dir,
            config=config,
            created=False,
            would_create=True,
        )

    if resolved_dir.exists() and not resolved_dir.is_dir():
        raise LibraryError(
            f"El directorio de metainformación existe y no es un directorio: {resolved_dir}"
        )

    try:
        resolved_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(
            f"No se pudo crear el directorio de metainformación: {reason}"
        ) from exc

    try:
        created = _create_descriptor(descriptor, config)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise LibraryError(f"No se pudo crear library.toml: {reason}") from exc

    if not created:
        # Otro proceso se adelantó (o su descriptor ya existía): se usa su
        # descriptor, nunca se sobrescribe.
        return LibraryBootstrap(
            metadata_dir=resolved_dir,
            config=load_library(resolved_dir, root),
            created=False,
        )

    return LibraryBootstrap(metadata_dir=resolved_dir, config=config, created=True)
