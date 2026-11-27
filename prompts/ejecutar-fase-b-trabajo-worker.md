# Prompt — Ejecutar Fase B: sacar del hilo de UI el trabajo restante del arranque

> Prompt autocontenido y ejecutable por un agente. Cubre **solo** las tareas
> **B.1–B.5** del informe
> [`mejora-arranque-operativo.md`](mejora-arranque-operativo.md). Parte de que
> **A.1/A.2 y A.3/A.4/A.5 están cerradas**: `list_present_files(system_tag=…)`,
> `has_system_tag(…)`, `count_present_photos()` y
> `load_from_catalog(…, system_tag=…)` existen con tests de paridad, y el
> arranque ya entrega dos fases desde un único worker con solo favoritas en la
> rejilla. Detalle y cifras en
> [`ejecutar-a1-a2-favoritas-sql.md`](ejecutar-a1-a2-favoritas-sql.md) § 6 y
> [`ejecutar-a3-a4-a5-arranque-dos-fases.md`](ejecutar-a3-a4-a5-arranque-dos-fases.md)
> § 6. **No reescribas ese contrato:** este prompt lo consume y lo extiende.
>
> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada una:
> márcala `[x]` con la fecha y, si hubo desviaciones del enunciado, añade debajo
> una línea `> Nota (fecha): …`. No reordenes ni borres tareas; si aparece una
> pendiente nueva, añádela al final con el mismo formato. Una tarea solo está
> cerrada con sus tests en verde y con `uv run pytest` + `uv run ruff check .`
> completos.

## Resumen de estado

| Tarea | Alcance | Estado |
|---|---|---|
| S1 | B.1 — Índice y orden precalculados en el worker | Cerrada 2026-11-27 |
| S2 | B.2 — `_format_taken` sin `strftime` en el camino caliente | Cerrada 2026-11-27 |
| S3 | B.3 — Resolución de rutas cacheada sin cambiar el resultado | Cerrada 2026-11-27 |
| S4 | B.4 — Omitir la pasada final del filtro si no cambió | Cerrada 2026-11-27 |
| S5 | B.5a — `QFontInfo` omitido en cocoa | Cerrada 2026-11-27 |
| S6 | B.5b — Overlays de ayuda y ubicaciones bajo demanda | Cerrada 2026-11-27 |
| S7 | Verificación, bench `--fase-b` y reporte | Cerrada 2026-11-27 |

---

## 0. Misión

Tras las fases A la ventana ya es navegable con las favoritas en **~0,52 s**
(mediana medida, frente a 3,65 s de línea base) y la fase 2 corre en segundo
plano. Queda trabajo que todavía se paga en el hilo de UI o en el camino
caliente de la carga completa:

| Operación | Coste medido (25k) | Dónde ocurre hoy |
|---|---:|---|
| `build_search_index()` en `set_photos()` (T1.5) | ~234 ms | hilo de UI, fase 1 y de nuevo en el append |
| `sorted_photos()` en `_populate_grid()` / `append_photos()` | ~80 ms cada una | hilo de UI |
| `_format_taken()` → `datetime.fromisoformat` + `strftime` | ~810 ms (25 000 ×) | worker, por foto |
| `pathlib` (`parent`, `suffix`, `__str__`, `resolve`) | ~880 ms | worker, por foto |
| `_apply_filter()` final en `_finish_populate()` | ~149 ms | hilo de UI, aunque el filtro no haya cambiado |
| `QFontInfo(app.font())` + overlays ocultos | ~197 ms + ~91 ms | arranque en frío, antes del primer lote |

**Objetivo de este prompt:** que el worker entregue el índice de búsqueda y el
orden ya calculados, que la materialización deje de pagar `strftime` y
`pathlib` por foto, que el populate no repita una pasada de filtro innecesaria
y que el primer píxel no cargue fuentes ni overlays que no se usan. Todo sin
cambiar el comportamiento visible: mismos textos, mismo orden, mismos filtros,
mismos recuentos, mismas teclas.

**Cifras de partida para comparar** (bench `--favoritas-primero`, 25 000 fotos,
3 repeticiones, tras A.3/A.4/A.5): primer píxel 102,2 ms; fase 1 navegable
517,6 ms; populate completo 5 947,4 ms. El informe estimaba «tras A+B»
0,2–0,35 s y 0,7–0,9 s; la medición real de A dejó el populate completo en
~5,9 s (dos materializaciones más el append por lotes). **Compara contra las
cifras medidas, no contra la estimación del informe.**

---

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables: originales intactos, sin Qt en
   `core/`/`storage/`, sin `git commit`/`push` sin permiso, sin dependencias
   nuevas, identificadores en inglés, textos y docstrings en español.
