"""Catálogo SQLite reconstruible de la fototeca."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from foto.core.errors import CatalogError, CatalogVersionError
from foto.core.geocoding import Place, PlaceRecord
from foto.core.metadata import PhotoMetadata
from foto.storage.tags import Tag, TagAssignment, assignments_by_hash, list_tags

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5
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

# Fases del historial de la pasada y caché por celda del lugar (ver ADR 0005).
# ``geocode_cells`` guarda además toda la información del servicio: el
# ``municipality`` literal, el ``display_name`` y el objeto ``address``
# completo serializado (revisión de 2026-11-09), y el ``zoom`` con el que
# resolvió la celda para poder invalidarla si sube el detalle pedido.
SCHEMA_V2_TABLES = """
CREATE TABLE IF NOT EXISTS geocode_cells (
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    country       TEXT,
    country_code  TEXT,
    state         TEXT,
    city          TEXT,
    municipality  TEXT,
    display_name  TEXT,
    address_json  TEXT,
    zoom          INTEGER NOT NULL,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (latitude, longitude)
);

CREATE TABLE IF NOT EXISTS run_phases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES scan_runs(id),
    phase         TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'skipped', 'error')),
    reason        TEXT,
    candidates    INTEGER,
    processed     INTEGER,
    errors        INTEGER,
    cache_hits    INTEGER,
    http_requests INTEGER,
    recorded_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS run_phases_run_idx ON run_phases(run_id);
"""

# Columnas de lugar y estado de geocodificación añadidas a ``photos`` en v2.
PLACE_COLUMNS_V2 = (
    ("place_status",
     ("TEXT NOT NULL DEFAULT 'pending' CHECK (place_status IN "
      "('none', 'pending', 'ok', 'error'))")),
    ("place_error", "TEXT"),
    ("place_country", "TEXT"),
    ("place_country_code", "TEXT"),
    ("place_state", "TEXT"),
    ("place_city", "TEXT"),
    ("place_updated_at", "TEXT"),
)

# Columnas de detalle completo del servicio añadidas a ``photos`` en v3
# (revisión de 2026-11-09): municipio literal, cadena completa del servicio
# y objeto ``address`` íntegro serializado.
PLACE_COLUMNS_V3 = (
    ("place_municipality", "TEXT"),
    ("place_display_name", "TEXT"),
    ("place_address_json", "TEXT"),
)

# Columnas de detalle añadidas a ``geocode_cells`` en v3. Las celdas que
# estaban guardadas quedan con el zoom 10 con el que se resolvieron: al
# exigir el zoom actual la siguiente pasada las vuelve a pedir (ADR 0005,
# revisión).
CELL_COLUMNS_V3 = (
    ("municipality", "TEXT"),
    ("display_name", "TEXT"),
    ("address_json", "TEXT"),
    ("zoom", "INTEGER NOT NULL DEFAULT 10"),
)

# Tablas de etiquetas (ADR 0006, revisión de 2026-11-11): taxonomía plana,
# sin ``parent_id`` ni ``merged_into``. ``tags`` guarda qué existe (con
# ``kind``) y ``photo_tags`` qué foto lleva qué etiqueta y con qué
# procedencia. El índice único parcial garantiza en el esquema que ``fav``
# sea la única etiqueta de sistema, y el índice único de asignaciones usa
# ``COALESCE`` porque en SQLite los NULL no colisionan en los índices.
SCHEMA_V4_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
    name_normalized TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL DEFAULT 'free'
                    CHECK (kind IN ('system', 'person', 'place', 'event', 'free')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 'fav' es la única etiqueta de sistema; el esquema lo garantiza.
CREATE UNIQUE INDEX IF NOT EXISTS tags_system_singleton_idx
    ON tags(kind) WHERE kind = 'system';

CREATE TABLE IF NOT EXISTS photo_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_hash  TEXT NOT NULL REFERENCES photos(hash) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'manual'
                CHECK (source IN ('manual', 'ml')),
    status      TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (status IN ('confirmed', 'suggested', 'rejected')),
    confidence  REAL CHECK (confidence IS NULL
                        OR (confidence >= 0.0 AND confidence <= 1.0)),
    region_x    REAL,
    region_y    REAL,
    region_w    REAL,
    region_h    REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((region_x IS NULL AND region_y IS NULL
            AND region_w IS NULL AND region_h IS NULL)
        OR (region_x >= 0.0 AND region_x <= 1.0
            AND region_y >= 0.0 AND region_y <= 1.0
            AND region_w > 0.0 AND region_w <= 1.0
            AND region_h > 0.0 AND region_h <= 1.0))
);

-- Una asignación por (foto, etiqueta, cara). El COALESCE existe porque en
-- SQLite los NULL no colisionan en los índices únicos: sin él, la
-- asignación de foto completa (región NULL) se podría duplicar.
CREATE UNIQUE INDEX IF NOT EXISTS photo_tags_assign_unique_idx
    ON photo_tags (photo_hash, tag_id,
                   COALESCE(region_x, -1.0), COALESCE(region_y, -1.0),
                   COALESCE(region_w, -1.0), COALESCE(region_h, -1.0));

CREATE INDEX IF NOT EXISTS photo_tags_photo_idx ON photo_tags(photo_hash);
CREATE INDEX IF NOT EXISTS photo_tags_tag_idx   ON photo_tags(tag_id);
"""

