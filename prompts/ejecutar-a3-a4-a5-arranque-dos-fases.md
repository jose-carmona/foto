# Prompt — Ejecutar A.3, A.4 y A.5: arranque en dos fases con solo favoritas en la rejilla

> Prompt autocontenido y ejecutable por un agente. Cubre **solo** las tareas
> **A.3**, **A.4** y **A.5** del informe
> [`mejora-arranque-operativo.md`](mejora-arranque-operativo.md). Parte de que
> **A.1 y A.2 ya están cerradas** (commit `aab51a0`, detalle en
> [`ejecutar-a1-a2-favoritas-sql.md`](ejecutar-a1-a2-favoritas-sql.md),
> § 6): `catalog.list_present_files(system_tag=…)`,
> `catalog.has_system_tag(…)` y `load_from_catalog(…, system_tag=…)` existen y
> tienen tests de paridad. **No las reescribas**; este prompt las consume.
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
| S1 | Recuento de presentes y sesión de dos fases en una sola conexión | Cerrada 2026-11-26 |
| S2 | `LibraryLoadWorker`: dos señales y vida del hilo | Cerrada 2026-11-26 |
| S3 | `MainWindow`: fase 1, fusión de fase 2 y barra de estado | Cerrada 2026-11-26 |
| S4 | `PhotoWindow.append_photos()` y recuentos con total conocido | Cerrada 2026-11-26 |
| S5 | Bench `--favoritas-primero` y medición | Cerrada 2026-11-26 |
| S6 | Tests de integración, verificación final y reporte | Cerrada 2026-11-26 |
| S7 | Bugfix: orden canónico al quitar `fav` tras la fase 2 | Cerrada 2026-11-26 |

---

## 0. Misión

Hoy `MainWindow._on_library_loaded()` recibe **todas** las fotos y decide
después arrancar con `:tag fav`. El filtro solo oculta ítems ya creados:
medido con 25 000 fotos, el primer lote navegable llega a **3,65 s** y el
populate completo a **5,26 s**; en la biblioteca real (~29 567 fotos,
**4 favoritas**) el arranque es de ~5,6 s. A.1/A.2 ya hicieron que filtrar en
SQL cueste ~66 ms y materializar solo favoritas ~197 ms, pero la UI todavía
no usa esa API.

**Objetivo de este prompt:** conectar esa API al arranque. La ventana debe
quedar navegable con las favoritas en **< 1 s** en 25 000 fotos
(objetivo del informe: 0,3–0,5 s), creando **solo tantos `QListWidgetItem`
como favoritas**, y cargar el resto en segundo plano **sin vaciar la rejilla,
sin perder selección, filtro ni scroll**. La barra debe decir desde el primer
momento «4 de 29 567 · cargando…» y, al terminar, «4 de 29 567 · etiqueta:
fav» (el separador de miles es el de `StatusBar.set_counts`: sin agrupar).

El worker abre **una sola vez** la conexión de solo lectura y emite dos fases
desde el mismo hilo; la conexión RW de favoritos sigue siendo propiedad de la
ventana (ADR 0006). Sin dependencias nuevas, sin originales, sin esquema
nuevo, sin ADRs ni README.

---

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables (originales intactos, sin Qt en
   `core/`/`storage/`, sin `git commit`/`push` sin permiso, sin dependencias
   nuevas, identificadores en inglés, textos en español).
2. `README.md` — alcance y arquitectura.
3. `prompts/mejora-arranque-operativo.md` — secciones 2, 3 (A.3, A.4, A.5),
   5 (criterios 1–6) y 6 (riesgos).
4. `prompts/ejecutar-a1-a2-favoritas-sql.md` (§ 2 y § 6) — API disponible y
   cifras ya medidas.
5. `docs/adr/0011-arranque-progresivo.md` — decide el estado de carga, el
   worker, la cancelación cooperativa y los lotes. **No se modifica en este
   prompt** (su § Alternativas descartadas queda así hasta que el dueño
   actualice el ADR); sí se cambia el contrato de señales, documenta el cambio
   en los docstrings del código.
6. `docs/adr/0006-etiquetas.md` — `fav` se resuelve por `kind='system'` y
   nombre normalizado; `confirmed`/`suggested` cuentan, `rejected` no.
