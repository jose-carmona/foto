# Informe — Propuesta concreta para minimizar el tiempo hasta «operativo»

> Este documento es una propuesta de trabajo, no un registro de tareas
> ejecutadas. Parte de las mediciones reales del árbol actual (2026-11-26) y
> propone cambios concretos, por archivo y función, con impacto estimado y
> criterios de aceptación. Se escribe en `prompts/` para acompañar a
> [`rendimiento-arranque-ui-25k.md`](rendimiento-arranque-ui-25k.md), que
> documenta las optimizaciones ya entregadas (T1.1–T2.10) y el ADR
> [0011](../docs/adr/0011-arranque-progresivo.md).

---

## 0. Misión

Hoy la ventana aparece rápido (~0,1 s) pero **no es operativa hasta ~3,6 s**
(primer lote navegable) y **no termina de poblarse hasta ~5,3 s** con 25 000
fotos. La acción anterior de arrancar con el filtro `fav` ya aplicado no ha
reducido ese tiempo: las cifras de abajo muestran que es incluso unos
milisegundos *más lenta* que poblar sin filtro.

**Objetivo propuesto:** foto navegable con las favoritas en **≤ 1 s desde el
lanzamiento del proceso**, y la biblioteca completa disponible en segundo
plano sin bloquear la interacción. En la biblioteca real del dueño (~29 500
fotos, **4 favoritas**) el ahorro es mucho mayor que en el banco sintético
(~1 250 favoritas de 25 000).

La palanca decisiva no está en la UI: está en **no cargar ni materializar lo
que no se va a mostrar**. El filtro `fav` se aplica hoy *después* de leer las
29 500 filas, construir los objetos y crear un `QListWidgetItem` por foto.

---

## 1. Línea base medida

Banco reproducible (`scripts/bench_arranque.py`, 25 000 fotos, 3 repeticiones,
`QT_QPA_PLATFORM=offscreen`):

```sh
QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --fotos 25000 --repeticiones 3
```

| Fase | Mínimo | Mediana |
|---|---:|---:|
| `load_photos` (catálogo → objetos) | 2914,3 ms | 2970,2 ms |
| `_populate_grid` | 1157,7 ms | 1214,8 ms |
| Primer píxel | 97,7 ms | 101,3 ms |
| Primer lote navegable | 3479,0 ms | 3648,8 ms |
| Populate completo | 5131,8 ms | 5260,2 ms |

Comprobación con la biblioteca real: **~29 567 fotos, 4 favoritas, arranque
~5,6 s** en offscreen.

Coste de arranque del proceso (3 repeticiones): `python -c pass` ≈ 0,03 s;
`uv run python -c pass` ≈ 0,07 s; **`.venv/bin/python -c "import foto.ui.app"`
≈ 0,50 s**. `-X importtime` atribuye ~423 ms acumulados a la importación:
`PySide6.QtCore` ~136 ms, `foto.core.library` ~93 ms,
`foto.storage.catalog` ~80 ms, `foto.ui.grid` ~37 ms.

### Desglose de la carga (`cProfile` sobre `load_from_catalog`)

5,4 s acumulados para 25 000 fotos. Los costes dominantes:

| Función | Coste | Causa |
|---|---:|---|
| `library.py:187 load_from_catalog` (propio) | 1,21 s | bucle de materialización por foto |
| `pathlib` (`parent`, `drive`, `with_segments`, `__str__`) | ~0,88 s | resolución de rutas por foto |
| `_format_taken` → `date.strftime` | ~0,81 s | 25 000 llamadas (25 000 × `strftime`) |
| `models.py:140 CatalogPhoto.from_catalog` | ~0,42 s | segunda materialización campo a campo |
| `catalog.py:1565 list_present_files` | 0,31 s | `fetchall()` del JOIN de 25 000 filas × 28 columnas |
| `catalog.tag_assignments` | 0,012 s | barato; ya trae todas las etiquetas |

### Desglose del populate y del filtro (medido con instrumentación temporal)

| Operación | Tiempo | Detalle |
|---|---:|---|
| Populate sin filtro (25 000 ítems) | 1174,6 ms | 25 005 filas en la rejilla |
| Populate con `:tag fav` (25 000 ítems) | 1277,2 ms | 25 005 filas; solo 1 250 visibles |
| `_apply_filter('')` final sobre 25 000 ítems | 148,9 ms | pasada extra de normalización |
| **Populate solo favoritas (1 250 ítems)** | **76,0 ms** | lo que realmente haría falta |

