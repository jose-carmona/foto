"""Factorías compartidas para los tests de UI (R11)."""

from __future__ import annotations

from foto.ui.models import Photo


def make_photo(name: str, taken: str, **overrides) -> Photo:
    """Foto sintética con metadatos por defecto; ``overrides`` los ajusta."""
    fields: dict = {
        "name": name,
        "taken": taken,
        "camera": "Nikon Z 6",
        "lens": "Nikkor 35mm",
        "focal": "35 mm",
        "aperture": "f/1,8",
        "shutter": "1/250 s",
        "iso": "200",
        "folder": "2024",
        "fmt": "JPEG",
        "dimensions": "6000 × 4000",
        "size": "2 MB",
        "location": "",
        "photo_hash": f"sha256:{name}",
    }
    fields.update(overrides)
    return Photo(**fields)