7. Código real, localizando por **nombre de función** (las líneas son una
   foto del 2026-11-26 y pueden desplazarse):
   - `src/foto/storage/catalog.py`: `list_present_files` (~1634),
     `has_system_tag` (~1665), constantes `_PRESENT_FILES_SELECT`,
     `_PRESENT_FILES_ORDER`, `_SYSTEM_TAG_EXISTS_SQL` (§ momento del módulo).
   - `src/foto/core/library.py`: `load_from_catalog` (~187) y
     `LibraryLoadCancelled`. No cambia.
   - `src/foto/ui/app.py`: `load_library` (~263), `_photos_from` (~211),
     `LibraryLoadWorker` (~365), `MainWindow._start_library_load` (~475),
     `_shutdown_background_work` (~500), `_on_library_loaded` (~530),
     `_on_library_failed` (~570), `closeEvent` (~586).
   - `src/foto/ui/window.py`: `_PopulateState` (~146), `_populate_grid`
     (~628), `_populate_range` (~669), `_populate_next_batch` (~740),
     `_finish_populate` (~765), `_matches_filter` (~799), `set_library`
     (~841), `set_photos` (~877), `_select_first_visible` (~930),
     `_after_set_photos_tail` (~965), `_visible_count` (~1562),
     `_apply_filter` (~1619), `_set_tag_filter` (~1799).
   - `src/foto/ui/grid.py`: `append_visible_rows` (~253),
     `set_visible_rows` (~247).
   - `src/foto/ui/panels.py`: `StatusBar.set_counts` (~559), `set_pinned`
     (~600).
   - `scripts/bench_arranque.py`: `_construir_biblioteca` (~98), `main`
     (~276) y `_arranque` (~342).
8. Tests de referencia: `tests/test_ui_progressive.py` (ciclo de arranque,
   worker, cancelación), `tests/test_catalog_system_tag_filter.py` (paridad
   de favoritos) y `tests/ui_factories.py` (`make_photo`).

---

## 2. API que debe quedar al terminar

```python
# storage/catalog.py (nuevo; sin cambios de esquema)
def count_present_photos(self) -> int: ...

# core/library.py — SIN CAMBIOS (A.1/A.2)
def load_from_catalog[T](
    catalog: Catalog,
    factory: Callable[..., T] = CatalogPhoto,
    should_continue: Callable[[], bool] | None = None,
    *,
    system_tag: str | None = None,
) -> list[T]: ...

# ui/app.py (nuevos)
@dataclass(frozen=True)
class LibraryFirstPhase:
    photos: list[Photo]                 # favoritas; todas si no hay favoritas
    tags: list[Tag]
    locations: tuple[LocationStatus, ...]
    total: int                          # catalog.count_present_photos()
    has_more: bool                      # False => no habrá fase 2

@dataclass(frozen=True)
class LibraryLoadPhases:
    first: LibraryFirstPhase
    remaining: list[Photo]              # solo las NO incluidas en first.photos

def load_library_phases(
    library_path: Path,
    metadata_dir: Path | None = None,
    *,
    should_continue: Callable[[], bool] | None = None,
    on_first_phase: Callable[[LibraryFirstPhase], None] | None = None,
) -> LibraryLoadPhases: ...

def load_library(  # firma y comportamiento ACTUALES, sin tocar
    library_path, metadata_dir, *, should_continue=None
) -> tuple[list[Photo], list[Tag], tuple[LocationStatus, ...]]: ...

# ui/app.py — LibraryLoadWorker
loaded = Signal(object, object, object, int, bool)  # fotos, tags, ubicaciones, total, has_more
library_complete = Signal(object)                  # restantes; [] al cerrar hilo
failed = Signal(str)

# ui/window.py
def append_photos(self, photos: list[Photo]) -> None: ...
def set_photos(
    self, photos, thumbnails=None, tag_filter=None, *, total_count: int | None = None
) -> None: ...                                      # parámetro nuevo, opcional
```

`on_first_phase` se invoca **dentro del hilo del worker, justo después de
materializar la fase 1 y antes de empezar la fase 2**. Qt entregará la señal
en el hilo de UI por conexión encolada; no hace falta `processEvents()` en el
worker (y no debe hacerse).

---

## 3. Tareas

### [x] S1 — Recuento de presentes y sesión de dos fases en una sola conexión (2026-11-26)

> Nota (2026-11-26): para no duplicar ni la lista de columnas ni el
> `FROM … WHERE` se partió `_PRESENT_FILES_SELECT` en
> `_PRESENT_FILES_COLUMNS + _PRESENT_FILES_FROM`; la consulta histórica sin
> filtro queda byte a byte igual. Los tests de esta capa se pusieron en el
> fichero nuevo de S6 (el enunciado permitía ese destino), no en
> `test_catalog_system_tag_filter.py`, que sigue siendo sin Qt.

