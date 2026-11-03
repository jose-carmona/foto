"""Catálogo SQLite reconstruible de la fototeca."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from foto.core.errors import CatalogError, CatalogVersionError
from foto.core.metadata import PhotoMetadata

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CATALOG_FILE_NAME = "catalog.sqlite"

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS photos (
    hash                TEXT PRIMARY KEY,
    file_type           TEXT,
    extension           TEXT NOT NULL,
    width               INTEGER,
    height              INTEGER,
    captured_at         TEXT,
    camera_make         TEXT,
    camera_model        TEXT,
    lens_model          TEXT,
    iso                 INTEGER,
    f_number            REAL,
    exposure_time       REAL,
    focal_length        REAL,
    gps_latitude        REAL,
    gps_longitude       REAL,
    orientation         INTEGER,
    metadata_status     TEXT NOT NULL DEFAULT 'pending'
                        CHECK (metadata_status IN ('pending', 'ok', 'error')),
    metadata_error      TEXT,
    metadata_updated_at TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    rel_path        TEXT PRIMARY KEY,
    photo_hash      TEXT NOT NULL REFERENCES photos(hash) ON DELETE RESTRICT,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    inode           INTEGER,
    device          INTEGER,
    status          TEXT NOT NULL DEFAULT 'present'
                    CHECK (status IN ('present', 'missing')),
    first_seen_at   TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL,
    missing_since   TEXT
);

CREATE INDEX IF NOT EXISTS locations_photo_hash_idx ON locations(photo_hash);
CREATE INDEX IF NOT EXISTS locations_status_idx ON locations(status);

CREATE TABLE IF NOT EXISTS scan_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    mode           TEXT NOT NULL,
    status         TEXT NOT NULL,
    scanned        INTEGER NOT NULL DEFAULT 0,
    unchanged      INTEGER NOT NULL DEFAULT 0,
    added          INTEGER NOT NULL DEFAULT 0,
    modified       INTEGER NOT NULL DEFAULT 0,
    missing        INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0,
    hashed_bytes   INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class LocationRow:
    """Fila de ``locations`` necesaria para reconciliar una ruta."""

    rel_path: str
    photo_hash: str
    size: int
    mtime_ns: int
    inode: int | None
    device: int | None
    status: str


@dataclass(frozen=True)
class MetadataTarget:
    """Foto pendiente de metadatos y una ubicación vista para leerla."""

    hash: str
    rel_path: str


@dataclass(frozen=True)
class CatalogCounts:
    """Totales del catálogo para el resumen."""

    photos: int
    locations: int
    duplicate_photos: int
    metadata_pending: int


class Catalog:
    """Conexión SQLite con esquema, migraciones y operaciones del indexador."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        path: Path,
        memory: bool = False,
        read_only: bool = False,
    ) -> None:
        self._conn = connection
        self.path = path
        self.memory = memory
        self.read_only = read_only
        try:
            self._configure()
            self._migrate()
        except Exception:
            try:
                connection.close()
            except sqlite3.Error:
                logger.debug("No se pudo cerrar el catálogo tras un fallo", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------
    @classmethod
    def open(cls, path: Path) -> Catalog:
        """Abre o crea el catálogo en disco."""
        try:
            connection = sqlite3.connect(str(path), timeout=5.0)
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo abrir el catálogo: {exc}") from exc
        return cls(connection, path)

    @classmethod
    def open_read_only(cls, path: Path) -> Catalog:
        """Abre el catálogo en modo solo lectura, sin crearlo ni migrarlo.

        Pensado para los lectores (la UI): usa URI ``mode=ro``, activa
        ``PRAGMA query_only``, no toca el journal y valida la versión del
        esquema sin aplicar migraciones. Nunca crea el fichero.
        """
        try:
            connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo abrir el catálogo: {exc}") from exc
        return cls(connection, path, read_only=True)

    @classmethod
    def open_in_memory(cls) -> Catalog:
        """Catálogo vacío en memoria, útil para tests y simulaciones."""
        try:
            connection = sqlite3.connect(":memory:")
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo crear el catálogo en memoria: {exc}") from exc
        return cls(connection, Path(":memory:"), memory=True)

    @classmethod
    def open_dry_run(cls, path: Path) -> Catalog:
        """Simula sobre una copia en memoria del catálogo existente.

        Si el catálogo no existe, crea uno vacío en memoria con el mismo
        esquema. Nunca escribe en disco.
        """
        connection: sqlite3.Connection
        if path.exists():
            try:
                source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
            except sqlite3.Error as exc:
                raise CatalogError(f"No se pudo abrir el catálogo: {exc}") from exc
            connection = sqlite3.connect(":memory:")
            try:
                source.backup(connection)
            except sqlite3.Error as exc:
                connection.close()
                raise CatalogError(f"No se pudo copiar el catálogo: {exc}") from exc
            finally:
                source.close()
            return cls(connection, path, memory=True)
        return cls.open_in_memory()

    # ------------------------------------------------------------------
    # Configuración y migraciones
    # ------------------------------------------------------------------
    def _configure(self) -> None:
        self._conn.row_factory = sqlite3.Row
        try:
            if self.read_only:
                self._conn.execute("PRAGMA query_only = ON")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA temp_store = MEMORY")
            if not self.memory and not self.read_only:
                self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo configurar SQLite: {exc}") from exc

    def _migrate(self) -> None:
        try:
            row = self._conn.execute("PRAGMA user_version").fetchone()
            version = int(row[0]) if row is not None else 0
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo leer la versión del catálogo: {exc}") from exc

        if version > SCHEMA_VERSION:
            raise CatalogVersionError(
                f"El catálogo usa el esquema {version}, más nuevo que el soportado "
                f"({SCHEMA_VERSION}). Actualiza foto antes de indexar."
            )

        if self.read_only:
            if version == SCHEMA_VERSION:
                return
            raise CatalogError(
                f"El catálogo tiene el esquema {version}, que no se puede leer con "
                f"la versión soportada ({SCHEMA_VERSION}). Ejecuta 'foto-index' "
                "para crear o actualizar el índice."
            )

        if version == SCHEMA_VERSION:
            return

        try:
            if version < 1:
                self._conn.executescript(SCHEMA_V1)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo aplicar la migración del catálogo: {exc}") from exc

    # ------------------------------------------------------------------
    # Utilidades de conexión
    # ------------------------------------------------------------------
    def commit(self) -> None:
        try:
            self._conn.commit()
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo confirmar la transacción: {exc}") from exc

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo revertir la transacción: {exc}") from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            logger.debug("La conexión SQLite ya estaba cerrada", exc_info=True)

    def journal_mode(self) -> str:
        row = self._conn.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]) if row is not None else ""

    def user_version(self) -> int:
        row = self._conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row is not None else 0

    # ------------------------------------------------------------------
    # Ejecuciones
    # ------------------------------------------------------------------
    def mark_interrupted_runs(self, now: str) -> int:
        cursor = self._conn.execute(
            "UPDATE scan_runs SET status = 'interrupted', finished_at = ? "
            "WHERE status = 'running'",
            (now,),
        )
        self.commit()
        return max(cursor.rowcount, 0)

    def start_run(self, mode: str, now: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO scan_runs (started_at, mode, status) VALUES (?, ?, 'running')",
            (now, mode),
        )
        self.commit()
        if cursor.lastrowid is None:
            raise CatalogError("No se pudo crear el registro de indexado")
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        finished_at: str,
        scanned: int,
        unchanged: int,
        added: int,
        modified: int,
        missing: int,
        errors: int,
        hashed_bytes: int,
    ) -> None:
        self._conn.execute(
            "UPDATE scan_runs SET finished_at = ?, status = ?, scanned = ?, unchanged = ?, "
            "added = ?, modified = ?, missing = ?, errors = ?, hashed_bytes = ? WHERE id = ?",
            (
                finished_at,
                status,
                scanned,
                unchanged,
                added,
                modified,
                missing,
                errors,
                hashed_bytes,
                run_id,
            ),
        )
        self.commit()

    # ------------------------------------------------------------------
    # Ubicaciones y fotos
    # ------------------------------------------------------------------
    def prepare_seen(self) -> None:
        self._conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen (rel_path TEXT PRIMARY KEY)")
        self._conn.execute("DELETE FROM seen")

    def add_seen(self, rel_paths: Iterable[str]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen (rel_path) VALUES (?)",
            ((rel_path,) for rel_path in rel_paths),
        )

    def drop_seen(self) -> None:
        try:
            self._conn.execute("DROP TABLE IF EXISTS seen")
        except sqlite3.Error:
            logger.debug("No se pudo eliminar la tabla temporal seen", exc_info=True)

    def get_location(self, rel_path: str) -> LocationRow | None:
        row = self._conn.execute(
            "SELECT rel_path, photo_hash, size, mtime_ns, inode, device, status "
            "FROM locations WHERE rel_path = ?",
            (rel_path,),
        ).fetchone()
        if row is None:
            return None
        return LocationRow(
            rel_path=str(row["rel_path"]),
            photo_hash=str(row["photo_hash"]),
            size=int(row["size"]),
            mtime_ns=int(row["mtime_ns"]),
            inode=int(row["inode"]) if row["inode"] is not None else None,
            device=int(row["device"]) if row["device"] is not None else None,
            status=str(row["status"]),
        )

    def insert_photo(
        self,
        photo_hash: str,
        extension: str,
        file_type: str | None,
        now: str,
    ) -> bool:
        """Inserta una foto si el hash aún no existe; devuelve si la creó."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO photos "
            "(hash, file_type, extension, metadata_status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (photo_hash, file_type, extension, now, now),
        )
        return cursor.rowcount == 1

    def insert_location(
        self,
        *,
        rel_path: str,
        photo_hash: str,
        size: int,
        mtime_ns: int,
        inode: int | None,
        device: int | None,
        now: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO locations "
            "(rel_path, photo_hash, size, mtime_ns, inode, device, status, "
            "first_seen_at, last_indexed_at, missing_since) "
            "VALUES (?, ?, ?, ?, ?, ?, 'present', ?, ?, NULL)",
            (rel_path, photo_hash, size, mtime_ns, inode, device, now, now),
        )

    def update_location_signature(
        self,
        *,
        rel_path: str,
        photo_hash: str,
        size: int,
        mtime_ns: int,
        inode: int | None,
        device: int | None,
        now: str,
    ) -> None:
        """Actualiza firma y hash; la ubicación vuelve a estar presente."""
        self._conn.execute(
            "UPDATE locations SET photo_hash = ?, size = ?, mtime_ns = ?, inode = ?, "
            "device = ?, status = 'present', missing_since = NULL, last_indexed_at = ? "
            "WHERE rel_path = ?",
            (photo_hash, size, mtime_ns, inode, device, now, rel_path),
        )

    def mark_location_present(self, rel_path: str, now: str) -> None:
        self._conn.execute(
            "UPDATE locations SET status = 'present', missing_since = NULL, "
            "last_indexed_at = ? WHERE rel_path = ?",
            (now, rel_path),
        )

    def mark_missing(self, now: str) -> int:
        cursor = self._conn.execute(
            "UPDATE locations SET status = 'missing', missing_since = ? "
            "WHERE status = 'present' AND rel_path NOT IN (SELECT rel_path FROM seen)",
            (now,),
        )
        return max(cursor.rowcount, 0)

    # ------------------------------------------------------------------
    # Metadatos
    # ------------------------------------------------------------------
    def fetch_metadata_batch(
        self,
        *,
        full: bool,
        after_hash: str | None,
        limit: int,
    ) -> list[MetadataTarget]:
        """Siguiente lote de fotos con ubicación vista y metadatos por hacer."""
        rows = self._conn.execute(
            "SELECT p.hash AS hash, MIN(l.rel_path) AS rel_path "
            "FROM photos p JOIN locations l ON l.photo_hash = p.hash "
            "WHERE l.rel_path IN (SELECT rel_path FROM seen) "
            "AND (p.metadata_status = 'pending' OR ? = 1) "
            "AND (? IS NULL OR p.hash > ?) "
            "GROUP BY p.hash ORDER BY p.hash LIMIT ?",
            (1 if full else 0, after_hash, after_hash, limit),
        ).fetchall()
        return [
            MetadataTarget(hash=str(row["hash"]), rel_path=str(row["rel_path"]))
            for row in rows
        ]

    def update_metadata(self, photo_hash: str, metadata: PhotoMetadata, now: str) -> None:
        self._conn.execute(
            "UPDATE photos SET file_type = ?, width = ?, height = ?, captured_at = ?, "
            "camera_make = ?, camera_model = ?, lens_model = ?, iso = ?, f_number = ?, "
            "exposure_time = ?, focal_length = ?, gps_latitude = ?, gps_longitude = ?, "
            "orientation = ?, metadata_status = 'ok', metadata_error = NULL, "
            "metadata_updated_at = ?, updated_at = ? WHERE hash = ?",
            (
                metadata.file_type,
                metadata.width,
                metadata.height,
                metadata.captured_at,
                metadata.camera_make,
                metadata.camera_model,
                metadata.lens_model,
                metadata.iso,
                metadata.f_number,
                metadata.exposure_time,
                metadata.focal_length,
                metadata.gps_latitude,
                metadata.gps_longitude,
                metadata.orientation,
                now,
                now,
                photo_hash,
            ),
        )

    def update_metadata_error(self, photo_hash: str, message: str, now: str) -> None:
        self._conn.execute(
            "UPDATE photos SET metadata_status = 'error', metadata_error = ?, "
            "metadata_updated_at = ?, updated_at = ? WHERE hash = ?",
            (message[:500], now, now, photo_hash),
        )

    # ------------------------------------------------------------------
    # Limpieza y totales
    # ------------------------------------------------------------------
    def prune(self, now: datetime, older_than_days: int) -> tuple[int, int]:
        """Elimina ubicaciones ausentes antiguas y fotos sin ubicación."""
        cutoff = (now - timedelta(days=older_than_days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM locations WHERE status = 'missing' AND missing_since IS NOT NULL "
            "AND datetime(missing_since) <= datetime(?)",
            (cutoff,),
        )
        pruned_locations = max(cursor.rowcount, 0)
        cursor = self._conn.execute(
            "DELETE FROM photos WHERE hash NOT IN (SELECT DISTINCT photo_hash FROM locations)"
        )
        pruned_photos = max(cursor.rowcount, 0)
        self.commit()
        return pruned_locations, pruned_photos

    def counts(self) -> CatalogCounts:
        photos = self._scalar("SELECT COUNT(*) FROM photos")
        locations = self._scalar("SELECT COUNT(*) FROM locations")
        duplicates = self._scalar(
            "SELECT COUNT(*) FROM ("
            "SELECT photo_hash FROM locations GROUP BY photo_hash HAVING COUNT(*) > 1"
            ")"
        )
        pending = self._scalar("SELECT COUNT(*) FROM photos WHERE metadata_status = 'pending'")
        return CatalogCounts(
            photos=photos,
            locations=locations,
            duplicate_photos=duplicates,
            metadata_pending=pending,
        )

    def list_present_locations(self) -> list[sqlite3.Row]:
        """Devuelve ubicaciones presentes con sus metadatos de foto."""
        rows = self._conn.execute(
            """
            SELECT
                l.rel_path,
                l.photo_hash,
                l.size,
                p.file_type,
                p.extension,
                p.width,
                p.height,
                p.captured_at,
                p.camera_make,
                p.camera_model,
                p.lens_model,
                p.iso,
                p.f_number,
                p.exposure_time,
                p.focal_length,
                p.gps_latitude,
                p.gps_longitude,
                p.orientation,
                p.metadata_status
            FROM locations l
            JOIN photos p ON p.hash = l.photo_hash
            WHERE l.status = 'present'
            ORDER BY l.rel_path
            """
        ).fetchall()
        return list(rows)

    def _scalar(self, query: str, parameters: Sequence[object] = ()) -> int:
        row = self._conn.execute(query, parameters).fetchone()
        return int(row[0]) if row is not None else 0
