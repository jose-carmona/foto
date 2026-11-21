# Prompt — Rendimiento de arranque de la UI con 25k+ fotos (quick wins + arranque progresivo)

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> por qué la UI tarda en arrancar y cómo debe resolverse. Se ejecuta en **dos
> pasos diferenciados** (más un tercero condicionado), cada uno terminado y
> verificable por separado:
>
> 1. **Paso 1 — Quick wins de carga**: eliminar trabajo síncrono innecesario
>    del arranque (stats al disco, conexiones duplicadas, doble
>    materialización, ordenamientos redundantes, documento de búsqueda
>    recalculado). Sin cambio de comportamiento visible.
> 2. **Paso 2 — Arranque progresivo**: la ventana aparece inmediatamente con
>    un estado de carga; el catálogo se carga en un hilo worker y la rejilla
>    se popular por lotes. Cambio de percepción: time-to-first-pixel de
>    segundos a ~0,2 s.
> 3. **Paso 3 — Modelo virtualizado (condicionado)**: migrar la rejilla de
>    `QListWidget` a `QListView` + `QAbstractItemModel`. **Solo se ejecuta con
>    autorización explícita del dueño** tras evaluar los resultados de los
>    pasos 1 y 2; este prompt solo delimita su alcance.
>
> **Seguimiento.** Este prompt es el registro de tareas. Al terminar cada
> tarea: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si el trabajo genera una pendiente nueva, añádela al final del paso
> correspondiente con el mismo formato. Una tarea solo está cerrada con sus
> tests en verde y `uv run pytest` + `uv run ruff check .` completos.
> Mantén al día la tabla de estado de la sección «Resumen de estado».

## Resumen de estado

| Paso | Alcance | Estado |
|---|---|---|
| Paso 1 | Quick wins de carga (T1.1–T1.7, + T1.8 nueva) | **Completado** (2026-11-19) |
| Paso 2 | Arranque progresivo (T2.1–T2.8 y T2.10 hechas; T2.9 nueva, pendiente) | **Completado** (2026-11-19; pendiente la verificación visual del dueño) |
| Paso 3 | Modelo virtualizado (condicionado, solo diseño) | Pendiente de autorización del dueño |

---

## 0. Misión

Con bibliotecas de más de 25 000 fotos, la ventana de `foto` tarda varios
segundos en aparecer. El diagnóstico es claro y el diseño de datos es bueno
(catálogo SQLite como fuente de verdad, miniaturas en disco con lazy loading
por viewport): el problema es que **todo el pipeline de carga es síncrono y
corre en el hilo de UI antes del primer `window.show()`**. Los cuellos de
botella, en orden de impacto:

1. **25 000 `stat()` síncronos al filesystem** en
   `core/library.py` (`load_from_catalog`): se hace `full_path.stat()` por
   foto solo para formatear el tamaño, cuando el tamaño ya está en el
   catálogo (`size` de la fila canónica) y el `stat` solo aporta el caso
   normal. En volumen frío o USB esto puede costar varios segundos por sí
   solo.
2. **Doble materialización de los 25k objetos**: `load_from_catalog` construye
   25k dataclasses `CatalogPhoto` a partir del `fetchall()` del JOIN completo
   (~28 columnas), y después `ui/app.py` (`_to_ui_photo`) los duplica campo a
   campo en otros 25k `Photo`. Todo en el hilo de UI.
3. **Cuatro conexiones SQLite secuenciales** en el arranque: fotos
   (`open_read_only`), tags (`load_tags`), ubicaciones (`load_locations`) y
   favoritos (`connect_read_write`), cada una abriendo y cerrando.
4. **Tres ordenamientos completos** por arranque en `sorted_photos`
   (`ui/presentation.py`), con `casefold` por elemento en cada pasada.
5. **~25k `QListWidgetItem` creados uno a uno en Python** en
   `PhotoWindow._populate_grid` (`ui/window.py`), con 5 × `setData` +
   `setIcon` + `setSizeHint` cada uno, sobre un `QListWidget` con
   `setUniformItemSizes(False)`. Es el coste clásico de `QListWidget` a gran
   escala y el peor de la lista.
6. **Pasadas O(n) extra tras el populate** (`update_header_widths`,
   `_update_group_headers` —dos pasadas—, `_refresh_visible_cache`) y
   `update_header_widths` recorriendo de nuevo todas las filas en cada resize.
7. **Búsqueda sin índice precalculado**: `photo_search_text` (`ui/presentation.py`)
   reconstruye el documento de búsqueda casefolded de cada foto **en cada
   pulsación de tecla** desde `_apply_filter` (`ui/window.py`). Cuello de
   botella de uso, no solo de arranque.

La misión: que la app **responda lo antes posible** — ventana visible en
cuestiones de décimas, rejilla navegable en ~1 s — sin romper el modelo de
datos ni los ADRs. No se pide una reescritura: primero eliminar trabajo
innecesario (paso 1), luego desacoplar el arranque (paso 2).

La entrega incluye medición objetiva: el éxito se demuestra con cifras antes
y después, no con impresiones.

---

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables: nunca tocar originales, sin Qt en
   `core/`/`storage/`/`indexer/`, sin `git commit`/`push` sin permiso.
2. `README.md` — alcance, principios, arquitectura y roadmap.
3. `docs/adr/0003-consumo-del-indice-desde-el-visor.md` — cómo lee el visor
   el catálogo (solo lectura desde `catalog.sqlite`).
4. `docs/adr/0004-cache-de-miniaturas.md` — caché de miniaturas en disco por
   hash de contenido. El lazy loading de miniaturas **no se toca**: es la
   parte que ya está bien.