2. `README.md` — alcance, arquitectura y roadmap.
3. `prompts/mejora-arranque-operativo.md` — secciones 1 (desgloses), 3 (Fase B,
   B.1–B.5), 4, 5 y 6.
4. `prompts/ejecutar-a3-a4-a5-arranque-dos-fases.md` (§ 2 y § 6) — contrato de
   señales, `LibraryFirstPhase`/`LibraryLoadPhases` y cifras ya medidas.
5. `docs/adr/0011-arranque-progresivo.md` — worker, señales, cancelación
   cooperativa y lotes. **No se modifica** en este prompt; documenta cualquier
   extensión del contrato en los docstrings del código.
6. Código real, localizando por **nombre de función** (las líneas son una foto
   y pueden desplazarse):
   - `src/foto/ui/presentation.py`: `build_search_index` (~387),
     `sorted_photos` (~459), `DEFAULT_SORT` (~404), `photo_search_text` (~351).
   - `src/foto/ui/app.py`: `_library_snapshot` (~272), `load_library_phases`
     (~366), `LibraryFirstPhase` (~335), `LibraryLoadPhases` (~354),
     `LibraryLoadWorker` (~502, señales en ~527, `load` en ~553),
     `MainWindow._on_library_loaded` (~710), `_on_library_complete` (~757).
   - `src/foto/ui/window.py`: `__init__` (overlays en ~525-533), `retheme`
     (~597), `_layout_content` (~1236), `_toggle_help` (~2249),
     `_toggle_locations` (~2287), `set_library` (~1004), `set_photos` (~1034),
     `_populate_grid` (~641), `append_photos` (~682), `_populate_range` (~806),
     `_finish_populate` (~899), `_apply_filter` (~1793), `_set_tag_filter`
     (~1971), `_restore_canonical_order` (~767).
   - `src/foto/core/library.py`: `_format_taken` (~141), `load_from_catalog`
     (~187, bloque de rutas en ~286-300 y `factory` en ~310).
   - `src/foto/ui/theme.py`: `_apply_system_font` (~30), `apply_theme`.
   - `scripts/bench_arranque.py`: `_arranque_dos_fases` (~276),
     `_favoritas_primero` (~320), `main` (~353).
7. Tests de referencia: `tests/test_ui_progressive.py`,
   `tests/test_ui_two_phase_startup.py`, `tests/test_ui_search.py`,
   `tests/test_ui_tags.py`, `tests/test_ui_help.py`, `tests/test_ui_locations.py`,
   `tests/test_ui_styles.py`, `tests/test_presentation.py`,
   `tests/test_multi_location.py`, `tests/ui_factories.py` (`make_photo`).

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible (`uv run pytest -q` y `uv run ruff check .`). No hagas
`git commit`, `git push` ni cambios de configuración de git sin permiso
explícito.

---

## 2. API que debe quedar al terminar

```python
# ui/presentation.py — módulo puro, sin Qt
@dataclass(frozen=True)
class PreparedPhotos:
    """Índice y orden precalculados de una lista de fotos (Fase B)."""
    search_index: dict[str, str]   # build_search_index(fotos)
    ordered: list[Photo]           # sorted_photos(fotos, DEFAULT_SORT)

def prepare_photos(photos: list[Photo], key: str = DEFAULT_SORT) -> PreparedPhotos: ...

# ui/app.py — extensiones opcionales para no romper los tests existentes
@dataclass(frozen=True)
class LibraryFirstPhase:
    photos: list[Photo]
    tags: list[Tag]
    locations: tuple[LocationStatus, ...]
    total: int
    has_more: bool
    prepared: PreparedPhotos | None = None        # NUEVO

@dataclass(frozen=True)
class LibraryLoadPhases:
    first: LibraryFirstPhase
    remaining: list[Photo]
    remaining_prepared: PreparedPhotos | None = None   # NUEVO

class LibraryLoadWorker(QObject):
    # 6º argumento = prepared de la fase 1
    loaded = Signal(object, object, object, int, bool, object)
    # (fotos restantes, prepared de esas fotos); una sola emisión final
    library_complete = Signal(object, object)
    failed = Signal(str)

# ui/window.py — parámetros nuevos, opcionales; None conserva el camino actual
def set_library(..., prepared: PreparedPhotos | None = None) -> None: ...
def set_photos(..., prepared: PreparedPhotos | None = None) -> None: ...
def append_photos(photos: list[Photo], prepared: PreparedPhotos | None = None) -> None: ...
```

`PreparedPhotos` es el único tipo nuevo compartido. Vive en `presentation.py`
(que ya es sin Qt) para que `app.py` y `window.py` lo importen sin ciclo:
`app.py` ya importa de `presentation`, y `window.py` también. **No lo definas en
`app.py`**: `app.py` importa `window.py`, y el sentido inverso no existe.