### Construcción de la ventana vacía

| Operación | Primera llamada | Siguientes |
|---|---:|---:|
| `apply_theme` | 433,1 ms | 0,2 ms |
| `PhotoWindow(...)` | 219,2 ms | ~95 ms |

Dentro de la primera ventana: `HelpOverlay.__init__` ~91 ms, de los cuales
`render_help`/`setHtml` ~30 ms y `_apply_line_height` ~47 ms (143 ×
`mergeBlockFormat`). `QFontInfo(app.font()).family()` cuesta ~197 ms en la
primera resolución de fuentes (incluso en cocoa, donde la familia ya es
`.AppleSystemUIFont`); Qt además avisa de la familia inexistente «Sans
Serif» (~182 ms de calentamiento de alias).

### Prototipos de medición (sin tocar el repositorio)

| Variante | Medición |
|---|---:|
| `load_from_catalog` actual | 2896 / 2965 ms |
| Materialización optimizada (sin `strftime`, rutas cacheadas, una sola construcción) | 1107 / 1105 ms |
| Optimizada pero filtrando en Python tras `fetchall()` | 410 / 416 ms (1 250 fotos) |
| **Consulta SQL con `EXISTS` por tag de sistema** | **61 ms (1 250 filas)** |
| Populate de solo favoritas | 76 ms |

---

## 2. Diagnóstico: por qué el arranque con `fav` no sirve

La cadena actual es:

1. `LibraryLoadWorker.load()` → `load_library()` → `_photos_from()` →
   `load_from_catalog()` lee **todas** las filas presentes y materializa
   **todas** las fotos (2,9–3,0 s).
2. `MainWindow._on_library_loaded()` comprueba si hay favoritas y decide
   `initial_tag_filter = FAVORITE_TAG` (`src/foto/ui/app.py:548-559`).
3. `set_library()` → `set_photos()` reconstruye el índice de búsqueda de
   **todas** las fotos (~234 ms) y `_populate_grid()` ordena **todas**
   (~80 ms) e inserta **un `QListWidgetItem` por foto** (~1,2 s).
4. Cada ítem se crea con `item.setHidden(True)` si no es favorita, pero el
   ítem **existe**: se paga `QListWidgetItem()`, 5 × `setData`, `setIcon`,
   `setSizeHint`, `addItem` y la entrada en `_grid_items` igualmente.
5. `_finish_populate()` hace una pasada final `_apply_filter()` sobre todas
   las filas (~149 ms).

Es decir: el filtro `fav` **solo ahorra pintado y peticiones de miniaturas**,
que no dominan el coste. Poblar con el filtro costó 1277 ms frente a 1175 ms
sin filtro: el `setHidden` adicional y la pasada final compensan con creces
el ahorro. En la biblioteca real el panorama es peor: de ~29 500 fotos solo
4 son favoritas, por lo que se crean ~29 500 ítems para mostrar 4.

**Conclusión:** el filtro tiene que bajar a la consulta SQL y a la
materialización, antes de que existan los objetos y los ítems. Todo lo demás
es trabajo ya realizado que no se puede recuperar después.

---

## 3. Propuesta concreta por fases

### Fase A — Cargar solo favoritas en SQL y poblar solo lo visible (máximo impacto)

**A.1 — Consulta filtrada en el catálogo.**
En `src/foto/storage/catalog.py`, junto a `list_present_files()` (línea
1565), añadir un parámetro opcional `system_tag: str | None = None` (o una
función nueva `list_present_files_with_system_tag()` para no cambiar la firma
existente). La cláusula `WHERE` actual (`f.status = 'present' AND
loc.state = 'active'`) se amplía con:

```sql
AND EXISTS (
    SELECT 1
      FROM photo_tags pt
      JOIN tags t ON t.id = pt.tag_id
     WHERE pt.photo_hash = p.hash
       AND pt.status IN ('confirmed', 'suggested')
       AND t.kind = 'system'
       AND t.name_normalized = :system_tag
)
```