1. Añade `Catalog.count_present_photos() -> int`: `COUNT(DISTINCT f.photo_hash)`
   sobre los **mismos** `JOIN` y `WHERE f.status='present' AND
   loc.state='active'` de `_PRESENT_FILES_SELECT`. Reutiliza constantes o
   extrae el `FROM … WHERE` a una constante compartida; **no dupliques la
   lista de 28 columnas ni cambies `list_present_files()`**. Docstring en
   español aclarando que cuenta fotos, no apariciones. Sin índices ni
   migraciones.
2. En `src/foto/ui/app.py`, extrae de `load_library()` un helper privado
   `_library_snapshot(catalog, should_continue, system_tag=None)` que devuelva
   `(photos, tags, locations)` usando `_photos_from`, `_tags_from` y
   `_locations_from`. `load_library()` queda como hoy (abre/cierra su conexión
   y delega en el helper): el bench y los tests existentes lo siguen usando.
3. Implementa `load_library_phases()` (firma en § 2):
   1. Abre el catálogo **una sola vez** con `Catalog.open_read_only`, con los
      mismos guardias y mensajes de `load_library()`; cierra en `finally`.
   2. Lee `tags` y `locations` primero (el banner y `:tag` hacen falta antes
      del primer lote) y calcula `total = catalog.count_present_photos()`.
   3. Decide la fase 1 con
      `use_fav = find_tag_in_catalog(FAVORITE_TAG, tags) is not None and catalog.has_system_tag(FAVORITE_TAG)`.
      Con `use_fav`: `photos = _library_snapshot(catalog, should_continue, system_tag=FAVORITE_TAG)[0]`;
      si no: carga completa con `system_tag=None`. En ambos casos
      `has_more = use_fav`, y se comprueba `should_continue` antes de seguir
      (si ya es `False`, lanza `LibraryLoadCancelled`).
   4. Construye `LibraryFirstPhase` y llama a `on_first_phase(first)` si se
      pasó, **antes de leer el resto**.
   5. Si `has_more`, materializa la lista completa con
      `_library_snapshot(catalog, should_continue)` y guarda en `remaining`
      solo las fotos cuyo `key` no esté en la fase 1. Si no, `remaining=[]`.
      `should_continue` se reenvía a ambas materializaciones; el corte sigue
      lanzando `LibraryLoadCancelled` y no devuelve resultados parciales.
   6. Devuelve `LibraryLoadPhases(first=first, remaining=remaining)`. Ningún
      objeto Qt, ninguna escritura, ninguna segunda apertura de BD.
4. Añade tests de esta capa en `tests/test_catalog_system_tag_filter.py` o en
   el nuevo fichero de S6: `count_present_photos()` coincide con
   `len(load_from_catalog(catalog))` con 0, 1 y N favoritas y con dos
   ubicaciones (`fast`/`archive`); `remaining` no contiene claves de la fase 1;
   la unión `first.photos + remaining` tiene `total` claves únicas; la
   cancelación entre fases no entrega `remaining`; `on_first_phase` se llama
   **antes** de la segunda materialización (ordena las llamadas en una lista).

### [x] S2 — `LibraryLoadWorker`: dos señales y vida del hilo (2026-11-26)

> Nota (2026-11-26): `library_complete([])` se emite una sola vez también
> en fallo de apertura, además de en cancelación y sin fase 2; es inofensivo
> porque `_on_library_complete` no toca nada con `_has_more=False` y la
> señal es la que detiene el hilo.

1. Cambia las señales a las de § 2. **No reutilices `loaded` con tres
   argumentos**: el cuarto (`total`) y el quinto (`has_more`) llegan con la
   fase 1 para que la ventana pinte recuentos correctos desde el primer momento.
2. `load()` llama a `load_library_phases(..., should_continue=lambda: not
   self._cancelled, on_first_phase=self._emit_first)`, donde `_emit_first`
   hace `self.loaded.emit(f.photos, f.tags, f.locations, f.total,
   f.has_more)`. Tras el retorno, si no se canceló, emite
   `self.library_complete.emit(phases.remaining)`.