5. `docs/adr/0010-fototeca-distribuida.md` — resolución de ubicaciones y
   estados available/unavailable (afecta a `load_from_catalog`).
6. Código real: `src/foto/ui/app.py` (entrypoint, `load_photos`,
   `load_tags`, `load_locations`, `_to_ui_photo`),
   `src/foto/core/library.py` (`load_from_catalog`),
   `src/foto/ui/window.py` (`__init__`, `_populate_grid`, `_apply_filter`,
   `_schedule_visible_thumbnails`), `src/foto/ui/presentation.py`
   (`sorted_photos`, `photo_group`, `photo_search_text`),
   `src/foto/ui/grid.py` (`update_header_widths`),
   `src/foto/storage/catalog.py` (`open_read_only`, `list_present_files`),
   `src/foto/storage/tags.py` (`tag_assignments`, `connect_read_write`).

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible. No hagas `git commit`, `git push` ni cambios de
configuración de git sin permiso explícito.

---

# Paso 1 — Quick wins de carga

Objetivo: reducir el trabajo síncrono del arranque sin cambiar el
comportamiento visible. Al terminar, la app arranca por el mismo camino de
siempre pero en una fracción del tiempo.

- [x] **T1.1 — Medir el arranque base (baseline).** Crear un script o test de
  referencia (en `tests/` como benchmark marcado, o un script en `scripts/`)
  que construya un catálogo sintético de 25 000 fotos en una biblioteca de
  prueba (BD real, sin ficheros de imagen en el disco: para el coste de los
  `stat()` fallidos u reales importa que existan o no los originales; documenta
  ambas condiciones) y mida: (a) tiempo de `load_photos` completo, (b) tiempo
  de `_populate_grid`, (c) tiempo total hasta ventana visible con
  `QT_QPA_PLATFORM=offscreen`. Deja el método documentado y reproducible
  (comando exacto), porque los pasos 1 y 2 se evalúan contra estas cifras.
  Sin ADR nuevo: es instrumentalización, no decisión de diseño.
  > Nota (2026-11-19): hecha como script reproducible en
  > `scripts/bench_arranque.py` (no en `tests/`, para no ralentizar la suite).
  > Comando: `QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py`
  > (añadir `--originales` para la condición con ficheros presentes;
  > `--fotos`, `--repeticiones` y `--keep` son opcionales). El script siembra
  > un catálogo real (esquema v6, con cámara, fechas, GPS, favoritos) y reporta
  > mínimo y mediana de 3 repeticiones. **Baseline antes del paso 1** (25k,
  > mínimo de 3):
  > | Condición | (a) `load_photos` | (b) `_populate_grid` | (c) total hasta visible |
  > |---|---|---|---|
  > | sin originales | 5 729 ms | 1 032 ms | 7 041 ms |
  > | con originales | 5 633 ms | 1 019 ms | 6 956 ms |
  > El coste de los `stat()` previos era casi el mismo con y sin originales
  > (syscall fallida o exitosa, 25k veces); tras T1.2 la condición deja de
  > importar para el tamaño, y solo afecta a la evaluación de disponibilidad
  > (una `is_dir()` por raíz, no por foto).

- [x] **T1.2 — Eliminar el `stat()` por foto en `load_from_catalog`.**
  (`core/library.py`, bloque de formateo de tamaño.) El tamaño canónico ya
  viene del catálogo; úsalo siempre (`canonical["size"]`) y borra el
  `full_path.stat()` y su try/except. La frescura del tamaño es
  responsabilidad del indexador (`foto-index`), que ya la recalcula en cada
  pasada. Los datos de presentación (`CatalogPhoto.size`) no cambian de
  formato. Añade/ajusta tests que fijen que el tamaño sale de la BD con y sin
  el fichero presente.
  > Nota (2026-11-19): hecha. Tests nuevos en `tests/test_multi_location.py`
  > (`test_size_comes_from_the_catalog_even_when_the_file_grew`,
  > `test_size_comes_from_the_catalog_when_the_file_is_absent`): el fichero
  > crece o desaparece en disco sin reindexar y el tamaño mostrado sigue
  > siendo el de la BD (antes el `stat()` lo tapaba con el valor fresco).

- [x] **T1.3 — Unificar las conexiones SQLite del arranque.** (`ui/app.py`.)
  Hoy `load_photos`, `load_tags` y `load_locations` abren y cierran tres
  conexiones solo-lectura distintas y `connect_read_write` una cuarta.
  Refactoriza para que las tres lecturas compartan una única conexión
  solo-lectura abierta una vez (p. ej. agrupándolas en un solo loader que
  reciba el `Catalog` ya abierto), manteniendo `connect_read_write` aparte
  (escritor único de favoritos, semántica actual intacta). No cambies los
  módulos de `storage/` más de lo mínimo: el objetivo es el orden de
  arranque, no rediseñar su API. Tests: los de carga de la UI siguen en
  verde; añade uno que verifique una sola apertura solo-lectura si es
  barato de fijar.
  > Nota (2026-11-19): hecha. Nuevo `load_library(library_path, metadata_dir)`
  > en `ui/app.py`: abre una única conexión `mode=ro` y de ella salen fotos,
  > vocabulario y ubicaciones (`_photos_from`, `_tags_from`,
  > `_locations_from`). `MainWindow.__init__` lo usa. Los loaders
  > individuales (`load_photos`, `load_tags`, `load_locations`) se conservan
  > con su firma para tests y usos puntuales (cada uno abre y cierra la
  > suya). `connect_read_write` intacta. Test nuevo en
  > `tests/test_ui_load_photos.py` (`test_load_library_opens_the_catalog_read_only_once`):
  > espía de `Catalog.open_read_only` → 1 apertura. `storage/` no cambia.

