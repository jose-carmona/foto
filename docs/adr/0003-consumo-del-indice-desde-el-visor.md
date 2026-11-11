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
- El catálogo debe abrirse en modo **solo lectura** desde la UI. *(Enmendado
  el 2026-11-10: la escritura de usuario —etiquetas— usa una conexión RW
  dedicada y acotada; ver Enmiendas.)*
- El indexador sigue siendo un proceso independiente (`foto-index`), lanzable desde CLI, cron o systemd. La UI no crea ni regenera el índice.

### 2. Fuente de datos

La UI leerá desde `storage/catalog`:

- `photos`: metadatos normalizados (captura, cámara, objetivo, exposición, dimensiones, tipo/extensión, estado de metadatos).
- `locations`: rutas relativas a la raíz de la biblioteca con `status = 'present'`. Solo se mostrarán ubicaciones presentes.

**Duplicados por contenido:** si el mismo hash aparece en varias ubicaciones `present`, la foto se mostrará **una sola vez** en la rejilla. Se elegirá una **ubicación canónica** de forma determinista (primera en orden lexicográfico POSIX de `rel_path`). Las demás ubicaciones se considerarán réplicas y se mostrarán en el panel de metadatos, junto con la canónica. La barra de estado muestra solo el nombre del fichero de la selección *(enmendado el 2026-11-03: la barra de estado no muestra la ubicación canónica)*.

Para construir `foto.ui.window.Photo` se usarán, preferentemente:

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
- **Ordenación (`:sort`)**: solo se admite por `captured_at` (más reciente primero) y por `camera_model`. Las fotos sin fecha o sin cámara quedan al final. El orden por defecto es fecha. Los empates se resuelven por nombre de fichero.
- **Conteos**: `visible/total` se basarán en el conjunto cargado desde el catálogo (una foto por hash, independientemente del número de ubicaciones).
- **Duplicados**: al buscar/ordenar se considera la foto una sola vez; las ubicaciones alternativas no crean entradas duplicadas en la rejilla.

### 4. Sincronización con el indexador *(enmendada el 2026-11-03)*

- La UI **no** invoca `foto-index` y el visor **no expone ninguna orden de recarga o reindexado** (no existe `:rescan`). La sincronización del índice es siempre una operación **externa**: shell, servicio o cron ejecutan `foto-index`, y el visor muestra el catálogo actualizado al reiniciar la aplicación o al abrir otra biblioteca.
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
- Agrupar por `photo_hash`: una entrada por foto, ubicación canónica = menor `rel_path` en orden lexicográfico POSIX.
- Mostrar la ubicación canónica y las réplicas en el panel de metadatos. La barra de estado muestra solo el nombre del fichero (decisión enmendada en 2026-11-03).
- Mantener compatibilidad con el flujo actual de decodificación de originales en segundo plano.
- Las búsquedas y ordenaciones deben operar sobre los datos cargados del catálogo (una entrada por foto).

## Relación con otros ADR

- **ADR 0001**: sigue aplicándose para la localización de la biblioteca y la configuración.
- **ADR 0002**: este ADR extiende la integración prevista; el catálogo sigue siendo reconstruible y solo lo escribe `foto-index`.

## Enmiendas

- **2026-11-10** — El visor gana una **conexión de escritura dedicada y
  acotada** para las etiquetas: mantiene la lectura en modo solo lectura
  para todo lo existente (`photos`, `locations` y el resto del catálogo), y
  la escritura de usuario queda limitada a `tags`/`photo_tags` a través de
  una conexión RW propia que **no toma `index.lock`** (`busy_timeout` + WAL
  serializan con los lotes del indexador). Reconciliar hashes sigue siendo
  trabajo exclusivo de `foto-index`; la UI nunca escribe en `photos` ni en
  `locations`. Origen de la enmienda:
  [`ADR 0006`](0006-etiquetas.md) — las etiquetas y el favorito persistido
  (etiqueta de sistema `fav`) exigen escritura de usuario desde el visor,
  decisión de `ADR 0006` que enmienda el modo solo lectura fijado aquí.
- **2026-11-03** — Se elimina la orden `:rescan` del visor (antes definida en la
  sección 4 como «recarga de los datos desde el catálogo»). La reindexación y
  la sincronización del índice se ejecutan siempre desde la shell, un servicio
  o cron lanzando `foto-index`; el visor refleja el catálogo actualizado al
  reiniciar la aplicación o al abrir otra biblioteca. Origen de la enmienda:
  hallazgo V02 de `docs/informe-discrepancias-adr-codigo.md` — la acción
  estaba implementada internamente pero era inalcanzable (no estaba en el
  vocabulario de comandos ni se despachaba), y la carga de datos ocurre ya al
  abrir la aplicación y al cambiar de biblioteca, de modo que una orden de
  recarga propia no aporta flujo de uso adicional.
- **2026-11-03** — La barra de estado muestra **solo el nombre del fichero** de
  la selección; antes se convenía mostrar la ubicación canónica en la esquina
  inferior derecha. La ubicación canónica y las réplicas siguen visibles en el
  panel de metadatos, que es donde residen los datos de ubicación. Origen de
  la enmienda: hallazgo V05 de `docs/informe-discrepancias-adr-codigo.md` — la
  implementación ya ignoraba el parámetro de ubicación canónica, y la decisión
  es mantener ese comportamiento simplificado en vez de ampliarlo. Elimina el
  contrato incumplido en vez de añadir el dato a la barra.
