"""Excepciones propias del núcleo y de los adaptadores de almacenamiento."""

from __future__ import annotations


class FotoError(Exception):
    """Error base del proyecto."""


class IndexingError(FotoError):
    """Error del proceso de indexado."""


class LibraryError(IndexingError):
    """La biblioteca no existe, no es válida o no se puede preparar."""


class HashError(IndexingError):
    """No se pudo calcular un hash estable del contenido."""


class ScanError(IndexingError):
    """Error al recorrer la biblioteca."""


class MetadataError(IndexingError):
    """Error no recuperable del extractor de metadatos."""


class CatalogError(FotoError):
    """Error del catálogo SQLite."""


class CatalogVersionError(CatalogError):
    """El catálogo usa una versión de esquema no soportada."""


class LockError(FotoError):
    """No se pudo usar el lock del indexador."""


class LockBusyError(LockError):
    """Otro proceso mantiene el lock del indexador."""