`prepared=None` significa «recalcula en la UI como hoy». Es la vía que siguen
la demo, los tests que construyen ventanas directamente y los tests que
simulan el worker con datos mínimos. En el arranque real el worker **siempre**
entrega `prepared`, de modo que la UI no llama a `build_search_index` ni a
`sorted_photos` durante el arranque.

---

## 3. Tareas

### [x] S1 — B.1: índice y orden precalculados en el worker (2026-11-27)

> Nota (2026-11-27): además de entregar `prepared`, `PhotoWindow` evita
> `build_search_index([])` y `sorted_photos([])` cuando se construye vacía en
> el arranque progresivo. Sin esas dos guardias el bench habría contado 1 / 1
> llamadas en la UI antes de que llegara la primera fase; el contrato de
> «cero recálculos en la UI» exige que ni las listas vacías se procesen allí.
> El reordenado canónico de A.3/S7 sigue intacto: es acción del usuario.

1. En `src/foto/ui/presentation.py`, añade `PreparedPhotos` y
   `prepare_photos()` (§ 2) junto a `build_search_index` y `sorted_photos`.
   `prepare_photos` es pura: llama a esas dos funciones y devuelve el par.
   Añade un test en `tests/test_presentation.py` que fije que
   `prepare_photos(ps).search_index == build_search_index(ps)` y
   `.ordered == sorted_photos(ps, DEFAULT_SORT)`, carácter a carácter y en el
   mismo orden.
2. En `load_library_phases()` (`ui/app.py`):
   1. Tras materializar la fase 1 y **antes de llamar a `on_first_phase`**,
      calcula `first.prepared = prepare_photos(first_photos)`. Solo hay
      favoritas (1 250 en el banco, 4 en la biblioteca real): cuesta ~12 ms.
      El callback debe recibir la fase ya preparada; no muevas el callback
      después de la fase 2 (contrato A.3).
   2. Tras materializar `remaining`, calcula
      `remaining_prepared = prepare_photos(remaining)` cuando `has_more` sea
      `True`; si no hay fase 2, déjalo en `None` (la fase 1 ya era la
      biblioteca completa).
   3. No cambies la decisión de `use_fav`, el `total`, la única apertura de
      BD, el cierre en `finally` ni la guardia `should_continue`. Ningún objeto
      Qt, ninguna escritura.
3. Extiende las señales del worker (§ 2) y sus emisiones:
   - `_emit_first(first)` emite `first.prepared` como sexto argumento.
   - `load()` emite `library_complete(phases.remaining,
     phases.remaining_prepared)` **una sola vez** en el `finally`, con un flag
     `emitted` como hoy; en cancelación, fallo o sin fase 2 emite `([], None)`.
   - Conserva los bloques `except` actuales: ninguna excepción escapa del
     hilo y `failed` solo si no se canceló. `library_complete` sigue siendo la
     señal que detiene el hilo; `loaded` no detiene nada.
4. Extiende el pipeline de UI sin cambiar su comportamiento:
   - `MainWindow._on_library_loaded(photos, tags, locations, total, has_more,
     prepared)` reenvía `prepared` a `set_library()`.
   - `set_library(..., prepared=...)` lo reenvía a `set_photos()`.
   - `set_photos(..., prepared=...)`: si `prepared` no es `None`, usa
     `prepared.search_index` en lugar de `build_search_index(photos)` y pasa
     `prepared.ordered` a `_populate_grid()`; si es `None`, hace lo que hoy.
     El resto de `set_photos` (cachés de imágenes, pinned, selección, etc.)
     no cambia.
   - `_populate_grid(photos, *, prepared=None)`: usa `prepared.ordered` si
     viene; si no, `sorted_photos(photos, self._sort_key)` como hoy.
   - `MainWindow._on_library_complete(remaining, prepared)` reenvía
     `prepared` a `append_photos()` (también en el camino de
     `_pending_append` de `_finish_populate`).
   - `append_photos(photos, *, prepared=None)`: usa
     `self._search_index.update(prepared.search_index)` y
     `prepared.ordered` como estado inicial del populate; si no viene,
     mantiene `build_search_index(photos)` + `sorted_photos(...)` actuales.
     Ni `grid.clear()` ni reinicio de cachés.
   - `_restore_canonical_order()` **no cambia**: el reordenado es una acción
     del usuario (quitar `fav` / `:sort`), no del arranque.
