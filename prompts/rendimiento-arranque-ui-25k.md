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
| Paso 1 | Quick wins de carga (T1.1–T1.7) | Pendiente |
| Paso 2 | Arranque progresivo (T2.1–T2.7) | Pendiente |
| Paso 3 | Modelo virtualizado (condicionado, solo diseño) | Pendiente |

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

- [ ] **T1.1 — Medir el arranque base (baseline).** Crear un script o test de
  referencia (en `tests/` como benchmark marcado, o un script en `scripts/`)
  que construya un catálogo sintético de 25 000 fotos en una biblioteca de
  prueba (BD real, sin ficheros de imagen en el disco: para el coste de los
  `stat()` fallidos u reales importa que existan o no los originales; documenta
  ambas condiciones) y mida: (a) tiempo de `load_photos` completo, (b) tiempo
  de `_populate_grid`, (c) tiempo total hasta ventana visible con
  `QT_QPA_PLATFORM=offscreen`. Deja el método documentado y reproducible
  (comando exacto), porque los pasos 1 y 2 se evalúan contra estas cifras.
  Sin ADR nuevo: es instrumentalización, no decisión de diseño.

- [ ] **T1.2 — Eliminar el `stat()` por foto en `load_from_catalog`.**
  (`core/library.py`, bloque de formateo de tamaño.) El tamaño canónico ya
  viene del catálogo; úsalo siempre (`canonical["size"]`) y borra el
  `full_path.stat()` y su try/except. La frescura del tamaño es
  responsabilidad del indexador (`foto-index`), que ya la recalcula en cada
  pasada. Los datos de presentación (`CatalogPhoto.size`) no cambian de
  formato. Añade/ajusta tests que fijen que el tamaño sale de la BD con y sin
  el fichero presente.

- [ ] **T1.3 — Unificar las conexiones SQLite del arranque.** (`ui/app.py`.)
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

- [ ] **T1.4 — Eliminar la doble materialización de fotos.** (`ui/app.py`,
  `_to_ui_photo`; `core/library.py`.) Evita construir 25k `CatalogPhoto`
  para después copiarlos campo a campo en 25k `Photo`. Opciones aceptadas
  (elige la de menor difusión y documenta con una nota): (a) que
  `load_from_catalog` produzca directamente los objetos de UI, moviendo
  `CatalogPhoto` a `ui/models.py` si es puro y sin Qt; o (b) un constructor
  de `Photo` desde fila del catálogo que evite la copia campo a campo. Si
  `CatalogPhoto` tiene consumidores fuera de la UI, indica la opción (b).
  Los tests que cubren `library.py` y la carga de la UI deben seguir en
  verde sin cambios de comportamiento.

- [ ] **T1.5 — Precalcular el documento de búsqueda.** (`ui/presentation.py`,
  `ui/window.py`.) Hoy `photo_search_text(photo)` se invoca por foto en cada
  pulsación de tecla. Introduce un índice precalculado — p. ej.
  `build_search_index(photos) -> dict[key, str]` en `presentation.py`
  (puro, sin Qt), construido una vez al cargar (y revalidado al cambiar
  tags/favoritos) — y que `_apply_filter` lo consulte. El texto del
  documento debe seguir siendo idéntico carácter a carácter (tests de
  `tests/test_ui_search.py` sin cambios de expectativas, solo de mecanismo
  si hace falta).

- [ ] **T1.6 — Reducir los ordenamientos redundantes.** (`ui/presentation.py`,
  `sorted_photos`.) Evalúa las tres pasadas de ordenación: si la clave
  elegida puede computarse una vez por foto (con `casefold` ya aplicado) y
  ordenar una única vez, hazlo. Conserva el resultado exacto (los tests de
  `tests/test_presentation.py` fijan el orden). Si la mejora medible es
  marginal frente a T1.2/T1.4, registra la decisión en una nota y deja la
  simplificación solo si no degrada claridad.

- [ ] **T1.7 — Verificación y cierre del paso 1.** Suite completa en verde
  (`uv run pytest`, `uv run ruff check .`); re-ejecuta el benchmark de T1.1
  en las mismas condiciones y anota las cifras antes/después en este
  fichero. Smoke manual: abrir la biblioteca real y comprobar que
  rejilla, búsqueda, grupos, favoritos y degradación por ubicación no
  disponible funcionan igual que antes.