3. **Una sola emisión final de `library_complete` por llamada a `load()`**,
   también en cancelación o sin fase 2 (con `[]` en esos casos): hazlo en un
   `finally` con un flag `emitted`. Es la señal que conecta a
   `self._loader_thread.quit`, junto con `failed`. **Elimina la conexión
   actual `loaded` → `thread.quit`** (A.3): si se dejara, el hilo moriría al
   entregar la fase 1 y nunca cargaría el resto. `thread.finished →
   deleteLater` se mantiene.
4. Los bloques `except` actuales (cancelada, `sqlite3.Error`, `OSError`/
   `RuntimeError` y `Exception` inesperada) siguen igual: ninguna excepción
   escapa del hilo, `failed` solo si no se canceló. `_shutdown_background_work`
   no cambia de lógica: al ser **un único worker**, `cancel()` cubre las dos
   fases; actualiza su docstring para decirlo explícitamente y añade el
   criterio 6 al test (abajo).
5. Actualiza los tests del worker en `tests/test_ui_progressive.py` que
   conectan `loaded` con lambda de 3 parámetros (`test_worker_emits_loaded_with_pure_data`,
   `test_worker_cancel_discards_the_result`, `test_worker_reports_*`) y los
   que parchean `app_module.load_library` (`test_worker_*`,
   `test_main_window_close_mid_load_cancels_cleanly`,
   `test_quit_mid_load_*`, `test_worker_thread_stops_after_*`,
   `test_worker_reports_unexpected_errors`): deben parchear
   `app_module.load_library_phases` y construir un `LibraryLoadPhases`/
   `LibraryFirstPhase` con `has_more=False` salvo en el test de dos fases.
   Conserva todas las aserciones anteriores (guardia, mensajes, hilo parado).
6. Añade `test_worker_emits_two_phases_in_order`: con un `LibraryLoadPhases`
   de dos fases, la lista de señales es
   `loaded(4, tags, locs, 29567, True)` → `library_complete([resto])`, y el
   hilo sigue vivo entre ambas y se detiene al terminar.

### [x] S3 — `MainWindow`: fase 1, fusión de fase 2 y barra de estado (2026-11-26)

> Nota (2026-11-26): el helper `_refresh_location_banner(photos)` se extrajo
> en `PhotoWindow` (no en `MainWindow`) para que `set_library` y el cierre
> de la fase 2 compartan exactamente el mismo código. El cierre del estado
> de carga se centralizó en `_finish_two_phase_startup`, que se difiere al
> `on_done` del populate por lotes de la fase 2.

1. `__init__` añade `self._total_count: int | None = None`,
   `self._has_more = False` y `self._pending_append: list[Photo] = []`. La
   conexión RW, el estado de carga y el arranque del worker siguen igual.
2. Conecta `self._loader.loaded` a `_on_library_loaded` (firma nueva de 5
   parámetros) y `self._loader.library_complete` a `_on_library_complete`.
3. `_on_library_loaded(photos, tags, locations, total, has_more)`:
   - Abre `tag_connection` exactamente como hoy (mismo error fatal, mismo
     `QMessageBox` y `QApplication.exit(1)`).
   - `initial_tag_filter = FAVORITE_TAG if has_more and find_tag_in_catalog(FAVORITE_TAG, tags) else None`.
     Con `has_more=False` la fase 1 **ya es la biblioteca completa**, así que
     no se activa filtro (comportamiento actual intacto).
   - Guarda `self._total_count = total` y `self._has_more = has_more`; pasa
     `total_count=total` a `set_library()` (que lo reenvía a `set_photos`).
   - Si `has_more`: `self.set_library_loading(True)` y
     `self.status.set_pinned("cargando el resto de la biblioteca…")` **después**
     de `set_library()` (porque `set_photos` fija «etiqueta: fav»). La barra
     derecha ya mostrará «4 de 29567» gracias a S4.
4. `_on_library_complete(remaining: list[Photo]) -> None`:
   - Si `self._load_cancelled` o no `self._has_more`: **no toca nada** (el
     caso sin favoritas ya cargó todo en la fase 1).
   - Guarda `self._has_more = False` y restaura el fijado a
     `f"etiqueta: {self._tag_filter}" if self._tag_filter else ""` (sin volver
     a aplicar el filtro ni a llamar a `set_photos`).
   - Si queda populate de la fase 1 en curso (`self._populate_active`),
     guarda `remaining` en `self._pending_append` y deja que `_finish_populate`
     lo aplique (S4, punto 2); no se lanza una segunda sesión simultánea.
   - Si no: llama a `self.append_photos(remaining)`.
   - Recalcula el banner de ubicaciones no disponibles con la biblioteca ya
     completa, usando las mismas funciones puras de `set_library`. Extrae un
     helper `_refresh_location_banner(photos)` si hace falta; con solo 4
     favoritas el recuento del banner estaría incompleto.
   - Finaliza el estado de carga solo cuando el append haya terminado: si el
     append quedó por lotes, difiere `set_library_loading(False)` y los
     recuentos finales al `on_done` de esa sesión.