El índice `photo_tags_photo_idx` ya existe (línea 294), por lo que la
consulta medida cuesta ~61 ms con 25 000 fotos. Añadir un índice
`tags(kind, name_normalized)` solo si `EXPLAIN QUERY PLAN` lo pide; con el
índice actual no hace falta. Añadir también `count_system_tag()` (un
`SELECT 1 … LIMIT 1`) para decidir en ~1 ms si hay favoritas, sin leer las
25 000 filas.

La regla `kind = 'system' AND status IN ('confirmed','suggested')` es
exactamente la de `CatalogPhoto.favorite` (`core/library.py:71-79`) y la de
`Photo.favorite` en la UI. No se inventa una semántica nueva.

**A.2 — Filtrar antes de materializar.**
En `src/foto/core/library.py`, `load_from_catalog()` acepta
`system_tag: str | None = None`. Después de `list_present_files()` y
`tag_assignments()`, y **antes** del bucle que construye los objetos,
recortar el diccionario agrupado por hash con la misma regla de favorito.
`tag_assignments()` ya cuesta 12 ms y trae todo; no se necesita otra
consulta. El `factory` inyectado (T1.4) se respeta sin cambios.

**A.3 — Dos fases en el worker.**
En `src/foto/ui/app.py`:

- `load_library()` gana `initial_system_tag: str | None = None` y lo reenvía
  a `_photos_from()`/`load_from_catalog()`.
- `LibraryLoadWorker` emite `loaded` con la fase 1 (solo favoritas) y, tras
  el primer `processEvents`, emite `library_complete` con la fase 2 (todo) —
  o se usa un segundo worker; se prefiere el mismo hilo con dos señales para
  no duplicar la apertura de la BD.
- `MainWindow._on_library_loaded()` decide la fase 1 con
  `count_system_tag()`; mantiene la conexión RW de favoritos y fija
  `initial_tag_filter=FAVORITE_TAG` como hoy.
- Nuevo `MainWindow._on_library_complete()` funde las fotos restantes en
  `self.photos` y las añade a la rejilla **por lotes ya existentes**, sin
  repoblar desde cero (ver A.4). Mientras tanto,
  `set_library_loading(True)`/`status.set_pinned("cargando el resto de la
  biblioteca…")` informan del estado.

**A.4 — No insertar ítems ocultos.**
En `src/foto/ui/window.py`, `set_photos()` recibe la lista ya filtrada de la
fase 1, de modo que `_populate_grid()` crea solo los ítems favoritos. Para la
fase 2 se añade `PhotoWindow.append_photos(new_photos)`: reutiliza
`_populate_range` con un estado extendido —no llama a `grid.clear()` ni
reinicia `_visible_keys_cache`— y cada ítem nace con la visibilidad correcta
según el filtro vigente en ese momento. Los recuentos pasan de «4 de 4» a
«4 de 29 567» al llegar la fase 2, sin perder la selección actual.

**A.5 — Comunicación al usuario.**
Con solo favoritas cargadas, `/` no encuentra el resto y `0`/`$` recorren 4
fotos. La barra de estado indicará «4 de 29 567 · cargando…» y, al terminar,
«4 de 29 567 · etiqueta: fav». Se mantiene el filtro activo: quitarlo con
`:tag fav` (o el comando que corresponda) revela la biblioteca completa sin
recargar nada, porque la fase 2 ya está en memoria.

**Impacto esperado (fase A sola):** de ~3,6 s a ~0,3–0,5 s hasta la primera
foto navegable en el banco sintético, y de ~5,6 s a ~0,4–0,6 s en la
biblioteca real (donde solo se materializan 4 fotos). La carga completa pasa
a segundo plano y deja de bloquear el primer uso.

---

### Fase B — Sacar del hilo de UI el trabajo restante

**B.1 — Índice y orden en el worker.**
`build_search_index()` y `sorted_photos()` (`ui/presentation.py`) se ejecutan
hoy en el hilo de UI: `set_photos()` (línea 903) reconstruye el índice (~234
ms) y `_populate_grid()` (línea 658) ordena (~80 ms). El worker debe
entregar `search_index` y la lista ordenada ya calculados; `PhotoWindow`
acepta ambos como parámetros opcionales y solo los recalcula si no vienen
(confirmación que hardcoded). Ahorro directo: ~310 ms en 25k, ~12 ms en la
fase de favoritas.

