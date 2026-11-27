"""Paridad del formateo de ``captured_at`` (Fase B, S2).

El catálogo guarda fechas ISO con hora; el camino rápido recorta los primeros
16 caracteres en lugar de llamar a ``fromisoformat`` + ``strftime``. Esta tabla
compara la función nueva contra la implementación anterior, carácter a
carácter, incluidos los casos que deben seguir por el *fallback*.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from foto.core import library


def _reference_format_taken(captured_at: str | None) -> str:
    """Implementación de referencia: la que había antes de la Fase B."""
    if not captured_at:
        return ""
    try:
        parsed = datetime.fromisoformat(captured_at)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return captured_at


@pytest.mark.parametrize(
    "captured_at",
    [
        "2026-11-26T12:34:56+00:00",
        "2026-11-26T12:34",
        "2026-11-26 12:34:56.123456",
        "2026-11-26T12:34:56Z",
        "2026-11-26",
        "2026-1-2T3:04",
        "no-es-fecha",
        "",
        None,
        # Casos extra: separador distinto y dígitos no ASCII nunca entran
        # por el camino rápido, que exige dígitos 0-9.
        "2026-11-26t12:34",
        "２０２６-11-26T12:34",
        "2026-11-26T12:3a",
    ],
)
def test_format_taken_matches_the_previous_implementation(captured_at: str | None):
    assert library._format_taken(captured_at) == _reference_format_taken(captured_at)


class _ExplodingDatetime:
    """Sustituto de ``library.datetime`` que falla si se usa para parsear."""

    @staticmethod
    def fromisoformat(value: str) -> datetime:
        raise AssertionError("fromisoformat no debe llamarse en el camino rápido")


def test_iso_strings_with_time_never_call_fromisoformat(monkeypatch):
    """Las cadenas ISO con hora se recortan sin tocar ``fromisoformat``.

    La clase ``datetime`` de la stdlib es inmutable, así que se parchea el
    símbolo ``datetime`` del módulo (lo que la función usaría como fallback).
    """
    monkeypatch.setattr(library, "datetime", _ExplodingDatetime)

    for captured_at in (
        "2026-11-26T12:34:56+00:00",
        "2026-11-26T12:34",
        "2026-11-26 12:34:56.123456",
        "2026-11-26T12:34:56Z",
    ):
        assert library._format_taken(captured_at) == "2026-11-26 12:34"


def test_non_iso_strings_still_use_the_fallback(monkeypatch):
    """La fecha sin hora sigue pasando por el parseo (y su *except*)."""
    calls: list[str] = []
    real_datetime = datetime

    class _CountingDatetime:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            calls.append(value)
            return real_datetime.fromisoformat(value)

    monkeypatch.setattr(library, "datetime", _CountingDatetime)

    assert library._format_taken("2026-11-26") == "2026-11-26 00:00"
    assert library._format_taken("no-es-fecha") == "no-es-fecha"
    assert calls == ["2026-11-26", "no-es-fecha"]
