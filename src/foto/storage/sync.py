"""Sincronización config ↔ catálogo al arrancar ``foto-index`` (ADR 0010).

``foto-index`` es el único componente que lee la declaración de
``[[library.locations]]`` para indexar; aquí compara esa declaración con la
tabla ``locations`` del catálogo y aplica el diff. La identidad de una
ubicación es su ``name`` (estable; ``path`` y ``tier`` pueden cambiar):

- **Alta** — nombre declarado que no está en el catálogo: se registra
  ``active`` y se escanea en esa misma ejecución (si está disponible).
- **Cambio** — nombre ``active`` con otro ``path`` o ``tier``: se
  actualiza, con aviso en el resumen; las filas de fotos siguen siendo
  válidas y el escaneo reconciliará la ruta nueva.
- **Baja** — nombre del catálogo ``active`` que ya no está en la config:
  pasa a ``deleted`` y sus filas de fotos se marcan ``deleted``. No se
  borra nada: ni filas ni ficheros (regla 1 del AGENT.md); la limpieza de
  filas es de ``--prune``.
- **Reactivación** — nombre declarado que está ``deleted``: vuelve a
  ``active``. No restaura filas: las restaura el escaneo de esa ubicación.
- **Renombrar** es baja + alta del mismo diff, sin caso especial.

Propiedades: idempotente (dos ejecuciones con la misma config no cambian
nada), determinista y reportada en un ``SyncReport``. Corre **dentro del
lock** del indexador, tras abrir (y migrar) el catálogo y antes de
escanear.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from foto.storage.catalog import Catalog
from foto.storage.config import Location

logger = logging.getLogger(__name__)

__all__ = ["SyncReport", "sync_locations"]


@dataclass(frozen=True)
class SyncReport:
    """Diff aplicado entre la config y la tabla ``locations``.

    ``path_changed`` recoge los avisos de cambio de ``path`` o ``tier`` de
    una ubicación activa (el bloque ``--json`` del ADR los agrupa).
    """

    added: tuple[str, ...] = field(default=())
    removed: tuple[str, ...] = field(default=())
    reactivated: tuple[str, ...] = field(default=())
    path_changed: tuple[str, ...] = field(default=())

    @property
    def changed(self) -> bool:
        """¿Hubo alguna operación en este diff?"""
        return bool(self.added or self.removed or self.reactivated or self.path_changed)

    def to_dict(self) -> dict[str, list[str]]:
        """Representación serializable para el bloque ``locations``."""
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "reactivated": list(self.reactivated),
            "path_changed": list(self.path_changed),
        }


def sync_locations(
    catalog: Catalog,
    declared: Sequence[Location],
    now: datetime,
) -> SyncReport:
    """Aplica el diff config ↔ catálogo. Idempotente y determinista.

    Las altas y los cambios se procesan en el orden de la config; las
    bajas, en orden alfabético. Todo el diff se confirma en una única
    transacción al final. Una ubicación ausente (volumen desmontado)
    **nunca** se da de baja aquí: la baja solo la provoca la config.
    """
    existing = {row.name: row for row in catalog.list_named_locations()}
    declared_by_name = {location.name: location for location in declared}

    added: list[str] = []
    removed: list[str] = []
    reactivated: list[str] = []
    path_changed: list[str] = []
    timestamp = now.replace(microsecond=0).isoformat()

    for name, location in declared_by_name.items():
        row = existing.get(name)
        if row is None:
            catalog.insert_named_location(
                name=name,
                path=location.path,
                tier=location.tier,
                now=timestamp,
            )
            added.append(name)
            logger.info(
                "Ubicación «%s» registrada (%s, %s)", name, location.tier, location.path
            )
            continue

        path_or_tier_changed = (
            Path(row.path) != location.path or row.tier != location.tier
        )
        if row.state == "deleted":
            catalog.reactivate_named_location(location_id=row.id)
            reactivated.append(name)
            logger.info(
                "Ubicación «%s» reactivada: sus filas las restaura el escaneo",
                name,
            )
        if path_or_tier_changed:
            catalog.update_named_location(
                location_id=row.id, path=location.path, tier=location.tier
            )
            path_changed.append(name)
            logger.warning(
                "La ubicación «%s» cambió (%s → %s, %s → %s): el escaneo "
                "reconciliará la ruta nueva",
                name,
                row.path,
                location.path,
                row.tier,
                location.tier,
            )

    for name in sorted(existing):
        row = existing[name]
        if row.state == "active" and name not in declared_by_name:
            marked = catalog.deactivate_named_location(location_id=row.id, now=timestamp)
            removed.append(name)
            logger.warning(
                "La ubicación «%s» ya no está declarada en la config: dada de "
                "baja con %d filas marcadas deleted (nada se borra; la "
                "limpieza es de --prune)",
                name,
                marked,
            )

    catalog.commit()
    return SyncReport(
        added=tuple(added),
        removed=tuple(removed),
        reactivated=tuple(reactivated),
        path_changed=tuple(path_changed),
    )
