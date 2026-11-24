"""Modelo de UI de una foto del catálogo.

``Photo`` es el modelo inmutable con el que la interfaz consume el catálogo
(ADR 0003): una entrada por foto, con sus metadatos normalizados y su clave
de identidad. Vive deliberadamente en la capa de UI —no en ``foto.core``—
porque es el modelo de presentación del visor, no lógica de negocio, y sus
consumidores dependen de textos en español. No importa Qt y se prueba sin
pantalla.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foto.storage.tags import Region, TagAssignment

MAP_URL = "https://www.google.com/maps?q={lat},{lon}"


def _ui_region(assignment: TagAssignment) -> Region | None:
    """Región en el sistema de la UI: origen arriba-izquierda, `y` hacia abajo.

    Las asignaciones con ``source='import'`` —tanto las de ``apple-import``
    como las que reimporta ``foto-import``— guardan `y` desde abajo (convención
    de ``ZDETECTEDFACE``, ADR 0009). Se convierten aquí, en la única frontera
    núcleo/UI: el catálogo y ``foto-export`` conservan el valor de origen y no
    cambia ningún fichero. El resto de fuentes ya usan la convención del ADR
    0006 y pasan tal cual; ``foto-import`` vuelve a escribir la misma
    coordenada, así que el viaje de ida y vuelta sigue siendo estable.
    """
    region = assignment.region
    if region is None or assignment.source != "import":
        return region
    x, y, w, h = region
    return (x, max(0.0, min(1.0, 1.0 - y - h)), w, h)


@dataclass(frozen=True)
class PhotoTag:
    """Etiqueta asignada a una foto, tal como la consume la UI (ADR 0006).

    ``kind``/``status``/``source`` son los valores del esquema del catálogo
    (``system``/``free``, ``confirmed``/``suggested``/``rejected``,
    ``manual``/``ml``); las reglas de presentación deciden qué se muestra.
    ``region`` es la región proporcional del visor en convención de la UI
    (origen arriba-izquierda), o ``None`` para la foto completa; se añade al
    final para no romper constructores existentes.
    """

    name: str
    kind: str
    status: str
    source: str
    region: Region | None = None


@dataclass(frozen=True)
class LocationStatus:
    """Estado de una ubicación de la fototeca para la presentación (ADR 0010).

    Los datos lógicos (nombre, ``tier``, ruta, estado, nº de fotos) salen de
    la tabla ``locations`` del catálogo —la única referencia de ubicaciones—;
    ``available`` es el estado físico evaluado con la función pura de
    ``storage/availability`` al cargar la ventana, no en cada acceso.
    """

    name: str
    tier: str
    path: Path
    state: str
    available: bool
    photo_count: int
    last_scanned_at: str | None = None


def map_url(latitude: float, longitude: float) -> str:
    """URL de Google Maps para unas coordenadas decimales (grados).

    El parámetro ``q`` fija el pin en el punto exacto; coma, punto y
    negativos son válidos sin escapar en la cadena de consulta.
    """
    return MAP_URL.format(lat=latitude, lon=longitude)


@dataclass(frozen=True)
class Photo:
    name: str
    taken: str
    camera: str
    lens: str
    focal: str
    aperture: str
    shutter: str
    iso: str
    folder: str
    fmt: str
    dimensions: str
    size: str
    location: str
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    place_country: str | None = None
    place_country_code: str | None = None
    place_state: str | None = None
    place_city: str | None = None
    place_municipality: str | None = None
    place_display_name: str | None = None
    place_address_json: str | None = None
    tags: tuple[PhotoTag, ...] = ()
    path: Path | None = None
    canonical_rel_path: str | None = None
    #: Nombre de la ubicación cuya aparición sirve la foto (ADR 0010).
    canonical_location: str | None = None
    #: ``False`` cuando el fichero que sirve la foto no está a mano (USB
    #: desmontado): *placeholder* fantasma con los datos del catálogo.
    available: bool = True
    other_locations: tuple[str, ...] = ()
    photo_hash: str | None = None

    @property
    def favorite(self) -> bool:
        """Favorito según la etiqueta de sistema ``fav`` (ADR 0006, Decisión 8).

        Ya no es un campo: deriva de las etiquetas. Cuentan los estados
        ``confirmed`` y ``suggested``; ``rejected`` jamás marca favorito.
        """
        return any(
            tag.kind == "system" and tag.status in ("confirmed", "suggested")
            for tag in self.tags
        )

    @property
    def key(self) -> str:
        """Identifica la foto aunque haya nombres repetidos en la biblioteca."""
        return str(self.path) if self.path is not None else self.name

    @classmethod
    def from_catalog(cls, *, tags: Sequence[Any] = (), **fields: Any) -> Photo:
        """Modelo de UI desde los campos que ya calculó el catálogo (T1.4).

        Fábrica inyectada en ``load_from_catalog``: recibe los argumentos
        nombrados que ese loader prepara —los mismos campos de ``Photo``,
        con las etiquetas como tupla de asignaciones del catálogo— y
        construye el objeto **una sola vez**. Sustituye a la copia campo a
        campo de ``_to_ui_photo``: la única conversión que queda es la de
        las etiquetas (``TagAssignment`` → ``PhotoTag``), que cambia de
        forma en la frontera núcleo/UI. Ahí se normaliza también el origen
        de `y` de las regiones importadas (ADR 0009).
        """
        converted = tuple(
            PhotoTag(
                name=tag.tag_name,
                kind=tag.kind,
                status=tag.status,
                source=tag.source,
                region=_ui_region(tag),
            )
            for tag in tags
        )
        return cls(tags=converted, **fields)  # type: ignore[arg-type]