5. Actualiza los tests del worker que conectan `loaded` con lambdas de cinco
   parámetros y los que parchean `app_module.load_library_phases`
   (`test_worker_emits_loaded_with_pure_data`, `test_worker_cancel_discards_the_result`,
   `test_worker_reports_*`, `test_worker_thread_stops_after_*`,
   `test_main_window_close_mid_load_cancels_cleanly`, `test_quit_mid_load_*`):
   acepta el sexto argumento y construye los dataclasses con `prepared=None`
   salvo en el test de dos fases. Conserva todas las aserciones previas.
   Añade `test_worker_emits_prepared_data_in_order`: con dos fases, la primera
   señal trae `prepared` no nulo y la segunda `(restantes, prepared_restantes)`
   no nulo.
6. Añade `tests/test_ui_worker_preparation.py` (Qt, offscreen) con:
   - `test_startup_does_not_build_index_or_sort_in_the_ui_thread` — parchea
     `window_module.build_search_index` y `window_module.sorted_photos` para
     contar llamadas mientras corre el arranque completo con 60 fotos y una
     favorita: **0 llamadas** en la UI, mientras los mismos parches en
     `app_module`/`presentation` registran al menos una en el hilo del worker
     (comprueba `QThread.currentThread()` si hace falta distingüirlos).
   - `test_prepared_and_recomputed_views_are_identical` — misma biblioteca,
     una ventana arrancada con `prepared` y otra con `prepared=None`: mismos
     ítems, mismo orden, mismas claves visibles y mismo documento de búsqueda.
   - `test_second_phase_finds_non_favorite_without_rebuilding_index` — tras la
     fase 2, `/` encuentra una foto no favorita y el contador de
     `build_search_index` de la UI sigue en 0.
   - `test_pending_append_carries_prepared_data` — con el fixture
     `progresivo`, la fase 2 llega antes de terminar la fase 1 y el append
     diferido usa el `prepared` entregado (sin recomputar en UI).

> Nota: si el orden de la fase 2 se entrega ya calculado para `remaining`,
> `append_photos` sigue insertando al final («favoritas primero, resto después»)
> y S7 del prompt anterior (reordenado diferido al quitar `fav`) se mantiene
> intacto. No reordines globalmente en el arranque.

### [x] S2 — B.2: `_format_taken` sin `strftime` en el camino caliente (2026-11-27)

> Nota (2026-11-27): el filtro posicional se apoya en el helper privado puro
> `_has_iso_time`, que exige ASCII en los 16 primeros caracteres para conservar
> la paridad con `fromisoformat` (antes había un `_is_ascii_digits` por grupo;
> se unificó en una sola comprobación). Bench local (25 000 × 3): 130,3 ms →
> 28,4 ms mediana (4,6×; mín 27,7 ms). La línea base de esta máquina ya era
> mucho más rápida que los ~810 ms del enunciado, así que no se alcanza el
> ratio ≥20×, aunque la cifra absoluta está en el rango previsto (10–40 ms).

1. En `src/foto/core/library.py`, reescribe `_format_taken()` con un camino
   rápido para cadenas ISO con forma `AAAA-MM-DDTHH:MM…` y `strftime` solo como
   *fallback*:
   - Si `captured_at` es falsy, devuelve `""` como hoy.
   - Comprueba la forma con índices (sin regex nuevo): longitud ≥ 16,
     `captured_at[4] == '-'`, `[7] == '-'`, `[10] == 'T'` (o `' '`),
     `[13] == ':'` y los dígitos en `[0:4]`, `[5:7]`, `[8:10]`, `[11:13]`,
     `[14:16]`. Entonces devuelve `captured_at[:16].replace("T", " ")`.
   - En cualquier otro caso (fecha sin hora, ISO raro, texto inválido),
     mantiene exactamente el `datetime.fromisoformat(...).strftime("%Y-%m-%d
     %H:%M")` actual y su `except (ValueError, AttributeError)`.
   - Docstring en español explicando que el catálogo ya guarda ISO y que el
     formato deseado es los primeros 16 caracteres con el separador cambiado;
     `strftime` queda para los casos raros.
2. **No toques** el `factory`, `system_tag`, `should_continue`, el orden ni
   ningún otro formateador de `load_from_catalog`. La salida de `Photo.taken`
   debe ser idéntica carácter a carácter.
3. Crea `tests/test_library_taken_format.py` (sin Qt) con una tabla
   parametrizada que compare la nueva función contra una implementación de
   referencia local (la antigua) para: `2026-11-26T12:34:56+00:00`,
   `2026-11-26T12:34`, `2026-11-26 12:34:56.123456`, `2026-11-26T12:34:56Z`,
   `2026-11-26`, `2026-1-2T3:04`, `no-es-fecha`, `""` y `None`. Añade un test
   que parchee `datetime.fromisoformat` para lanzar si se llama y verifique
   que las cadenas ISO con hora **no** lo invocan (camino rápido).
