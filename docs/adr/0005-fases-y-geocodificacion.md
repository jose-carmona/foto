# ADR 0005 — Pipeline de fases del indexador y geocodificación inversa

- **Estado:** aceptada
- **Fecha:** 2026-11-08
- **Decide:** Jose
- **Ámbito:** el proceso `foto-index` (pipeline por fases) y la geocodificación
  inversa de las fotos con GPS. Quedan fuera la poda de la caché de miniaturas,
  otros proveedores de geocodificación, el menú o vista de mapa y la escritura
  del lugar en XMP. La integración en el visor (buscar y ordenar por ubicación)
  se decide aquí y se implementa en la misma entrega, en un segundo paso.

## Contexto

El indexador actual es un proceso de una sola pasada que hace tres trabajos
encadenados: recorrer el filesystem y hashear, extraer metadatos y reconciliar
ausencias. Están apareciendo trabajos que no deben bloquear a los anteriores
(la geocodificación inversa consulta la red y Nominatim impone un ritmo de una
petición por segundo) y futuras fases (poda de la caché de miniaturas). Un
fallo o un rechazo del servicio externo no debe dejar la pasada incoherente ni
repetir trabajo.

Restricciones fijadas en `AGENT.md` y `ADR 0002` que se mantienen: originales
intactos, solo escritura en `.foto/`, `core/`, `storage/` e `indexer/` sin Qt,
sin dependencias nuevas de runtime, catálogo reconstruible y `PRAGMA
user_version` como versión de esquema con migraciones idempotentes.

La regla 3 de `AGENT.md` («sin red por defecto») se modifica de forma explícita
y limitada (ver Decisión 7): la geocodificación inversa contra OpenStreetMap es
la única salida nueva a la red, está **activada por defecto** y es desactivable
sin tocar código.

## Decisión

### 1. Pipeline de fases

`foto-index` es un **pipeline de fases** que se ejecutan en orden dentro de una
única pasada, con un solo lock, un solo catálogo y un solo resumen:

1. `scan` — todo el comportamiento actual: recorrido del filesystem, hashing,
   ubicaciones, extracción de metadatos, reconciliación de ausencias y `--prune`.
2. `geocode` — geocodificación inversa de las fotos con GPS y sin lugar.

- Cada fase tiene su propio estado (`ok`, `partial`, `skipped`, `error`),
  contadores propios y un registro en el historial de la pasada.
- Una fase solo arranca si la pasada llegó a ella sin error fatal (raíz
  ausente, lock, esquema o configuración). Un error fatal aborta el resto de
  fases y cierra la pasada como `error`.
- El historial se guarda en una tabla nueva `run_phases(run_id, phase, status,
  reason, contadores, marcas de tiempo)`, una fila por fase y pasada. `scan_runs`
  se amplía solo con el estado y el motivo agregados de la pasada; los
  contadores de `scan` siguen en sus columnas propias de siempre.
- `--skip scan` y `--skip geocode` (repetible) excluyen fases; la fase excluida
  se registra como `skipped`. Con `--skip scan`, `geocode` trabaja sobre el
  catálogo existente sin recorrer el filesystem.

### 2. Servicio y contrato

- El núcleo define un **contrato propio** en `core/geocoding.py`: el protocolo
  `Geocoder` recibe coordenadas y devuelve un `Place` normalizado o lanza las
  excepciones propias (`GeocodingRejectedError` para rechazo/fallo de red,
  `GeocodingResponseError` para una respuesta no interpretable). El indexador y
  los tests no conocen HTTP.
- La implementación inicial es un cliente Nominatim `reverse` aislado en
  `indexer/geonames.py`, sobre `urllib.request` de la stdlib, sin shell y sin
  dependencias nuevas. Peticiones contra
  `https://nominatim.openstreetmap.org/reverse` con `format=jsonv2`,
  `addressdetails=1` y **`zoom=10`** (nivel de la jerarquía Nominatim que
  devuelve país/región/ciudad sin arrastrar calles). El endpoint es
  configurable para poder apuntar a otra instancia o a un futuro proveedor.
- **Throttle siempre activo: máximo 1 petición por segundo**, garantizado con
  `sleep` y reloj monótonicos **inyectables** (probables en tests). Se cumple
  desde la primera petición, no solo tras un rechazo.
- **User-Agent identificativo y honesto**: `foto/<versión> (biblioteca personal
  en local)`, configurable. `accept-language` configurable (`es` por defecto).
- Timeout configurables: **10 s por defecto**.

### 3. Selección de fotos y estados de lugar

El lugar es un dato de la **foto** (por hash, función del contenido): una foto
con varias ubicaciones se geocodifica una vez. Se relaciona con las
coordenadas GPS del EXIF, que son la fuente de verdad: el catálogo guarda un
lugar derivable con `--full` y el servicio, y las tablas de lugar viven en el
catálogo (solo SQLite, sin sidecars XMP en esta fase).

