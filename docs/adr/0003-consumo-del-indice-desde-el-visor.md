# ADR 0003 — El visor consume el índice, no el sistema de ficheros

- **Estado:** aceptada
- **Fecha:** 2026-10-31
- **Decide:** Jose
- **Ámbito:** integración del visualizador con el catálogo SQLite. Quedan fuera las miniaturas, XMP, valoraciones, favoritos y la gestión de metadatos de usuario.

## Contexto

El indexador (`foto-index`) ya genera y mantiene `<fototeca>/.foto/catalog.sqlite` con las fotos, sus ubicaciones, metadatos normalizados y estado de indexado. Sin embargo, el visor actual (`src/foto/ui/app.py`) sigue recorriendo el sistema de ficheros directamente con `core.library.list_images()` para construir la rejilla.

Esto genera varias incoherencias:

- El visor ignora el catálogo y, por tanto, ignora la identidad por contenido, duplicados, ausencias lógicas y metadatos extraídos.
- Las búsquedas, ordenaciones y conteos no se basan en lo que realmente está indexado.
- No hay un único punto de verdad entre lo que se ha indexado y lo que se muestra.
- El comportamiento diverge de la filosofía "SQLite es un índice reconstruible" fijada en ADR 0002.

El objetivo es que la UI pase a ser un consumidor del índice, tal como se dejó preparado en el ADR 0002.

## Decisión

### 1. El índice pasa a ser obligatorio

El visualizador **solo** mostrará fotos que existan en el catálogo.

- Si no existe `<fototeca>/.foto/catalog.sqlite`, la aplicación debe mostrar un error claro y **cerrarse**.
- Si el catálogo existe pero no tiene fotos con ubicaciones `present`, se muestra el estado vacío existente, pero **no** se hace un barrido del filesystem para "rellenarlo".
- El catálogo debe abrirse en modo **solo lectura** desde la UI.
- El indexador sigue siendo un proceso independiente (`foto-index`), lanzable desde CLI, cron o systemd. La UI no crea ni regenera el índice.

### 2. Fuente de datos

La UI leerá desde `storage/catalog`:

- `photos`: metadatos normalizados (captura, cámara, objetivo, exposición, dimensiones, tipo/extensión, estado de metadatos).
- `locations`: rutas relativas a la raíz de la biblioteca con `status = 'present'`. Solo se mostrarán ubicaciones presentes.

Para construir `foto.ui.prototype.Photo` se usarán, preferentemente:

| Campo UI | Origen |
| --- | --- |
| `name` | `Path(rel_path).name` |
| `taken` | `photos.captured_at` (formateado a partir del ISO 8601 normalizado) |
| `camera` | Combinación de `photos.camera_make` y `photos.camera_model` |
| `lens` | `photos.lens_model` |
| `focal` | `photos.focal_length` (formateado) |
| `aperture` | `photos.f_number` (formateado como `f/...`) |
| `shutter` | `photos.exposure_time` (formateado) |
| `iso` | `photos.iso` |
| `folder` | Carpeta relativa derivada de `rel_path` (respecto a la raíz de la biblioteca) |
| `fmt` | `photos.file_type` o extensión normalizada |
| `dimensions` | `photos.width` × `photos.height` |
| `size` | Tamaño del fichero en disco (obtenido con `stat` del original si está accesible) |
| `location` | Coordenadas GPS formateadas si existen (`photos.gps_latitude`/`gps_longitude`) |
| `path` | `<library_path> / rel_path` (ruta absoluta al original para abrirla en el visor) |

Se mantendrá `Photo.path` para que `PhotoCanvas` siga decodificando el original en segundo plano tal como hace ahora.

### 3. Ordenaciones y búsquedas contra el índice

Toda la lógica de filtrado, búsqueda y ordenación deberá hacerse **contra los datos del catálogo**, no contra el resultado de un recorrido del filesystem.

- **Búsqueda (`/`)**: se filtra por nombre de fichero, ruta relativa, cámara, objetivo o cualquier campo indexado. El filtrado se hace en memoria sobre la lista cargada desde el catálogo.
- **Ordenación (`:sort`)**: se ordenará por campos disponibles en el índice (`captured_at`, `camera_model`, `name`/`rel_path`, etc.). Si `captured_at` es `NULL`, se aplicará un criterio secundario estable (por ejemplo, `rel_path`).
- **Conteos**: `visible/total` se basarán en el conjunto cargado desde el catálogo.

### 4. Sincronización con el indexador

- La UI **no** invoca `foto-index`. La acción `:rescan` recargará los datos desde el catálogo (no volverá a recorrer el filesystem). Si se desea reindexar, se debe ejecutar `foto-index` externamente.
- El visor convivirá con el indexador gracias a SQLite en WAL (ya habilitado en ADR 0002). La UI abrirá el catálogo en modo lectura.
- Los estados `missing` se ignoran por completo. Las fotos ausentes no se muestran.

### 5. Arquitectura

- `core/` y `storage/` seguirán sin importar Qt.
- Se añadirá una función para cargar desde catálogo, por ejemplo `foto.core.library.load_from_catalog(...)` o lógica equivalente en `foto.ui.app` usando `storage.catalog`.
- `foto.core.library.list_images()` dejará de usarse desde la UI. Puede mantenerse para compatibilidad o marcarse como obsoleto, pero **no** debe ser la fuente para mostrar la galería.
- La carga de configuración sigue rigiéndose por ADR 0001.

## Consecuencias

### Positivas

- Única fuente de verdad: lo mostrado coincide con lo indexado.
- Soporta deduplicación por contenido de forma coherente con el modelo del catálogo.
- Menor I/O al arrancar: no se recorre todo el árbol de ficheros, se consulta SQLite.
- Permite mostrar metadatos reales extraídos por el indexador.
- Preparado para filtrar por estado, fechas, cámara, etc. sin depender del filesystem.
- Se cumple la filosofía de "índice reconstruible" y separación clara entre indexador y visor.

### Negativas

- La UI deja de funcionar si no se ha indexado la biblioteca previamente.
- Requiere que `foto-index` se haya ejecutado al menos una vez.
- Hay que mapear correctamente `Photo` desde el esquema del catálogo.
- Algunas operaciones que antes "descubrían" archivos nuevos ya no lo harán.
- Si el catálogo está desactualizado, la UI mostrará lo que haya en el índice (comportamiento intencional).

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Seguir escaneando y cruzar con catálogo | Mezcla dos fuentes, propenso a incoherencias. |
| Crear el índice desde la UI si no existe | Rompe la separación: indexado es tarea del `foto-index` (CLI/cron/systemd). Además requiere lógica de hashing/extracción fuera de lo que debe hacer la UI. |
| Usar el filesystem como fallback | Oculta el hecho de que el índice no existe y dificulta detectar que hay que indexar. |
| Leer solo desde filesystem cuando el catálogo esté vacío | Contradice el objetivo de tener una única fuente de verdad basada en el índice. |

## Notas de implementación

- Abrir `catalog.sqlite` en modo solo lectura desde la UI.
- Filtrar explícitamente por `locations.status = 'present'`.
- Si falta el catálogo, mostrar mensaje de error y salir con código distinto de cero.
- Mantener compatibilidad con el flujo actual de decodificación de originales en segundo plano.
- Las búsquedas y ordenaciones deben operar sobre los datos cargados del catálogo.

## Relación con otros ADR

- **ADR 0001**: sigue aplicándose para la localización de la biblioteca y la configuración.
- **ADR 0002**: este ADR extiende la integración prevista; el catálogo sigue siendo reconstruible y solo lo escribe `foto-index`.
