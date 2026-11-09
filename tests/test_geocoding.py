"""Tests del modelo de lugar, la caché por celda y el cliente Nominatim."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse

import pytest

from foto.core.errors import GeocodingRejectedError, GeocodingResponseError
from foto.core.geocoding import Place, PlaceRecord, cell_for, place_from_address
from foto.indexer.geonames import NominatimGeocoder, ReverseGeocoderConfig


def make_place(**changes) -> Place:
    """Place por defecto con los cambios pedidos."""
    fields = {
        "country": "España",
        "country_code": "ES",
        "state": "Comunidad de Madrid",
        "city": "Madrid",
    }
    fields.update(changes)
    return Place(**fields)


# ----------------------------------------------------------------------
# Place y celda
# ----------------------------------------------------------------------
def test_place_display_format_is_city_country():
    assert make_place().display() == "Madrid, España"


def test_place_display_prefers_the_municipality():
    # Revisión del ADR 0005: el grupo es «Municipio, País»; cuando el
    # servicio dio municipality, manda sobre la localidad.
    assert make_place(municipality="Madrid").display() == "Madrid, España"
    assert make_place(
        municipality="Comunidad de Madrid", city="Madrid"
    ).display() == "Comunidad de Madrid, España"
    assert make_place(municipality=None).display() == "Madrid, España"


def test_place_display_with_missing_parts():
    assert make_place(city=None).display() == "Comunidad de Madrid, España"
    assert make_place(city=None, state=None).display() == "España"
    assert make_place(city=None, state=None, country=None).display() == "ES"
    assert make_place(state=None).display() == "Madrid, España"
    assert make_place(
        city=None, state=None, country=None, country_code=None
    ).display() is None


def test_place_record_defaults_without_detail():
    record = PlaceRecord(place=make_place())

    assert record.place == make_place()
    assert record.display_name is None
    assert record.address_json is None


def test_cell_rounding_to_three_decimals():
    cell = cell_for(40.4167754, -3.7037902)
    assert cell.key == (40.417, -3.704)


def test_same_cell_shares_one_key():
    assert cell_for(40.41680, -3.70381).key == cell_for(40.41704, -3.70376).key


def test_place_from_address_variants_of_city():
    place = place_from_address({"town": "Zumaia", "country": "España"})
    assert place.city == "Zumaia"
    place = place_from_address({"village": "Zumaia"})
    assert place.city == "Zumaia"
    place = place_from_address({"municipality": "Zumaia"})
    assert place.city == "Zumaia"


def test_place_from_address_keeps_the_municipality_literal():
    # Caso real de Arcachón: town y municipality coinciden; en comunas
    # rurales solo hay municipality y ``city`` cae a ella por el fallback.
    place = place_from_address(
        {
            "hamlet": "Ancien Sanatorium Vincent de Paul",
            "town": "Arcachón",
            "municipality": "Arcachón",
            "state": "Nueva Aquitania",
            "country": "Francia",
            "country_code": "fr",
        }
    )

    assert place.city == "Arcachón"
    assert place.municipality == "Arcachón"
    assert place.state == "Nueva Aquitania"
    assert place.display() == "Arcachón, Francia"


def test_place_from_address_normalizes_empty_values():
    place = place_from_address({"city": "  ", "country": "", "state": "N/A"})
    assert place.city is None
    assert place.state is None
    assert place.country_code is None
    assert place.known is False


# ----------------------------------------------------------------------
# Cliente Nominatim
# ----------------------------------------------------------------------
class FakeURLOpen:
    """``urlopen`` falso que devuelve un cuerpo o lanza una excepción."""

    def __init__(self, *, body: bytes | None = b"{}", error: Exception | None = None):
        self.body = body if body is not None else b"{}"
        self.error = error
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.body)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def make_client(
    fake_urlopen: FakeURLOpen,
    *,
    language: str = "es",
    interval: float = 1.0,
    timeout: float = 5.0,
    sleeps: list[float] | None = None,
    clock: float = 0.0,
) -> NominatimGeocoder:
    """Cliente Nominatim sin red con reloj y sleep inyectables."""
    recorded = sleeps if sleeps is not None else []
    config = ReverseGeocoderConfig(
        endpoint="https://nominatim.openstreetmap.org/reverse",
        user_agent="foto/test (biblioteca de pruebas)",
        language=language,
        request_interval_seconds=interval,
        timeout_seconds=timeout,
    )
    return NominatimGeocoder(
        config,
        urlopen=fake_urlopen,
        sleep=recorded.append,
        monotonic=lambda: clock,
    )


HIERARCHICAL_BODY = json.dumps(
    {
        "place_id": 123,
        "lat": "40.4168",
        "lon": "-3.7038",
        "display_name": "Madrid, España",
        "address": {
            "city": "Madrid",
            "state": "Comunidad de Madrid",
            "country": "España",
            "country_code": "es",
        },
    }
).encode("utf-8")


def test_reverse_maps_a_hierarchical_response():
    fake = FakeURLOpen(body=HIERARCHICAL_BODY)
    client = make_client(fake)

    record = client.reverse(40.4168, -3.7038)

    assert record.place == make_place(country_code="es")
    # «Toda la información del servicio»: cadena completa y address íntegro.
    assert record.display_name == "Madrid, España"
    assert json.loads(record.address_json) == {
        "city": "Madrid",
        "state": "Comunidad de Madrid",
        "country": "España",
        "country_code": "es",
    }


def test_reverse_request_holds_policy_and_parameters():
    fake = FakeURLOpen(body=HIERARCHICAL_BODY)
    client = make_client(fake, language="ca")

    client.reverse(40.4168, -3.7038)

    request = fake.requests[0]
    assert request.get_header("User-agent") == "foto/test (biblioteca de pruebas)"
    query = urllib.parse.parse_qs(request.full_url.split("?", 1)[1])
    assert query["format"] == ["jsonv2"]
    assert query["addressdetails"] == ["1"]
    # Zoom 14 (aldea/localidad): con 10 la respuesta era solo país/región
    # (caso de Arcachón: «Francia metropolitana, Francia»).
    assert query["zoom"] == ["14"]
    assert query["lat"] == ["40.4168"]
    assert query["lon"] == ["-3.7038"]
    assert query["accept-language"] == ["ca"]


def test_reverse_handles_response_without_hierarchy_parts():
    body = json.dumps(
        {"place_id": 5, "display_name": "Mont Blanc", "address": {"country": "Francia"}}
    ).encode()
    client = make_client(FakeURLOpen(body=body))

    record = client.reverse(45.8326, 6.8652)

    assert record.place == Place(country="Francia")
    assert record.display_name == "Mont Blanc"
    assert json.loads(record.address_json) == {"country": "Francia"}


def test_reverse_keeps_only_string_address_values():
    body = json.dumps(
        {
            "display_name": "Rara",
            "address": {"country": "España", "extra": {"anidada": 1}},
        }
    ).encode()
    client = make_client(FakeURLOpen(body=body))

    record = client.reverse(1.0, 2.0)

    assert json.loads(record.address_json) == {"country": "España"}


def test_reverse_without_display_name_leaves_it_none():
    body = json.dumps(
        {"address": {"country": "España", "country_code": "es"}}
    ).encode()
    client = make_client(FakeURLOpen(body=body))

    record = client.reverse(1.0, 2.0)

    assert record.display_name is None
    assert record.place.display() == "España"


def test_response_without_address_is_not_interpretable():
    body = json.dumps({"error": "Unable to geocode"}).encode()
    client = make_client(FakeURLOpen(body=body))

    with pytest.raises(GeocodingResponseError, match="interpretable"):
        client.reverse(0.0, 0.0)


def test_invalid_json_is_not_interpretable():
    client = make_client(FakeURLOpen(body=b"<html>"))

    with pytest.raises(GeocodingResponseError, match="JSON"):
        client.reverse(0.0, 0.0)


def test_http_429_rejection_stops_the_call():
    error = urllib.error.HTTPError(
        "https://nominatim.openstreetmap.org/reverse",
        429,
        "Too Many Requests",
        None,
        None,
    )
    client = make_client(FakeURLOpen(error=error))

    with pytest.raises(GeocodingRejectedError, match="429"):
        client.reverse(0.0, 0.0)


def test_server_error_is_rejection():
    error = urllib.error.HTTPError("https://x", 500, "Server Error", None, None)
    client = make_client(FakeURLOpen(error=error))

    with pytest.raises(GeocodingRejectedError, match="500"):
        client.reverse(0.0, 0.0)


def test_network_failure_is_rejection():
    client = make_client(FakeURLOpen(error=urllib.error.URLError(reason="conexión rehusada")))

    with pytest.raises(GeocodingRejectedError, match="conexión"):
        client.reverse(0.0, 0.0)


def test_timeout_is_rejection():
    client = make_client(FakeURLOpen(error=TimeoutError("agotado")), timeout=5.0)

    with pytest.raises(GeocodingRejectedError):
        client.reverse(0.0, 0.0)


def test_throttle_uses_sleep_between_requests():
    fake = FakeURLOpen(body=HIERARCHICAL_BODY)
    sleeps: list[float] = []
    client = make_client(fake, interval=1.0, sleeps=sleeps, clock=0.0)

    client.reverse(1.0, 2.0)
    client.reverse(1.0, 2.0)

    assert sleeps == [1.0]


def test_no_sleep_when_the_wall_clock_has_advanced_enough():
    fake = FakeURLOpen(body=HIERARCHICAL_BODY)
    sleeps: list[float] = []
    ticks = iter([0.0, 2.5, 2.5, 5.0, 5.0])
    config = ReverseGeocoderConfig(
        endpoint="https://x",
        user_agent="ua",
        language="es",
        request_interval_seconds=1.0,
        timeout_seconds=5.0,
    )
    client = NominatimGeocoder(
        config, urlopen=fake, sleep=sleeps.append, monotonic=lambda: next(ticks)
    )

    client.reverse(1.0, 2.0)
    client.reverse(1.0, 2.0)

    assert sleeps == []


def test_first_request_never_sleeps():
    fake = FakeURLOpen(body=HIERARCHICAL_BODY)
    sleeps: list[float] = []
    client = make_client(fake, interval=1.0, sleeps=sleeps, clock=100.0)

    client.reverse(1.0, 2.0)

    assert sleeps == []