4. Verificación de rendimiento **fuera del repositorio**: 25 000 llamadas,
   3 repeticiones, comparando la implementación antigua y la nueva; esperado
   ≥ 20× más rápida (de ~810 ms a ~10–40 ms). Adjunta las cifras al reporte.

### [x] S3 — B.3: resolución de rutas cacheada sin cambiar el resultado (2026-11-27)

> Nota (2026-11-27): se extrae el helper privado puro `_suffix_from_name`, que
> delega en `Path(name).suffix` para conservar la semántica exacta (Python 3.15
> cambió el comportamiento con puntos finales). `resolved_parents` usa claves
> `(str(raíz), folder)` y `location_roots` cachea el `Path` por raíz. Tests en
> `tests/test_library_path_cache.py`. Bench de 25 000 fotos ejecutado después
> fuera del repo (comparando con la revisión anterior vía `git show`): 3 015,5 ms
> → 1 514,3 ms mediana (1,99×; mín 1 498,4 ms), con paridad en 25 000 fotos.
> La mediana queda 14 ms por encima del objetivo de 1 500 ms del informe; el
> mínimo ya lo cumple. No se eliminaron `str()` sobre campos SQLite para no
> desviarse del contrato literal del enunciado.

1. En `load_from_catalog()` (`core/library.py`), sustituye la construcción de
   `Path(canonical["rel_path"])` por foto por una división de cadena una sola
   vez por foto, reutilizando el patrón de T1.8:
   - `rel = str(canonical["rel_path"])`; separa padre y hoja con
     `rpartition("/")` (las rutas del catálogo son POSIX). `folder` es el padre
     o `"."` cuando no hay, con la misma normalización `replace(os.sep, "/")`
     de hoy; `name` es la hoja.
   - Cachea `Path(location_path)` por raíz distinta y mantén
     `resolved_parents` indexado por `(location_path, padre)`; una resolución
     por directorio distinto, nunca por foto. `full_path = resolved_parent /
     name`.
   - Para `extension`, conserva la semántica de `Path(name).suffix` cuando
     `canonical["extension"]` esté vacío (hoja sin punto → `""`; `.oculto` →
     `""`; `a.b.JPG` → `.JPG`). Si extraes un helper privado, hazlo puro, con
     type hints y docstring en español, y cúbrelo con tests.
   - `canonical_rel_path`, `available`, `other_locations`, `states`, la elección
     por `tier`, el `factory` y la guardia `should_continue` quedan **igual**.
     La salvedad documentada de T1.8 sobre enlaces simbólicos en la hoja sigue
     vigente y se mantiene en el docstring.
2. Añade tests de paridad en `tests/test_multi_location.py` (o un fichero nuevo
   `tests/test_library_path_cache.py` sin Qt): misma `path`, `folder`,
   `canonical_rel_path`, `extension`/`fmt` y `other_locations` que antes para
   rutas simple, anidada, multinivel con puntos, hoja oculta, sin extensión y
   con dos ubicaciones `fast`/`archive` (una de ellas no disponible). Espía
   `Path.resolve` para fijar que se llama una vez por directorio distinto, no
   una por foto.
3. Verificación fuera del repo: reutiliza
   `scripts.bench_arranque._construir_biblioteca` con 25 000 fotos y compara
   `load_from_catalog(catalog)` antes/después (línea base de A: ~2 686 ms;
   prototipo del informe: ~1 107 ms). Adjunta cifras al reporte.

### [x] S4 — B.4: no repetir la pasada final del filtro (2026-11-27)

> Nota (2026-11-27): el instantáneo se toma al abrir cada sesión de populate
> (incluido el append de la fase 2) y el flag lo marcan `_apply_filter` y
> `_set_tag_filter`. `_restore_canonical_order` abre una sesión nueva con el
> filtro ya vigente, así que reinicia el instantáneo y no dispara una pasada
> innecesaria. El bench `--fase-b` registró **0** llamadas a `_apply_filter`
> durante el arranque.

1. En `src/foto/ui/window.py`, añade el estado
   `_filter_changed_during_populate: bool` y el instantáneo del filtro
   efectivo `(texto casefoldado, tag)` al iniciar cada sesión de populate
   (`_populate_grid` y `append_photos`). Un helper privado compara el filtro
   vigente con ese instantáneo y marca el flag **solo** si cambió mientras
   `_populate_active` es `True`.
2. Llama a ese helper desde `_apply_filter()` y `_set_tag_filter()` (y desde
   cualquier otro camino que cambie `_filter_text`/`_tag_filter` durante el
   populate). La propia pasada final de `_finish_populate()` usa el mismo
   filtro del instantáneo, así que **no** debe marcar el flag.
