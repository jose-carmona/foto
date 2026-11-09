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


class GeocodingError(IndexingError):
    """Error de la fase de geocodificación inversa."""


class GeocodingRejectedError(GeocodingError):
    """El servicio rechazó la petición (o la red no respondió).

    La fase de geocodificación se detiene en el acto sin marcar error en
    ninguna foto: lo pendiente se retoma en la siguiente pasada.
    """


class GeocodingResponseError(GeocodingError):
    """Respuesta no interpretable o vacía para una foto concreta.

    Se marca esa foto como ``error`` y la fase continúa con el resto.
    """


class CatalogError(FotoError):
    """Error del catálogo SQLite."""


class CatalogVersionError(CatalogError):
    """El catálogo usa una versión de esquema no soportada."""


class LockError(FotoError):
    """No se pudo usar el lock del indexador."""


class LockBusyError(LockError):
    """Otro proceso mantiene el lock del indexador."""