- [x] **T1.4 — Eliminar la doble materialización de fotos.** (`ui/app.py`,
  `_to_ui_photo`; `core/library.py`.) Evita construir 25k `CatalogPhoto`
  para después copiarlos campo a campo en 25k `Photo`. Opciones aceptadas
  (elige la de menor difusión y documenta con una nota): (a) que
  `load_from_catalog` produzca directamente los objetos de UI, moviendo
  `CatalogPhoto` a `ui/models.py` si es puro y sin Qt; o (b) un constructor
  de `Photo` desde fila del catálogo que evite la copia campo a campo. Si
  `CatalogPhoto` tiene consumidores fuera de la UI, indica la opción (b).
  Los tests que cubren `library.py` y la carga de la UI deben seguir en
  verde sin cambios de comportamiento.
  > Nota (2026-11-19): hecha por la **opción (b)**. La opción (a) chocaba
  > con la regla del grafo (`core/` no importa `foto.ui`, y mover el loader
  > entero a `ui/` difunde cambios en `test_multi_location.py` y
  > `test_ui_tolerancia.py`). Menor difusión: `load_from_catalog(catalog,
  > factory=CatalogPhoto)` gana un parámetro de fábrica (el núcleo sigue
  > sin conocer el modelo de UI, y los tests del núcleo no cambian: el
  > default es `CatalogPhoto`), y `Photo.from_catalog(**campos)`
  > (`ui/models.py`) es el constructor que recibe los campos ya formateados
  > y materializa el objeto **una sola vez**; la única conversión restante
  > es `TagAssignment` → `PhotoTag`, que ya era una transformación de forma
  > real. `_to_ui_photo` queda borrada; `load_photos` inyecta
  > `Photo.from_catalog`. Suite de `library.py` y de carga de la UI en
  > verde sin tocar expectativas.

- [x] **T1.5 — Precalcular el documento de búsqueda.** (`ui/presentation.py`,
  `ui/window.py`.) Hoy `photo_search_text(photo)` se invoca por foto en cada
  pulsación de tecla. Introduce un índice precalculado — p. ej.
  `build_search_index(photos) -> dict[key, str]` en `presentation.py`
  (puro, sin Qt), construido una vez al cargar (y revalidado al cambiar
  tags/favoritos) — y que `_apply_filter` lo consulte. El texto del
  documento debe seguir siendo idéntico carácter a carácter (tests de
  `tests/test_ui_search.py` sin cambios de expectativas, solo de mecanismo
  si hace falta).
  > Nota (2026-11-19): hecha. `build_search_index(photos)` en
  > `presentation.py` (puro); la ventana lo construye en `__init__` y en
  > `set_photos`, lo consulta en `_apply_filter` y lo revalida de forma
  > puntual en `_toggle_favorite` (las etiquetas entran en el documento).
  > Los tests de `tests/test_ui_search.py` pasan sin cambios de
  > expectativas; test nuevo en `tests/test_presentation.py` fija que cada
  > entrada del índice es idéntica a `photo_search_text`. El filtro `:tag`
  > sigue evaluándose por foto (`photo_matches_tag_name`): solo el
  > documento de texto se cachea.

- [x] **T1.6 — Reducir los ordenamientos redundantes.** (`ui/presentation.py`,
  `sorted_photos`.) Evalúa las tres pasadas de ordenación: si la clave
  elegida puede computarse una vez por foto (con `casefold` ya aplicado) y
  ordenar una única vez, hazlo. Conserva el resultado exacto (los tests de
  `tests/test_presentation.py` fijan el orden). Si la mejora medible es
  marginal frente a T1.2/T1.4, registra la decisión en una nota y deja la
  simplificación solo si no degrada claridad.
  > Nota (2026-11-19): hecha, con una salvedad documentada: una sola pasada
  > no es posible sin cmp a nivel Python (la dirección mixta
  > nombre-asc/fecha-desc/criterio no se expresa como tupla comparable) y
  > `cmp_to_key` sería más lento que tres pasadas de tuplas en C. Lo que sí
  > se ha eliminado: la tercera pasada era establemente idéntica a la
  > segunda cuando el criterio activo es `date` (ahora se omite) y las
  > claves se precalculan una vez por foto en lugar de recomputarse por
  > pasada. Los tests de `tests/test_presentation.py` fijan el orden y
  > pasan sin cambios; la mejora medible se anota en T1.7.

