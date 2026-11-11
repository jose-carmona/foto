"""Etiquetas del catálogo: normalización, lectura y escritura (ADR 0006).

El catálogo SQLite es la fuente de verdad de las etiquetas y los favoritos.
Módulo sin Qt: la ventana consume sus funciones y nunca ejecuta SQL. Las
asignaciones están ligadas al hash de contenido y nunca se trasladan entre
hashes; ``fav`` es la única etiqueta de sistema y se resuelve siempre por
``kind='system'``, jamás por ``id`` (los ``AUTOINCREMENT`` varían entre
bibliotecas).

La unicidad la garantiza el esquema (``INSERT OR IGNORE``/``UPDATE OR
IGNORE``), no el código. Las operaciones de escritura son transacciones
mínimas con commit inmediato, para no retrasar los checkpoints ni bloquear
los lotes del indexador más de lo inevitable.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from foto.core.errors import CatalogError, TagError

#: Nombre de la etiqueta de sistema del favorito.
FAVORITE_TAG = "fav"

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Tag:
    """Etiqueta existente en el catálogo."""

    id: int
    name: str
    kind: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TagAssignment:
    """Asignación foto–etiqueta tal como viaja a la UI.

    La región no se carga todavía: solo queda definida en el esquema
    (ADR 0006, Decisión 7); el visor no pinta regiones en esta entrega.
    """

    photo_hash: str
    tag_id: int
    tag_name: str
    kind: str
    source: str
    status: str


# ----------------------------------------------------------------------
# Normalización (ADR 0006, Decisión 5)
# ----------------------------------------------------------------------
def normalize_tag_name(name: str) -> str:
    """Normaliza un nombre de etiqueta: NFC + casefold + espacios + strip.

    Sin plegar acentos: ``año 2024`` y ``AÑO 2024`` son la misma etiqueta,
    pero ``año 2024`` y ``anio 2024`` no (el plegado fusionaría personas
    distintas; ``ñ`` es letra). Rechaza el vacío y el solo espacios.
    """
    normalized = unicodedata.normalize("NFC", name).casefold()
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    if not normalized:
        raise TagError("El nombre de la etiqueta no puede quedar vacío")
    return normalized


# ----------------------------------------------------------------------
# Lectura (conexión de solo lectura del visor o cualquier otra)
# ----------------------------------------------------------------------
def list_tags(connection: sqlite3.Connection) -> list[Tag]:
    """Devuelve todas las etiquetas, por orden de creación."""
    rows = connection.execute(
        "SELECT id, name, kind, created_at, updated_at FROM tags ORDER BY id"
    ).fetchall()
    return [_tag_from_row(row) for row in rows]


def find_tag_by_name(connection: sqlite3.Connection, name: str) -> Tag | None:
    """Resuelve una etiqueta por su nombre normalizado, o ``None``."""
    normalized = normalize_tag_name(name)
    row = connection.execute(
        "SELECT id, name, kind, created_at, updated_at FROM tags "
        "WHERE name_normalized = ?",
        (normalized,),
    ).fetchone()
    return _tag_from_row(row) if row is not None else None


def favorite_tag(connection: sqlite3.Connection) -> Tag:
    """Devuelve la etiqueta de sistema ``fav``, resuelta por ``kind``.

    Jamás por ``id``. La migración la garantiza; su ausencia es un error
    del catálogo, no un estado normal.
    """
    row = connection.execute(
        "SELECT id, name, kind, created_at, updated_at FROM tags "
        "WHERE kind = 'system'"
    ).fetchone()
    if row is None:
        raise TagError("El catálogo no tiene la etiqueta de sistema 'fav'")
    return _tag_from_row(row)


def assignments_by_hash(
    connection: sqlite3.Connection,
) -> dict[str, tuple[TagAssignment, ...]]:
    """Asignaciones por hash, para cargar en memoria al arrancar.

    Una entrada por hash con asignaciones; los hashes sin asignaciones no
    aparecen. Contar ``confirmed`` + ``suggested`` (jamás ``rejected``) es
    regla de presentación, no de la consulta.
    """
    rows = connection.execute(
        "SELECT pt.photo_hash, pt.tag_id, t.name, t.kind, pt.source, pt.status "
        "FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id "
        "ORDER BY pt.photo_hash, t.name"
    ).fetchall()
    grouped: dict[str, list[TagAssignment]] = {}
    for row in rows:
        photo_hash = str(row["photo_hash"])
        grouped.setdefault(photo_hash, []).append(
            TagAssignment(
                photo_hash=photo_hash,
                tag_id=int(row["tag_id"]),
                tag_name=str(row["name"]),
                kind=str(row["kind"]),
                source=str(row["source"]),
                status=str(row["status"]),
            )
        )
    return {photo_hash: tuple(items) for photo_hash, items in grouped.items()}


def _tag_from_row(row: sqlite3.Row) -> Tag:
    return Tag(
        id=int(row["id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


# ----------------------------------------------------------------------
# Escritura (conexión RW dedicada y acotada del visor, ADR 0006 Decisión 10)
# ----------------------------------------------------------------------
def connect_read_write(path: Path) -> sqlite3.Connection:
    """Abre la conexión RW dedicada del visor, acotada a ``tags``/``photo_tags``.

    No toma ``index.lock``: WAL y ``busy_timeout`` (5 s) serializan con los
    lotes del indexador. Nunca crea el fichero: el visor exige un catálogo
    ya migrado por ``foto-index``.
    """
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=rw", uri=True, timeout=5.0
        )
    except sqlite3.Error as exc:
        raise CatalogError(f"No se pudo abrir el catálogo: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error as exc:
        connection.close()
        raise CatalogError(f"No se pudo configurar SQLite: {exc}") from exc
    return connection


def assign_tag(
    connection: sqlite3.Connection,
    photo_hash: str,
    tag: Tag,
    *,
    now: str,
) -> None:
    """Asigna la etiqueta a la foto completa (región NULL).

    La fila existente es el estado: confirmar una sugerencia actualiza la
    misma fila sin pisar ``source`` (queda el origen: sugerida por ML,
    confirmada por humano) ni ``created_at`` (cuándo se sugirió). Una fila
    ``rejected`` no se revive: reactivarla es borrarla, decisión explícita
    del usuario.
    """
    _assert_not_rejected(connection, photo_hash, tag.id)
    try:
        cursor = connection.execute(
            "UPDATE OR IGNORE photo_tags SET status = 'confirmed', "
            "updated_at = ? WHERE photo_hash = ? AND tag_id = ? "
            "AND region_x IS NULL AND region_y IS NULL "
            "AND region_w IS NULL AND region_h IS NULL",
            (now, photo_hash, tag.id),
        )
        if cursor.rowcount == 0:
            connection.execute(
                "INSERT OR IGNORE INTO photo_tags "
                "(photo_hash, tag_id, source, status, created_at, updated_at) "
                "VALUES (?, ?, 'manual', 'confirmed', ?, ?)",
                (photo_hash, tag.id, now, now),
            )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise CatalogError(f"No se pudo escribir la asignación: {exc}") from exc


def unassign_tag(
    connection: sqlite3.Connection,
    photo_hash: str,
    tag: Tag,
) -> bool:
    """Quita la asignación completa (región NULL); devuelve si existía."""
    try:
        cursor = connection.execute(
            "DELETE FROM photo_tags WHERE photo_hash = ? AND tag_id = ? "
            "AND region_x IS NULL AND region_y IS NULL "
            "AND region_w IS NULL AND region_h IS NULL",
            (photo_hash, tag.id),
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise CatalogError(f"No se pudo quitar la asignación: {exc}") from exc
    return cursor.rowcount > 0


def toggle_favorite(
    connection: sqlite3.Connection,
    photo_hash: str,
    *,
    now: str,
) -> bool:
    """Conmuta la asignación de ``fav`` en la foto; devuelve el nuevo estado.

    ``fav`` se resuelve por ``kind='system'``, jamás por ``id``. El
    conmutado borra la asignación completa (región NULL) sea cual sea su
    estado y, si no existía, la crea ``manual``/``confirmed`` en una única
    transacción.
    """
    fav = favorite_tag(connection)
    try:
        cursor = connection.execute(
            "DELETE FROM photo_tags WHERE photo_hash = ? AND tag_id = ? "
            "AND region_x IS NULL AND region_y IS NULL "
            "AND region_w IS NULL AND region_h IS NULL",
            (photo_hash, fav.id),
        )
        if cursor.rowcount == 0:
            connection.execute(
                "INSERT OR IGNORE INTO photo_tags "
                "(photo_hash, tag_id, source, status, created_at, updated_at) "
                "VALUES (?, ?, 'manual', 'confirmed', ?, ?)",
                (photo_hash, fav.id, now, now),
            )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise CatalogError(f"No se pudo conmutar el favorito: {exc}") from exc
    return cursor.rowcount == 0


def _assert_not_rejected(
    connection: sqlite3.Connection, photo_hash: str, tag_id: int
) -> None:
    """Bloquea asignar sobre una fila ``rejected`` de foto completa."""
    row = connection.execute(
        "SELECT status FROM photo_tags WHERE photo_hash = ? AND tag_id = ? "
        "AND region_x IS NULL AND region_y IS NULL "
        "AND region_w IS NULL AND region_h IS NULL",
        (photo_hash, tag_id),
    ).fetchone()
    if row is not None and str(row["status"]) == "rejected":
        raise TagError(
            "La etiqueta fue rechazada en esa foto: borrar la fila "
            "'rejected' la reactiva"
        )