**B.2 — `strftime` fuera del camino caliente.**
`_format_taken()` (`core/library.py:141-149`) llama a
`datetime.fromisoformat(...).strftime(...)` 25 000 veces (~810 ms). Los
valores del catálogo ya son ISO; cuando la cadena tiene forma
`AAAA-MM-DDTHH:MM…` basta con `captured_at[:16].replace("T", " ")`, con
`strftime` solo como fallback para formatos raros. Es el cambio de una línea
con mayor retorno en la fase 2 (de ~810 ms a ~10 ms).

**B.3 — Resolución de rutas cacheada.**
El ~0,88 s de `pathlib` se reparte entre `Path(...).resolve()` por foto y
`parent`/`name` de `PurePath`. El prototipo ya mostró que cachear
`resolved_parents` por `(location_path, folder)` y dividir `rel_path` con
`rfind("/")` reduce la carga completa de 2896 ms a 1107 ms. Aplicar el mismo
patrón en `load_from_catalog()` sin cambiar el resultado (mismo
`canonical_rel_path`, mismo `available`).

**B.4 — No repetir la pasada final del filtro.**
`_finish_populate()` (línea 765) llama siempre a `_apply_filter()` (~149 ms
en 25k). Cada lote ya nace con la visibilidad correcta y mantiene
`_visible_keys_cache`, `_visible_positions_by_key` y `grid.append_visible_rows()`
al día. Basta un flag `_filter_changed_during_populate`: si el filtro no ha
cambiado entre lotes, se omite la pasada y solo se actualizan recuentos; si
ha cambiado, se ejecuta como hoy. Ahorro: ~149 ms por populate completo.

**B.5 — Diferir lo que no se ve.**
- `theme._apply_system_font()` (`ui/theme.py:32`): en cocoa la familia ya
  resuelve a sí misma; saltar `QFontInfo` cuando
  `QGuiApplication.platformName() == "cocoa"` evita ~197 ms y el aviso de
  «Sans Serif». En plataformas sin tema se mantiene la corrección (T2.8).
- `HelpOverlay` y `LocationsOverlay` se construyen en `PhotoWindow.__init__`
  (líneas 512-520) aunque arrancan ocultos y el panel de ayuda ni siquiera
  está visible (`initial_panel_visible=False`). Crearlos bajo demanda al
  primer `?`/`L` y hacer `retheme()` solo si existen. Ahorro: ~91 ms la
  primera vez, cero en las siguientes.
- `update_header_widths()` ya tiene guardia de ancho; se mantiene, pero
  guardar las filas de cabecera en una lista aparte evita el `item(row)` por
  fila cuando el ancho sí cambia (beneficio marginal, solo en resize).

**Impacto esperado (fase B):** ~0,5 s menos en la fase completa y ~0,3 s
menos en el arranque en frío (fuentes + overlays + importación diferida de
módulos de UI). En la fase de favoritas el efecto es pequeño porque ya hay
pocos objetos, pero reduce el tiempo hasta la primera ventana.

---

### Fase C — Condicionada: modelo virtualizado con paginación SQL

Solo si tras A y B el populate completo sigue siendo el cuello de botella, o
si la biblioteca crece más allá de ~100 000 fotos. Alcance (sin ejecutar
ahora, como en T3.1 del prompt anterior):

- Migrar `PhotoGrid`/`QListWidget` a `QListView` + `QAbstractItemModel` con
  `setUniformItemSizes(True)` y `data(Qt.DecorationRole)` para miniaturas.
- El modelo consulta la BD por páginas (keyset por `photo_hash` o
  `LIMIT/OFFSET` sobre el orden canónico) en lugar de materializar todo;
  `rowCount()` crece a medida que se cargan páginas.
- La visibilidad por filtro pasa a ser responsabilidad del modelo: `:tag
  fav` se convierte en una cláusula SQL, no en `setHidden`.
- Afecta a `ui/grid.py`, `_populate_grid`, `_grid_items`, cabeceras de grupo,
  búsqueda, navegación y tests de UI. Es el cambio más caro y **requiere
  autorización explícita**; la fase A ya elimina el motivo principal para
  hacerlo.

---

## 4. Impacto agregado estimado

