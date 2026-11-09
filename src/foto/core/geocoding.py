"""Modelo normalizado del lugar, contrato del geocodificador y caché por celda.

Sin Qt y sin HTTP: el cliente de Nominatim (``foto.indexer.geonames``)
implementa el protocolo ``Geocoder`` y queda aislado del indexador y de los
tests, que no conocen HTTP.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

#: Estados del lugar de una foto, en la línea de ``metadata_status``.
PLACE_STATES = ("none", "pending", "ok", "error")

#: Decimales del redondeo por celda: 3 decimales ≈ 110 m de lado.
CELL_DECIMALS = 3

#: Zoom de Nominatim para las peticiones ``reverse``: nivel aldea/localidad,
#: que devuelve la jerarquía completa (país, región, municipio…) sin arrastrar
#: números de portal. Las celdas cacheadas con menos zoom se vuelven a pedir
#: (ADR 0005, revisión de 2026-11-09).
REVERSE_ZOOM = 14

#: Variantes de «ciudad» que devuelve la sección ``address`` de Nominatim.
CITY_KEYS = ("city", "town", "village", "municipality")


@dataclass(frozen=True)
class Place:
    """Lugar normalizado de una foto.

    Los campos que el servicio no devolvió son ``None``: no se inventan
    cadenas vacías ni nombres. ``city`` agrupa las variantes de localidad
    (``city``, ``town``, ``village`` y, por último, ``municipality``);
    ``municipality`` conserva el campo literal del servicio para agrupar
    administrativamente.
    """

    country: str | None = None
    country_code: str | None = None
    state: str | None = None
    city: str | None = None
    municipality: str | None = None

    @property
    def known(self) -> bool:
        """True si el servicio devolvió alguna parte de la jerarquía."""
        return any(
            part is not None
            for part in (
                self.country,
                self.country_code,
                self.state,
                self.city,
                self.municipality,
            )
        )

    def display(self) -> str | None:
        """Formato de visualización ``Municipio, País`` con lo disponible.

        Prefiere el municipio (la entidad administrativa que agrupa las
        fotos de un mismo sitio), luego la localidad y luego la región.
        """
        primary = self.municipality or self.city or self.state
        if primary is None:
            primary = self.country
            secondary = None
        else:
            secondary = self.country
        if primary is None:
            # Última oportunidad: el código de país, si el servicio no dio
            # ni nombre de país. Si nada queda, sin lugar que mostrar.
            return self.country_code
        if secondary is None:
            return primary
        return f"{primary}, {secondary}"


@dataclass(frozen=True)
class PlaceRecord:
    """Lugar resuelto por el servicio con toda su información.

    ``place`` es la jerarquía normalizada que usan orden, grupos y panel;
    ``display_name`` y ``address_json`` son «toda la información» del
    servicio tal cual llegó: la cadena completa que renderiza Nominatim y
    el objeto ``address`` íntegro serializado (ADR 0005, revisión).
    """

    place: Place
    display_name: str | None = None
    address_json: str | None = None


def _clean_part(value: object) -> str | None:
    """Normaliza un campo de la respuesta: sin vacíos ni marcadores falsos."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower() in {"unknown", "none", "null", "n/a"}:
        return None
    return text


def place_from_address(address: Mapping[str, object]) -> Place:
    """Construye un ``Place`` desde la sección ``address`` de Nominatim."""
    city = None
    for key in CITY_KEYS:
        city = _clean_part(address.get(key))
        if city is not None:
            break
    return Place(
        country=_clean_part(address.get("country")),
        country_code=_clean_part(address.get("country_code")),
        state=_clean_part(address.get("state")),
        city=city,
        municipality=_clean_part(address.get("municipality")),
    )


@dataclass(frozen=True)
class Cell:
    """Celda del plan de caché: coordenadas ya redondeadas."""

    latitude: float
    longitude: float

    @property
    def key(self) -> tuple[float, float]:
        """Clave de caché de la celda."""
        return (self.latitude, self.longitude)


def cell_for(latitude: float, longitude: float) -> Cell:
    """Redondea unas coordenadas GPS a su celda de caché."""
    return Cell(
        latitude=round(latitude, CELL_DECIMALS),
        longitude=round(longitude, CELL_DECIMALS),
    )


class Geocoder(Protocol):
    """Contrato de un geocodificador inverso: coordenadas en, lugar fuera."""

    def reverse(self, latitude: float, longitude: float) -> PlaceRecord:
        """Resuelve una celda con el servicio.

        Devuelve un ``PlaceRecord`` —la jerarquía normalizada en ``place``
        (con partes ``None`` si venía incompleta) más el ``display_name`` y
        el ``address`` completo del servicio— o lanza
        ``GeocodingRejectedError`` ante un rechazo del servicio o un fallo
        de red, y ``GeocodingResponseError`` si la respuesta es vacía o no
        interpretable para esa petición concreta.
        """