# Ampliación del ``CHECK`` de ``photo_tags.source`` con ``'import'`` (ADR 0007,
# enmienda del ADR 0006): ampliar un ``CHECK`` cerrado exige reconstruir la
# tabla en SQLite. La migración renombra la original a un nombre temporal,
# crea la tabla nueva con su nombre definitivo (texto idéntico al del
# ADR 0006 salvo el ``CHECK`` de ``source``), copia las filas tal cual, suelta
# la vieja y recrea los tres índices byte a byte iguales a los del ADR 0006.
# Todo en una única transacción: o la reconstrucción entera o nada.
SCHEMA_V5_PHOTO_TAGS_REBUILD = """
DROP TABLE IF EXISTS photo_tags_v5_old;

BEGIN;
ALTER TABLE photo_tags RENAME TO photo_tags_v5_old;

CREATE TABLE photo_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_hash  TEXT NOT NULL REFERENCES photos(hash) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'manual'
                CHECK (source IN ('manual', 'ml', 'import')),
    status      TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (status IN ('confirmed', 'suggested', 'rejected')),
    confidence  REAL CHECK (confidence IS NULL
                        OR (confidence >= 0.0 AND confidence <= 1.0)),
    region_x    REAL,
    region_y    REAL,
    region_w    REAL,
    region_h    REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((region_x IS NULL AND region_y IS NULL
            AND region_w IS NULL AND region_h IS NULL)
        OR (region_x >= 0.0 AND region_x <= 1.0
            AND region_y >= 0.0 AND region_y <= 1.0
            AND region_w > 0.0 AND region_w <= 1.0
            AND region_h > 0.0 AND region_h <= 1.0))
);

INSERT INTO photo_tags
    (photo_hash, tag_id, source, status, confidence,
     region_x, region_y, region_w, region_h, created_at, updated_at)
SELECT photo_hash, tag_id, source, status, confidence,
       region_x, region_y, region_w, region_h, created_at, updated_at
FROM photo_tags_v5_old;

DROP TABLE photo_tags_v5_old;

CREATE UNIQUE INDEX IF NOT EXISTS photo_tags_assign_unique_idx
    ON photo_tags (photo_hash, tag_id,
                   COALESCE(region_x, -1.0), COALESCE(region_y, -1.0),
                   COALESCE(region_w, -1.0), COALESCE(region_h, -1.0));

CREATE INDEX IF NOT EXISTS photo_tags_photo_idx ON photo_tags(photo_hash);
CREATE INDEX IF NOT EXISTS photo_tags_tag_idx   ON photo_tags(tag_id);
COMMIT;
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
    place_pending: int


@dataclass(frozen=True)
class GeocodeTarget:
    """Foto con GPS pendiente de geocodificar."""

    hash: str
    latitude: float
    longitude: float


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
            if version < 2:
                self._migrate_to_v2()
            if version < 3:
                self._migrate_to_v3()
            if version < 4:
                self._migrate_to_v4()
            if version < 5:
                self._migrate_to_v5()
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo aplicar la migración del catálogo: {exc}") from exc

    def _migrate_to_v2(self) -> None:
        """Aplica la migración a v2: esquema base, columnas de lugar y tablas.

        Idempotente: sirve tanto para un catálogo vacío como para uno real
        de la versión 1.
        """
        tables = {
            str(row[0])
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "photos" not in tables:
            self._conn.executescript(SCHEMA_V1)
        self._ensure_columns("photos", PLACE_COLUMNS_V2)
        self._conn.executescript(SCHEMA_V2_TABLES)

    def _migrate_to_v3(self) -> None:
        """Aplica la migración a v3: detalle completo del lugar.

        Añade a ``photos`` las columnas del detalle del servicio y a
        ``geocode_cells`` el municipio, el detalle completo y el ``zoom``
        de resolución. Las celdas existentes quedan con el zoom 10 con el
        que se crearon: al exigir el zoom actual (14), la siguiente pasada
        las vuelve a pedir y refresca el lugar con la jerarquía completa
        (ADR 0005, revisión de 2026-11-09).
        """
        self._ensure_columns("geocode_cells", CELL_COLUMNS_V3)
        self._ensure_columns("photos", PLACE_COLUMNS_V3)

    def _migrate_to_v4(self) -> None:
        """Aplica la migración a v4: tablas de etiquetas y etiqueta ``fav``.

        Taxonomía plana (ADR 0006, revisión de 2026-11-11): sin jerarquía
        ni fusión. No hay datos que convertir —el favorito nunca tuvo
        columna ni persistencia—, así que solo se crean las tablas y se
        inserta la etiqueta de sistema ``fav``, que se resolverá siempre
        por ``kind='system'`` (los ``AUTOINCREMENT`` varían entre
        bibliotecas) con la hora por parámetro.
        """
        self._conn.executescript(SCHEMA_V4_TAGS)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO tags (name, name_normalized, kind, "
            "created_at, updated_at) VALUES ('fav', 'fav', 'system', :now, :now)",
            {"now": now},
        )

    def _migrate_to_v5(self) -> None:
        """Aplica la migración a v5: ``photo_tags.source`` admite ``'import'``.

        Enmienda del ADR 0006 por el ADR 0007: toda fila creada por
        ``foto-import`` nace con ``source='import'``. Ampliar el ``CHECK``
        cerrado exige reconstruir la tabla en SQLite: crea
        ``photo_tags_v5_new``, copia las filas tal cual (sin conversión de
        datos), suelta la original, renombra y recrea los tres índices
        byte a byte iguales a los del ADR 0006. Idempotente y sin datos que
        convertir.
        """
        self._conn.executescript(SCHEMA_V5_PHOTO_TAGS_REBUILD)

    def _ensure_columns(self, table: str, columns: tuple[tuple[str, str], ...]) -> None:
        """Añade a la tabla las columnas que le falten (idempotente)."""
        existing = {
            str(row[1])
            for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        for name, definition in columns:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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
        """Guarda metadatos y corrige el estado del lugar.

        Con coordenadas GPS el lugar pasa a ``pending`` si aún no está
        resuelto; sin coordenadas queda ``none``. Un ``ok`` nunca se degrada.
        """
        self._conn.execute(
            "UPDATE photos SET file_type = ?, width = ?, height = ?, captured_at = ?, "
            "camera_make = ?, camera_model = ?, lens_model = ?, iso = ?, f_number = ?, "
            "exposure_time = ?, focal_length = ?, gps_latitude = ?, gps_longitude = ?, "
            "orientation = ?, metadata_status = 'ok', metadata_error = NULL, "
            "place_status = CASE "
            "  WHEN ? IS NULL AND ? IS NULL AND place_status = 'pending' THEN 'none' "
            "  WHEN ? IS NOT NULL AND ? IS NOT NULL AND place_status = 'none' THEN 'pending' "
            "  ELSE place_status END, "
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
                metadata.gps_latitude,
                metadata.gps_longitude,
                metadata.gps_latitude,
                metadata.gps_longitude,
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
    # Geocodificación inversa y caché por celda (ADR 0005)
    # ------------------------------------------------------------------
    def fetch_geocode_targets(
        self,
        *,
        full: bool,
        after_hash: str | None,
        limit: int | None,
    ) -> list[GeocodeTarget]:
        """Fotos con GPS y sin lugar, a reanudar por ``place_status``.

        En ``full`` también entran las fotos cuyo lugar quedó en ``error``,
        igual que los metadatos.
        """
        query = (
            "SELECT hash, gps_latitude, gps_longitude FROM photos "
            "WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL "
            "AND (place_status = 'pending' OR (? = 1 AND place_status = 'error')) "
            "AND (? IS NULL OR hash > ?) "
            "ORDER BY hash"
        )
        query += " LIMIT ?" if limit is not None else ""
        parameters: list[object] = [1 if full else 0, after_hash, after_hash]
        if limit is not None:
            parameters.append(limit)
        rows = self._conn.execute(query, parameters).fetchall()
        return [
            GeocodeTarget(
                hash=str(row["hash"]),
                latitude=float(row["gps_latitude"]),
                longitude=float(row["gps_longitude"]),
            )
            for row in rows
        ]

    def count_geocode_candidates(self, *, full: bool) -> int:
        return self._scalar(
            "SELECT COUNT(*) FROM photos "
            "WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL "
            "AND (place_status = 'pending' OR (? = 1 AND place_status = 'error'))",
            (1 if full else 0,),
        )

    def get_place_cell(
        self, latitude: float, longitude: float, *, min_zoom: int
    ) -> PlaceRecord | None:
        """Lugar cacheado para una celda, o ``None`` si hay que pedirlo.

        ``min_zoom`` invalida la caché: una celda resuelta con menos zoom
        del exigido se vuelve a pedir para refrescarla con más detalle
        (ADR 0005, revisión).
        """
        row = self._conn.execute(
            "SELECT country, country_code, state, city, municipality, "
            "display_name, address_json, zoom FROM geocode_cells "
            "WHERE latitude = ? AND longitude = ?",
            (latitude, longitude),
        ).fetchone()
        if row is None or int(row["zoom"]) < min_zoom:
            return None
        return PlaceRecord(
            place=Place(
                country=row["country"],
                country_code=row["country_code"],
                state=row["state"],
                city=row["city"],
                municipality=row["municipality"],
            ),
            display_name=row["display_name"],
            address_json=row["address_json"],
        )

    def put_place_cell(
        self,
        latitude: float,
        longitude: float,
        record: PlaceRecord,
        *,
        zoom: int,
        now: str,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO geocode_cells "
            "(latitude, longitude, country, country_code, state, city, "
            "municipality, display_name, address_json, zoom, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (latitude, longitude, record.place.country, record.place.country_code,
             record.place.state, record.place.city, record.place.municipality,
             record.display_name, record.address_json, zoom, now),
        )

    def update_place(self, photo_hash: str, record: PlaceRecord, now: str) -> None:
        """Guarda el lugar de una foto: la fila queda siempre en ``ok``.

        Guarda la jerarquía normalizada y el detalle completo del servicio
        (``display_name`` y ``address`` íntegro) para el panel.
        """
        self._conn.execute(
            "UPDATE photos SET place_country = ?, place_country_code = ?, "
            "place_state = ?, place_city = ?, place_municipality = ?, "
            "place_display_name = ?, place_address_json = ?, "
            "place_status = 'ok', "
            "place_error = NULL, place_updated_at = ?, updated_at = ? WHERE hash = ?",
            (record.place.country, record.place.country_code, record.place.state,
             record.place.city, record.place.municipality, record.display_name,
             record.address_json, now, now, photo_hash),
        )

    def update_place_error(self, photo_hash: str, message: str, now: str) -> None:
        self._conn.execute(
            "UPDATE photos SET place_status = 'error', place_error = ?, "
            "place_updated_at = ?, updated_at = ? WHERE hash = ?",
            (message[:500], now, now, photo_hash),
        )

    # ------------------------------------------------------------------
    # Historial de fases de la pasada
    # ------------------------------------------------------------------
    def record_phase(
        self,
        run_id: int,
        phase: str,
        *,
        status: str,
        reason: str | None = None,
        candidates: int | None = None,
        processed: int | None = None,
        errors: int | None = None,
        cache_hits: int | None = None,
        http_requests: int | None = None,
        now: str,
    ) -> None:
        """Registra el resultado de una fase en ``run_phases``."""
        self._conn.execute(
            "INSERT INTO run_phases "
            "(run_id, phase, status, reason, candidates, processed, errors, "
            "cache_hits, http_requests, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                phase,
                status,
                reason[:500] if reason is not None else None,
                candidates,
                processed,
                errors,
                cache_hits,
                http_requests,
                now,
            ),
        )
        self.commit()

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
        place_pending = self._scalar(
            "SELECT COUNT(*) FROM photos "
            "WHERE place_status = 'pending' "
            "AND gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL"
        )
        return CatalogCounts(
            photos=photos,
            locations=locations,
            duplicate_photos=duplicates,
            metadata_pending=pending,
            place_pending=place_pending,
        )

    def tag_list(self) -> list[Tag]:
        """Vocabulario de etiquetas del catálogo (delega en ``storage.tags``)."""
        return list_tags(self._conn)

    def tag_assignments(self) -> dict[str, tuple[TagAssignment, ...]]:
        """Asignaciones foto–etiqueta por hash (delega en ``storage.tags``)."""
        return assignments_by_hash(self._conn)

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
                p.place_country,
                p.place_country_code,
                p.place_state,
                p.place_city,
                p.place_municipality,
                p.place_display_name,
                p.place_address_json,
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
