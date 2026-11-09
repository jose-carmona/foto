# Prompt — Fases del indexador y geolocalización (buscar y ordenar por lugar)

## 0. Misión

Implementa en el repositorio `foto` la evolución del indexador hacia un
**proceso por fases** y su **segunda fase: geocodificación inversa** de las
fotos con GPS usando OpenStreetMap (Nominatim), con integración en el visor
para **buscar por ubicación y ordenar por ubicación** con `:sort location`.

La entrega se ejecuta en **dos pasos diferenciados**, en este orden, cada uno
terminado y verificable por separado:

1. **Paso 1 — Indexador** (`src/foto/core/`, `src/foto/storage/`,
   `src/foto/indexer/`): marco de fases del pipeline, nueva fase `geocode`
   contra Nominatim con caché por celda, parada por rechazo y reanudación
   automática, esquema del catálogo ampliado y CLI/resumen por fases.
   Al terminar: `foto-index` funciona completo con las dos fases, probado y
   verificado **sin tocar la UI**.
2. **Paso 2 — Visor** (`src/foto/ui/`): el visor consume el lugar del
   catálogo: aparece en el documento de búsqueda, en `:sort location`
   (orden país → región → ciudad, con grupos por ciudad) y en el panel de
   metadatos. **No se toca nada del indexador en este paso.**

Resumen del comportamiento pedido:

- El indexador ejecuta, en orden, varias fases. La primera es la actual
  (recorrido del filesystem, hashing, ubicaciones y metadatos). La segunda lee
  del catálogo las fotos con coordenadas GPS y sin lugar calculado, consulta
  un servicio de geocodificación inversa (Nominatim en un principio) y guarda
  la información de lugar junto al resto de metadatos.
- Futuras fases (por ejemplo, revisar la caché de miniaturas y eliminar
  miniaturas muertas) se apoyarán en el mismo marco de fases. **En esta
  entrega solo se implementan las dos primeras**; pon la estructura pero no
  la poda.
- Si el servicio rechaza una petición por volumen (o la red no responde), la
  fase se **detiene en el acto**; lo ya guardado queda guardado. Como la fase
  siempre trabaja sobre las fotos **sin ubicación calculada**, la siguiente
  ejecución retoma exactamente donde se paró, sin pedir dos veces lo mismo.

La entrega incluye un **ADR nuevo** que documente las decisiones, no solo el
código.

---

## 1. Lectura obligatoria antes de tocar código

Lee completos y respeta:

1. `AGENT.md` — reglas no negociables, stack, convenciones y flujo de trabajo.
   Fíjate en la regla 3 sobre la red: esta entrega la modifica (ver §2.1.2).
2. `README.md` — alcance, principios, arquitectura y roadmap.
3. `docs/adr/0001-configuracion.md` — configuración y rutas de `.foto/`.
4. `docs/adr/0002-indexado.md` — catálogo, incremental y CLI actual.
5. `docs/adr/0003-consumo-del-indice-desde-el-visor.md` — cómo lee el visor.
6. `docs/diseno-ui.md` — reglas de interfaz, teclas y comandos `:`.
7. Código real: paso 1: `src/foto/core/indexing.py`,
   `src/foto/core/metadata.py`, `src/foto/storage/catalog.py` y
   `src/foto/indexer/` (CLI y `__main__`); paso 2:
   `src/foto/ui/presentation.py`, `src/foto/ui/models.py` y
   `src/foto/ui/window.py`.

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible. No hagas `git commit`, `git push` ni cambios de
configuración de git sin permiso explícito.

---

# Paso 1 — Indexador: pipeline por fases y geocodificación

## 2. Decisiones vinculantes del indexador

Estas decisiones ya están tomadas. No las cambies sin justificarlo y sin
actualizar el ADR.

### 2.1. Marco de fases y opción de red

#### 2.1.1. Marco de fases del indexador

- `foto-index` pasa a ser un **pipeline de fases** que se ejecutan en orden
  dentro de una única pasada:
  1. `scan` — recorrido del filesystem, hashing, ubicaciones y extracción de
     metadatos (todo el comportamiento actual).
  2. `geocode` — geocodificación inversa de las fotos con GPS y sin lugar.