5. Los recuentos de la barra pasan de «4 de 29567» (fase 1) a «29567 de
   29567» solo si el usuario quita `:tag fav`; con el filtro activo se queda
   en «4 de 29567». La selección, el filtro y el scroll no se reinician.
6. `test_main_window_opens_with_the_fav_tag_filter` debe seguir pasando con la
   nueva secuencia: tras bombear, `len(window.photos)==2`,
   `_tag_filter=="fav"`, `_visible_keys_cache==[favorita]`,
   `len(window._grid_items)==2` y `_pinned=="etiqueta: fav"`. Añade un test
   que observe el estado **intermedio** (fase 1 entregada, fase 2 en vuelo):
   `len(window.photos)==1`, un solo ítem en la rejilla, `_pinned` con
   «cargando el resto…» y `_library_loading is True`.

### [x] S4 — `PhotoWindow.append_photos()` y recuentos con total conocido (2026-11-26)

> Nota (2026-11-26): `append_photos()` conserva la firma `-> None` de § 2
> en lugar de devolver `True`; el llamador detecta el populate diferido
> comprobando `_populate_active` tras la llamada, que es equivalente y evita
> contradecir la API pública. El cierre diferido se apoya en un hook
> interno `_append_on_done` que `MainWindow` asigna al llegar la fase 2.
> `_update_counts()` se usa también en `_on_current_changed` y en
> `__init__` para que ninguna puntada de la barra pierda el total de fase 1.
> No se reordenó globalmente: se mantiene «favoritas primero, resto después»
> y `:sort <clave>` restaura el orden canónico, según permite el enunciado.

1. Añade `self._total_count: int | None = None` en `__init__`, el parámetro
   opcional `total_count` en `set_photos()` y un helper privado
   `_update_counts()` que centralice **todas** las llamadas
   `status.set_counts(self._visible_count(), len(self.photos), …)` de
   `_select_first_visible`, `_populate_next_batch`, `_apply_filter` y
   `_after_set_photos_tail`. El total a mostrar es `self._total_count` si no
   es `None`, y `len(self.photos)` en caso contrario (demo y tests actuales no
   cambian). `set_photos(..., total_count=None)` lo limpia.
2. Implementa `append_photos(photos: list[Photo]) -> None` con estas
   invariantes:
   - **No** llama a `grid.clear()`, no reinicia `_grid_items`,
     `_visible_keys_cache`, `_visible_positions_by_key`, `_filter_text`,
     `_tag_filter`, `search_text` ni la caché de imágenes. No cierra el visor.
   - Actualiza `self.photos` y amplía `_search_index` con
     `build_search_index(photos)` + `dict.update` (sin reconstruir el índice
     de las fotos ya cargadas). Para que `/` encuentre una foto no favorita
     justo después de la fase 2, el índice debe quedar completo.
   - Reutiliza `_populate_range` con un `_PopulateState` en modo append:
     `ordered=sorted_photos(photos, self._sort_key)`, `index=0`,
     `row=self.grid.count()`, `headers` inicializado con las cabeceras ya
     insertadas y `current_group` con el grupo del último ítem presente para
     no duplicar cabecera si el grupo continúa. Cada ítem nace con
     `item.setHidden(not self._matches_filter(...))` contra el filtro vigente
     en ese momento; las filas visibles siguen registrándose con
     `grid.append_visible_rows` y extendiendo las cachés, lote a lote.
   - Guarda `current_key` y `grid.verticalScrollBar().value()` antes de
     insertar y restaura ambos al terminar (si la clave sigue visible). El
     estado del populate por lotes se gestiona igual que en `_populate_grid`:
     si supera `POPULATE_SYNC_LIMIT`, increméntalo con `_populate_timer` y
     devuelve `True` para que el llamador difiera su cierre.
   - Llama a `_update_counts()` en cada lote y al terminar. Con el filtro
     `fav` activo los recuentos son «4 de 29567» durante toda la fase 2.
   - Si al llegar la fase 2 aún había populate de la fase 1 en curso,
     `_finish_populate()` aplica `self._pending_append` **después** de ejecutar
     el `on_done` de la fase 1, sin `clear()` y sin perder la selección que ese
     `on_done` restauró. Añade un test con el fixture `progresivo`
     (`POPULATE_SYNC_LIMIT=2`, `POPULATE_BATCH_SIZE=2`) para este solapamiento.
   - Nota de diseño (docstring): al insertar al final, el orden tras la fase 2
     es «favoritas primero, resto después»; con `:tag fav` la vista es
     idéntica y `:sort <clave>` vuelve al orden canónico. No es obligatorio
     reordenar globalmente en este prompt; si se hace, documenta la desviación
     y añade un test de orden global.