**Criterio de fin del paso 1:** ninguna syscall de `stat` por foto en el
arranque, una sola conexión solo-lectura, 25k objetos construidos una única
vez, búsqueda con índice precalculado y una mejora medible y anotada del
tiempo de `load_photos` + `_populate_grid`.

---

# Paso 2 — Arranque progresivo: ventana primero, datos después

Objetivo: aunque el trabajo total baje (paso 1), mientras sea síncrono el
usuario ve un proceso congelado. Aquí la ventana aparece de inmediato, la
carga corre en un hilo worker y la rejilla se popular por lotes.

- [ ] **T2.1 — Decidir y documentar el mecanismo de concurrencia.** Un ADR
  corto (o una enmienda al ADR 0003 con nota de origen) que fije: hilo
  worker (`QThread` + objeto worker, sin `QThreadPool` global), comunicación
  por señales (lista de `Photo` lista → señal al hilo de UI), propiedad del
  catálogo durante la carga (solo-lectura, sin lock de escritor: es el
  modelo actual del visor) y qué pasa si la carga falla o el usuario cierra
  la ventana a mitad (worker cancelable o ignorado, sin escrituras
  pendientes). Sin Qt en `core/`/`storage/`: el worker vive en `ui/`.

- [ ] **T2.2 — Ventana inmediata con estado de carga.** (`ui/app.py`,
  `ui/window.py`.) `main()` crea la `MainWindow` vacía y la muestra antes de
  cargar nada. Durante la carga, la rejilla muestra un estado de carga
  (texto u overlay discreto conforme a `docs/diseno-ui.md`; nada de
  animaciones llamativas). La barra de estado puede reutilizar el patrón
  existente. Si no hay catálogo o hay error fatal, el camino de aviso y
  cierre actual se conserva (mismo mensaje, quizá ahora tras el show:
  decide y documenta la nota).

- [ ] **T2.3 — Carga del catálogo en el worker.** (`ui/app.py`,
  `ui/window.py`.) Mover `load_photos` (+ tags, ubicaciones y disponibilidad,
  ya unificadas por T1.3) al worker. La señal entrega la lista de `Photo` al
  hilo de UI. Ningún objeto Qt cruz el hilo (solo datos); los iconos
  quedan, como hoy, en el servicio de miniaturas del hilo de UI.

- [ ] **T2.4 — Populate de la rejilla por lotes.** (`ui/window.py`,
  `_populate_grid`.) Sustituir la inserción monolítica por lotes de ~500
  ítems procesados con un `QTimer` de disparo cero (o patrón equivalente)
  para que el hilo de UI respire entre lotes: tras el primer lote la rejilla
  ya es visible y navegable; los siguientes se insertan en fondo. Mantén
  `self._grid_items` coherente durante y tras la carga (búsqueda y
  favoritos deben funcionar incluso a medio populate o, como mínimo,
  esperarse a que termine: decide y documenta en una nota). El cálculo de
  orden y grupos se hace una vez antes del primer lote.

- [ ] **T2.5 — Miniaturas y visibilidad durante el populate.** El mecanismo
  actual (viewport + debounce + cola reemplazable) ya es correcto: verifica
  solo que no se dispare contra la rejilla a medio llenar (p. ej. ignorar
  peticiones de ítems aún no insertados) y que al terminar el populate la
  primera tanda de miniaturas del viewport sale sin jitter. No cambies
  `ui/thumbnails.py` salvo que la verificación lo exija.

- [ ] **T2.6 — Cancelación y cierre limpio.** Si el usuario cierra la ventana
  durante la carga o el populate: el worker se detiene (o su resultado se
  descarta), no quedan timers de populate vivos y la app sale sin
  traceback. Tests con `QT_QPA_PLATFORM=offscreen` del ciclo completo:
  ventana vacía → señal → populate por lotes → cierre a mitad sin errores.

- [ ] **T2.7 — Verificación y cierre del paso 2.** Suite completa en verde;
  re-ejecuta el benchmark de T1.1 midiendo ahora el **time-to-first-pixel**
  (ventana visible) y el time-to-navigable (primer lote navegable) además
  del time-to-complete. Anota las cifras. Smoke manual en la biblioteca
  real: arranque, scroll durante la carga (si procede), búsqueda a medio
  populate, favoritos, y cierre a mitad de carga.

**Criterio de fin del paso 2:** con 25k fotos, la ventana es visible en
cuestiones de décimas y la rejilla navegable en ~1 s, medido con el
benchmark; el comportamiento final (rejilla completa, búsqueda, grupos,
favoritos, degradación) es indistinguible del actual.

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