| Métrica | Hoy (25k) | Tras A | Tras A+B |
|---|---:|---:|---:|
| Primer píxel | 0,10 s | 0,10 s | ≤ 0,10 s |
| Primera foto navegable | 3,65 s | 0,3–0,5 s | **0,2–0,35 s** |
| Populate completo (en fondo) | 5,26 s | ~1,2 s | ~0,7–0,9 s |
| Biblioteca real (~29,5k, 4 favs) | ~5,6 s | ~0,4–0,6 s | ~0,3–0,5 s |

El suelo de ~0,4–0,5 s es la importación de módulos (~423 ms), que no se
puede bajar sin diferir imports o cambiar el empaquetado; por eso el objetivo
se mide desde el lanzamiento del proceso y no solo desde `show()`.

---

## 5. Criterios de aceptación

1. `scripts/bench_arranque.py` gana un modo `--favoritas-primero` que mide la
   fase 1 por separado; la mediana de «primera foto navegable» baja de
   3,6 s a **< 1,0 s** en 25 000 fotos y a **< 0,8 s** con la biblioteca real.
2. El número de `QListWidgetItem` creados en el arranque es igual al número
   de favoritas (4 en la biblioteca real), no al de fotos totales. Se
   comprueba con un contador en el bench.
3. La fase 2 carga el resto sin `grid.clear()` y sin perder selección,
   filtro ni posición de scroll; `status.set_counts()` pasa de «4 de 4» a
   «4 de 29 567».
4. `:tag fav`, `/`, `0`/`$` y el visor funcionan igual a medio populate y
   tras la fase 2; todos los tests existentes de
   `tests/test_ui_progressive.py`, `test_ui_search.py`, `test_ui_tags.py` y
   `test_ui_help.py` siguen en verde.
5. `uv run pytest` y `uv run ruff check .` completos, sin dependencias
   nuevas y sin tocar originales, ADRs ni el esquema del catálogo (salvo un
   índice nuevo si `EXPLAIN` lo justifica).
6. No hay regresión en el apagado: `aboutToQuit` y `closeEvent` cancelan
   también el worker de la fase 2 (A.3).

---

## 6. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| El usuario no ve fotos no favoritas al arrancar y cree que faltan | Estado visible «cargando el resto…» y recuento «4 de 29 567» desde el primer momento; quitar el filtro revela la biblioteca ya cargada. |
| Marcar/desmarcar favoritos antes de que llegue la fase 2 | La conexión RW ya existe en UI; si el hash no está en memoria, se consulta esa sola foto por hash (índice `files_photo_hash_idx`) o se encola el toggle hasta la fase 2 (~1 s). |
| Cambiar la semántica de «favorita» al filtrar en SQL | Usar literalmente la regla de `CatalogPhoto.favorite`/`Photo.favorite`; test de paridad entre ambas sobre un catálogo con `confirmed`, `suggested`, `rejected` y tags libres. |
| La conexión de solo lectura se abra dos veces en dos fases | Un único worker con dos emisiones (A.3), cerrando la BD en `finally` como hoy. |
| `setHidden` ya no se usa para las no favoritas y se rompe la navegación | Los lotes siguen actualizando `append_visible_rows` y las cachés; test explícito de `0`/`$` a medio populate con el filtro activo. |
| `QFontInfo` diferido rompa la fuente en plataformas sin tema | Solo se omite en cocoa; en `offscreen` y Linux se mantiene T2.8 y su test. |
| La fase 2 cancele el populate de una búsqueda viva | `_populate_range` lee el filtro de cada lote; si el usuario escribe entre lotes, se aplica a los nuevos ítems exactamente como hoy. |

---

## 7. Orden de ejecución recomendado

1. **A.1 + A.2** con tests de paridad de favoritos y de consulta (sin tocar
   UI). Ya reduce 2,9 s → ~0,15 s en la fase 1.
2. **A.3 + A.4 + A.5**, con el bench `--favoritas-primero`. Aquí se cumple el
   objetivo principal.
3. **B.1–B.4** (trabajo del worker), y **B.5** como quick wins finales.
4. Medir de nuevo y solo entonces decidir sobre la **Fase C**.

Sin commits ni pushes sin autorización expresa; sin nuevas dependencias;
sin modificar los originales de la fototeca del dueño.
