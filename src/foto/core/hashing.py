"""Hash de contenido de los originales."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from foto.core.errors import HashError

HASH_PREFIX = "sha256:"
CHUNK_SIZE = 1024 * 1024


class Hasher(Protocol):
    """Contrato mínimo para calcular el hash de un fichero."""

    def hash_file(self, path: Path) -> str:
        """Devuelve el hash de contenido con el formato ``sha256:<hex>``."""


class Sha256Hasher:
    """Hash SHA-256 calculado en streaming, sin cargar el fichero en memoria."""

    def __init__(self, chunk_size: int = CHUNK_SIZE) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size debe ser positivo")
        self.chunk_size = chunk_size

    def hash_file(self, path: Path) -> str:
        """Calcula el hash de contenido de ``path``."""
        sha = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(self.chunk_size), b""):
                    sha.update(chunk)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise HashError(f"No se pudo leer el fichero: {reason}") from exc
        return f"{HASH_PREFIX}{sha.hexdigest()}"


def hash_bytes(data: bytes) -> str:
    """Calcula el hash de un bloque de bytes (útil en tests y utilidades)."""
    return f"{HASH_PREFIX}{hashlib.sha256(data).hexdigest()}"
