"""Carga y escritura de configuración."""

from __future__ import annotations

import logging
import os
import re
import tempfile

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore
from dataclasses import dataclass
from pathlib import Path

import platformdirs

VERSION_KEY = "version"
LIBRARY_SECTION = "library"
PATH_KEY = "path"
LATEST_CONFIG_VERSION = 1


@dataclass(frozen=True)
class AppConfig:
    """Configuración global de la aplicación."""

    config_path: Path
    library_path: Path | None = None
    version: int = 1


class ConfigError(Exception):
    """Error de configuración."""


def get_default_config_path() -> Path:
    """Ruta por defecto del fichero de configuración."""
    config_dir = Path(platformdirs.user_config_dir("foto"))
    return config_dir / "config.toml"


def resolve_config_path(config_path: Path | None = None) -> Path:
    """Resuelve la ruta del fichero de configuración con precedencia."""
    if config_path is not None:
        return Path(config_path).expanduser().resolve()

    env_config = os.environ.get("FOTO_CONFIG")
    if env_config:
        return Path(env_config).expanduser().resolve()

    return get_default_config_path().expanduser().resolve()


def _expand_path(value: str, base_dir: Path) -> Path:
    """Expande ruta relativa respecto al directorio base."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


# Política de versión (ver D03 del informe): un fichero sin ``version`` se
# trata como v1, la única versión soportada por ahora.
def _validate_version(data: dict[str, object]) -> int:
    """Valida ``version`` y devuelve la versión como entero."""
    if VERSION_KEY not in data:
        return 1

    version = data[VERSION_KEY]
    # bool es instancia de int en Python: se rechaza con comprobación exacta.
    if type(version) is not int:
        raise ConfigError(f"El campo '{VERSION_KEY}' debe ser un entero")
    if version < 1:
        raise ConfigError(f"La versión de la configuración no es válida: {version}")
    if version > LATEST_CONFIG_VERSION:
        raise ConfigError(
            f"La configuración usa la versión {version}, más nueva que la "
            f"soportada ({LATEST_CONFIG_VERSION}). Actualiza foto."
        )

    return version


def _validate_library_section(data: dict[str, object], base_dir: Path) -> Path | None:
    """Valida ``[library]`` y devuelve la ruta expandida, o ``None`` si no está."""
    if LIBRARY_SECTION not in data:
        return None

    section = data[LIBRARY_SECTION]
    if not isinstance(section, dict):
        raise ConfigError(f"La sección '[{LIBRARY_SECTION}]' debe ser una tabla TOML")

    if PATH_KEY not in section:
        return None

    path_value = section[PATH_KEY]
    if not isinstance(path_value, str):
        raise ConfigError(f"El campo '[{LIBRARY_SECTION}].{PATH_KEY}' debe ser texto")

    return _expand_path(path_value, base_dir)


def load_config(config_path: Path | None = None) -> AppConfig:
    """Carga la configuración global, validando esquema y versión."""
    resolved = resolve_config_path(config_path)
    if not resolved.exists():
        return AppConfig(config_path=resolved, library_path=None, version=1)

    data = _read_toml_file(resolved)
    version = _validate_version(data)
    library_path = _validate_library_section(data, resolved.parent)

    return AppConfig(config_path=resolved, library_path=library_path, version=version)


def _read_toml_file(path: Path) -> dict[str, object]:
    """Lee un fichero TOML; un contenido inválido produce ``ConfigError``."""
    try:
        if tomllib is None:
            raise ConfigError("No hay soporte para leer TOML")
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"No se pudo leer la configuración: {exc}") from exc


# ----------------------------------------------------------------------
# Escritura conservadora (C01) y serialización TOML (C06)
# ----------------------------------------------------------------------
def _escape_toml_string(value: str) -> str:
    """Devuelve ``value`` como literal TOML básico, escapado y entre comillas."""
    escapes = {
        "\\": "\\\\",
        '"': '\\"',
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    pieces = []
    for char in value:
        escape = escapes.get(char)
        if escape is not None:
            pieces.append(escape)
        elif char < " " or char == "\x7f":
            pieces.append(f"\\u{ord(char):04x}")
        else:
            pieces.append(char)
    return '"' + "".join(pieces) + '"'


def _render_canonical(version: int, library_path: Path | None) -> str:
    """Contenido completo para un fichero que aún no existe."""
    lines = [f"{VERSION_KEY} = {version}", "", f"[{LIBRARY_SECTION}]"]
    if library_path is not None:
        lines.append(f"{PATH_KEY} = {_escape_toml_string(str(library_path))}")
    else:
        lines.append(f'# {PATH_KEY} = "/ruta/a/fototeca"')
    return "\n".join(lines) + "\n"


def _table_name(line: str) -> str | None:
    """Nombre de la tabla declarada en la línea, o ``None`` si no es cabecera.

    Las subtablas (``[library.x]``), los arrays de tablas y las cabeceras con
    claves entre comillas devuelven cadena vacía: no son ``[library]``.
    """
    head = line.strip().partition("#")[0].strip()
    if not (head.startswith("[") and head.endswith("]")):
        return None
    inner = head.lstrip("[").rstrip("]").strip()
    if not inner or '"' in inner or "'" in inner or "." in inner:
        return ""
    return inner


def _path_line_details(line: str) -> tuple[str, str] | None:
    """Fin de línea y comentario final de una línea ``path``, si es esa clave."""
    stripped = line.strip()
    if not re.match(r"^path\s*=", stripped):
        return None

    remainder = re.sub(r"^\s*path\s*=\s*", "", stripped, count=1)
    if remainder.startswith('"'):
        match = re.match(r'"(?:[^"\\]|\\.)*"', remainder)
        end_of_value = match.end() if match else 0
    elif remainder.startswith("'"):
        end_of_value = remainder.find("'", 1) + 1
    else:
        end_of_value = 0

    if end_of_value <= 0:
        raise ConfigError(f"El campo '[{LIBRARY_SECTION}].{PATH_KEY}' debe ser texto")

    tail = remainder[end_of_value:].strip()
    if tail and not tail.startswith("#"):
        raise ConfigError("No se pudo actualizar la configuración: la clave "
                          f"'{PATH_KEY}' tiene un formato inesperado")

    eol = ""
    if line.endswith("\r\n"):
        eol = "\r\n"
    elif line.endswith("\n"):
        eol = "\n"
    return eol, (f"  {tail}" if tail else "")


def _update_library_path(text: str, library_path: Path | None) -> str:
    """Reescribe solo ``[library].path`` y conserva el resto del contenido.

    Comentarios, claves desconocidas, secciones desconocidas y el formato del
    resto del fichero no se tocan. Si la sección no existe, se añade al final;
    si existe sin ``path``, la clave se inserta tras la cabecera.
    """
    if library_path is not None:
        active_value = _escape_toml_string(str(library_path))
    else:
        active_value = ""

    def new_line(eol: str = "\n", trailing: str = "") -> str:
        if library_path is not None:
            return f"{PATH_KEY} = {active_value}{trailing}{eol}"
        return f"# {PATH_KEY} = \"/ruta/a/fototeca\"{eol}"

    lines = text.splitlines(keepends=True)
    if not lines:
        return _render_canonical(1, library_path)

    result: list[str] = []
    section_found = False
    in_library = False
    replaced = False
    insert_at: int | None = None
    header_ends_open = False

    for line in lines:
        name = _table_name(line)
        if name is not None:
            in_library = name == LIBRARY_SECTION
            if in_library:
                if section_found:
                    raise ConfigError(
                        "No se pudo actualizar la configuración: la sección "
                        f"'[{LIBRARY_SECTION}]' está duplicada"
                    )
                section_found = True
                insert_at = len(result) + 1
                header_ends_open = not line.endswith("\n")
            result.append(line)
            continue

        if in_library and not replaced:
            details = _path_line_details(line)
            if details is not None:
                line_eol, trailing = details
                if library_path is not None:
                    result.append(f"{PATH_KEY} = {active_value}{trailing}{line_eol}")
                else:
                    result.append(f"# {PATH_KEY} = \"/ruta/a/fototeca\"{line_eol}")
                replaced = True
                continue

        result.append(line)

    if section_found and not replaced:
        if insert_at is None:  # pragma: no cover - invariante de estructura
            raise ConfigError("No se pudo actualizar la configuración")
        body = new_line()
        if header_ends_open:
            body = "\n" + body
        result.insert(insert_at, body)
    elif not section_found:
        if not result[-1].endswith("\n"):
            result.append("\n")
        result.append("\n")
        result.append(f"[{LIBRARY_SECTION}]\n")
        result.append(new_line())

    return "".join(result)


def _write_text_atomic(path: Path, content: str) -> None:
    """Persiste ``content`` con un temporal del mismo directorio y ``os.replace``."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".tmp")
    tmp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).debug(
                "No se pudo limpiar el temporal", exc_info=True
            )


def save_config(config: AppConfig) -> None:
    """Guarda la configuración global de forma atómica y conservadora.

    Si el fichero ya existe, solo se actualiza ``[library].path``: comentarios,
    claves y secciones desconocidas se conservan tal cual (C01). Un fichero
    existente inválido o con versión incompatible produce ``ConfigError``
    en lugar de sobrescribirse silenciosamente (C02).
    """
    path = config.config_path
    path.parent.mkdir(parents=True, exist_ok=True)

    canonical = _render_canonical(config.version, config.library_path)
    content = canonical

    try:
        text = path.read_text(encoding="utf-8") if path.exists() else None
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise ConfigError(f"No se pudo leer la configuración: {reason}") from exc

    if text is not None and text.strip():
        # Validar el contenido existente antes de tocarlo.
        data = _read_toml_file(path)
        _validate_version(data)
        _validate_library_section(data, path.parent)

        content = _update_library_path(text, config.library_path)

        # Defensa adicional (C06): el resultado debe seguir siendo TOML válido.
        try:
            if tomllib is None:
                raise ConfigError("No hay soporte para escribir TOML")
            tomllib.loads(content)
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(
                f"No se pudo actualizar la configuración: {exc}"
            ) from exc

    _write_text_atomic(path, content)