3. Tests unitarios de `append_photos` sobre `PhotoWindow` (sinworker):
   - `grid.clear` parcheado para contar llamadas: la fase 2 no lo invoca;
     `len(window._grid_items)` crece de 4 a N sin claves duplicadas.
   - Selección, `_tag_filter`, `_filter_text` y valor del scroll iguales antes
     y después; `_visible_keys_cache` sigue conteniendo solo favoritas con
     `fav` activo; al quitar el filtro (`:tag` sin nombre) las N fotos quedan
     visibles **sin recargar nada**; `0`/`$` recorren la lista visible.
   - `_search_index` contiene una foto añadida y `/` la encuentra sin llamar a
     `build_search_index` sobre la biblioteca completa (parchea para contar).
   - `set_photos(..., total_count=29_567)` muestra «4 de 29567» antes de que
     existan las 29 567 fotos en `self.photos`.

### [x] S5 — Bench `--favoritas-primero` y medición (2026-11-26)

> Nota (2026-11-26): el fin de la fase 1 se detecta envolviendo el
> `_finish_populate` de la instancia y con el contador de `loaded` del
> bench, sin tiempos fijos. El teardown no dio el exit 139 conocido, así
> que no hizo falta el `os._exit(0)` fuera del repo; no se añadió ningún
> script nuevo.

1. `scripts/bench_arranque.py` gana el flag `--favoritas-primero` (sin
   dependencias nuevas). Con el flag:
   - Se omiten las secciones (a), (b) y (d); se construye la biblioteca igual
     (`--fotos`, `--originales`, `--repeticiones`, `--keep`).
   - El `_arranque()` de la sección (c) mide: primer píxel (`t1-t0`), **fase 1**
     (desde `t0` hasta que la fase 1 esté entregada y su populate terminado) y
     **populate completo** (fase 2 terminada). Para detectar el fin de la fase
     1 sin carreras, usa `window._has_more`, `window._populate_active` y un
     contador de `loaded` conectado en el propio bench; no duermas tiempos
     fijos.
   - En cuanto termine la fase 1, registra `len(window._grid_items)` y
     `len(window.photos)`; **el contador de ítems debe ser igual al de
     favoritas** (`fotos//20` con `_construir_biblioteca`).
   - Imprime medianas (y mínimos) con `_linea`/`_reportar`, más una línea
     `ítems creados en la fase 1: N (esperado: M)` y el veredicto frente al
     umbral: **mediana de fase 1 < 1000 ms** con 25 000 fotos. Sin el flag,
     los modos existentes no cambian ni una línea de salida.
2. Ejecuta, con la moderación que pide el tamaño del banco:

   ```sh
   QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py \
       --fotos 25000 --repeticiones 3 --favoritas-primero
   ```

   y registra en el reporte: primer píxel, fase 1, completo, ítems creados y
   veredicto. Si el teardown de PySide6/Python 3.15 da el exit 139 conocido
   (T2.9), termina el script con `os._exit(0)` **fuera del repo** y dilo en el
   reporte. No añadas nuevos scripts al repositorio.

### [x] S6 — Tests de integración, verificación final y reporte (2026-11-26)

> Nota (2026-11-26): los 7 casos de integración y los tests de la capa sin
> Qt de S1 más 3 tests unitarios de `append_photos` viven en el fichero
> nuevo; `test_worker_emits_two_phases_in_order` se añadió a
> `test_ui_progressive.py`. `test_quit_and_close_cancel_both_phases` está
> parametrizado en dos vías (`close`/`quit`), por lo que aporta dos casos.
> Los 4 tests existentes que parcheaban `load_library` pasan a parchear
> `load_library_phases`, conservando todas sus aserciones. En la vía `quit`
> de `test_quit_and_close_cancel_both_phases` se invoca directamente el
> handler conectado a `aboutToQuit` en vez de emitir la señal global: en la
> suite completa otros tests dejan ventanas conectadas y emitirla tocaba
> objetos Qt ya destruidos (T2.9). La conexión real está cubierta por
> `test_quit_mid_load_cancels_the_worker_about_to_quit`.