Modelo de estados, en la línea de `metadata_status`:

- `none` — sin coordenadas GPS en el EXIF. Estado definitivo.
- `pending` — con GPS, pendiente de geocodificar. Procesado en **todas** las
  pasadas: así la reanudación retoma exactamente donde se paró.
- `ok` — lugar guardado. Los `ok` no se vuelven a pedir; refrescar un lugar ya
  calculado **no** forma parte de esta decisión (ver Pendiente).
- `error` — respuesta no interpretable o vacía para esa foto concreta; se
  reintenta solo con `--full`, igual que los metadatos.

`place_status` vive en `photos`. Las fotos nuevas arrancan `pending`;
`update_metadata` corrige el estado al terminar la extracción (`none` sin
coordenadas, `pending` con ellas) sin degradar nunca un `ok`. La fase
selecciona: `gps_latitude`/`gps_longitude` no nulos y `place_status='pending'`
(más `place_status='error'` en `--full`). Las fotos sin GPS quedan fuera para
siempre; la fase funciona aunque `exiftool` no esté: se geocodifica lo que ya
tenga coordenadas leídas y el resto sigue pendiente de metadatos.

### 4. Datos de lugar, caché por celda y esquema

- De la respuesta se extrae y normaliza una jerarquía: `country`,
  `country_code`, región/provincia (`state` en Nominatim) y `city` (con las
  variantes `city`, `town`, `village`, `municipality`). El formato de
  visualización es **`Ciudad, País`**; para ordenar y agrupar se usan los
  campos separados. Un campo ausente se guarda `NULL`, nunca cadena vacía.
- **Caché por celda:** las coordenadas se redondean a **3 decimales**
  (~110 m) y la clave de caché es la pareja redondeada, persistida en la tabla
  `geocode_cells` como dato derivado. Dos fotos del mismo sitio comparten una
  petición; una celda ya resuelta no genera HTTP ni siquiera entre pasadas.
- No se guarda el JSON crudo de la respuesta; solo la jerarquía normalizada.
- Esquema: **bump de `PRAGMA user_version` a 2** con migración idempotente al
  abrir: columnas de lugar y `place_status` en `photos` y tabla
  `geocode_cells`. Un catálogo de una versión más nueva aborta con mensaje
  claro, como hasta ahora.
- Respuestas válidas sin parte de la jerarquía (montaña, lugar aislado): la
  foto queda `ok` con los campos ausentes como `NULL`; la presentación las
  tratará como sin ubicación (al final, sin grupo). No se inventan cadenas.

Condiciones de error por foto (`place_status='error'`): respuesta no válida
como JSON, respuesta sin `address` (incluye el objeto de error de Nominatim:
celda sin cobertura, p. ej. el mar) o sin ningún campo de lugar reconocible.
Un rechazo del servicio o un fallo de red **no** marca `error` (ver Decisión 5).

### 5. Rechazo del servicio: parar y reanudar

- Ante un **rechazo** (HTTP 4xx/5xx, incluido 429 por volumen) o un **fallo de
  red** (timeout, conexión, DNS): la fase **se detiene en el acto**, sin
  reintentos ni backoff, lo ya guardado queda guardado (transacciones por
  lote) y la fase se registra como `partial` con el motivo. No hay fases
  posteriores que saltar hoy, pero el mecanismo lo contempla.
- La detención no es un error de proceso: usa los códigos de salida actuales.
  Como la pasada queda `partial`, el código es `1`; el motivo se expone en el
  resumen de texto y en el JSON.
- Reanudación: la selección «con GPS y sin lugar» y el throttle garantizan que
  la siguiente ejecución continúa donde se paró sin repetir peticiones.
- `SIGINT` durante la fase: comportamiento actual (`interrupted`), con la
  transacción en vuelo cerrada y los `pending` intactos.

### 6. CLI, configuración y privacidad

- Fases por defecto: **todas, en orden**. `--skip <fase>` repetible valida los
  nombres (`scan`, `geocode`); un nombre inválido termina con código `2` y un
  mensaje claro.
- `--dry-run` no toca la red ni escribe: la fase informa de cuántas fotos
  geocodificaría (candidatas) y cuántas celdas pediría, sin pedir nada.
- Configuración (`[geocoding]` en `config.toml`, siguiendo `ADR 0001`, sin
  secretos): `enabled` (true), `endpoint`, `language` (`es`), `user_agent`,
  `request_interval_seconds` (1.0) y `timeout_seconds` (10.0). Valores por
  defecto sensatos y documentados; la librería no describe el servicio.
- Resumen de la fase (texto y JSON): candidatas, geocodificadas `ok`, con
  `error`, aciertos de caché, peticiones HTTP realizadas y, si se paró, motivo.
- **Privacidad:** los logs no imprimen coordenadas ni lugares salvo en modo
  `--verbose`, mismo criterio que las rutas completas. El JSON publica solo
  contadores, nunca coordenadas ni nombres de lugar.

