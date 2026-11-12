"""Transferencia de metadatos de usuario: exportación e importación JSON.

Módulo sin Qt (ADR 0007): ``foto-export`` vuelca a dos ficheros JSON
(``tags.json`` y ``places.json``) y ``foto-import`` los aplica con merge
aditivo. La exportación solo lee el catálogo; la importación (paso 3) es el
único escritor fuera del indexador y toma ``index.lock``.
"""
