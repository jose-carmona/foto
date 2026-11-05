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
    favorite: bool = False
    path: Path | None = None
    canonical_rel_path: str | None = None
    other_locations: tuple[str, ...] = ()
    photo_hash: str | None = None

    @property
    def key(self) -> str:
        """Identifica la foto aunque haya nombres repetidos en la biblioteca."""
        return str(self.path) if self.path is not None else self.name
