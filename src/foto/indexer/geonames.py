"""Cliente de geocodificación inversa sobre Nominatim.

Implementa el contrato ``Geocoder`` de ``core/geocoding.py`` con solo la
stdlib: ``urllib.request`` sin shell, un User-Agent identificativo y honesto,
throttle máximo de una petición por segundo (siempre activo) y timeout
configurables. Las excepciones propias de ``core`` separan el rechazo del
servicio (o fallo de red), que detiene la fase, de una respuesta no
interpretable para una petición concreta, que se marca como ``error``.

El resto del indexador y los tests no conocen HTTP: inyectan ``sleep``,
reloj monótonico y ``urlopen`` en los tests.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from foto.core.errors import GeocodingRejectedError, GeocodingResponseError
from foto.core.geocoding import Place, place_from_address

logger = logging.getLogger(__name__)

#: Parámetros fijos del endpoint ``reverse`` de Nominatim (jsonv2 + dirección).
REVERSE_FORMAT_PARAMS = (("format", "jsonv2"), ("addressdetails", "1"))

#: Zoom de Nominatim que devuelve jerarquía país/región/ciudad (ADR 0005).
REVERSE_ZOOM = 10


@dataclass(frozen=True)
class ReverseGeocoderConfig:
    """Configuración de un cliente de geocodificación inversa.

    Los valores por defecto sensatos los aporta
    ``foto.storage.config.GeocodingSettings`` (``[geocoding]`` de
    ``config.toml``); aquí ningún campo es opcional para que el mapa de
    configuración quede en un solo sitio.
    """

    endpoint: str
    user_agent: str
    language: str
    request_interval_seconds: float
    timeout_seconds: float


class NominatimGeocoder:
    """Geocodificador inverso contra el endpoint ``reverse`` de Nominatim."""

    def __init__(
        self,
        config: ReverseGeocoderConfig,
        *,
        urlopen: Callable[..., object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if config.request_interval_seconds < 0:
            raise ValueError("request_interval_seconds no puede ser negativo")
        if config.timeout_seconds <= 0:
            raise ValueError("timeout_seconds debe ser positivo")
        self._config = config
        self._urlopen = urlopen or urllib.request.urlopen
        self._sleep = sleep
        self._monotonic = monotonic
        self._next_at: float | None = None

    def reverse(self, latitude: float, longitude: float) -> Place:
        """Resuelve una celda con Nominatim, respetando el throttle."""
        self._throttle()
        place = self._reverse_raw(latitude, longitude)
        self._next_at = self._monotonic() + self._config.request_interval_seconds
        return place

    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        """Duerme el tiempo que falte hasta la próxima ventana de petición."""
        if self._next_at is None:
            return
        remaining = self._next_at - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)
            # El reloj reinyectable puede no avanzar durante el sleep: se
            # relee para dejar la ventana coherente con el reloj inyectado.
            self._next_at = self._monotonic()

    def _reverse_raw(self, latitude: float, longitude: float) -> Place:
        params = {
            "lat": repr(float(latitude)),
            "lon": repr(float(longitude)),
            "zoom": REVERSE_ZOOM,
            "accept-language": self._config.language,
        }
        for key, value in REVERSE_FORMAT_PARAMS:
            params[key] = value
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self._config.endpoint}?{query}",
            headers={
                "User-Agent": self._config.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with self._urlopen(request, timeout=self._config.timeout_seconds) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise GeocodingRejectedError(
                f"El servicio rechazó la petición (HTTP {exc.code})"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise GeocodingRejectedError(
                f"No se pudo contactar con el servicio: {reason}"
            ) from exc

        try:
            payload: Mapping[str, object] = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GeocodingResponseError(
                "El servicio devolvió una respuesta que no es JSON válido"
            ) from exc

        if not isinstance(payload, Mapping):
            raise GeocodingResponseError(
                "El servicio devolvió una respuesta inesperada"
            )

        address = payload.get("address")
        if not isinstance(address, Mapping):
            # Incluye los objetos de error de Nominatim (p. ej. «Unable to
            # geocode»): sin sección ``address`` no hay lugar interpretable.
            raise GeocodingResponseError(
                "El servicio no devolvió un lugar interpretable para esa celda"
            )
        return place_from_address(address)