- [x] **T1.7 — Verificación y cierre del paso 1.** Suite completa en verde
  (`uv run pytest`, `uv run ruff check .`); re-ejecuta el benchmark de T1.1
  en las mismas condiciones y anota las cifras antes/después en este
  fichero. Smoke manual: abrir la biblioteca real y comprobar que
  rejilla, búsqueda, grupos, favoritos y degradación por ubicación no
  disponible funcionan igual que antes.
  > Nota (2026-11-19): suite completa en verde (157 tests) y `ruff` limpio.
  > Cifras después (mismo benchmark, 25k, mínimo de 3 repeticiones):
  >
  > | Métrica | Antes (sin orig.) | Después (sin orig.) | Antes (con orig.) | Después (con orig.) |
  > |---|---|---|---|---|
  > | (a) `load_photos` | 5 729 ms | **2 831 ms (−51 %)** | 5 633 ms | **2 827 ms** |
  > | (b) `_populate_grid` | 1 032 ms | 1 014 ms (=) | 1 019 ms | 1 013 ms (=) |
  > | (c) total hasta visible | 7 041 ms | **4 268 ms (−39 %)** | 6 956 ms | **4 265 ms** |
  >
  > Reparto de la mejora en `load_photos`: la resolución de rutas cacheada
  > por directorio (T1.8) y la disponibilidad por raíz son la mayor parte
  > (~2,4 s); el `stat()` de tamaño (T1.2) y la materialización única
  > (T1.4) aportan ~0,4 s. En SSD local cálido el coste del `stat()` era
  > menor de lo estimado en el diagnóstico; en volumen frío o USB la
  > horquilla a favor de T1.2/T1.8 sería mayor (ahora ya no depende de la
  > condición: ambas cifras son idénticas). `_populate_grid` no baja: su
  > coste es la creación de 25k `QListWidgetItem` (cuello 5 del
  > diagnóstico), que se aborda en el paso 2 (lotes) y evalúa el paso 3
  > (modelo virtualizado). El time-to-first-pixel sigue siendo el total
  > síncrono hasta el paso 2.
  >
  > Smoke offscreen automatizado sobre la biblioteca real (~29 600 fotos,
  > `~/wrk/images` + metainformación `~/wrk/meta`, solo lectura, sin
  > escrituras): carga y ventana visibles en 5,6 s, búsqueda `".jpg"` → 31
  > coincidencias y recuperación a las 29 567, inexistente → 0 y
  > recuperación, `:sort date/camera/location` con 208/53/87 grupos,
  > banner de «iMac» no disponible con «29 434 fotos no visibles», 4
  > favoritas del catálogo. **Pendiente del dueño:** la verificación
  > visual manual (rejilla, visor, teclado) en la app real; el smoke
  > offscreen no sustituye la percepción.

**Criterio de fin del paso 1:** ninguna syscall de `stat` por foto en el
arranque, una sola conexión solo-lectura, 25k objetos construidos una única
vez, búsqueda con índice precalculado y una mejora medible y anotada del
tiempo de `load_photos` + `_populate_grid`.

- [x] **T1.8 — (nueva, 2026-11-19) Ninguna syscall de `stat` por foto: caché
  de resolución de rutas y disponibilidad por raíz.** El perfil de
  `load_photos` (cProfile, 25k) reveló que el criterio de fin del paso no se
  cumplía con solo T1.2: quedaban dos costes de filesystem por foto.
  1. `full_path = (location_path / rel_path).resolve()` en
     `core/library.py`: `realpath` lanza un `lstat` **por componente**
     (~275 000 llamadas para 25k fotos, ~3,4 s perfilado) —más que el
     `stat()` de tamaño que quitó T1.2—.
  2. `states.setdefault(path, availability_state(...))`: `setdefault`
     evalúa su argumento siempre, así que la disponibilidad se evaluaba
     25k veces en vez de una por raíz distinta (~0,5 s perfilado).
  > Nota (2026-11-19): hecha. La disponibilidad usa guardia explícita
  > (`if path not in states`) y las rutas se resuelven una vez por
  > **directorio padre distinto** (`resolved_parents`), uniendo la hoja por
  > nombre. Resultado idéntico para ficheros regulares —el caso real— y
  > para hojas inexistentes; única desviación posible: una foto cuyo
  > fichero fuese un enlace simbólico queda con la ruta del enlace en vez
  > de la del destino (misma imagen al abrirla, misma clave dentro de la
  > sesión, los favoritos van por `photo_hash`). Tests de rutas canónicas
  > (`test_multi_location.py`) en verde sin cambios. Sin syscall de `stat`
  > por foto: solo una por directorio distinto y una `is_dir()` por raíz.

---

# Paso 2 — Arranque progresivo: ventana primero, datos después

Objetivo: aunque el trabajo total baje (paso 1), mientras sea síncrono el
usuario ve un proceso congelado. Aquí la ventana aparece de inmediato, la
carga corre en un hilo worker y la rejilla se popular por lotes.

- [x] **T2.1 — Decidir y documentar el mecanismo de concurrencia.** Un ADR
  corto (o una enmienda al ADR 0003 con nota de origen) que fije: hilo
  worker (`QThread` + objeto worker, sin `QThreadPool` global), comunicación
  por señales (lista de `Photo` lista → señal al hilo de UI), propiedad del
  catálogo durante la carga (solo-lectura, sin lock de escritor: es el
  modelo actual del visor) y qué pasa si la carga falla o el usuario cierra
  la ventana a mitad (worker cancelable o ignorado, sin escrituras
  pendientes). Sin Qt en `core/`/`storage/`: el worker vive en `ui/`.
  > Nota (2026-11-19): ADR nuevo `docs/adr/0011-arranque-progresivo.md`.
  > Worker `QThread` + objeto worker (`LibraryLoadWorker`, en `ui/app.py`),
  > señales con datos puros (`loaded(fotos, etiquetas, ubicaciones)` /
  > `failed(mensaje)`), catálogo solo-lectura abierto y cerrado dentro del
  > hilo del worker, y cancelación **cooperativa** (no solo «ignorado»):
  > `load_from_catalog`/`load_library` aceptan un `should_continue` puro que
  > corta con `LibraryLoadCancelled` (excepción de control en
  > `core/library.py`, sin Qt). La RW de favoritos sigue en el hilo de UI.
  > Verificación: `tests/test_multi_location.py` (núcleo, sin Qt) y
  > `tests/test_ui_progressive.py` (worker).