### 7. Opción de red

La regla 3 de `AGENT.md` queda así: **sin red por defecto; las dos únicas
salidas a la red son la copia de seguridad a S3 (optativa, no implementada) y
la geocodificación inversa con OpenStreetMap (activada por defecto en la fase
`geocode`, desactivable con `--skip geocode` o `[geocoding] enabled = false`)**.
No hay telemetría ni llamadas adicionales; el resto del programa sigue sin
llamar a la red.

### 8. Integración en el visor (decisiones, implementadas en el paso 2)

- El modelo `Photo` de la UI gana los campos de lugar resueltos y la consulta
  de carga los incluye. Sin Qt en el SQL ni en las reglas puras.
- Los campos de lugar entran en `photo_search_text()` (ciudad, región, país),
  con el estilo casefold/espacios del documento de búsqueda existente.
- `:sort location`: orden país → región → ciudad (`casefold`, ascendente) con
  las fotos sin ubicación siempre al final; grupos por ciudad con clave y
  etiqueta `Ciudad, País` y grupo propio «Sin ubicación» al final. `g m` sigue
  funcionando desde las coordenadas EXIF y no cambia.
- El panel de metadatos muestra el lugar resuelto en formato `Ciudad, País`
  cuando existe; si falta alguna parte, muestra lo disponible.

## Consecuencias

**Positivas**

- El trabajo lento o externo (geocodificar) no bloquea ni rompe el recorrido;
  una pasada queda siempre con el catálogo consistente y el lock liberado.
- Reanudación exacta tras un rechazo: la próxima pasada pide solo lo pendiente.
- Las fotos duplicadas o repetidas en una celda comparten una única petición;
  una biblioteca estable solo habla con Nominatim por fotos nuevas con GPS.
- El marco de fases deja listas las futuras fases
  (poda de miniaturas) sin rehacer la pasada.
- El catálogo sigue siendo un índice reconstruible: el lugar se deriva del EXIF
  con `--full` y el servicio.

**Negativas**

- La fase `geocode` sale a la red por defecto: hay que poder apagarla sin
  código (hecho con `--skip` y `[geocoding]`), y el ritmo de Nominatim limita a
  ~3 600 fotos nuevas por hora en la primera geocodificación de una biblioteca.
- La geocodificación de celdas sin cobertura (mar) quedará `error` y solo se
  reintentará con `--full`.
- Dos tablas de historial (`scan_runs` + `run_phases`) en lugar de una.
- Un catálogo creado por una foto antigua requiere migración antes de que el
  visor (que exige el esquema actual) pueda abrirlo.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Ampliar `scan_runs` con columnas por fase | El historial se degradaría con cada fase nueva; `run_phases` es extensible sin esquemas consecutivos. |
| Geocodificar por ruta o por ubicación | El lugar es función del contenido; geocodificar por ruta repetiría peticiones para cada copia. |
| Peticiones concurrentes o por lotes (`/batch`) | Violan la política de uso de Nominatim y no son parte de esta entrega. |
| Reintento con backoff ante rechazos | Un 429 pide parar; la reanudación sin repetir peticiones hace innecesario insistir en la misma pasada. |
| Guardar el JSON crudo de Nominatim | Infla el catálogo y guarda datos que cambian al refinar Nominatim; solo se conserva la jerarquía normalizada. |
| Métodos de caché en ficheros aparte | La caché por celda es derivada pero estática; en SQLite viaja con el catálogo y se respeta al mover la biblioteca. |
| Pasadas separadas por fase (varios procesos) | Complica cron/lock y obliga a reabrir el catálogo; el marco de fases dentro de una pasada conserva la garantía de escritor único. |
| Redes/bibliotecas de geocodificación (SDK) | Dependencias nuevas sin necesidad: `urllib` y JSON de la stdlib bastan. |
| Devolver `{}` u objetos sueltos como lugar | El contrato del núcleo exige un modelo normalizado: `Place` congelado, no dicts cruzando módulos. |

## Decisiones pendientes y riesgos

- **Refresco de lugares ya calculados** (p. ej. tras mejorar los datos de
  Nominatim o cambiar `language`): se necesitaría `--full geocode` o un
  indicador de versión de servicio en `geocode_cells`. No se implementa ahora.
- **Borrado de la caché de celdas** (reparar una celda mal resuelta): hoy exige
  borrar la fila de `geocode_cells` y lanzar `--full` para volver a pedir;
  falta una historia cómoda para el usuario.
- **Otros proveedores** (geocodificación local sin red, proveedores por lotes):
  el contrato en `core/` los permite sin tocar el indexador.
- **Fase futura de poda de miniaturas** se apoyará en el marco de fases; su
  diseño no está aquí.
- Las celdas de 3 decimales (~110 m) pueden solapar fotos de barrios
  distintos de una misma manzana grande; si hace falta afinar, la versión de
  esquema de `geocode_cells` podrá cambiar a 4 decimales invalidando la cache.