3. `_finish_populate()`: si el flag es `False`, omite `self._apply_filter(
   self._filter_text)`; en su lugar solo actualiza recuentos y estado vacío
   (`_update_counts()`, `_update_empty_state()`) y mantiene
   `_schedule_visible_thumbnails()`, el `on_done` y el `_pending_append` de la
   fase 2 exactamente como hoy. Si el flag es `True`, ejecuta la pasada como
   hasta ahora. Reinicia el flag en cada nueva sesión de populate.
4. Tests en `tests/test_ui_progressive.py` (fixture `progresivo`,
   `POPULATE_SYNC_LIMIT=2`, `POPULATE_BATCH_SIZE=2`):
   - `test_finish_populate_skips_filter_pass_when_filter_did_not_change` —
     envuelve `PhotoWindow._apply_filter` para contar llamadas; con filtro
     estable, `_finish_populate` no añade ninguna pasada y
     `_visible_keys_cache`, `_visible_positions_by_key` y las filas visibles
     de `PhotoGrid` siguen correctas.
   - `test_finish_populate_reapplies_filter_when_it_changed_between_batches` —
     cambia `:tag` o escribe en el buscador entre lotes; la pasada final se
     ejecuta y la visibilidad final coincide con la de un recálculo completo.
   - Comprueba además que los recuentos de la barra y la selección son
     idénticos en ambos caminos, y que `0`/`$` recorren las mismas claves.

### [x] S5 — B.5a: `QFontInfo` omitido en cocoa (2026-11-27)

1. En `src/foto/ui/theme.py`, `_apply_system_font()` (T2.8): si
   `QGuiApplication.platformName() == "cocoa"`, sale sin llamar a
   `QFontInfo(app.font())`. La fuente del sistema ya resuelve a sí misma en
   cocoa (`.AppleSystemUIFont`); el guard de T2.8 se mantiene intacto para
   `offscreen` y cualquier plataforma sin tema.
2. No cambies `ui_font`, las paletas ni el QSS. `apply_theme` sigue siendo
   idempotente y no cambia el texto visible ni la familia en cocoa.
3. Tests en `tests/test_ui_styles.py`: parchea `QGuiApplication.platformName`
   con `"cocoa"` y comprueba que `apply_theme` deja `app.font()` igual y no
   invoca `QFontInfo`; con `"offscreen"` (o sin parche) los tres tests de T2.8
   siguen pasando. Añade una nota en el docstring sobre el ahorro medido
   (~197 ms en la primera resolución).

### [x] S6 — B.5b: overlays de ayuda y ubicaciones bajo demanda (2026-11-27)

> Nota (2026-11-27): se omitió el punto 4 (opcional) —caché de filas de
> cabecera para `update_header_widths()`—: es un beneficio marginal solo en
> resize y no se quería tocar `grid.py` sin una medición que lo justificara.
> Las propiedades `help_overlay`/`locations_overlay` siguen siendo la API
> pública; los caminos que solo consultan usan el backing para no construir.

1. En `src/foto/ui/window.py`, deja de construir `HelpOverlay` y
   `LocationsOverlay` en `__init__` (~~líneas 525-533~~). Sustituye los
   atributos por propiedades perezosas con backing `_help_overlay` /
   `_locations_overlay` a `None`, de modo que **cualquier acceso actual**
   (`window.help_overlay`, `window.locations_overlay`) siga funcionando y
   construya el overlay la primera vez:
   - `_ensure_help_overlay()` crea el overlay y lo oculta.
   - `_ensure_locations_overlay()` crea el overlay, llama a
     `set_locations(location_rows(self._locations))` con los datos ya guardados
     y lo oculta.
2. Guarda los datos en `__init__` (`self._locations = tuple(locations)`) y
   elimina la llamada a `self.locations_overlay.set_locations(...)` de
   `set_library()`: la fase 1 solo actualiza `self._locations` y el banner.
   Así el panel no se construye durante el arranque.
3. Haz que los caminos que solo **consultan** los overlays no los creen:
   `retheme()` usa `if self._help_overlay is not None: …retheme()` (igual para
   ubicaciones), y `_layout_content()` comprueba el backing en lugar de la
   propiedad antes de preguntar `isVisible()`. Los caminos que sí los abren
   (`_toggle_help`, `_toggle_locations`) siguen usando la propiedad, que los
   crea en ese momento. Un cambio de tema antes de abrir la ayuda no debe
   construir el overlay: al crearse, ya recibe el tema vigente.
4. Opcional (beneficio marginal, solo en resize): guarda las filas de cabecera
   en una lista aparte en `_populate_range`/`_apply_filter` para evitar
   `grid.item(row)` por fila en `update_header_widths()` cuando el ancho sí
   cambia. **No toques** la guardia de ancho existente ni el QSS. Si lo haces,
   documenta la desviación y añade el test de resize.
