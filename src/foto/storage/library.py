"""Helpers de la metainformación de la biblioteca.

Con el ADR 0010 el descriptor ``library.toml`` deja de existir —no se crea,
no se lee, no se valida— y la tabla ``locations`` del catálogo (esquema v6)
es la única referencia de ubicaciones; la coherencia del sistema pasa a ser
«config ↔ catálogo», responsabilidad de ``storage/sync.py``. Aquí quedan los
helpers de colocación que sobreviven: la derivación del directorio de
metadatos por defecto y la caché de miniaturas (ADR 0008).

Los descriptores ``library.toml`` ya presentes en ``.foto/`` se ignoran
(huérfanos); no se borran automáticamente.
"""

from __future__ import annotations

from pathlib import Path

FOTO_DIR_NAME = ".foto"
THUMBS_DIR_NAME = "thumbs"


def foto_dir(root: Path) -> Path:
    """Directorio de metadatos derivado de la biblioteca (nivel 4 por defecto)."""
    return root / FOTO_DIR_NAME


def thumbs_dir(metadata_dir: Path) -> Path:
    """Directorio de la caché de miniaturas en el directorio de metadatos."""
    return metadata_dir / THUMBS_DIR_NAME
