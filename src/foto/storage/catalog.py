"""Catálogo SQLite reconstruible de la fototeca.

Esquema v6 (ADR 0010): la tabla per-foto se renombra a ``files`` —
apariciones de fichero con clave compuesta ``(location_id, rel_path)`` y
estado ``present``/``missing``/``deleted`` — y ``locations`` queda
reservada para las ubicaciones con nombre (``name``, ``path``, ``tier``,
``state``). ``library.toml`` desaparece: esta tabla es la única referencia
de ubicaciones del sistema.

Esquema v7 (ADR 0009): única columna nueva ``photos.apple_uuid``, la clave
de unión entre ``photos(hash)`` y el identificador del asset de Apple
(``ZASSET.ZUUID``, que es el nombre del fichero en ``originals/``). Sin
tablas nuevas: favoritos y personas viven como etiquetas del ADR 0006.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from foto.core.errors import CatalogError, CatalogVersionError
from foto.core.geocoding import Place, PlaceRecord
from foto.core.metadata import PhotoMetadata
from foto.storage.config import Location
from foto.storage.tags import (
    Tag,
    TagAssignment,
    assignments_by_hash,
    favorite_tag,
    list_tags,
    normalize_tag_name,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7
CATALOG_FILE_NAME = "catalog.sqlite"

#: Orden canónico por ``tier`` (ADR 0010, Decisión 4): ``fast`` gana a
#: ``archive``; el ``rel_path`` solo desempata dentro del mismo ``tier``.
#: Colapso de espacios para el nombre visible de una etiqueta creada aquí.
_WHITESPACE = re.compile(r"\s+")

TIER_RANK: dict[str, int] = {"fast": 0, "archive": 1}

# Sentencia de apariciones presentes, partida en dos para no repetir la lista
# de columnas al añadir el filtro por etiqueta de sistema. La concatenación
# sin filtro es byte a byte la consulta histórica de ``list_present_files``.
_PRESENT_FILES_SELECT = """
            SELECT
                f.location_id,
                f.rel_path,
                f.photo_hash,
                f.size,
                loc.path AS location_path,
                loc.name AS location_name,
                loc.tier AS location_tier,
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
            FROM files f
            JOIN locations loc ON loc.id = f.location_id
            JOIN photos p ON p.hash = f.photo_hash
            WHERE f.status = 'present' AND loc.state = 'active'
            """

_PRESENT_FILES_ORDER = """ORDER BY f.rel_path
            """

_PRESENT_FILES_SQL = _PRESENT_FILES_SELECT + _PRESENT_FILES_ORDER

# Predicado de etiqueta de sistema (ADR 0006, Decisiones 4 y 6): el nombre
# normalizado se liga con ``?``; nunca se interpola en la sentencia. El
# correlativo es el hash de la foto, no la aparición: todas las apariciones
# presentes de una foto etiquetada siguen viajando.
_SYSTEM_TAG_EXISTS_SQL = """AND EXISTS (
                SELECT 1
                  FROM photo_tags pt
                  JOIN tags t ON t.id = pt.tag_id
                 WHERE pt.photo_hash = p.hash
                   AND pt.status IN ('confirmed', 'suggested')
                   AND t.kind = 'system'
                   AND t.name_normalized = ?
            )
            """

# Comprobación barata para decidir si vale la pena filtrar, sin leer filas.
_SYSTEM_TAG_EXISTS_ANY_SQL = (
    "SELECT 1 FROM photo_tags pt "
    "JOIN tags t ON t.id = pt.tag_id "
    "WHERE pt.status IN ('confirmed', 'suggested') "
    "AND t.kind = 'system' AND t.name_normalized = ? LIMIT 1"
)


def _utcnow_iso() -> str:
    """Marca de tiempo ISO-8601 con segundos, como en el resto del catálogo."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()

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

# Esquema v6 (ADR 0010): tabla nueva de ubicaciones con nombre. ``name`` es
# la identidad estable (``path`` y ``tier`` pueden cambiar); ``state`` solo
# pasa a ``deleted`` cuando la config deja de declarar la ubicación —
# nunca por estar el volumen desmontado. Sin fila por ``deleted_at`` en
# ``files``: la ventana de poda usa el ``deleted_at`` de la ubicación.
SCHEMA_V6_LOCATIONS = """
CREATE TABLE IF NOT EXISTS locations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    path            TEXT NOT NULL,
    tier            TEXT NOT NULL CHECK (tier IN ('fast', 'archive')),
    state           TEXT NOT NULL DEFAULT 'active'
                    CHECK (state IN ('active', 'deleted')),
    created_at      TEXT NOT NULL,
    deleted_at      TEXT,
    last_scanned_at TEXT
);
"""

