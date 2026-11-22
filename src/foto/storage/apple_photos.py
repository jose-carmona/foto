"""Lector solo lectura de ``database/Photos.sqlite`` (ADR 0009, § 8).

La base de datos de Apple Photos es la única fuente de favoritos y
personas. Este módulo abre el fichero del paquete en **solo lectura
estricta** (URI SQLite ``mode=ro&immutable=1``: no crea ``-wal`` ni
``-shm``, no toma locks y no toca el paquete) y devuelve un modelo propio
reducido a lo que la fase ``apple-import`` importa: el asset con su
indicador de favorito y sus caras de personas nombradas con la región
facial.

Nada más se mapea: álbumes (``ZGENERICALBUM``), títulos, descripciones y
el resto del modelo Core Data de Apple no se leen ni se importan —no hay
destino en el esquema actual y su incorporación futura está en el roadmap
(README, Fase 5)—. Módulo sin Qt.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from foto.core.errors import ApplePhotosError

logger = logging.getLogger(__name__)

#: Ruta de la base de datos dentro del paquete de la fototeca.
DB_RELATIVE_PATH = Path("database") / "Photos.sqlite"

#: Tablas y columnas que la fase necesita; el resto del modelo se ignora.
_REQUIRED_MODEL: dict[str, tuple[str, ...]] = {
    "ZASSET": ("ZUUID", "ZFAVORITE"),
    "ZPERSON": ("ZFULLNAME",),
    "ZDETECTEDFACE": (
        "ZCENTERX",
        "ZCENTERY",
        "ZSIZE",
        "ZASSETFORFACE",
        "ZPERSONFORFACE",
    ),
}


@dataclass(frozen=True)
class ApplePerson:
    """Una cara importable: persona nombrada y su región [0,1].

    La región es el rectángulo derivado del centro y el lado que Apple
    guarda en ``ZDETECTEDFACE`` (ADR 0009, § 11), ya recortado a [0,1]
    para el ``CHECK`` de ``photo_tags``.
    """

    name: str
    region_x: float
    region_y: float
    region_w: float
    region_h: float


@dataclass(frozen=True)
class AppleAsset:
    """Un asset de ``ZASSET`` con lo que se importa de él."""

    uuid: str
    favorite: bool
    persons: tuple[ApplePerson, ...] = ()


@dataclass(frozen=True)
class AppleSnapshot:
    """Estado importable de la biblioteca leída.

    ``assets`` indexa por el identificador del asset (``ZASSET.ZUUID``);
    ``unnamed_faces`` cuenta las caras de personas sin nombre, que no se
    importan (no hay etiqueta que crear) pero sí se reportan.
    """

    assets: dict[str, AppleAsset] = field(default_factory=dict)
    unnamed_faces: int = 0


def _face_region(
    center_x: float, center_y: float, size: float
) -> tuple[float, float, float, float] | None:
    """Región rectangular [0,1] desde el centro y el lado (§ 11).

    ``x = cx − s/2``, ``y = cy − s/2``, ``w = h = s``, recortada a [0,1].
    Devuelve ``None`` si la cara no da una región válida (fuera de rango o
    vacía): no se importa y el recuento de errores no la considera error
    de fase, es un dato que Apple no describió bien.
    """
    if not 0.0 < size <= 1.0:
        return None
    if not 0.0 <= center_x <= 1.0 or not 0.0 <= center_y <= 1.0:
        return None
    region_x = min(max(center_x - size / 2, 0.0), 1.0)
    region_y = min(max(center_y - size / 2, 0.0), 1.0)
    side = min(size, 1.0)
    return region_x, region_y, side, side


class ApplePhotosLibrary:
    """Abre ``Photos.sqlite`` de un paquete Apple en solo lectura estricta.

    La URI ``mode=ro&immutable=1`` garantiza que SQLite no cree ``-wal`` ni
    ``-shm`` (los sidecars son del paquete y viven en modo WAL), no toma
    locks y no toca el fichero. ``immutable=1`` puede leer datos
    ligeramente anteriores al último checkpoint de Photos: aceptable,
    porque la fase es repetible y recalcula el estado completo cada pasada
    (ADR 0009, Decisión 6).
    """

    def __init__(self, root: Path) -> None:
        self._path = root / DB_RELATIVE_PATH
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Cierra la conexión si llegó a abrirse (idempotente)."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                logger.debug(
                    "La conexión de Photos.sqlite ya estaba cerrada", exc_info=True
                )
            self._conn = None

    def _connection(self) -> sqlite3.Connection:
        """La conexión de solo lectura, abierta la primera vez."""
        if self._conn is not None:
            return self._conn
        try:
            exists = self._path.is_file()
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise ApplePhotosError(
                f"No se pudo acceder a {DB_RELATIVE_PATH}: {reason}"
            ) from exc
        if not exists:
            raise ApplePhotosError(
                f"El paquete no contiene {DB_RELATIVE_PATH}: la fase "
                "apple-import no tiene de dónde importar favoritos y personas"
            )
        try:
            connection = sqlite3.connect(
                f"{self._path.as_uri()}?mode=ro&immutable=1", uri=True
            )
        except sqlite3.Error as exc:
            raise ApplePhotosError(
                f"No se pudo abrir {DB_RELATIVE_PATH} en solo lectura: {exc}"
            ) from exc
        connection.row_factory = sqlite3.Row
        self._conn = connection
        return connection

    def read_snapshot(self) -> AppleSnapshot:
        """Lee favoritos, personas y caras; nada más se consulta.

        El modelo se valida por presencia de tablas y columnas: una base de
        datos de una versión sin lo esperado es un error explícito, no un
        escaneo silencioso.
        """
        connection = self._connection()
        self._validate_model(connection)
        assets: dict[str, AppleAsset] = {}
        for row in connection.execute("SELECT ZUUID, ZFAVORITE FROM ZASSET"):
            uuid = str(row["ZUUID"])
            assets[uuid] = AppleAsset(
                uuid=uuid,
                favorite=bool(row["ZFAVORITE"]),
                persons=(),
            )

        faces_by_uuid: dict[str, list[ApplePerson]] = {}
        unnamed_faces = 0
        for row in connection.execute(
            "SELECT a.ZUUID AS asset_uuid, p.ZFULLNAME AS person_name, "
            "f.ZCENTERX AS center_x, f.ZCENTERY AS center_y, f.ZSIZE AS size "
            "FROM ZDETECTEDFACE f "
            "JOIN ZASSET a ON a.Z_PK = f.ZASSETFORFACE "
            "LEFT JOIN ZPERSON p ON p.Z_PK = f.ZPERSONFORFACE"
        ):
            name = row["person_name"]
            if name is None or not str(name).strip():
                # Cara de persona sin nombre: se cuenta, no se importa (§ 11).
                unnamed_faces += 1
                continue
            region = _face_region(
                float(row["center_x"]), float(row["center_y"]), float(row["size"])
            )
            if region is None:
                continue
            faces_by_uuid.setdefault(str(row["asset_uuid"]), []).append(
                ApplePerson(
                    name=str(name).strip(),
                    region_x=region[0],
                    region_y=region[1],
                    region_w=region[2],
                    region_h=region[3],
                )
            )

        for uuid, faces in faces_by_uuid.items():
            asset = assets.get(uuid)
            if asset is None:  # pragma: no cover - el join lo impide
                continue
            assets[uuid] = AppleAsset(
                uuid=uuid,
                favorite=asset.favorite,
                persons=tuple(faces),
            )
        return AppleSnapshot(assets=assets, unnamed_faces=unnamed_faces)

    @staticmethod
    def _validate_model(connection: sqlite3.Connection) -> None:
        """Exige las tablas y columnas del modelo que la fase consume."""
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing_tables = sorted(set(_REQUIRED_MODEL) - tables)
        if missing_tables:
            raise ApplePhotosError(
                f"{DB_RELATIVE_PATH} no tiene las tablas esperadas: "
                f"{', '.join(missing_tables)}"
            )
        for table, columns in _REQUIRED_MODEL.items():
            existing = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            absent = [column for column in columns if column not in existing]
            if absent:
                raise ApplePhotosError(
                    f"{DB_RELATIVE_PATH}: la tabla {table} no tiene las "
                    f"columnas esperadas: {', '.join(absent)}"
                )


def apple_uuid_from_rel_path(rel_path: str) -> str | None:
    """El UUID del asset de un ``rel_path`` ``originals/<h>/<UUID>.<ext>``.

    El nombre base del fichero original **es** el identificador del asset
    en ``Photos.sqlite`` (ADR 0009, Apéndice A.2); se devuelve en
    mayúsculas, como lo escribe Apple. Cualquier otra ruta —variantes
    editadas, ficheros sueltos— no es un asset indexado y devuelve
    ``None``.
    """
    match = APPLE_ASSET_REL_PATH.match(rel_path)
    return match.group(1).upper() if match is not None else None


APPLE_ASSET_REL_PATH = re.compile(
    r"^originals/[^/]+/([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.[A-Za-z0-9]+$"
)