1. Crea `tests/test_ui_two_phase_startup.py` (Qt, `QT_QPA_PLATFORM=offscreen`
   por fixture) con catálogos sembrados como en `_seed_catalog` de
   `tests/test_ui_progressive.py` y favoritos escritos con `toggle_favorite`.
   Casos mínimos, con nombres en inglés y docstrings en español:
   1. `test_two_phase_startup_loads_favorites_only_first` — 3 fotos, 1
      favorita: fase 1 con 1 foto/1 ítem y pinned «cargando el resto…»; fase 2
      con 3 fotos/3 ítems, pinned «etiqueta: fav», visible solo la favorita y
      recuento «1 de 3».
   2. `test_no_favorites_skips_second_phase` — sin favoritas: una sola fase,
      sin filtro, pinned `""`, cosas existentes intactas.
   3. `test_second_phase_keeps_selection_filter_and_scroll` — monkeypatch
      `PhotoGrid.clear` para contar, selecciona la favorita, fija filtro y
      scroll, bombea hasta la fase 2: cero `clear()` nuevos, misma clave
      seleccionada, mismo filtro, mismo scroll, sin claves duplicadas.
   4. `test_remove_tag_filter_after_phase_two_reveals_everything` — `:tag` sin
      nombre revela las N fotos sin recargar; `0` y `$` navegan; `/` encuentra
      una no favorita.
   5. `test_pending_append_when_first_populate_is_still_running` — con el
      fixture `progresivo`, la fase 2 llega antes de terminar la fase 1; se
      aplica al terminar, sin `clear()` y con la selección de la fase 1.
   6. `test_quit_and_close_cancel_both_phases` — `aboutToQuit` y `closeEvent`
      cancelan el worker único con fase 2 en curso; sin traceback, hilo
      detenido, sin resultados parciales (criterio 6).
   7. `test_banner_uses_full_library_after_phase_two` — con una ubicación
      ausente, el banner de la fase 2 cuenta las fotos degradadas de la
      biblioteca completa, no solo de las favoritas.
2. Verificación final (todo en verde antes de cerrar S6):
   ```sh
   uv run pytest tests/test_catalog_system_tag_filter.py -q
   uv run pytest tests/test_ui_progressive.py tests/test_ui_two_phase_startup.py -q
   uv run pytest -q
   uv run ruff check .
   ```
3. Rellena el § 6 (reporte) con: tests nuevos, suite completa, cifras del
   bench (antes/después del informe: 3,65 s → mediana medida), contador de
   ítems y desviaciones. **No hagas commit ni push.**

---

### [x] S7 — Bugfix: orden canónico al quitar `fav` tras la fase 2 (2026-11-26)

> Nota (2026-11-26): tarea añadida por un bug observado tras la ejecución: el
> append deja «favoritas primero, resto después», así que al quitar `:tag fav`
> la vista no seguía el orden subyacente `:sort date` y hacía falta el doble
> salto `:sort camera` → `:sort date`. Se implementa el reordenado global que
> el enunciado de S4 dejaba como opcional, documentando aquí la desviación.
> El reordenado es **diferido**: no se paga en el arranque ni mientras `fav`
> siga activo (donde la vista ya coincide), sino al retirar/cambiar el filtro,
> al pedir `:sort` —ahora `:sort date` basta solo— o al terminar un append que
> ya llegara sin el filtro. Se reutiliza el populate por lotes con las señales
> bloqueadas, restaurando selección y scroll.

1. `PhotoWindow` lleva `_order_needs_restore` y `_canonical_order_dirty`;
   `append_photos()` marca el primero al empezar y el segundo al terminar.
2. `_restore_canonical_order()` recompone la rejilla con
   `sorted_photos(self.photos.values(), self._sort_key)`, cancela cualquier
   append pendiente sin solapar sesiones y restaura selección y scroll.
3. `_set_tag_filter()` lo invoca al quitar/cambiar `fav`, y `_sort_grid()`
   ya no hace el early-return cuando el orden está pendiente.
4. Tests nuevos: orden global tras quitar `fav`, `:sort date` en un solo
   paso, y filtro retirado a medio populate de la fase 2.

---

## 4. Fuera del alcance de este prompt