5. Tests existentes en `tests/test_ui_help.py` y
   `tests/test_ui_locations.py` siguen en verde sin cambiar expectativas.
   Añade:
   - `test_overlays_are_not_built_during_startup` — tras `MainWindow` con
     datos, `_help_overlay is None` y `_locations_overlay is None`; al pulsar
     `?` se crea solo el de ayuda; al abrir `:locations` se crea solo el de
     ubicaciones con las filas correctas.
   - `test_retheme_before_opening_overlay_does_not_build_it` — `retheme()`
     no crea ninguno; el overlay creado después usa el tema actual.
   - `test_help_and_locations_close_behaviour_is_unchanged` — un solo overlay
     abierto a la vez, blocker y foco como hoy.

### [x] S7 — Verificación, bench `--fase-b` y reporte (2026-11-27)

> Nota (2026-11-27): `--fase-b` envuelve las cuatro funciones solo durante el
> arranque y restaura los parches en `finally`, sin cambiar una línea de los
> modos existentes. No apareció el exit 139 del teardown, así que no hizo falta
> `os._exit(0)` ni añadir scripts al repo. Los benchs de S2/S3 se ejecutaron con
> scripts temporales fuera del repositorio.

1. `scripts/bench_arranque.py` gana el flag `--fase-b` (sin dependencias
   nuevas). Con el flag:
   - Se comporta como `--favoritas-primero` (mismos `_arranque_dos_fases`,
   mismo contador de ítems y mismo veredicto de 1 s) y además envuelve, solo
   durante el arranque y restaurándolos en `finally`:
     - `foto.ui.window.build_search_index` y `foto.ui.window.sorted_photos`
       (contador de llamadas en la UI): esperado **0**;
     - `foto.ui.app.prepare_photos` / `foto.ui.presentation.prepare_photos`
       (contador en el worker): esperado **≥ 1**;
     - `PhotoWindow._apply_filter` (contador y tiempo acumulado): esperado
       tiempo < 20 ms en el arranque porque la pasada final se omite.
   - Imprime las tres líneas nuevas y el veredicto. Sin el flag, la salida de
     los modos existentes no cambia ni una línea.
2. Ejecuta, con la moderación que pide el tamaño del banco:

   ```sh
   QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py \
       --fotos 25000 --repeticiones 3 --favoritas-primero
   QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py \
       --fotos 25000 --repeticiones 3 --fase-b
   uv run pytest -q
   uv run ruff check .
   ```

3. Compara contra la línea base de A (§ 0): objetivo de fase 1 **< 1,0 s** y
   al menos **100 ms más rápida** que 517,6 ms; populate completo al menos
   **0,4 s más rápido** que 5 947,4 ms; primer píxel ≤ 0,10 s en offscreen;
   cero llamadas de índice/orden en la UI. Si el teardown de PySide6/Python
   3.15 da el exit 139 conocido (T2.9), termina el script con `os._exit(0)`
   **fuera del repo** y dilo en el reporte. No añadas scripts nuevos al
   repositorio.
4. Rellena el § 6 con: tests nuevos, suite completa, cifras antes/después de
   cada sub-tarea medida, contadores del bench y desviaciones. **No hagas
   commit ni push.**

---

## 4. Fuera del alcance de este prompt

- A.1/A.2 y A.3/A.4/A.5: no reescribas `list_present_files`,
  `has_system_tag`, `count_present_photos`, `load_from_catalog`,
  `load_library_phases` ni la semántica de las dos fases; solo extiendes su
  contrato con `prepared`.
- Fase C (modelo virtualizado `QListView` + `QAbstractItemModel`, paginación
  SQL): requiere autorización explícita y no se ejecuta aquí.
- Cambios de esquema, migraciones, ADRs, `README.md` ni `docs/diseno-ui.md`.
- Cambios de teclas, comandos `:`, textos visibles o apariencia (salvo el
  ahorro interno de fuentes y la creación diferida de overlays, invisibles al
  usuario).
- Mecanismo de miniaturas (`ui/thumbnails.py`, ADR 0004) y servicio de
  decodificación del visor: no se tocan.
- Nuevas dependencias, commits, pushes o escritura en los originales de la
  fototeca del dueño.

---

## 5. Definición de done

- [x] `PreparedPhotos`/`prepare_photos()` en `presentation.py`, sin Qt, con
       paridad demostrada por test.
- [x] El worker entrega índice y orden ya calculados en ambas fases; la UI los
       usa y **no** recalcula durante el arranque (contador del bench a 0).
- [x] `loaded` y `library_complete` con las firmas nuevas; una sola emisión
       final; hilo detenido y cancelación de ambas fases intactas.