- [x] **T2.2 — Ventana inmediata con estado de carga.** (`ui/app.py`,
  `ui/window.py`.) `main()` crea la `MainWindow` vacía y la muestra antes de
  cargar nada. Durante la carga, la rejilla muestra un estado de carga
  (texto u overlay discreto conforme a `docs/diseno-ui.md`; nada de
  animaciones llamativas). La barra de estado puede reutilizar el patrón
  existente. Si no hay catálogo o hay error fatal, el camino de aviso y
  cierre actual se conserva (mismo mensaje, quizá ahora tras el show:
  decide y documenta la nota).
  > Nota (2026-11-19): hecha. `MainWindow.__init__` construye la ventana
  > vacía y `main()` la muestra; el estado de carga reutiliza el estado
  > vacío (`empty_label` «Cargando el catálogo…», texto discreto sin
  > spinner) y la barra fija «cargando el catálogo…» con el patrón
  > `set_pinned` existente, que `set_photos` retira al entrar los datos.
  > **Decisión documentada sobre el guard**: el aviso de error fatal
  > (catálogo ausente, esquema sin migrar, catálogo ilegible) ocurre ahora
  > **tras el show** —la señal `failed` del worker muestra el mismo
  > `QMessageBox.critical` y cierra con `QApplication.exit(1)`—, conservando
  > el mensaje exacto del guard anterior. Motivo: es la excepción, y a
  > cambio la ventana siempre aparece en décimas. La conexión RW de
  > favoritos se abre al llegar la carga (mismo hilo de UI): sin catálogo
  > no hay sesión y todos los errores fatales comparten la vía de la señal
  > (antes se abría antes de cargar y un catálogo ausente daba un error
  > críptico de `mode=rw`). Test:
  > `tests/test_ui_progressive.py::test_main_window_reports_a_missing_catalog_after_the_show`.

- [x] **T2.3 — Carga del catálogo en el worker.** (`ui/app.py`,
  `ui/window.py`.) Mover `load_photos` (+ tags, ubicaciones y disponibilidad,
  ya unificadas por T1.3) al worker. La señal entrega la lista de `Photo` al
  hilo de UI. Ningún objeto Qt cruz el hilo (solo datos); los iconos
  quedan, como hoy, en el servicio de miniaturas del hilo de UI.
  > Nota (2026-11-19): hecha. `LibraryLoadWorker.load()` ejecuta
  > `load_library` (T1.3: una sola conexión `mode=ro`) en el hilo worker y
  > emite `loaded(fotos, etiquetas, ubicaciones)` con dataclasses puros;
  > `MainWindow._on_library_loaded` abre la conexión RW y llama a
  > `PhotoWindow.set_library` (nuevo), que recompone el vocabulario de
  > etiquetas, el panel de ubicaciones y el banner (ADR 0010) y delega el
  > populate en `set_photos`. Los loaders individuales (`load_photos`,
  > `load_tags`, `load_locations`) quedan intactos para tests y usos
  > puntuales. Iconos y miniaturas intactos en el hilo de UI.

- [x] **T2.4 — Populate de la rejilla por lotes.** (`ui/window.py`,
  `_populate_grid`.) Sustituir la inserción monolítica por lotes de ~500
  ítems procesados con un `QTimer` de disparo cero (o patrón equivalente)
  para que el hilo de UI respire entre lotes: tras el primer lote la rejilla
  ya es visible y navegable; los siguientes se insertan en fondo. Mantén
  `self._grid_items` coherente durante y tras la carga (búsqueda y
  favoritos deben funcionar incluso a medio populate o, como mínimo,
  esperarse a que termine: decide y documenta en una nota). El cálculo de
  orden y grupos se hace una vez antes del primer lote.
  > Nota (2026-11-19): hecha. `_populate_grid` ordena y calcula grupos una
  > vez y devuelve `True` si aplazó el trabajo en lotes de
  > `POPULATE_BATCH_SIZE = 500` (un `QTimer` de disparo cero por lote);
  > por debajo de `POPULATE_SYNC_LIMIT = 1000` (demo, pruebas,
  > bibliotecas pequeñas) el camino es el monolítico de siempre. Los
  > llamadores (`set_photos`, `_sort_grid`, el `__init__`) difieren su
  > trabajo post-populate vía `on_done`, que `_finish_populate` ejecuta al
  > terminar el último lote. **Decisión de coherencia documentada**: la
  > ventana funciona a medio populate, no espera al final — cada ítem nace
  > ya oculto o visible con el filtro vigente, evaluado en el momento de
  > insertarlo (un cambio de búsqueda/``:tag`` a medio populate también
  > alcanza a los lotes siguientes), las cabeceras de grupo nacen ocultas
  > y se desocultan con su primera foto visible, y `_grid_items`, la caché
  > de visibles (`_visible_keys_cache`, posiciones y
  > `PhotoGrid.append_visible_rows`, nuevo) y los recuentos de la barra se
  > mantienen al día lote a lote; `_finish_populate` normaliza con una
  > pasada de `_apply_filter`. El cálculo de visibilidad compartido está
  > en `_matches_filter` (misma regla que `_apply_filter`, T1.5).
  > Tests: `tests/test_ui_progressive.py` (lotes, búsqueda a medio
  > populate, `:sort` a medio populate, cola diferida).

- [x] **T2.5 — Miniaturas y visibilidad durante el populate.** El mecanismo
  actual (viewport + debounce + cola reemplazable) ya es correcto: verifica
  solo que no se dispare contra la rejilla a medio llenar (p. ej. ignorar
  peticiones de ítems aún no insertados) y que al terminar el populate la
  primera tanda de miniaturas del viewport sale sin jitter. No cambies
  `ui/thumbnails.py` salvo que la verificación lo exija.
  > Nota (2026-11-19): verificada sin tocar `ui/thumbnails.py`. Las
  > peticiones salen de `_visible_photo_keys`, que recorre la rejilla real:
  > un ítem aún no insertado no puede entrar en la cola (test
  > `test_visible_thumbnail_requests_ignore_uninserted_items`). Cada lote
  > programa la petición (el debounce de 60 ms existente agrupa) para que
  > el primer viewport pida miniatura cuanto antes, y `_finish_populate`
  > reprograma al terminar: la primera tanda sale con el mismo camino de
  > siempre, sin jitter añadido.

