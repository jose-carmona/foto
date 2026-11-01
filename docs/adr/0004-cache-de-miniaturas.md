# ADR 0004 — Caché persistente de miniaturas

- **Estado:** aceptada
- **Fecha:** 2026-11-01
- **Decide:** Jose
- **Ámbito:** miniaturas derivadas de la rejilla. Quedan fuera las imágenes
  originales del visor, XMP y los metadatos de usuario.

## Contexto

La rejilla ya genera las miniaturas visibles en segundo plano y pide al lector
una decodificación ya escalada, pero cada arranque vuelve a decodificar los
originales. En bibliotecas grandes, y especialmente con RAW o HEIC, eso supone
un coste evitable: el contenido de una foto no cambia si el fichero no cambia.

Las miniaturas son datos derivados. Se pueden borrar y reconstruir en cualquier
momento, no son fuente de verdad y nunca deben modificar el original. El
directorio derivado ya está reservado en `docs/adr/0001-configuracion.md`:

```
<fototeca>/.foto/thumbs/
```

## Decisión

### 1. Ubicación y contenido

La caché vive en `<fototeca>/.foto/thumbs/`, junto al catálogo. Contiene una
imagen JPEG por miniatura, ya orientada según EXIF y recortada al tamaño exacto
de la rejilla (`400 × 247` en la implementación actual). No se guardan los
originales ni imágenes intermedias.

Se usa JPEG con calidad 90: la miniatura es un derivado visual y el ahorro de
espacio frente a PNG compensa la pérdida imperceptible a ese tamaño.

### 2. Clave por contenido, tamaño y versión

El nombre del fichero combina tres datos:

```
<sha256-hex>-<ancho>x<alto>-v<versión>.jpg
```

- **Hash del contenido:** `photos.hash` del catálogo. Mover o renombrar una foto
  no cambia su miniatura; dos copias con el mismo contenido comparten caché.
- **Tamaño:** si la rejilla cambia de tamaño, la miniatura anterior no se
  reutiliza con una escala equivocada.
- **Versión:** `CACHE_VERSION` en `core/thumbnails.py`. Cambiar el algoritmo de
  recorte, orientación o formato invalida la caché sin borrar ficheros a mano.

Los hashes que no tengan el formato esperado se normalizan con SHA-256 antes de
usarse como nombre, para que un catálogo corrupto no pueda escribir fuera de
`.foto/thumbs/`.

### 3. Lectura y escritura

La lectura y la escritura ocurren en los hilos de trabajo de
`ThumbnailService`, nunca en el hilo de UI:

1. Si hay hash y la miniatura cacheada existe, no está vacía y tiene el tamaño
   esperado, se devuelve directamente.
2. Si no, se decodifica el original con `QImageReader.setScaledSize()`, se
   recorta y se devuelve.
3. La miniatura generada se guarda con un fichero temporal en el mismo
   directorio y un `os.replace()` atómico.

Un fallo de lectura o escritura de la caché no es fatal: la miniatura se genera
igual y el error solo se registra en modo depuración. Un fichero corrupto se
detecta al leerlo (`QImage` nulo o tamaño incorrecto) y se regenera.

### 4. La caché no es fuente de verdad

- Borrar `.foto/thumbs/` solo provoca que las miniaturas se regeneren.
- La UI no escribe fuera de `.foto/`.
- La caché no se versiona en git ni se copia como si fueran originales.
- No se implementa todavía una poda de ficheros huérfanos. Es deuda conocida:
  fotos borradas o cambiadas pueden dejar miniaturas antiguas hasta que se
  añada una limpieza.

## Alternativas descartadas

- **Guardar las miniaturas como blobs en SQLite:** engorda el catálogo, lo hace
  menos inspeccionable y complica la copia de seguridad selectiva. La caché en
  ficheros es visible y reconstruible.
- **Nombrar por ruta del original:** se invalida al mover o renombrar fotos,
  justo cuando la identidad por contenido aporta valor.
- **PNG para todas las miniaturas:** mayor tamaño sin ventaja visual perceptible
  a `400 × 247`.
- **Generar la caché completa al abrir la fototeca:** bloquea o satura el disco
  con fotos que el usuario no está viendo. Se mantiene la generación bajo
  demanda de la zona visible.

## Consecuencias

**Positivas**

- El segundo arranque no decodifica los originales ya visitados.
- Mover o renombrar la biblioteca conserva la caché.
- La caché es inspeccionable, reconstruible y no toca los originales.
- Un problema de permisos o de disco no impide ver las miniaturas.

**Negativas**

- Ocupa espacio adicional en disco.
- Los ficheros huérfanos crecen hasta que se implemente una poda.
- JPEG introduce una pérdida mínima en el derivado, nunca en el original.