# La tabla per-foto pasa a llamarse ``files`` (es lo que contiene:
# apariciones de fichero) con clave compuesta ``(location_id, rel_path)``
# y el estado ``deleted`` además de ``present``/``missing``.
SCHEMA_V6_FILES = """
CREATE TABLE IF NOT EXISTS files (
    location_id     INTEGER NOT NULL REFERENCES locations(id),
    rel_path        TEXT NOT NULL,
    photo_hash      TEXT NOT NULL REFERENCES photos(hash) ON DELETE RESTRICT,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    inode           INTEGER,
    device          INTEGER,
    status          TEXT NOT NULL DEFAULT 'present'
                    CHECK (status IN ('present', 'missing', 'deleted')),
    first_seen_at   TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL,
    missing_since   TEXT,
    PRIMARY KEY (location_id, rel_path)
);

CREATE INDEX IF NOT EXISTS files_photo_hash_idx ON files(photo_hash);
CREATE INDEX IF NOT EXISTS files_status_idx ON files(status);
CREATE INDEX IF NOT EXISTS files_location_idx ON files(location_id);
"""

# Columna de vínculo con Apple añadida a ``photos`` en v7 (ADR 0009,
# Decisión 9): el UUID del asset cuya original es el fichero indexado
# (``ZASSET.ZUUID``, que es el nombre base en ``originals/``). ``NULL``
# para las fotos que no vienen de Apple; el índice único parcial garantiza
# que un asset no enlace con dos fotos.
APPLE_UUID_COLUMN_V7 = (("apple_uuid", "TEXT"),)


@dataclass(frozen=True)
class FileRow:
    """Fila de ``files``: una aparición de fichero en una ubicación (v6)."""

    location_id: int
    rel_path: str
    photo_hash: str
    size: int
    mtime_ns: int
    inode: int | None
    device: int | None
    status: str


@dataclass(frozen=True)
class NamedLocationRow:
    """Fila de ``locations``: una ubicación con nombre del catálogo (v6)."""

    id: int
    name: str
    path: Path
    tier: str
    state: str
    created_at: str
    deleted_at: str | None
    last_scanned_at: str | None


@dataclass(frozen=True)
class MetadataTarget:
    """Foto pendiente de metadatos y una aparición vista para leerla."""

    hash: str
    rel_path: str
    location_path: Path


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


@dataclass(frozen=True)
class ImportedPersonRow:
    """Asignación importada de una etiqueta ``person`` (ADR 0009).

    La región viaja tal cual está en ``photo_tags``: es la clave para
    decidir si el estado de Apple la sigue pidiendo o hay que revocarla.
    """

    id: int
    name_normalized: str
    region_x: float | None
    region_y: float | None
    region_w: float | None
    region_h: float | None