- A.1/A.2: `list_present_files`, `has_system_tag`, `load_from_catalog` y sus
  tests de paridad (ya cerrados).
- Fase B: `build_search_index`/`sorted_photos` en el worker, `_format_taken`,
  caché de rutas, flag `_filter_changed_during_populate`, `QFontInfo` y
  overlays diferidos. Aunque S4 toca `_update_counts`, **no** incorpora el
  ahorro de B.4 (la pasada final de `_apply_filter` sigue como hoy).
- Fase C (modelo virtualizado), ADRs, README, `docs/diseno-ui.md` y esquema
  del catálogo.
- Escritura en originales, nuevas dependencias, commits o pushes.

---

## 5. Definición de done

- [x] `count_present_photos()` y `load_library_phases()` con type hints y
      docstrings en español; una sola apertura de BD demostrada por test.
- [x] Worker con dos señales, hilo vivo hasta la fase 2 y cancelación de
      ambas fases por `closeEvent` y `aboutToQuit`.
- [x] Primera foto navegable con solo favoritas; `QListWidgetItem` creados en
      la fase 1 = número de favoritas.
- [x] Fase 2 sin `grid.clear()`, sin perder selección, filtro ni scroll;
      recuentos «4 de 29567»; `/`, `0`/`$` y el visor intactos.
- [x] Barra: «cargando el resto de la biblioteca…» → «etiqueta: fav».
- [x] `--favoritas-primero` con mediana < 1,0 s en 25 000 fotos y contador
      de ítems correcto; cifras adjuntas al reporte.
- [x] `uv run pytest` y `uv run ruff check .` completos, sin regresión en
      `test_ui_progressive.py`, `test_ui_search.py`, `test_ui_tags.py` ni
      `test_ui_help.py`.
- [x] Sin commits/pushes, sin archivos nuevos en el repo aparte de
      `tests/test_ui_two_phase_startup.py` y los módulos modificados.
- [x] Al quitar `:tag fav` (S7) la rejilla queda en orden canónico `:sort
      date`, con `:sort date` bastando por sí solo; selección y scroll se
      conservan.

---

## 6. Reporte de ejecución (rellenar al terminar)

**Tests nuevos:** 21 (20 en `tests/test_ui_two_phase_startup.py`, incluidos
los 3 del bugfix S7, y `test_worker_emits_two_phases_in_order` en
`test_ui_progressive.py`); el test
`test_main_window_opens_with_the_fav_tag_filter` se amplió con la observación
del estado intermedio. Suite completa: **1214 passed** (los 1193 previos siguen
en verde). `uv run ruff check .`: limpio.

**Cifras del bench** (`--fotos 25000 --repeticiones 3
--favoritas-primero`, 25 000 fotos sintéticas, 1 favorita cada 20, sin
originales; mediana y mínimo de 3 repeticiones):

| Métrica | Informe (antes) | Medido | Veredicto |
|---|---:|---:|---|
| Primer píxel | 0,10 s | mediana 102,2 ms (mín 99,3 ms) | — |
| Fase 1 navegable | 3,65 s | mediana 517,6 ms (mín 506,4 ms) | **OK**, < 1,0 s |
| Populate completo | 5,26 s | mediana 5 947,4 ms (mín 5 631,8 ms) | — (en fondo) |
| Ítems en la fase 1 | 25 000 | 1 250 (fotos en memoria: 1 250) | **OK**, = favoritas (1 250) |

El objetivo del informe (0,3–0,5 s) se cumple apenas por encima: 0,52 s de
mediana frente a 3,65 s de línea base, con el suelo de ~0,10 s de primer
píxel y ~0,42 s de importación de módulos. La fase 1 crea exactamente 1 250
ítems, no 25 000. El populate completo se hace en segundo plano y no bloquea
la interacción; su cifra incluye ahora las dos fases más el append sin
`clear()`. El script terminó con código 0: no apareció el exit 139 del
teardown, así que no se usó `os._exit(0)` ni se añadió script alguno.

**Desviaciones:** las registradas en las notas `[x]` de S1–S7. Las dos
principales: `append_photos()` mantiene `-> None` y el llamador detecta el
populate diferido con `_populate_active` en lugar de un valor de retorno; y
S7 implementa el reordenado global que S4 dejaba opcional (diferido al quitar
`fav` o al ordenar, para no encarecer el arranque). Las cifras del bench no se
remidieron: el cambio de S7 no toca la fase 1 ni su populate. Sin commits ni
pushes, sin ADRs/README/schema nuevos y sin tocar originales.