- Cada fase tiene su propio resultado: estado (`ok`, `partial`, `skipped`,
  `error`), contadores propios y registro en el historial de la pasada (tabla
  `scan_runs` ampliada o tabla nueva de fases por run; decide y documenta el
  ADR).
- Una fase solo arranca si la pasada llegó a ella sin error fatal (root
  ausente, lock, esquema, configuración). Un error fatal aborta el resto.
- Las fases comparten el mismo lock, el mismo catálogo y la misma salida:
  sigue siendo un proceso de una sola pasada, sin daemon.
- El resumen y el JSON de salida segmentan por fase, para que sea visible qué
  trabajo hizo cada una (como hoy con `hashed_files` y `metadata_extracted`).

#### 2.1.2. Red: nueva excepción a "sin red por defecto"

- La fase de geocodificación está **activada por defecto**. Es la única salida
  a la red de esta entrega y la segunda permitida en el proyecto (con la copia
  de seguridad a S3, que sigue siendo optativa).
- Debe poder desactivarse sin tocar código: flag CLI (`--skip geocode`) y
  opción de configuración (§2.7). Nada de telemetría ni llamadas adicionales.
- La regla 3 de `AGENT.md` y los principios de `README.md` se actualizan para
  reflejar esta excepción explícita ("la única red permitida es la
  geocodificación inversa con OpenStreetMap, desactivable"), no se cambia de
  espíritu: el resto del programa sigue sin llamar a la red.
- Los logs **no imprimen coordenadas ni lugares** salvo en modo `--verbose`,
  igual que las rutas completas hoy (privacidad, mismo criterio).

### 2.2. Servicio de geocodificación: Nominatim

- Implementación inicial: **Nominatim / reverse**
  (`https://nominatim.openstreetmap.org/reverse`, con `format=jsonv2`,
  `addressdetails=1` y un `zoom` fijo que devuelva jerarquía país/región/ciudad
  — el valor exacto se fija en el ADR —) y `accept-language` configurable
  (por defecto `es`).
- El servicio se esconde tras un **contrato propio en `core/`** (Protocol de
  geocodificador inverso: coordenadas en, lugar normalizado fuera). El
  indexador y los tests no conocen HTTP; el cliente Nominatim queda aislado en
  `storage/` o en un módulo propio del indexador. Endpoint configurable para
  poder apuntar a otra instancia o a un futuro proveedor.
- Sin dependencias nuevas: HTTP con `urllib.request` de la stdlib, sin shell.
- Cumple la política de uso de Nominatim desde la primera petición:
  - **Máximo 1 petición por segundo** (throttle siempre activo, no solo al
    rechazar). El `sleep` es inyectable/reloj inyectable para probarlo.
  - **User-Agent identificativo y honesto** (p. ej.
    `foto/<versión> (biblioteca personal en local)`), configurable.
- Timeout de red configurables con valor razonable por defecto (p. ej. 10 s).

### 2.3. Selección de fotos y estados de lugar

- El lugar es un dato de la **foto** (por hash, función del contenido), no de
  la ruta: una foto con dos ubicaciones se geocodifica una vez.
- Candidatas de la fase: fotos con `gps_latitude` y `gps_longitude` no nulos
  **y sin lugar calculado**. Fotos sin GPS quedan fuera para siempre; las que
  tienen lugar no se vuelven a pedir.
- Modelo de estados, en la línea del `metadata_status` actual:
  - `none` — sin coordenadas GPS en el EXIF; estado definitivo.
  - `pending` — con GPS, pendiente de geocodificar. Procesado en **todas** las
    pasadas: esta es la base de la reanudación.
  - `ok` — lugar guardado (aunque alguna parte jerárquica sea nula; ver §2.4).
  - `error` — el servicio devolvió una respuesta no interpretable o vacía para
    esa foto concreta; se reintenta con `--full`, igual que los metadatos.
- La parada por rechazo o por red **no convierte nada en `error`**: las fotos
  aún no procesadas siguen `pending` y se retoman en la siguiente ejecución.
- Los `ok` no se vuelven a pedir. Un refresco del lugar ya calculado no es
  parte de esta entrega: documenta en el ADR cómo se pediría más adelante.

### 2.4. Datos de lugar, caché por celda y esquema

- De la respuesta se extrae y normaliza una **jerarquía completa**:
  `country` (y `country_code`), región/provincia (`state` en Nominatim) y
  `city` (con sus variantes habituales: `city`, `town`, `village`,
  `municipality`). El formato de **visualización** es `Ciudad, País`; para
  ordenar y agrupar se usan los campos separados.
- **Caché por celda**: se redondean las coordenadas a **3 decimales**
  (~110 m) y la clave de caché es la pareja redondeada. Fotos del mismo sitio
  comparten una única petición y una foto de una celda ya resuelta ni siquiera
  llega a HTTP. La caché vive en el catálogo como dato derivado, no como
  fuente de verdad.
- **Solo SQLite**: la información de lugar vive únicamente en el catálogo.
  No se crean ni se modifican sidecars XMP en esta fase. La fuente de verdad
  del lugar son las **coordenadas GPS del EXIF** de la imagen: el lugar es
  derivable con `--full` y el servicio, por lo que el catálogo sigue siendo
  un índice reconstruible en espíritu.
- No guardes el JSON crudo de la respuesta (mismo criterio que con exiftool).
- Esquema: **bump de `PRAGMA user_version`** con migración idempotente al
  abrir: columnas nuevas en `photos` para el lugar y estado, y tabla de caché
  de celdas. El catálogo más nuevo aborta con mensaje claro, como hoy.
- Respuestas sin parte de la jerarquía (mar, montaña, lugar aislado): la foto
  queda `ok` con los campos ausentes como `NULL`; la presentación las trata
  como sin ubicación (al final del orden, sin grupo). No inventes cadenas
  vacías ni nombres.
- Las fotos con coordenadas leídas por `exiftool` cuentan: la fase funciona
  igual aunque `exiftool` no esté y `metadata_status` sea `pending` para el
  resto de metadatos.

### 2.5. Rechazo del servicio: parar y reanudar

- Ante un **rechazo del servicio** (HTTP 4xx/5xx, incluido el 429 por volumen)
  o un **fallo de red** (timeout, conexión, DNS): la fase **se detiene
  inmediatamente**, sin reintentos ni backoff, y la pasada registra la fase
  como `partial` con el motivo. El resto de fases que la sigan también se
  saltan (no hay ninguna más en esta entrega) y la pasada termina con el
  proceso correcto: catálogo consistente, lock liberado, respuesta clara.
- Código de salida: la detención por rechazo **no es error de proceso**; usa
  los códigos actuales (`1` para recorrido parcial/errores de ficheros) y
  expón el motivo en el resumen y en el JSON. No añadas un código nuevo sin
  necesidad; decide en el ADR.
- Reanudación: como la selección es "fotos con GPS y sin lugar", la ejecución
  siguiente continúa exactamente en el punto de parada, gracias al throttle y
  la caché de celdas, sin repetir peticiones.
- `SIGINT` durante la fase: mismo comportamiento que hoy (`interrupted`), con
  lo ya geocodificado en una transacción cerrada.

### 2.6. CLI y configuración

- Fases por defecto: **todas**, en orden. `--skip <fase>` (repetible,
  `--skip geocode`, `--skip scan`) las excluye; con `--skip scan`, la fase
  `geocode` trabaja sobre el catálogo existente sin recorrer el filesystem.
  Valida los nombres de fase con error claro (`código 2`).
- `--dry-run` **no toca la red ni escribe**: la fase `geocode` informa de
  cuántas fotos geocodificaría (candidatas) y qué celdas pediría, sin pedir.
- Persistencia: sección `[geocoding]` en `config.toml` (siguiendo ADR 0001,
  `tomllib`, sin secretos): `enabled`, `endpoint`, `language`,
  `user_agent`, `request_interval_seconds`, `timeout_seconds`. Valores por
  defecto sensatos y documentados; la librería no describe el servicio.
- Resumen de la fase (texto y JSON): candidatas, geocodificadas `ok`, con
  `error`, cacheadas (aciertos de celda), peticiones HTTP realizadas,
  peticiones saltadas por caché y, si se paró, motivo.

### 2.7. Diseño esperado del indexador (orientativo, no literal)

- `core/geocoding.py` — modelo normalizado del lugar (`Place` o similar como
  dataclass congelada, sin dicts sueltos), el Protocol de geocodificador
  inverso y el redondeo por celda. Sin Qt, sin HTTP.
- `indexer/geonames.py` o similar — cliente Nominatim sobre `urllib` con
  throttle, timeout y User-Agent propios; implementa el contrato de `core/`.
- `core/indexing.py` — nueva fase `geocode` del indexador, con el servicio
  inyectable, integrada en el pipeline de fases y con los contadores por fase.
- `storage/catalog.py` — migración a la nueva versión de esquema, acceso a la
  caché por celda y a los estados de lugar.
- `indexer/cli.py` — `--skip`, resumen por fase (texto y JSON).

## 3. Tests del paso 1

- Sin red en toda la suite: el servicio es un fake con inyección (resultados,
  latencias, errores y respuestas de rechazo configurables por test); el
  throttle usa reloj/sleep inyectables.
- Cubre, al menos:
  - contratos del cliente (respuesta Nominatim jerárquica, respuesta sin
    `address`, JSON inválido, 429 y otros códigos, timeout);
  - redondeo por celda y deduplicación de peticiones (misma celda dentro del
    run y acierto en la caché SQLite entre runs);
  - throttle: intervalo exacto entre peticiones;
  - parada por rechazo: nada se marca `error`, el run queda `partial`, el
    proceso y las transacciones quedan consistentes y la siguiente pasada
    retoma por los `pending`;
  - respuestas sin jerarquía parcial (`ok` con campos `NULL`);
  - migración de esquema de `user_version` a la nueva versión y arranque sobre
    catálogo viejo y más nuevo;
  - estados: fotos sin GPS (`none`), `pending`, `ok` y reintento de `error`
    con `--full`;
  - CLI: `--skip` (combinado, argumentos inválidos), `--dry-run` sin red,
    resumen y JSON por fases;
  - pipeline de fases: orden de ejecución, salto de fases tras error fatal y
    contadores por fase.
- Todo bug corregido lleva su test de regresión. Ejecuta `uv run pytest` y
  `uv run ruff check .` hasta pasar limpio.

## 4. Verificación del paso 1

1. `uv sync` — entorno consistente y sin dependencias nuevas.
2. `uv run pytest` y `uv run ruff check .` — toda la suite en verde, sin
   pantalla y sin red.
3. `uv run foto-index --library <biblioteca de prueba temporal> --help` y una
   pasada real con fotos con GPS (genera los fixtures igual que en la suite:
   imágenes mínimas con EXIF GPS sintético, creando los ficheritos que hagan
   falta en `tmp_path`) mostrando: fase `scan`, fase `geocode` con candidatos,
   caché y resumen por fase; segunda pasada sin repetir peticiones; una
   tercera con `--skip geocode` para tocar solo `scan`.
4. **Criterio de fin del paso 1:** `foto-index` completo con las dos fases,
   suite en verde, catálogo con el esquema nuevo poblado y **la UI sin tocar**
   (seguirá mostrando el catálogo de antes sin los campos de lugar). No
   continúes al paso 2 sin cerrar esto.

---

# Paso 2 — Visor: buscar y ordenar por ubicación

## 5. Decisiones vinculantes del visor

### 5.1. Consumo del lugar

- El paso 2 parte del **esquema del paso 1 ya migrado y poblado**. No cambies
  esquema ni comportamiento del indexador en este paso.
- El modelo `Photo` de la UI gana los campos de lugar resueltos; la consulta
  de carga del catálogo los incluye. Sin Qt en el SQL ni en las reglas puras.

### 5.2. Búsqueda por ubicación

- Los campos de lugar entran en `photo_search_text()` (ciudad, región, país;
  `España`, `Barcelona`...). Mantén el estilo del documento existente
  (casefold y espacios).

### 5.3. `:sort location`

- Se añade a `SORT_KEYS`, al vocabulario de los comandos
  (`:sort date | camera | location`, texto de uso y de la barra de estado) y
  a la ayuda integrada.
- Orden por **país → región → ciudad** (`casefold`, alfabético ascendente),
  con las fotos **sin ubicación siempre al final** (mismo patrón que
  `photo_sort_value`).
- **Grupos por ciudad**: `photo_group(location)` devuelve como clave y
  etiqueta `Ciudad, País` para cada grupo de ciudad.
- Sin ubicación: grupo propio "Sin ubicación" al final.
- El grupo activo, la navegación por grupos y los saltos de teclado funcionan
  igual que con `camera`, sin cambios estructurales.

### 5.4. Presentación del lugar

- El **panel de metadatos** muestra el lugar resuelto cuando existe (formato
  `Ciudad, País`; si falta alguna parte, muestra lo disponible; si no hay
  GPS, nada nuevo).
- `g m` sigue funcionando desde las coordenadas EXIF. **No lo cambies.**
- Teclas, ayudas y textos: nada de inventar atajos; esta parte no añade
  teclas nuevas aparte de todo lo relativo a `:sort`.

## 6. Diseño esperado del visor (orientativo, no literal)

- `ui/` — solo `models.py`, `presentation.py` (documento de búsqueda,
  `SORT_KEYS`, `photo_sort_value`, `photo_group`), `panels.py` (lugar en el
  panel) y el trato de `:sort location` en `window.py`; nada de lógica nueva
  en la ventana.

## 7. Tests del paso 2

- Fotos de prueba en memoria con y sin lugar; sin red y sin Qt donde el
  módulo lo permita (como hoy en `test_presentation.py`).
- Cubre, al menos:
  - documento de búsqueda con lugar (ciudad, región, país) y sin él;
  - orden por país → región → ciudad con sin-ubicación al final;
  - grupos por ciudad con etiqueta `Ciudad, País` y grupo "Sin ubicación";
  - carga del catálogo en la ventana con los campos de lugar nuevos;
  - inventario de teclas, ayuda y comandos documentados actualizados (los
    tests de inventario/retirados que hoy vigilan `:sort`).
- Todo bug corregido lleva su test de regresión.

## 8. Verificación del paso 2

1. `uv run pytest` y `uv run ruff check .` — suite en verde.
2. `uv run foto` con la biblioteca de prueba del paso 1: búsqueda por ciudad,
   `:sort location` con grupos por `Ciudad, País` y sin-ubicación al final,
   `g m` intacto y panel mostrando el lugar.
3. **Criterio de fin del paso 2:** visor completo con búsqueda, orden, grupos
   y panel; tu suite en verde y cero cambios pendientes sobre el indexador.

---

## 9. Documentación (parte de las dos entregas)

Se debe entregar documentación actualizada, no solo código. El ADR nuevo se
redacta en el paso 1 (fija las decisiones antes de implementar); el resto se
cierra al terminar el paso 2 para no duplicar cambios:

1. **ADR nuevo** (`docs/adr/0005-...`) siguiendo el formato de los anteriores
   (Estado, Fecha, Decide, Ámbito, Contexto, Decisión numerada, Consecuencias,
   Alternativas descartadas, Decisiones pendientes): el pipeline de fases, el
   servicio y su contrato, el modelo de estados, la caché por celda, la
   política de parada por rechazo, la opción de red y los límites de este
   ADR. Incluye también la integración del visor como decisión (documento de
   búsqueda, `:sort location`, grupos). *(paso 1)*
2. **`docs/adr/0002-indexado.md`** — actualiza las secciones que quedan
   superadas por el marco de fases y enlaza al ADR nuevo; no lo reescribas.
   *(paso 1)*
3. **`AGENT.md`** — regla 3 con la excepción de la geocodificación, la
   estructura del indexador por fases y el `:sort location` en lo que toque.
   *(paso 1 lo de la red y las fases; paso 2 lo del visor)*
4. **`README.md`** — principios (excepción de red), roadmap (la búsqueda y el
   orden por ubicación entran aquí, adelantando lo previsto para la Fase 2) y
   arquitectura (pipeline de fases y cliente de geocodificación).
   *(paso 1 lo de red y fases; paso 2 lo del visor)*
5. **`docs/diseno-ui.md`** — `:sort location` en las tablas de comandos y en
   el texto de metadatos del panel (lugar mostrado). *(paso 2)*

---

## 10. Fuera del objetivo

- Poda de la caché de miniaturas y límites de tamaño (fase futura).
- Otros proveedores de geocodificación, geocodificación por lotes
  (`/batch`) o servicios autónomos sin HTTP.
- Menú de mapa, vista de mapa o miniatura del mapa en la UI.
- Lugar autodeclarado por el usuario o edición manual del lugar.
- Escribir el lugar y coordenadas en XMP.
- `git commit` / `git push` sin permiso explícito.