- [ ] `_format_taken` con camino rápido ISO y `strftime` solo de fallback;
       salida idéntica y ≥ 20× más rápido en 25 000 llamadas.
  > Nota (2026-11-27): salida idéntica y camino rápido verificados; el ratio
  > medido en esta máquina es 4,6× (130,3 → 28,4 ms) porque la línea base local
  > ya era 6× más rápida que los ~810 ms del enunciado. La cifra absoluta sí
  > está en el rango previsto (10–40 ms).
- [x] `load_from_catalog` sin `Path` por foto en el camino caliente; mismos
       `canonical_rel_path`, `folder`, `available` y `other_locations`.
- [x] `_finish_populate` omite la pasada final si el filtro no cambió y la
       ejecuta si cambió; cachés, selección y recuentos intactos.
- [x] `QFontInfo` no se llama en cocoa; overlays no se construyen en el
       arranque y se crean bajo demanda con el tema y los datos vigentes.
- [x] Suite completa en verde (`test_ui_progressive.py`,
       `test_ui_two_phase_startup.py`, `test_ui_search.py`, `test_ui_tags.py`,
       `test_ui_help.py`, `test_ui_locations.py`, `test_ui_styles.py` incluidos)
       y `uv run ruff check .` limpio.
- [x] Bench `--fase-b` ejecutado con cifras y veredictos adjuntos al reporte;
       sin commits/pushes y sin archivos nuevos en el repo aparte de los tests
       indicados y los módulos modificados.

---

## 6. Reporte de ejecución (rellenar al terminar)

**Tests nuevos:** 40. `tests/test_presentation.py` (1: paridad de
`prepare_photos` con el índice y el orden), `tests/test_ui_progressive.py` (1:
señales con `prepared`; 3 de B.4: omisión/reaplicación de la pasada final,
parametrizado en búsqueda y `:tag`), `tests/test_ui_worker_preparation.py`
(4: integración de extremo a extremo), `tests/test_library_taken_format.py`
(14), `tests/test_library_path_cache.py` (12), `tests/test_ui_styles.py` (2)
y `tests/test_ui_help.py` (3). **Suite completa:** **1254 passed** (los 1214
previos siguen en verde). **`uv run ruff check .`:** limpio.

**Cifras antes/después** (25 000 fotos, 3 repeticiones, offscreen; mediana con
mínimo entre paréntesis; línea base de A del § 0):

| Métrica | Antes (tras A) | Después | Veredicto |
|---|---:|---:|---|
| Primer píxel | 102,2 ms | 13,6 ms (10,4) | **OK**, ≤ 100 ms |
| Fase 1 navegable | 517,6 ms | 344,2 ms (335,7) | **OK**, < 1,0 s y 173,4 ms más rápida |
| Populate completo | 5 947,4 ms | 4 181,1 ms (3 861,2) | **OK**, 1 766 ms más rápido |
| `build_search_index`/`sorted_photos` en UI | 3 / 2 | 0 / 0 | **OK** |
| Tiempo en `_apply_filter` durante el arranque | ~149 ms | 0,0 ms (0 llamadas) | **OK**, < 20 ms |
| `load_from_catalog` completo | ~2 686 ms | 1 514,3 ms (1 498,4) | Casi: 14 ms sobre el objetivo de 1 500 ms |
| 25 000 × `_format_taken` | ~810 ms | 28,4 ms (27,7) | Cifra absoluta en rango; en esta máquina 4,6× (ver nota S2) |

Modo `--favoritas-primero` sin contadores, medido en la misma sesión: primer
píxel 13,9 ms (10,6), fase 1 347,1 ms (334,7), completo 4 187,2 ms (3 880,4),
1 250 ítems en la fase 1 (esperado 1 250). El modo `--fase-b` añadió:
`build_search_index`/`sorted_photos` en UI **0 / 0**, `prepare_photos` en el
worker **2 por arranque**, `_apply_filter` **0 llamadas / 0,0 ms**, veredicto
**OK**. Los dos benchs terminaron con código 0; sin exit 139.

**Desviaciones:** las registradas en las notas `[x]` de S1–S7. Las principales:
se evitaron dos llamadas vacías de índice/orden en la construcción de la
ventana para que el contador de UI sea literalmente 0; se omitió el punto 4
opcional de S6 (caché de cabeceras en resize); S2 no alcanza el ratio ≥20×
porque la línea base local es 130 ms y no los ~810 ms del informe (la cifra
final, 28 ms, sí está en el rango previsto); S3 queda 14 ms por encima del
objetivo de 1 500 ms en la mediana (el mínimo ya lo cumple) y se prefirió no
quitar los `str()` literales del enunciado. **Sin commits ni pushes.**
