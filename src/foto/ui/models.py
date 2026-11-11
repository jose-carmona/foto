"""Modelo de UI de una foto del catálogo.

``Photo`` es el modelo inmutable con el que la interfaz consume el catálogo
(ADR 0003): una entrada por foto, con sus metadatos normalizados y su clave
de identidad. Vive deliberadamente en la capa de UI —no en ``foto.core``—
porque es el modelo de presentación del visor, no lógica de negocio, y sus
consumidores dependen de textos en español. No importa Qt y se prueba sin
pantalla.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAP_URL = "https://www.google.com/maps?q={lat},{lon}"


@dataclass(frozen=True)
class PhotoTag:
    """Etiqueta asignada a una foto, tal como la consume la UI (ADR 0006).

    ``kind``/``status``/``source`` son los valores del esquema del catálogo
    (``system``/``free``, ``confirmed``/``suggested``/``rejected``,
    ``manual``/``ml``); las reglas de presentación deciden qué se muestra.
    """

    name: str
    kind: str
    status: str
    source: str


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