class Catalog:
    """Conexión SQLite con esquema, migraciones y operaciones del indexador."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        path: Path,
        memory: bool = False,
        read_only: bool = False,
        declared_locations: Sequence[Location] = (),
    ) -> None:
        self._conn = connection
        self.path = path
        self.memory = memory
        self.read_only = read_only
        self._declared_locations = tuple(declared_locations)
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
    def open(
        cls,
        path: Path,
        declared_locations: Sequence[Location] = (),
    ) -> Catalog:
        """Abre o crea el catálogo en disco.

        ``declared_locations`` alimenta la migración a v6 (ADR 0010): un
        catálogo anterior con filas heredadas de raíz única exige la
        ubicación declarada única —con su nombre real— a la que asignarlas;
        si la config declara varias (o ninguna) y hay filas, el error es
        explícito antes de migrar nada.
        """
        try:
            connection = sqlite3.connect(str(path), timeout=5.0)
        except sqlite3.Error as exc:
            raise CatalogError(f"No se pudo abrir el catálogo: {exc}") from exc
        return cls(connection, path, declared_locations=tuple(declared_locations))

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
    def open_dry_run(
        cls,
        path: Path,
        declared_locations: Sequence[Location] = (),
    ) -> Catalog:
        """Simula sobre una copia en memoria del catálogo existente.

        Si el catálogo no existe, crea uno vacío en memoria con el mismo
        esquema. Nunca escribe en disco. ``declared_locations`` alimenta la
        migración simulada, igual que en ``open``.
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
            return cls(
                connection,
                path,
                memory=True,
                declared_locations=tuple(declared_locations),
            )
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
            if version < 6:
                self._migrate_to_v6(self._declared_locations)
            if version < 7:
                self._migrate_to_v7()
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

    def _migrate_to_v6(self, declared_locations: Sequence[Location]) -> None:
        """Aplica la migración a v6 (ADR 0010, Decisión 4).

        Renombra la tabla per-foto ``locations`` a ``files`` (reconstruida
        con clave compuesta ``(location_id, rel_path)`` y el estado
        ``deleted``), crea la tabla ``locations`` de ubicaciones con nombre
        y, si hay filas heredadas, crea la **ubicación declarada única con
        su nombre real** y se las asigna. ``default`` solo aparece cuando
        la config es v1 (T1.6). Con varias ubicaciones declaradas y filas
        heredadas —o con ninguna declarada— el error es explícito **antes**
        de tocar nada: las filas no saben a qué raíz pertenecen.
        """
        legacy_rows = self._scalar("SELECT COUNT(*) FROM locations")
        if legacy_rows:
            if len(declared_locations) > 1:
                raise CatalogError(
                    f"El catálogo (esquema anterior) tiene {legacy_rows} "
                    "apariciones heredadas de una única raíz y la "
                    "configuración declara varias ubicaciones: ejecuta "
                    "foto-index primero con solo la ubicación original "
                    "declarada en '[[library.locations]]'"
                )
            if not declared_locations:
                raise CatalogError(
                    f"El catálogo (esquema anterior) tiene {legacy_rows} "
                    "apariciones de fichero y la configuración no declara "
                    "ninguna ubicación: declara en '[[library.locations]]' "
                    "la ubicación que contiene esas fotos y vuelve a "
                    "ejecutar foto-index"
                )

        # Los índices de la tabla legacy siguen su tabla tras el renombre:
        # se sueltan y se recrean con los nombres de la v6.
        self._conn.execute("DROP INDEX IF EXISTS locations_photo_hash_idx")
        self._conn.execute("DROP INDEX IF EXISTS locations_status_idx")
        self._conn.execute("ALTER TABLE locations RENAME TO files_v6_legacy")
        self._conn.executescript(SCHEMA_V6_LOCATIONS + SCHEMA_V6_FILES)

        location_id: int | None = None
        if legacy_rows:
            declared = declared_locations[0]
            location_id = self.insert_named_location(
                name=declared.name,
                path=declared.path,
                tier=declared.tier,
                now=_utcnow_iso(),
            )
        self._conn.execute(
            "INSERT INTO files "
            "(location_id, rel_path, photo_hash, size, mtime_ns, inode, "
            "device, status, first_seen_at, last_indexed_at, missing_since) "
            "SELECT ?, rel_path, photo_hash, size, mtime_ns, inode, device, "
            "status, first_seen_at, last_indexed_at, missing_since "
            "FROM files_v6_legacy",
            (location_id,),
        )
        self._conn.execute("DROP TABLE files_v6_legacy")

    def _ensure_columns(self, table: str, columns: tuple[tuple[str, str], ...]) -> None:
        """Añade a la tabla las columnas que le falten (idempotente)."""
        existing = {
            str(row[1])
            for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        for name, definition in columns:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_to_v7(self) -> None:
        """Aplica la migración a v7: columna de vínculo ``photos.apple_uuid``.

        ADR 0009, Decisión 9: única columna nueva y su índice único parcial
        (``WHERE apple_uuid IS NOT NULL``). Sin tablas nuevas —favoritos y
        personas viven como etiquetas del ADR 0006— y sin datos que
        convertir: las fotos existentes no vienen de Apple.
        """
        self._ensure_columns("photos", APPLE_UUID_COLUMN_V7)
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS photos_apple_uuid_idx "
            "ON photos(apple_uuid) WHERE apple_uuid IS NOT NULL"
        )

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

    @property
    def connection(self) -> sqlite3.Connection:
        """Conexión SQLite subyacente, para lecturas acotadas (``foto-export``).

        Solo lectura de filas: las herramientas de transferencia nunca
        ejecutan SQL de escritura con ella (ADR 0007, Decisión 4).
        """
        return self._conn

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
    # Ubicaciones con nombre (tabla ``locations``, v6)
    # ------------------------------------------------------------------
    def list_named_locations(self) -> list[NamedLocationRow]:
        """Todas las ubicaciones con nombre, activas y dadas de baja."""
        rows = self._conn.execute(
            "SELECT id, name, path, tier, state, created_at, deleted_at, "
            "last_scanned_at FROM locations ORDER BY name"
        ).fetchall()
        return [
            NamedLocationRow(
                id=int(row["id"]),
                name=str(row["name"]),
                path=Path(str(row["path"])),
                tier=str(row["tier"]),
                state=str(row["state"]),
                created_at=str(row["created_at"]),
                deleted_at=(
                    str(row["deleted_at"]) if row["deleted_at"] is not None else None
                ),
                last_scanned_at=(
                    str(row["last_scanned_at"])
                    if row["last_scanned_at"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def get_named_location(self, name: str) -> NamedLocationRow | None:
        """La ubicación con ese nombre, o ``None`` si no existe."""
        row = self._conn.execute(
            "SELECT id, name, path, tier, state, created_at, deleted_at, "
            "last_scanned_at FROM locations WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return NamedLocationRow(
            id=int(row["id"]),
            name=str(row["name"]),
            path=Path(str(row["path"])),
            tier=str(row["tier"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
            deleted_at=(
                str(row["deleted_at"]) if row["deleted_at"] is not None else None
            ),
            last_scanned_at=(
                str(row["last_scanned_at"]) if row["last_scanned_at"] is not None else None
            ),
        )

    def insert_named_location(
        self, *, name: str, path: Path, tier: str, now: str
    ) -> int:
        """Da de alta una ubicación con nombre y devuelve su ``id``."""
        cursor = self._conn.execute(
            "INSERT INTO locations (name, path, tier, state, created_at) "
            "VALUES (?, ?, ?, 'active', ?)",
            (name, str(path), tier, now),
        )
        if cursor.lastrowid is None:
            raise CatalogError("No se pudo registrar la ubicación en el catálogo")
        return int(cursor.lastrowid)

    def update_named_location(
        self, *, location_id: int, path: Path, tier: str
    ) -> None:
        """Actualiza ``path`` y ``tier`` de una ubicación activa (cambio).

        Las filas de fotos siguen siendo válidas: son ``rel_path`` +
        ``location_id``; el escaneo reconciliará lo que haya en la ruta
        nueva (ADR 0010, Decisión 3).
        """
        self._conn.execute(
            "UPDATE locations SET path = ?, tier = ? WHERE id = ?",
            (str(path), tier, location_id),
        )

    def deactivate_named_location(self, *, location_id: int, now: str) -> int:
        """Da de baja una ubicación y marca sus filas ``deleted``.

        La baja solo la provoca quitar la ubicación de la config (jamás una
        desmontada). No se borra nada: ni filas ni ficheros; la limpieza es
        de ``--prune``. Devuelve cuántas filas de fichero se marcaron.
        """
        self._conn.execute(
            "UPDATE locations SET state = 'deleted', deleted_at = ? WHERE id = ?",
            (now, location_id),
        )
        cursor = self._conn.execute(
            "UPDATE files SET status = 'deleted' "
            "WHERE location_id = ? AND status != 'deleted'",
            (location_id,),
        )
        return max(cursor.rowcount, 0)

    def reactivate_named_location(self, *, location_id: int) -> None:
        """Reactiva una ubicación dada de baja (vuelve a ``active``).

        No restaura filas por sí sola: las restaura el escaneo de esa
        ubicación (ficheros vistos → ``present``). Las filas que aún siguen
        ``deleted`` quedan a la espera de ese escaneo.
        """
        self._conn.execute(
            "UPDATE locations SET state = 'active', deleted_at = NULL WHERE id = ?",
            (location_id,),
        )

    def mark_location_scanned(self, location_id: int, now: str) -> None:
        """Registra la última vez que se escaneó la ubicación."""
        self._conn.execute(
            "UPDATE locations SET last_scanned_at = ? WHERE id = ?", (now, location_id)
        )

    def present_counts_by_location(self) -> dict[int, int]:
        """Nº de filas ``present`` por ubicación (fuente de verdad, ADR 0010).

        Lo consume la UI para el panel de ubicaciones: el recuento sale de la
        BD, no del disco. Las filas ``missing`` y ``deleted`` no cuentan.
        """
        rows = self._conn.execute(
            "SELECT location_id, COUNT(*) AS total FROM files "
            "WHERE status = 'present' GROUP BY location_id"
        ).fetchall()
        return {int(row["location_id"]): int(row["total"]) for row in rows}

    # ------------------------------------------------------------------
    # Apariciones de fichero (tabla ``files``, v6)
    # ------------------------------------------------------------------
    def prepare_seen(self) -> None:
        """Tabla temporal de ficheros vistos, con la ubicación como parte."""
        self._conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS seen "
            "(location_id INTEGER NOT NULL, rel_path TEXT NOT NULL, "
            "PRIMARY KEY (location_id, rel_path))"
        )
        self._conn.execute("DELETE FROM seen")

    def add_seen(self, location_id: int, rel_paths: Iterable[str]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen (location_id, rel_path) VALUES (?, ?)",
            ((location_id, rel_path) for rel_path in rel_paths),
        )

    def drop_seen(self) -> None:
        try:
            self._conn.execute("DROP TABLE IF EXISTS seen")
        except sqlite3.Error:
            logger.debug("No se pudo eliminar la tabla temporal seen", exc_info=True)

    def get_file(self, location_id: int, rel_path: str) -> FileRow | None:
        """La fila ``(location_id, rel_path)``, o ``None`` si no existe."""
        row = self._conn.execute(
            "SELECT location_id, rel_path, photo_hash, size, mtime_ns, inode, "
            "device, status FROM files WHERE location_id = ? AND rel_path = ?",
            (location_id, rel_path),
        ).fetchone()
        if row is None:
            return None
        return FileRow(
            location_id=int(row["location_id"]),
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

    def insert_file(
        self,
        *,
        location_id: int,
        rel_path: str,
        photo_hash: str,
        size: int,
        mtime_ns: int,
        inode: int | None,
        device: int | None,
        now: str,
    ) -> None:
        """Registra una aparición nueva ``(location_id, rel_path)``."""
        self._conn.execute(
            "INSERT INTO files "
            "(location_id, rel_path, photo_hash, size, mtime_ns, inode, device, "
            "status, first_seen_at, last_indexed_at, missing_since) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, NULL)",
            (location_id, rel_path, photo_hash, size, mtime_ns, inode, device, now, now),
        )

    def update_file_signature(
        self,
        *,
        location_id: int,
        rel_path: str,
        photo_hash: str,
        size: int,
        mtime_ns: int,
        inode: int | None,
        device: int | None,
        now: str,
    ) -> None:
        """Actualiza firma y hash; la aparición vuelve a estar presente."""
        self._conn.execute(
            "UPDATE files SET photo_hash = ?, size = ?, mtime_ns = ?, inode = ?, "
            "device = ?, status = 'present', missing_since = NULL, "
            "last_indexed_at = ? WHERE location_id = ? AND rel_path = ?",
            (photo_hash, size, mtime_ns, inode, device, now, location_id, rel_path),
        )

    def mark_file_present(self, location_id: int, rel_path: str, now: str) -> None:
        self._conn.execute(
            "UPDATE files SET status = 'present', missing_since = NULL, "
            "last_indexed_at = ? WHERE location_id = ? AND rel_path = ?",
            (now, location_id, rel_path),
        )

    def mark_missing_files(self, location_id: int, now: str) -> int:
        """Marca ``missing`` las apariciones presentes no vistas en el escaneo.

        El estado ``deleted`` no se toca: lo gobierna la sincronización
        config ↔ catálogo, no el recorrido.
        """
        cursor = self._conn.execute(
            "UPDATE files SET status = 'missing', missing_since = ? "
            "WHERE location_id = ? AND status = 'present' AND rel_path NOT IN "
            "(SELECT rel_path FROM seen WHERE location_id = ?)",
            (now, location_id, location_id),
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
        """Siguiente lote de fotos con aparición vista y metadatos por hacer.

        Solo se leen ficheros vistos en esta pasada (tabla ``seen``). El
        objetivo canónico de cada hash se elige por ``tier`` primero
        (``fast`` gana a ``archive``) y por menor ``rel_path`` dentro del
        mismo ``tier`` (ADR 0010, Decisión 4).
        """
        rows = self._conn.execute(
            "SELECT hash, rel_path, location_path FROM ("
            "SELECT p.hash AS hash, f.rel_path AS rel_path, loc.path AS location_path, "
            "ROW_NUMBER() OVER ("
            "  PARTITION BY p.hash "
            "  ORDER BY (CASE loc.tier WHEN 'fast' THEN 0 ELSE 1 END), f.rel_path"
            ") AS rn "
            "FROM photos p "
            "JOIN files f ON f.photo_hash = p.hash "
            "JOIN locations loc ON loc.id = f.location_id "
            "WHERE (f.location_id, f.rel_path) IN "
            "  (SELECT location_id, rel_path FROM seen) "
            "AND (p.metadata_status = 'pending' OR ? = 1) "
            "AND (? IS NULL OR p.hash > ?)"
            ") WHERE rn = 1 ORDER BY hash LIMIT ?",
            (1 if full else 0, after_hash, after_hash, limit),
        ).fetchall()
        return [
            MetadataTarget(
                hash=str(row["hash"]),
                rel_path=str(row["rel_path"]),
                location_path=Path(str(row["location_path"])),
            )
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
    # Importación de Apple (fase apple-import, ADR 0009)
    # ------------------------------------------------------------------
    def present_rel_paths(self, location_id: int) -> list[tuple[str, str]]:
        """Pares ``(rel_path, photo_hash)`` presentes de una ubicación.

        Lo consume la fase ``apple-import`` para resolver el vínculo con
        los assets de Apple a partir del ``rel_path`` (originals/…).
        """
        rows = self._conn.execute(
            "SELECT rel_path, photo_hash FROM files "
            "WHERE location_id = ? AND status = 'present'",
            (location_id,),
        ).fetchall()
        return [(str(row["rel_path"]), str(row["photo_hash"])) for row in rows]

    def set_apple_uuid(self, photo_hash: str, apple_uuid: str, now: str) -> None:
        """Vincula la foto con el asset de Apple (clave de unión, § 9).

        El vínculo sigue al contenido actual del catálogo: si otra fila de
        ``photos`` conservaba el mismo ``apple_uuid`` (el contenido cambió,
        se rehasheó y nació un hash nuevo), se libera de la fila antigua
        para respetar el índice único parcial. La escritura solo ocurre si
        el vínculo cambia, para no tocar ``updated_at`` en cada pasada.
        """
        self._conn.execute(
            "UPDATE photos SET apple_uuid = NULL WHERE apple_uuid = ? AND hash != ?",
            (apple_uuid, photo_hash),
        )
        self._conn.execute(
            "UPDATE photos SET apple_uuid = ?, updated_at = ? "
            "WHERE hash = ? AND (apple_uuid IS NULL OR apple_uuid != ?)",
            (apple_uuid, now, photo_hash, apple_uuid),
        )

    def upsert_imported_favorite(self, photo_hash: str, now: str) -> bool:
        """Crea la asignación importada de ``fav``; ``False`` si ya estaba.

        ``INSERT OR IGNORE``: una asignación existente —manual del dueño,
        sugerida por ML o importada con otra región— no se toca (§ 10 y
        § 12): el dueño manda sobre lo importado.
        """
        fav = favorite_tag(self._conn)
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO photo_tags "
            "(photo_hash, tag_id, source, status, created_at, updated_at) "
            "VALUES (?, ?, 'import', 'confirmed', ?, ?)",
            (photo_hash, fav.id, now, now),
        )
        return cursor.rowcount == 1

    def revoke_imported_favorite(self, photo_hash: str) -> bool:
        """Quita la asignación importada de ``fav`` (región completa).

        Solo filas ``source = 'import'``: una asignación manual o de otra
        vía jamás se revoca desde la importación (§ 12). Devuelve si hubo
        fila que quitar.
        """
        fav = favorite_tag(self._conn)
        cursor = self._conn.execute(
            "DELETE FROM photo_tags WHERE photo_hash = ? AND tag_id = ? "
            "AND source = 'import' "
            "AND region_x IS NULL AND region_y IS NULL "
            "AND region_w IS NULL AND region_h IS NULL",
            (photo_hash, fav.id),
        )
        return cursor.rowcount > 0

    def ensure_person_tag(self, name: str, now: str) -> Tag:
        """La etiqueta ``person`` con ese nombre, creada si no existía.

        Normalización del ADR 0006 (NFC + casefold + espacios). La
        unicidad la garantiza ``name_normalized UNIQUE`` (``INSERT OR
        IGNORE``) y la fila se resuelve por su nombre normalizado, como
        ``fav`` por su ``kind``.
        """
        normalized = normalize_tag_name(name)
        display_name = _WHITESPACE.sub(" ", name).strip()
        row = self._conn.execute(
            "SELECT id, name, kind, created_at, updated_at FROM tags "
            "WHERE name_normalized = ?",
            (normalized,),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT OR IGNORE INTO tags (name, name_normalized, kind, "
                "created_at, updated_at) VALUES (?, ?, 'person', ?, ?)",
                (display_name, normalized, now, now),
            )
            row = self._conn.execute(
                "SELECT id, name, kind, created_at, updated_at FROM tags "
                "WHERE name_normalized = ?",
                (normalized,),
            ).fetchone()
        if row is None:  # pragma: no cover - la unicidad lo impide
            raise CatalogError("No se pudo crear la etiqueta de persona")
        return Tag(
            id=int(row["id"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def insert_imported_person_assignment(
        self,
        photo_hash: str,
        tag: Tag,
        *,
        region: tuple[float, float, float, float],
        now: str,
    ) -> bool:
        """Crea la asignación importada de una cara; ``False`` si ya estaba.

        ``source='import'`` y ``status='suggested'`` (§ 11). ``INSERT OR
        IGNORE`` respeta la fila existente sea cual sea su procedencia:
        una cara ya asignada —a mano, por ML o importada— no se toca.
        """
        region_x, region_y, region_w, region_h = region
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO photo_tags "
            "(photo_hash, tag_id, source, status, confidence, "
            "region_x, region_y, region_w, region_h, created_at, updated_at) "
            "VALUES (?, ?, 'import', 'suggested', NULL, ?, ?, ?, ?, ?, ?)",
            (photo_hash, tag.id, region_x, region_y, region_w, region_h, now, now),
        )
        return cursor.rowcount == 1

    def imported_person_assignments(self, photo_hash: str) -> list[ImportedPersonRow]:
        """Asignaciones importadas de etiquetas ``person`` de una foto."""
        rows = self._conn.execute(
            "SELECT pt.id, t.name_normalized, pt.region_x, pt.region_y, "
            "pt.region_w, pt.region_h "
            "FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id "
            "WHERE pt.photo_hash = ? AND t.kind = 'person' AND pt.source = 'import'",
            (photo_hash,),
        ).fetchall()
        return [
            ImportedPersonRow(
                id=int(row["id"]),
                name_normalized=str(row["name_normalized"]),
                region_x=(
                    float(row["region_x"]) if row["region_x"] is not None else None
                ),
                region_y=(
                    float(row["region_y"]) if row["region_y"] is not None else None
                ),
                region_w=(
                    float(row["region_w"]) if row["region_w"] is not None else None
                ),
                region_h=(
                    float(row["region_h"]) if row["region_h"] is not None else None
                ),
            )
            for row in rows
        ]

    def delete_imported_assignment(self, assignment_id: int) -> None:
        """Borra una asignación de ``photo_tags`` por su ``id``.

        Solo la llama la revocación import-only de la fase ``apple-import``
        sobre filas ya filtradas por ``source = 'import'``.
        """
        self._conn.execute("DELETE FROM photo_tags WHERE id = ?", (assignment_id,))

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
    def prune(
        self,
        now: datetime,
        older_than_days: int,
        scanned_location_ids: Sequence[int] = (),
    ) -> tuple[int, int]:
        """Limpieza de filas del catálogo (ADR 0010, Decisión 5).

        Solo borra filas —nunca ficheros— y solo de:

        a. ubicaciones **verificadas presentes** en esa ejecución
           (``scanned_location_ids``): filas ``missing`` antiguas según la
           ventana ``--prune-after``;
        b. ubicaciones **``deleted``** (dadas de baja por la config): sus
           filas ``deleted``, con la ventana medida desde el ``deleted_at``
           de la ubicación — la misma gracia que las ``missing``.

        Nunca toca filas de ubicaciones declaradas pero no escaneadas por
        estar ausentes: el prune de un USB desmontado no destruye nada.
        Después limpia las fotos huérfanas (cero filas en ``files``).
        """
        cutoff = (now - timedelta(days=older_than_days)).isoformat()
        pruned_locations = 0
        if scanned_location_ids:
            placeholders = ", ".join("?" for _ in scanned_location_ids)
            cursor = self._conn.execute(
                "DELETE FROM files WHERE status = 'missing' "
                "AND missing_since IS NOT NULL "
                "AND datetime(missing_since) <= datetime(?) "
                f"AND location_id IN ({placeholders})",
                (cutoff, *scanned_location_ids),
            )
            pruned_locations = max(cursor.rowcount, 0)
        cursor = self._conn.execute(
            "DELETE FROM files WHERE status = 'deleted' AND location_id IN ("
            "SELECT id FROM locations WHERE state = 'deleted' "
            "AND deleted_at IS NOT NULL AND datetime(deleted_at) <= datetime(?))",
            (cutoff,),
        )
        pruned_locations += max(cursor.rowcount, 0)
        cursor = self._conn.execute(
            "DELETE FROM photos WHERE hash NOT IN "
            "(SELECT DISTINCT photo_hash FROM files)"
        )
        pruned_photos = max(cursor.rowcount, 0)
        self.commit()
        return pruned_locations, pruned_photos

    def counts(self) -> CatalogCounts:
        photos = self._scalar("SELECT COUNT(*) FROM photos")
        locations = self._scalar("SELECT COUNT(*) FROM files")
        duplicates = self._scalar(
            "SELECT COUNT(*) FROM ("
            "SELECT photo_hash FROM files GROUP BY photo_hash HAVING COUNT(*) > 1"
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

    def list_present_files(
        self, system_tag: str | None = None
    ) -> list[sqlite3.Row]:
        """Apariciones presentes de ubicaciones activas, con su contexto.

        Devuelve cada fila de ``files`` con los metadatos de su foto y la
        ubicación unida (``location_id`` → ``locations``): ``path``,
        ``name`` y ``tier`` salen de la BD, que es la única verdad de
        rutas para la UI (ADR 0010, Decisión 7). Las filas de ubicaciones
        ``deleted`` no se muestran: están pendientes de ``--prune``.

        ``system_tag`` es el nombre de una etiqueta de sistema (ADR 0006).
        Con ``None`` la consulta es la histórica, sin filtro. Con un nombre
        se normaliza y se liga como parámetro en una subconsulta ``EXISTS``
        de la propia sentencia SQL: solo viajan las fotos con una asignación
        ``confirmed``/``suggested`` de esa etiqueta. El predicado corre sobre
        el **hash de la foto**, no sobre la aparición: todas las apariciones
        presentes de una foto etiquetada siguen viajando, de modo que la
        elección canónica por ``tier`` y el ``other_locations`` no cambian.
        Las mismas columnas y el mismo orden en ambos casos.
        """
        if system_tag is None:
            rows = self._conn.execute(_PRESENT_FILES_SQL).fetchall()
        else:
            normalized = normalize_tag_name(system_tag)
            rows = self._conn.execute(
                _PRESENT_FILES_SELECT + _SYSTEM_TAG_EXISTS_SQL + _PRESENT_FILES_ORDER,
                (normalized,),
            ).fetchall()
        return list(rows)

    def has_system_tag(self, system_tag: str) -> bool:
        """Indica si alguna foto tiene vigente la etiqueta de sistema dada.

        Consulta directa con ``SELECT 1 … LIMIT 1`` (no ``_scalar``: aquí
        interesa solo la existencia, no un conteo): es la comprobación barata
        que decide si merece la pena filtrar sin leer las filas de ``files``.
        Cuenta ``confirmed`` y ``suggested``, nunca ``rejected`` (ADR 0006,
        Decisión 6), y resuelve la etiqueta por ``kind='system'`` y nombre
        normalizado, jamás por ``id`` (Decisión 4). ``False`` si la etiqueta
        existe pero no tiene asignaciones vigentes.
        """
        normalized = normalize_tag_name(system_tag)
        row = self._conn.execute(
            _SYSTEM_TAG_EXISTS_ANY_SQL,
            (normalized,),
        ).fetchone()
        return row is not None

    def _scalar(self, query: str, parameters: Sequence[object] = ()) -> int:
        row = self._conn.execute(query, parameters).fetchone()
        return int(row[0]) if row is not None else 0
