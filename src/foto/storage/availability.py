"""Disponibilidad de ubicaciones (ADR 0010, Decisión 6).

Función pura e inyectable compartida en ``storage/`` (sin Qt): la usan
``foto-index`` antes de escanear y la UI al cargar; nunca en cada acceso a
fichero. Solo hay dos estados — el marcador de volumen y el estado
``mismatch`` se descartaron (§ 7.2 del prompt): ``available`` es «la raíz
existe y es directorio», y todo lo demás es ``unavailable``.

La comprobación se encapsula tras el parámetro ``checker`` para que el
futuro backend de volúmenes remotos (ADR 0010, Decisión 9) pueda sustituir
«el directorio existe» por «el backend responde» sin tocar a los
consumidores.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

#: La raíz existe y es directorio: la ubicación está a mano.
AVAILABLE: Literal["available"] = "available"

#: No existe (USB desmontado): aviso y degradación, jamás baja lógica.
UNAVAILABLE: Literal["unavailable"] = "unavailable"

AvailabilityState = Literal["available", "unavailable"]


def _is_local_directory(path: Path) -> bool:
    """Comprobación por defecto: «existe y es directorio»."""
    return path.is_dir()


def availability_state(
    path: Path,
    *,
    checker: Callable[[Path], bool] | None = None,
) -> AvailabilityState:
    """Estado de disponibilidad de una ubicación (función pura).

    ``checker`` devuelve ``True`` cuando la raíz está a mano; por defecto
    es «existe y es directorio». Inyectable para tests y para un futuro
    backend remoto.
    """
    check = checker if checker is not None else _is_local_directory
    return AVAILABLE if check(path) else UNAVAILABLE