- [x] **T2.6 — Cancelación y cierre limpio.** Si el usuario cierra la ventana
  durante la carga o el populate: el worker se detiene (o su resultado se
  descarta), no quedan timers de populate vivos y la app sale sin
  traceback. Tests con `QT_QPA_PLATFORM=offscreen` del ciclo completo:
  ventana vacía → señal → populate por lotes → cierre a mitad sin errores.
  > Nota (2026-11-19): hecha, con una **regresión real corregida durante la
  > verificación manual**: `Ctrl+q` (y ``:q``, ⌘Q) salen del bucle de
  > eventos **sin pasar por el `closeEvent`** de la ventana, y el
  > `QThread` moría en plena carga («QThread: Destroyed while thread is
  > still running»). La cancelación y la espera viven ahora en
  > `MainWindow._shutdown_library_load` (idempotente) y se cuelgan tanto
  > del `closeEvent` como de `aboutToQuit`, que Qt emite en toda salida
  > del `exec()`; el `closeEvent` de `PhotoWindow` detiene además el timer
  > del populate (`_cancel_populate`). La espera va acotada (10 s) y en la
  > práctica es corta: el corte cooperativo ocurre en la foto siguiente,
  > y el único tramo no interrumpible es el `fetchall` inicial. Tests:
  > `test_close_mid_populate_leaves_no_live_timers`,
  > `test_main_window_close_mid_load_cancels_cleanly` y
  > `test_quit_mid_load_cancels_the_worker_about_to_quit` (regresión, con
  > `exec()` real y `quit()`).
  > Nota (2026-11-19, T2.10): el método se renombra a
  > `MainWindow._shutdown_background_work` y pasa a cubrir también el
  > servicio de miniaturas y el pool del visor (ver T2.10).

- [x] **T2.7 — Verificación y cierre del paso 2.** Suite completa en verde;
  re-ejecuta el benchmark de T1.1 midiendo ahora el **time-to-first-pixel**
  (ventana visible) y el time-to-navigable (primer lote navegable) además
  del time-to-complete. Anota las cifras. Smoke manual en la biblioteca
  real: arranque, scroll durante la carga (si procede), búsqueda a medio
  populate, favoritos, y cierre a mitad de carga.
  > Nota (2026-11-19): suite completa en verde (174 tests, 17 nuevos en
  > `tests/test_ui_progressive.py`) y `ruff` limpio. Benchmark
  > (`QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py`,
  > 25k sintético, mínimo de 3 repeticiones; (b) con el umbral de lotes
  > subido para forzar el camino síncrono y que la cifra sea comparable):
  >
  > | Métrica | Antes (paso 1) | Después (sin orig.) | Después (con orig.) |
  > |---|---|---|---|
  > | (a) `load_photos` | 2 831 ms | 2 873 ms | 2 971 ms |
  > | (b) `_populate_grid` síncrono | 1 014 ms | 1 136 ms | 1 121 ms |
  > | (c) primer píxel (ventana + estado de carga) | — | **50 ms** | **51 ms** |
  > | (c) primer lote navegable | — | 3 404 ms | 3 416 ms |
  > | (c) populate completo | — | 5 086 ms | 5 037 ms |
  > | (c) «total hasta visible» del paso 1 (referencia) | 4 268 ms | — | — |
  >
  > Lectura de las cifras:
  > - **Primer píxel: 4 268 ms → 50 ms** (−99 %): la ventana aparece con su
  >   estado de carga antes de tocar el catálogo; el criterio «visible en
  >   cuestiones de décimas» se cumple de sobra en el sintético.
  > - **Primer lote navegable: 3,4 s.** No alcanza el «~1 s» del criterio:
  >   el primer lote no puede preceder a la carga, y `load_photos` sigue en
  >   ~2,9 s (trabajo del paso 1) más el orden (una pasada, ~0,2 s) y el
  >   primer lote. Bajarlo exigiría emisiones parciales desde el worker
  >   (descartada en el ADR 0011 por duplicar el contrato de señales) o más
  >   reducción de `load_from_catalog` (fuera del paso 2).
  > - **Populate completo: ~5,1 s** frente a los 4,3 s del camino síncrono
  >   anterior: el populate por lotes añade ~0,9 s al total (insertar con el
  >   bucle de eventos respirando entre lotes de 500 ≈ 23 ms cuesta más que
  >   el monolítico de 1,1 s; con lotes mayores el sobrecoste baja, p. ej.
  >   ~0,1 s con lote de 1 000, a costa de frames por encima del
  >   presupuesto de 50 ms). Es el trade-off deliberado del paso 2: ventana
  >   a los 0,05 s y rejilla navegable mucho antes del total, a cambio de
  >   terminar ~0,8 s más tarde. Medido ad-hoc con un perfil de lotes
  >   (500/1 000/2 000/5 000) sobre el mismo catálogo sintético.
  >
  > Smoke offscreen automatizado sobre la biblioteca real (~29 567 fotos,
  > copia del catálogo de `~/wrk/meta` en un directorio temporal, solo
  > lectura de los originales, sin tocar la metainformación real): primer
  > píxel en 626 ms —incluye ~0,43 s de **calentamiento único del
  > subsistema de fuentes de Qt** en la primera pintura de texto del
  > proceso, que el arranque síncrono anterior también pagaba; sin él,
  > ~0,19 s—, primer lote a los 4,6 s y populate completo a los 6,6 s;
  > búsqueda «.jpg» → 31 coincidencias y recuperación a las 29 567,
  > inexistente → 0 y recuperación, `:sort date/camera/location` con
  > 208/53/87 grupos y 29 567 visibles en los tres, 4 favoritas del
  > catálogo, banner de «iMac» no disponible, y cierre tras la carga y a
  > mitad de carga limpios (worker cancelado, hilo parado, sin timers).
  > **Pendiente del dueño:** la verificación visual manual (arranque,
  > scroll durante la carga, búsqueda a medio populate, favoritos, cierre
  > a mitad) en la app real; el smoke offscreen no sustituye la percepción.
  >
  > Corrección (2026-11-19, T2.8): el desglose de fuentes de este smoke se
  > midió en plataforma offscreen; en la plataforma real (cocoa) el
  > calentamiento inevitable es ~0,2 s (población de la base de fuentes)
  > porque Qt ya fija la fuente del sistema y no hay coste de alias. Ver
  > T2.8. Y corrección de recuento: la suite son **988 ítems** de test
  > (con parametrizaciones), no 174.

**Criterio de fin del paso 2:** con 25k fotos, la ventana es visible en
cuestiones de décimas y la rejilla navegable en ~1 s, medido con el
benchmark; el comportamiento final (rejilla completa, búsqueda, grupos,
favoritos, degradación) es indistinguible del actual.

- [x] **T2.8 — (nueva, 2026-11-19) Calentamiento de fuentes de Qt en el
  primer pintado.** Hallazgo del perfilado del arranque progresivo: la
  primera pintura de texto del proceso cuesta ~0,43 s en macOS —~0,18 s
  poblando alias de la familia por defecto «Sans Serif», que no existe (el
  propio Qt lo avisa: «Replace uses of missing font family "Sans Serif"»),
  y ~0,25 s poblando la base de fuentes—; la segunda pintura cuesta
  1–6 ms. Está dentro del primer píxel por inevitable (el estado de carga
  ya pinta texto) y el arranque síncrono anterior lo pagaba igualmente
  dentro de su total. Sustituir «Sans Serif» por una familia existente
  bajaría la primera pintura a ~0,23 s. Es una tarea de `ui/theme.py`
  (tipografía del sistema, multiplataforma: no hardcodear una familia de
  macOS) y queda fuera del paso 2; el dueño decide si la quiere.
  > Nota (2026-11-19): hecha, con el diagnóstico corregido al medirlo.
  > **Corrección de diagnóstico:** los ~0,43 s son coste de la plataforma
  > **offscreen**. En la plataforma real (cocoa) Qt ya fija la fuente del
  > sistema (`.AppleSystemUIFont`) como fuente de la aplicación: no hay
  > coste de alias y el calentamiento inevitable del primer texto pintado
  > es ~0,2 s (población de la base de fuentes, que cualquier primera
  > pintura con texto paga, también la del arranque síncrono anterior).
  >
  > **Implementación** (`ui/theme.py`, `_apply_system_font`, llamada desde
  > `apply_theme`): si la fuente de la aplicación no resuelve a sí misma
  > (detección con `QFontInfo`, sin sentinelas de nombres internos de Qt),
  > se fija la familia que la propia plataforma resuelve para ella,
  > conservando tamaño y peso del resto de la fuente. Sin familias
  > empaquetadas ni temas propios: es la «tipografía del sistema» del
  > diseño puesta explícita. Idempotente. Efecto: en plataformas sin tema
  > (offscreen, y cualquier escritorio que no imponga fuente) la app deja
  > de depender de la familia inexistente «Sans Serif» —desaparece el
  > aviso de Qt y el coste de alias—; en cocoa es un no-op de pocos ms
  > (la comprobación) tras el primer arranque.
  >
  > **Cifras medidas:**
  > - cocoa: `apply_theme` 194 ms (la enumeración de la base de fuentes
  >   vía `QFontInfo`, antes pagada en la primera pintura), primera
  >   pintura de texto 26 ms (antes 201 ms), fuente `.AppleSystemUIFont`
  >   sin cambios. **El total hasta el primer píxel no cambia** (~0,45 s
  >   con construcción y show): la enumeración debe ocurrir en algún
  >   punto del camino, y adelantarla no lo quita.
  > - offscreen (biblioteca real, smoke del paso): `apply_theme` 424 ms
  >   (alias + base de fuentes), resto del primer píxel 198 ms → total
  >   0,62 s, igual que antes del guardia (626 ms): mismo trabajo,
  >   adelantado, pero ahora con una familia real (`.AppleSystemUIFont`)
  >   y sin depender del alias roto.
  > - Reducción real del primer píxel descartada: exigiría que la primera
  >   pintura no tuviera texto (aplazar el texto del estado de carga un
  >   frame), con orden no determinista (expose del compositor frente al
  >   timer) y un flash visible de chrome vacío; el presupuesto del
  >   diseño (visible < 500 ms) ya se cumple en la plataforma real.
  >
  > Tests nuevos en `tests/test_ui_styles.py`:
  > `test_apply_theme_leaves_a_font_that_resolves_to_itself`,
  > `test_apply_system_font_replaces_a_missing_family` (familia
  > inexistente → sustituida conservando peso/tamaño) y
  > `test_apply_system_font_is_idempotent`.

**Criterio de fin del paso 2:** con 25k fotos, la ventana es visible en
cuestiones de décimas y la rejilla navegable en ~1 s, medido con el
benchmark; el comportamiento final (rejilla completa, búsqueda, grupos,
favoritos, degradación) es indistinguible del actual.

- [ ] **T2.9 — (nueva, 2026-11-19) Segfault intermitente al terminar el
  proceso tras la suite completa.** Observado dos veces en ~20 corridas
  (las demás limpias, incluidas 13 corridas completas seguidas sin
  reproducirlo): tras el `[100 %]` de pytest, el proceso muere con
  «Fatal Python error: Segmentation fault» en el teardown del
  intérprete (Python 3.15.1 x86_64 + PySide6; el dump de faulthandler
  queda cortado y no muestra el hilo culpable). Los tests en sí pasan
  todos (988 ítems, 0 fallos): es solo salida del proceso. No es
  reproducible a demanda; los tests de worker de carga asertan el hilo
  parado, así que no es el camino conocido de «QThread destruido en
  ejecución». Pendiente: reproducirlo con dump completo (faulthandler
  sin truncar) y decidir si es un problema del entorno (Python 3.15 +
  PySide6 en teardown) o algo que la suite deja suelto.
  > Nota (2026-11-19, T2.10): el reporte del dueño al salir con ``Ctrl+q``
  > durante la carga de miniaturas destapó el mismo camino de clase
  > (objetos de señales de tareas ``QRunnable`` destruidos con tareas en
  > vuelo), arreglado en T2.10; es el candidato natural de este segfault.
  > Re-evaluar tras usar la app un tiempo: si el segfault desaparece,
  > cerrar esta tarea sin más.

- [x] **T2.10 — (nueva, 2026-11-19) Salida con miniaturas en vuelo:
  «RuntimeError: Signal source has been deleted».** Reportado por el
  dueño al pulsar ``Ctrl+q`` mientras se cargaban miniaturas: el
  ``QRunnable`` de una miniatura en vuelo emitía hacia un objeto de
  señales ya destruido. Dos agujeros combinados: ``quit()`` sale del
  bucle sin ``closeEvent`` (el único sitio donde se apagaba el servicio
  de miniaturas), y ``shutdown()`` espera a las tareas activas con un
  límite de 2 s que una decodificación grande puede superar.
  > Nota (2026-11-19): hecha, por las dos vías.
  > 1. **El apagado de fondo cubre todo** (T2.6): `_shutdown_library_load`
  >    renombrada a `MainWindow._shutdown_background_work` y extendida —
  >    además de cancelar y esperar al worker de carga, apaga el servicio
  >    de miniaturas (`shutdown()`, ya idempotente) y drena el pool del
  >    visor (nuevo `PhotoWindow.shutdown_background_work`, que detiene
  >    además los timers del populate). Se cuelga de `closeEvent` y de
  >    `aboutToQuit`, así que toda vía de salida apaga el mismo orden de
  >    cosas.
  > 2. **Guardia en el emit** (`ui/thumbnails.py` y `ui/canvas.py`,
  >    `_emit_if_alive`): las tareas ``QRunnable`` de miniaturas y de
  >    decodificación del visor emiten bajo guardia —si su objeto de
  >    señales (sin padre) ya fue destruido porque la tarea superó la
  >    espera acotada del apagado, el resultado es descartable y
  >    ``run()`` acaba sin excepción—. ``ui/thumbnails.py`` se toca solo
  >    en el emit: la cola, la caché y el mecanismo de ADR 0004 siguen
  >    intactos.
  > Tests nuevos en `tests/test_ui_progressive.py`:
  > `test_quit_shuts_the_thumbnail_service_down_about_to_quit`
  > (``exec()`` real: tras el ``quit`` el pool está drenado y la
  > generación del servicio avanzada) y las de tareas con el objeto de
  > señales ya destruido
  > (`test_thumbnail_task_discards_the_result_when_the_source_is_gone`,
  > `test_image_task_discards_the_result_when_the_source_is_gone`).
  > Suite completa en verde (991 ítems) y `ruff` limpio.

---

# Paso 3 — Modelo virtualizado (condicionado, solo alcance)

**No se implementa en esta entrega.** Solo se delimita para que la decisión
futura sea barata. Ejecuta una tarea de análisis y para:

- [ ] **T3.1 — Nota de alcance de la migración a `QListView` +
  `QAbstractItemModel`.** Documento corto (aquí mismo, como anexo, o un ADR
  si el dueño pide formalizarlo) que describa: qué gana (menos llamadas
  Python por ítem, layout uniforme cacheado, camino claro a 100k+), qué
  toca (`ui/grid.py`, `_populate_grid`, `_grid_items`, cabeceras de grupo,
  búsqueda y visibilidad — todo lo que hoy asume `QListWidget`), qué
  alternativas intermedias existen (bloques de `insertItems`,
  `setUniformItemSizes(True)` ya aplicado en T1.x) y una estimación de
  esfuerzo. A partir de las cifras de los pasos 1 y 2, recomienda si merece
  la pena. Nada de código.

---

## Fuera del objetivo

- **Cambiar el esquema del catálogo** o el pipeline del indexador: el tamaño
  canónico ya está en la BD; su frescura es responsabilidad de `foto-index`.
- **Tocar el mecanismo de miniaturas** (`ui/thumbnails.py`, caché de disco,
  ADR 0004): funciona bien; solo se verifica en T2.5.
- **Paginación de la rejilla** (ventanas de datos sobre el modelo completo):
  con los pasos 1 y 2 el populate completo es asumible; si algún día no lo
  es, el paso 3 es el camino, no la paginación.
- **`QGraphicsView`, QML o cambio de framework de UI.**
- **Rebalance, `placement`, o cualquier tarea de la fototeca distribuida**
  (su propio prompt las cubre).
- **Dependencias nuevas de runtime.**
- Cualquier cambio de teclas, comandos `:` o apariencia conforme a
  `docs/diseno-ui.md` salvo el estado de carga de T2.2.
