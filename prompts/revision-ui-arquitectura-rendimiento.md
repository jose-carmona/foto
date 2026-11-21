# Revisión de UI: arquitectura y rendimiento

- **Fecha:** 2026-11-21
- **Ámbito:** `src/foto/ui/` y su contrato con `storage/`, `core/`, tests y benchmark.
- **Objetivo:** mejoras de arquitectura y rendimiento con el código más simple y mantenible posible.
- **Estado:** informe de revisión. No se ha modificado código de la aplicación.

> El árbol de trabajo tiene cambios sin commit y dos ficheros nuevos sin seguimiento
> (`docs/adr/0011-arranque-progresivo.md`, `tests/test_ui_progressive.py`). Este informe
> se basa en ese estado y no propone revertirlos ni commitearlos.

---

## 1. Resumen ejecutivo

La UI está en mejor forma de lo que sugiere el tamaño de `window.py`. Las decisiones
estructurales importantes son correctas: presentación pura sin Qt, keybindings
centralizados, miniaturas perezosas con cola acotada, populate por lotes y caches
limitadas. **No hace falta un rediseño, ni MVC, ni migrar a `QListView`.**

Hay, sin embargo, cinco defectos concretos que conviene corregir antes de optimizar:

1. El conmutado de favorito actualiza cuatro proyecciones a mano y se olvida de dos
   (panel de metadatos y filtro activo); además, el rollback no revierte el índice de
   búsqueda y se ignora el estado persistido que devuelve `toggle_favorite`.
2. Un `sqlite3.Error` no capturado en el worker de carga deja la ventana en
   «Cargando…» para siempre, sin señal de error.
3. Durante el populate por lotes conviven dos estados de búsqueda (`search_text`
   confirmado y el texto vivo del prompt), y el último lote descarta la búsqueda viva.
4. Con el panel de ubicaciones abierto, `?` cierra ubicaciones en vez de abrir la ayuda.
5. El apagado no detiene `_visible_timer` ni `_g_timer`, e ignora el resultado de
   `waitForDone`.

Después hay un conjunto de mejoras de latencia, casi todas locales y medibles. Las
tres de mayor impacto potencial son: el filtro hace tres pasadas completas por pulsación,
`j`/`k` recalculan la geometría de toda la rejilla en cada tecla, y el panel de metadatos
se reconstruye en cada selección aunque esté oculto (que es el estado inicial de la app real).

| ID | Hallazgo | Tipo | Prioridad | Esfuerzo | Riesgo |
| --- | --- | --- | --- | --- | --- |
| A1 | Favorito: proyecciones divergentes y rollback incompleto | Corrección | P0 | S | Bajo |
| A2 | Worker: excepción no capturada y `QThread` que no termina | Robustez | P0 | S | Bajo |
| A3 | Búsqueda viva vs. confirmada durante el populate | Corrección | P0 | S | Bajo |
| A4 | `?` secuestrado por el overlay de ubicaciones | Corrección | P0 | XS | Bajo |
| A5 | Apagado: timers vivos y esperas sin comprobar | Robustez | P0 | S | Bajo |
| R1 | Doble populate en el constructor | Rendimiento | P1 | XS | Bajo |
| R2 | Filtro en tres pasadas completas por tecla | Rendimiento | P1 | M | Medio |
| R3 | `j`/`k` recalculan toda la geometría | Rendimiento | P1 | M | Medio |
| R4 | Panel de metadatos reconstruido aunque esté oculto | Rendimiento | P1 | XS | Bajo |
| R5 | Cola del visor sin sustitución ni descarte temprano | Rendimiento | P1 | M | Medio |
| R6 | Dos `osascript` síncronos en el hilo de UI | Latencia | P1 | S | Bajo |
| R7 | `busy_timeout` de 5 s en el toggle de favorito | Latencia | P1 | S | Medio (ADR) |
| R8 | `_ghost_counts` recorre todas las fotos aunque no falte nada | Rendimiento | P2 | XS | Bajo |
| R9 | Grupos materializados antes del primer lote | Rendimiento | P2 | S | Bajo |
| R10 | `:sort` con la misma clave repuebla toda la rejilla | Rendimiento | P2 | XS | Bajo |
| R11 | Factorías `_photo` duplicadas en los tests | Mantenibilidad | P2 | S | Bajo |

**Regla de trabajo propuesta:** P0 primero, con tests de regresión; después medir R2/R3/R5
antes de tocarlos; el resto son cambios de pocas líneas que no cambian contratos.
Los identificadores `A#`/`R#` son solo etiquetas; la prioridad real es la columna
`Prioridad` (P0, P1, P2).

---

## 2. Alcance y método

Revisión estática de:

- `src/foto/ui/window.py` (orquestador, populate, filtro, favoritos, visor, cierre).
- `src/foto/ui/grid.py` (navegación y geometría).
- `src/foto/ui/presentation.py` (funciones puras de orden, grupos, búsqueda y banner).
- `src/foto/ui/panels.py` (panel de metadatos y barra de estado).
- `src/foto/ui/thumbnails.py` (patrón de referencia para colas acotadas).
- `src/foto/ui/canvas.py` (carga de originales).
- `src/foto/ui/app.py` (arranque, worker de catálogo, activación macOS).
- `src/foto/storage/tags.py` (conexión RW y favoritos).
- Tests de UI, `scripts/bench_arranque.py`, ADR 0006, ADR 0010 y ADR 0011.

Se hicieron dos pasadas independientes (arquitectura y rendimiento) y se contrastaron
contra el código y los tests existentes. Los hallazgos marcados como **verificados** se
pueden reproducir leyendo el código citado. Los marcados como **hipótesis** dependen de
medir con 25k fotos en hardware real; no se presentan como certezas.

**Limitación:** no se ha ejecutado un perfil con 25k fotos ni una medición en frío. Las
cifras históricas que se citan salen de `prompts/rendimiento-arranque-ui-25k.md` y del
benchmark actual.

---

## 3. Criterios de diseño

Toda propuesta debe cumplir:

1. **Menos código, no más capas.** Nada de controladores, casos de uso, buses de eventos
   ni inyección de dependencias para problemas locales.
2. **Sin dependencias nuevas.** Qt y la biblioteca estándar bastan.
3. **Medir antes de reescribir.** Las optimizaciones estructurales (modelo de lista,
   colas nuevas) solo si el benchmark demuestra que el coste actual duele.
4. **Conservar lo que ya funciona:** presentación pura, keybindings centralizados,
   miniaturas perezosas, caches acotadas, populate por lotes.
5. **Un solo camino para cada dato.** Si una foto cambia, todas sus proyecciones se
   actualizan desde un único helper; si un estado cambia, hay una única fuente de verdad.

---

## 4. Hallazgos verificados

### A1 — Favorito: cuatro actualizaciones a mano, dos olvidos y rollback incompleto

**Prioridad:** P0 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

`_toggle_favorite` (`src/foto/ui/window.py:2018-2053`) hace, en este orden:

- actualiza `self.photos` (2024);
- actualiza `self._search_index` (2027);
- actualiza el ítem actual de la rejilla (2028-2029);
- actualiza el lienzo del visor (2030);
- escribe en SQLite (2038-2042), **ignorando el `bool` que devuelve** `toggle_favorite`
  (`src/foto/storage/tags.py:237-269`).

No actualiza:

- `MetadataPanel`: los chips de etiquetas siguen mostrando el estado anterior
  (`panels.py:159-257` reconstruye el contenido a partir de `Photo`);
- el filtro activo: si la búsqueda o `:tag` dependen de la etiqueta `fav`, la foto
  visible sigue siendo la misma hasta la siguiente pulsación.

En el rollback (2046-2051) restaura `photos`, el ítem y el lienzo, pero **no restaura
`_search_index`**: el documento de búsqueda queda con la etiqueta de favorito que la
escritura no llegó a persistir.

**Impacto**

- Panel de metadatos obsoleto tras marcar/desmarcar favorito, incluso si el panel estaba
  oculto y se abre después (porque `toggle_panel` no refresca el contenido).
- Resultados de búsqueda incoherentes con la base de datos tras un fallo de escritura.
- Si el estado de la BD difiere del optimista (otro escritor, edición externa), la UI
  muestra una mentira aunque `toggle_favorite` ya devolvió el estado real.

**Propuesta mínima**

Un único helper que aplique una `Photo` a todas sus proyecciones:

```python
def _apply_photo_update(self, photo: Photo) -> None:
    self.photos[photo.key] = photo
    self._search_index[photo.key] = photo_search_text(photo)

    item = self._grid_items.get(photo.key)
    if item is not None:
        item.setData(FAVORITE_ROLE, photo.favorite)
        item.setData(Qt.AccessibleTextRole, photo_accessible_name(photo))

    if self.panel_visible:
        self.panel.set_photo(photo)
    current = self._current_photo()
    if self._viewer_visible and current is not None and current.key == photo.key:
        self.viewer.canvas.set_favorite(photo.favorite)
    if self._filter_text or self._tag_filter:
        self._apply_filter(self._filter_text)
    else:
        self.grid.viewport().update()
```

`_toggle_favorite` queda:

```python
updated = with_favorite_tag(photo, not photo.favorite)
self._apply_photo_update(updated)
...
try:
    persisted = toggle_favorite(...)
except CatalogError as exc:
    self._apply_photo_update(photo)
    self.status.show_error(str(exc))
    return
if persisted != updated.favorite:
    self._apply_photo_update(with_favorite_tag(photo, persisted))
self.status.show_message(message, 2000)
```

Detalles:

- Consumir el `bool` persistido evita que la UI y la BD diverjan.
- Reaplicar el filtro solo si hay filtro activo; sin filtro, no hace falta recorrer la
  rejilla.
- El helper también sirve para el rollback: una sola ruta, sin listas de campos.
- No hace falta un “gestor de estado” ni un modelo observable. Es un método privado.

**Pruebas de aceptación**

1. `test_favorite_updates_all_projections`: con panel visible y búsqueda `fav` activa,
   alternar favorito y comprobar `photos`, `_search_index`, rol del ítem, lienzo, chip
   del panel y conjunto de visibles.
2. `test_favorite_write_failure_rolls_back_all_projections`: extender el test actual
   (`tests/test_ui_tags.py:334-364`) para comprobar índice de búsqueda, panel y filtro,
   no solo `photos`/ítem/lienzo.
3. `test_favorite_follows_persisted_state`: modificar el favorito en la BD por fuera de
   la UI, alternar y comprobar que la UI termina en el estado que devolvió
   `toggle_favorite`.

**No hacer:** convertir `Photo` en objeto observable, ni añadir señales por campo.

---

### A2 — Worker de catálogo: excepción no capturada y `QThread` que no termina

**Prioridad:** P0 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

`LibraryLoadWorker.load` (`src/foto/ui/app.py:379-395`) captura `OSError` y
`RuntimeError`, pero no `sqlite3.Error`. Las lecturas del catálogo
(`Catalog.list_present_files`, `tag_assignments`, etc.) ejecutan SQL directamente y
pueden lanzar `sqlite3.Error` sin envolver. Si eso ocurre:

- el slot termina con una traza en stderr;
- no se emite `failed`;
- la ventana permanece en «Cargando…» indefinidamente;
- el `QThread` sigue vivo con su bucle de eventos.

Además, en el camino feliz nadie llama a `quit()` sobre `_loader_thread`
(`app.py:447-463`); el hilo queda corriendo hasta el apagado, aunque el ADR 0011 lo
describe como «hilo propio y corto».

**Impacto**

Un catálogo corrupto, un fallo de E/S o una versión inesperada pueden colgar el arranque
sin mensaje. Es el peor modo de fallo posible: el usuario no sabe si esperar o salir.

**Propuesta mínima**

1. Capturar `sqlite3.Error` explícitamente y, en el límite del worker, un
   `except Exception` con `logger.exception` que emita `failed`. En un hilo de fondo,
   convertir cualquier excepción en señal es más seguro que dejarla escapar.
2. Terminar el hilo tras `loaded`/`failed`: llamar a `self._loader_thread.quit()` en
   `_on_library_loaded` y `_on_library_failed`, o conectar ambas señales a `quit`.
3. Registrar el resultado de `thread.wait(...)`; si expira, dejar un `logger.warning`.

No hace falta reestructurar el worker ni usar `QThreadPool`.

**Pruebas de aceptación**

- `test_worker_reports_sqlite_errors`: monkeypatch de `load_library` que lanza
  `sqlite3.OperationalError`; comprobar `failed`, mensaje en la ventana y
  `_loader_thread.isRunning() is False` tras bombear eventos.
- `test_worker_thread_stops_after_success`: tras `loaded`, el hilo deja de correr.
- El test de cierre a mitad de carga sigue pasando.

---

### A3 — Búsqueda viva y búsqueda confirmada conviven durante el populate

**Prioridad:** P0 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

- `status.search_changed` está conectado a `_apply_filter` (`window.py:537`), que recibe
  el texto vivo del prompt.
- `_populate_range` lee `self.search_text` (`window.py:671`), que solo cambia al
  confirmar con Enter (`_commit_search`, 1632-1635).
- `_finish_populate` vuelve a aplicar `self.search_text` (`window.py:758`).

Consecuencia: si el usuario escribe mientras entran lotes, los lotes nuevos se filtran
con la consulta vieja, `_apply_filter` muestra la consulta nueva sobre lo ya insertado y,
al terminar, el último lote **descarta la consulta viva** y vuelve a la confirmada. El
conjunto visible parpadea y puede acabar seleccionando un ítem que no coincide.

El test existente `test_search_works_mid_populate` (`tests/test_ui_progressive.py:168`)
asigna también `window.search_text = "a.jpg"`, así que no cubre el caso real de escribir
sin confirmar.

**Propuesta mínima**

Una única fuente de verdad para el filtro efectivo:

```python
# __init__
self._filter_text = ""

def _apply_filter(self, text: str) -> None:
    self._filter_text = text
    needle = text.strip().casefold()
    ...
```

Y usar `self._filter_text` en `_populate_range`, `_finish_populate`, `_sort_grid` y
`_set_tag_filter`. Los caminos que cancelan o limpian la búsqueda
(`_cancel_prompt`, `_handle_escape`) siguen pasando explícitamente el texto confirmado
(o `""`), con lo que `_filter_text` se resetea solo.

No hace falta un `Signal` nuevo ni consultar el widget del prompt.

**Pruebas de aceptación**

- `test_live_search_survives_populate`: con lotes de 2, llamar a `_apply_filter("a.jpg")`
  sin tocar `search_text`, bombear hasta el final y comprobar que los visibles siguen
  siendo los de la consulta viva.
- `test_cancel_search_restores_committed_filter`: escribir, cancelar con Esc y comprobar
  que vuelve el filtro confirmado.
- `:sort` y `:tag` a mitad de populate conservan la consulta viva.

---

### A4 — Con el overlay de ubicaciones abierto, `?` no abre la ayuda

**Prioridad:** P0 · **Esfuerzo:** XS · **Riesgo:** Bajo

**Evidencia**

`_dispatch` (`window.py:1841-1848`) comprueba primero la ayuda y después ubicaciones:

```python
if self.locations_overlay.isVisible():
    if action in ("escape", "help", "close"):
        self._toggle_locations()
    return
```

Con ubicaciones abierto, `help` cae en la misma rama que `escape`: cierra el overlay y
sale. La ayuda nunca se abre. El inventario de teclas documenta `?` como «abrir ayuda»
(`tests/test_ui_documented_features.py:760`), así que es una regresión de contrato.

**Propuesta mínima**

Separar “cerrar” de “abrir ayuda”:

```python
if self.locations_overlay.isVisible():
    if action in ("escape", "close"):
        self._toggle_locations()
        return
    if action == "help":
        self._toggle_locations()  # y sigue al handler de ayuda
    else:
        return
```

O, más legible, un `_close_overlays()` compartido y dejar que `help` continúe. No hace
falta una pila de overlays.

**Pruebas de aceptación**

- `test_help_opens_over_locations`: abrir ubicaciones, pulsar `?`, comprobar que la ayuda
  está visible y ubicaciones cerrado (o que la ayuda tiene foco).
- `test_escape_closes_locations_without_opening_help`: el comportamiento actual de Esc
  se conserva.

---

### A5 — Apagado: timers vivos y esperas cuyo resultado se ignora

**Prioridad:** P0 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

`shutdown_background_work` (`window.py:2055-2067`) llama a `_cancel_populate` (que para
`_populate_timer`) y al pool de imágenes, pero no detiene:

- `_visible_timer` (`window.py:422-425`, debounce de miniaturas);
- `_g_timer` (`window.py:409-412`, prefijo `g`).

Tampoco comprueba el retorno de `waitForDone(2000)`. En `MainWindow` se añade
`thread.wait(10_000)` sin mirar el resultado (`app.py:486-488`).

**Impacto**

En salidas por `Ctrl+q`, `:q` o ⌘Q, los timers pueden dispararse durante el desmontaje.
Las tareas que exceden la espera no dejan rastro; si una decodificación se cuelga, el
cierre parece correcto pero el proceso puede tardar o quedar con trabajo vivo.

**Propuesta mínima**

```python
def shutdown_background_work(self) -> None:
    self._closing = True
    self._visible_timer.stop()
    self._g_timer.stop()
    self._cancel_populate()
    self._loader_pool.clear()
    if not self._loader_pool.waitForDone(2000):
        logger.warning("quedan decodificaciones del visor al cerrar")
```

Y un guard `if self._closing: return` al inicio de `_populate_next_batch`,
`_request_visible_thumbnails` y `_clear_g_pending`. En `MainWindow`, registrar también
el resultado de `thread.wait`.

**Pruebas de aceptación**

- Extender `test_close_mid_populate_leaves_no_live_timers`
  (`tests/test_ui_progressive.py:240`) para comprobar `_visible_timer` y `_g_timer`.
- `test_shutdown_is_idempotent` sigue pasando.
- Cerrar con el visor abierto y una decodificación en curso no produce avisos de Qt.

---

### R1 — Doble populate en el constructor

**Prioridad:** P1 · **Esfuerzo:** XS · **Riesgo:** Bajo

**Evidencia**

`window.py:525` llama a `self._populate_grid(photos)` e ignora el resultado. Más tarde,
tras conectar señales, `window.py:554-559` vuelve a llamarlo y ahora sí usa el `bool` para
diferir la primera selección. Entre ambas llamadas no se bombea el bucle de eventos, así
que la primera solo hace trabajo y la segunda lo cancela y repite.

Con 25k fotos se duplican: `sorted_photos`, la materialización de grupos, el `grid.clear()`
y la primera asignación de estado. Es un coste pequeño comparado con la carga, pero es
gratis de eliminar.

**Propuesta mínima**

Borrar la primera llamada (525) y quedarse con la de 554, que es la que ya contempla el
camino síncrono y el diferido. `_update_empty_state` puede quedarse donde está o moverse
después del populate; el resultado final es el mismo.

De paso, el contrato de `_populate_grid` puede mejorar sin coste: en vez de devolver un
`bool` y obligar a cada llamador a mutar `self._populate_state.on_done` (constructor,
`set_photos`, `_sort_grid`), aceptar `on_done=None` y guardarlo dentro del estado. Es un
cambio de firma pequeño que elimina un acoplamiento frágil y el riesgo de olvidar el
callback.

**Pruebas de aceptación**

- Un test con espía sobre `_populate_grid` comprueba una sola llamada en el constructor.
- `test_small_library_populates_synchronously` y los tests de primera selección siguen
  pasando.
- El `on_done` se ejecuta igual en el camino síncrono y en el diferido.

---

### R2 — El filtro hace tres pasadas completas por pulsación

**Prioridad:** P1 · **Esfuerzo:** M · **Riesgo:** Medio

**Evidencia**

`_apply_filter` (`window.py:1588-1630`):

1. recorre `range(self.grid.count())`, obtiene cada ítem, evalúa `_matches_filter` y llama
   a `item.setHidden(...)` **siempre**, aunque el valor no cambie;
2. llama a `_update_group_headers` (`window.py:793`), que vuelve a recorrer todos los
   ítems;
3. llama a `_refresh_visible_cache` (`window.py:1105`), que vuelve a recorrer todos los
   ítems para construir filas, claves y posiciones.

Con 25k fotos son del orden de 75k accesos a ítems de Qt por tecla, más el coste de
relayout que provoca `setHidden` aunque no cambie nada.

**Impacto**

Es la causa más probable de que la búsqueda incremental se sienta lenta en bibliotecas
grandes. **Hipótesis:** el objetivo razonable es p95 < 50 ms por pulsación; hay que medirlo
antes y después.

**Propuesta mínima**

Una sola pasada que construya todo:

```python
rows: list[int] = []
keys: list[str] = []
positions: dict[str, int] = {}
visible_groups: set[str] = set()
headers: list[tuple[QListWidgetItem, str]] = []

for row in range(self.grid.count()):
    item = self.grid.item(row)
    if item is None:
        continue
    if item.data(HEADER_ROLE):
        headers.append((item, str(item.data(GROUP_ROLE) or "")))
        continue
    photo = self.photos.get(item.data(PHOTO_ROLE))
    visible = photo is None or self._matches_filter(photo, needle, tag_filter)
    hidden = not visible
    if item.isHidden() != hidden:  # solo cambia si hace falta
        item.setHidden(hidden)
    if visible:
        key = str(item.data(PHOTO_ROLE))
        positions[key] = len(keys)
        keys.append(key)
        rows.append(row)
        visible_groups.add(str(item.data(GROUP_ROLE) or ""))

for header, group in headers:
    header.setHidden(group not in visible_groups)

self.grid.set_visible_rows(rows)
self._visible_keys_cache = keys
self._visible_positions_by_key = positions
```

`_update_group_headers` puede quedarse para otros llamadores o absorberse en este bucle.
No hace falta un modelo de datos nuevo.

**Pruebas de aceptación**

- Los tests de filtro, cabeceras, recuentos y cursor siguen pasando.
- Un test con contador comprueba que `setHidden` solo se llama cuando el valor cambia
  (p. ej. reaplicar el mismo filtro no genera llamadas).
- Benchmark: p95 de 50 pulsaciones sobre 25k, antes/después.

---

### R3 — `j`/`k` recalculan la geometría de toda la rejilla

**Prioridad:** P1 · **Esfuerzo:** M · **Riesgo:** Medio

**Evidencia**

`_visual_rows` (`grid.py:262-290`) recorre `self._visible_rows` y llama a
`visualItemRect` por cada foto. `_move_vertical` (`grid.py:372-389`) vuelve a recorrer
todas las filas visuales y calcula `_row_center_x` para cada celda. Cada `j`/`k` es, por
tanto, O(n) en el número de fotos visibles, con decenas de miles de llamadas a Qt.

**Impacto**

Navegar con `j`/`k` en 25k fotos debería ser O(1) o O(filas de la ventana). **Hipótesis:**
el coste actual puede superar los 50 ms por tecla y hacer que la navegación se sienta
pegajosa. Medir antes de cambiar.

**Propuesta mínima**

Cachear las filas visuales y sus centros por “generación de layout”:

- `_visual_rows_cache: list[list[int]] | None`;
- invalidar en `set_visible_rows`, `append_visible_rows`, `resizeEvent` y `retheme`;
- `_visual_rows()` construye una vez y reutiliza;
- `_move_vertical` usa la caché en vez de reconstruir `geometry`.

La geometría de `QListWidget` es estable mientras no cambie el ancho del viewport, el
tamaño de los ítems ni la lista de visibles; esos son exactamente los puntos de
invalidación. No hace falta cachear por scroll: los `top` son coordenadas de contenido y
el agrupado relativo no cambia al desplazarse.

**Alternativa descartada por ahora:** migrar a `QListView` + `QAbstractItemModel`. Es una
reescritura grande para un problema que se resuelve con una caché local; solo tendría
sentido si el propio `QListWidget` demuestra ser el cuello con 25k ítems.

**Pruebas de aceptación**

- Los tests de navegación vertical, grupos y filtros siguen pasando.
- Un test comprueba que dos `j` consecutivos sin cambios de layout no reconstruyen la
  caché (contador sobre `_compute_visual_rows`).
- Tras `resize`, filtro y `:sort`, la caché se invalida y el destino sigue siendo correcto.
- Benchmark: p95 de 100 `j`/`k` sobre 25k, antes/después.

---

### R4 — El panel de metadatos se reconstruye aunque esté oculto

**Prioridad:** P1 · **Esfuerzo:** XS · **Riesgo:** Bajo

**Evidencia**

`_on_current_changed` (`window.py:1409-1416`) llama siempre a `self.panel.set_photo(photo)`.
`MetadataPanel.set_photo` (`panels.py:159-257`) crea un `QWidget` nuevo, un layout, secciones,
labels y chips en cada selección. La app real arranca con `initial_panel_visible=False`
(`app.py:427`), así que cada `j`/`k` reconstruye un panel que nadie ve.

**Impacto**

Trabajo de UI y basura por cada movimiento de cursor. Es especialmente noticeable al
mantener `j`/`k` pulsado.

**Propuesta mínima**

- En `_on_current_changed`, llamar a `panel.set_photo` solo si `self.panel_visible`.
- En `toggle_panel`, al abrir, refrescar con `self.panel.set_photo(self._current_photo())`.

No hace falta actualizar etiquetas en sitio todavía; eso sería una optimización posterior
si el panel visible también se nota lento.

**Pruebas de aceptación**

- `test_hidden_panel_is_not_rebuilt_on_navigation`: espía sobre `set_photo` y navegar con
  el panel oculto.
- `test_opening_panel_shows_current_photo`: navegar oculto, abrir panel y comprobar que
  muestra la foto actual, no la primera.
- Los tests con panel visible siguen pasando.

---

### R5 — Cola del visor: sin sustitución y sin descarte temprano

**Prioridad:** P1 (hipótesis) · **Esfuerzo:** M · **Riesgo:** Medio

**Evidencia**

`_ensure_image_loaded` (`window.py:1466-1480`) arranca tareas para la foto actual y sus
vecinas, sin límite de cola ni cancelación. `_on_image_loaded` (`window.py:1482-1501`)
convierte la `QImage` a `QPixmap` **antes** de comprobar si la clave sigue siendo la actual;
una imagen obsoleta se decodifica y se convierte igualmente. El pool tiene 2 hilos
(`window.py:413-414`), así que una ráfaga de `j`/`k` puede dejar varias decodificaciones
en cola.

El patrón correcto ya existe en `ThumbnailService` (`thumbnails.py:213-315`): cola
sustituible, generación y descarte de resultados viejos.

**Impacto**

Navegación rápida por originales grandes: CPU y memoria gastadas en fotos que ya no se
ven, y latencia para la foto actual. **Hipótesis:** si el usuario navega más rápido que la
decodificación, la cola crece; medir con originales reales.

**Propuesta mínima**

Reutilizar el patrón, sin clase nueva si no hace falta:

- una cola ordenada de claves pendientes (actual + vecinas), sustituida en cada selección;
- un contador de generación en `ImageLoadTask`;
- descartar en `_on_image_loaded` antes de `QPixmap.fromImage` si la generación no coincide;
- no arrancar más de 2 tareas activas.

Si se prefiere encapsular, un `ImageService` mínimo calcado de `ThumbnailService` es
aceptable, pero no es obligatorio.

**Pruebas de aceptación**

- Con un loader falso, una ráfaga de 20 selecciones no deja más de 2 tareas activas y no
  convierte imágenes obsoletas.
- La foto actual siempre termina mostrándose.
- El test de cierre con tareas en vuelo sigue pasando.

---

### R6 — Dos `osascript` síncronos en el hilo de UI

**Prioridad:** P1 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

`app.py:626-658` ejecuta `subprocess.run(["osascript", ...])` de forma síncrona dos veces:
una antes del bucle de eventos y otra dentro de un `QTimer.singleShot(50, _focus)`. Cada
llamada puede tardar cientos de milisegundos y bloquea la UI. El código está duplicado.

**Impacto**

Retraso perceptible en el arranque y en el primer frame útil, precisamente cuando se
quiere que la ventana responda.

**Propuesta mínima**

Un helper único con `QProcess.startDetached("osascript", ["-e", ...])` (o un `QProcess`
sin `waitForFinished`), llamado una sola vez tras `show()`. No bloquea, no deja zombis y
no añade dependencias. Si el foco no es crítico, incluso puede eliminarse la segunda
llamada y quedarse solo con `raise_()`/`activateWindow()`.

**Pruebas de aceptación**

- Test con monkeypatch de `QProcess.startDetached`: no se llama a `subprocess.run` en el
  camino de arranque.
- Verificación manual en macOS: la ventana sigue activándose.

---

### R7 — `busy_timeout` de 5 s en el toggle de favorito

**Prioridad:** P1 (decisión tras medir) · **Esfuerzo:** S · **Riesgo:** Medio (ADR)

**Evidencia**

`connect_read_write` fija `PRAGMA busy_timeout = 5000` (`tags.py:156-173`), decisión
documentada en ADR 0006. `_toggle_favorite` escribe de forma síncrona en el hilo de UI
(`window.py:2038-2042`). Si el indexador mantiene el lock de escritura, la UI puede
quedarse congelada hasta 5 s.

**Impacto**

Un tirón de hasta 5 s al marcar un favorito durante la indexación. El rollback optimista
ya existe, así que fallar rápido es una alternativa aceptable.

**Propuesta mínima**

Medir primero la contención real (frecuencia y duración). Si duele:

- añadir `busy_timeout_ms` a `connect_read_write` con valor por defecto 5000;
- que el visor pase 250 ms (o 100 ms) y muestre «catálogo ocupado, reintenta»;
- actualizar ADR 0006 para reflejar la excepción interactiva.

**No hacer por ahora:** mover la escritura a un hilo. Añade carrera entre toggles,
rollback asíncrono y estados intermedios; no compensa si bajar el timeout resuelve el
problema.

**Pruebas de aceptación**

- Test con `BEGIN EXCLUSIVE` y timeout corto: el toggle vuelve en el orden de 250 ms y
  muestra error, sin bloquear 5 s.
- El test de rollback existente sigue pasando.

---

### R8 — `_ghost_counts` recorre todas las fotos aunque no falte ninguna ubicación

**Prioridad:** P2 · **Esfuerzo:** XS · **Riesgo:** Bajo

**Evidencia**

`location_banner_text` (`presentation.py:185-207`) calcula `_ghost_counts(photos)` antes
de comprobar si hay ubicaciones no disponibles. `_ghost_counts` (`presentation.py:167-177`)
recorre las 25k fotos. Se llama en cada carga (`window.py:517` y `854`). El caso normal
—todo disponible— paga una pasada O(n) para nada.

**Propuesta mínima**

```python
missing = unavailable_locations(locations)
if not missing:
    return None
ghosts = _ghost_counts(photos)
```

**Prueba de aceptación:** con todas las ubicaciones disponibles, un contador sobre
`_ghost_counts` no se incrementa y el banner no aparece.

---

### R9 — Grupos materializados antes del primer lote

**Prioridad:** P2 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

`_populate_grid` (`window.py:643-646`) construye `groups = [photo_group(photo, key) for
photo in ordered]` para las 25k fotos antes de insertar el primer lote. Con orden por
lugar, `photo_group` llama a `photo_place` y construye etiquetas. Ese coste retrasa el
primer lote navegable, que es la métrica que el ADR 0011 quiere mejorar.

**Propuesta mínima**

Calcular el grupo dentro de `_populate_range`, una vez por foto, y eliminar
`_PopulateState.groups`. El coste total es el mismo, pero se reparte entre lotes y el
primer lote llega antes. No hace falta paralelizar ni cachear grupos.

**Pruebas de aceptación**

- Los tests de grupos y cabeceras siguen pasando.
- Benchmark: tiempo hasta el primer lote antes/después (con `:sort location` incluido).

---

### R10 — `:sort` con la misma clave repuebla toda la rejilla

**Prioridad:** P2 · **Esfuerzo:** XS · **Riesgo:** Bajo

**Evidencia**

`_sort_grid` (`window.py:1740-1765`) siempre llama a `_populate_grid`, aunque
`key == self._sort_key`. Repetir `:sort date` reordena, limpia y reconstruye 25k ítems
para obtener exactamente la misma lista.

**Propuesta mínima**

Salir pronto si la clave no cambia:

```python
if key == self._sort_key:
    self.status.show_message(f"ordenado por {key}", 3000)
    return
```

**Prueba de aceptación:** un espía sobre `_populate_grid` comprueba que repetir la misma
clave no repuebla; cambiar de clave sí.

---

### R11 — Factorías `_photo` duplicadas en los tests

**Prioridad:** P2 · **Esfuerzo:** S · **Riesgo:** Bajo

**Evidencia**

Hay al menos ocho helpers `_photo` con defaults ligeramente distintos en
`test_ui_action_registry.py`, `test_ui_documented_features.py`, `test_ui_grid_groups.py`,
`test_ui_interaction.py`, `test_ui_key_inventory.py`, `test_ui_map.py`,
`test_ui_progressive.py`, `test_ui_search.py` y `test_ui_viewer_layout.py`.
`test_ui_progressive.py:36-54` ya tiene la forma más útil (`**overrides`).

**Propuesta mínima**

Mover esa factoría a `tests/ui_factories.py` (o a `conftest.py`) como
`make_photo(name, taken, **overrides)` y sustituir los helpers duplicados de forma
incremental. No toca código de aplicación; reduce ruido y evita que cada test invente
campos distintos.

**Prueba de aceptación:** la suite sigue pasando sin cambios de comportamiento.

---

## 5. Hipótesis que requieren medición

No se deben convertir en cambios estructurales sin datos. Protocolo propuesto: 25k fotos,
tres repeticiones, reportar p50/p95 (no solo el mínimo), antes y después.

| Hipótesis | Cómo medir | Criterio propuesto |
| --- | --- | --- |
| `j`/`k` O(n) duele | 100 pulsaciones con `QTest.keyClick`, medir evento → selección | p95 < 50 ms |
| Filtro de 3 pasadas duele | 50 actualizaciones de filtro con consultas de 1–3 letras | p95 < 50 ms |
| Panel oculto reconstruido duele | 100 selecciones con panel oculto, perfil de `set_photo` | coste < 2 ms por selección o eliminarlo |
| Cola del visor crece | ráfaga de 20 `j`/`k` con originales; contar tareas activas y pico de RSS | ≤ 2 tareas activas, sin crecimiento sostenido |
| `busy_timeout` bloquea | toggle con `BEGIN EXCLUSIVE`; medir tiempo de retorno y frecuencia en indexación | p95 < 100 ms; si no, bajar timeout |
| Primer lote < 1 s | benchmark actual: 3,4 s sintético / 4,6 s real | **no cumplido**; perfilar carga vs. sort/grupos vs. primer lote |
| Arranque en frío | el benchmark actual es caliente (catálogo recién creado, imports hechos) | medir proceso CLI tras reinicio/desmontaje, documentar condición |

**Sobre el objetivo de ~1 s:** `prompts/rendimiento-arranque-ui-25k.md` lo fija para
«rejilla navegable», y las cifras registradas son ~3,4 s (sintético) y ~4,6 s (real). El
ADR 0011 y el checklist no deben leerse como si el objetivo estuviera cumplido: la ventana
aparece en ~50 ms, pero la primera foto navegable tarda segundos. La diferencia entre
«primer píxel» y «primer lote navegable» es la que importa al usuario.

---

## 6. Benchmarks: qué falta y cómo cerrarlo

`scripts/bench_arranque.py` mide bien el pipeline interno, pero sus etiquetas y métricas
se quedan cortas:

1. **«Primer píxel» no es un píxel.** `window.show(); app.processEvents()` en plataforma
   `offscreen` mide “ventana mostrada y eventos procesados”, no un `paintEvent` real.
   Renombrar a «ventana mostrada» o medir con `QTest.qWaitForWindowExposed` en la
   plataforma real y un event filter de `QEvent.Paint`.
2. **No es arranque en frío.** El catálogo se acaba de crear en el mismo proceso y los
   imports ya están hechos; la página está caliente. Documentarlo o medir el proceso CLI
   por separado tras reiniciar/desmontar el volumen.
3. **Solo mínimo y mediana.** `_linea` (`scripts/bench_arranque.py:271-273`) reporta
   mínimo y mediana. Para latencia percibida importan p95 y máximo; añadirlos.
4. **No mide interacción.** No hay `j`/`k`, filtro incremental, apertura del visor ni
   aceptación de teclas. Es justo donde están R2/R3/R5.
5. **No mide memoria.** Añadir RSS antes/después del populate, tras recorrer 500 fotos y
   tras abrir/cerrar el visor. En macOS, `resource.getrusage(RUSAGE_SELF).ru_maxrss`
   (bytes) es suficiente para una tendencia.
6. **No hay deadlines.** Un benchmark sin umbral no dice si vamos bien. Añadir una tabla
   PASS/FAIL con los criterios de la sección 5.

**Propuesta concreta:** un modo `--interaccion` en el script existente (no un framework
nuevo) que, tras el populate completo:

- pulse 100 `j`/`k` y reporte p50/p95/máx;
- aplique 50 filtros y reporte p50/p95/máx;
- abra el visor, recorra 20 fotos y cuente tareas activas y pico de RSS;
- imprima una tabla de umbrales con `OK`/`FALLA`.

Mantener el modo actual para el pipeline de arranque. No hace falta pytest para esto.

---

## 7. Plan recomendado

### Fase 1 — Corrección (P0)

1. A1: helper único de actualización de foto + consumo del `bool` persistido + 3 tests.
2. A2: capturar `sqlite3.Error`/excepciones en el worker, terminar el hilo y probar el
   fallo.
3. A3: `_filter_text` como única fuente del filtro efectivo + test de búsqueda viva.
4. A4: `?` con ubicaciones abierto + test.
5. A5: parar timers, guard `_closing`, registrar esperas + tests de cierre.

**Salida esperada:** ningún estado divergente conocido, ningún cuelgue silencioso en
«Cargando…», cierre limpio en todas las vías.

### Fase 2 — Latencia con cambios pequeños (P1)

1. R1: eliminar el doble populate.
2. R4: no reconstruir el panel oculto; refrescar al abrirlo.
3. R6: `QProcess` asíncrono y una sola llamada.
4. R2: filtro en una pasada + `setHidden` solo si cambia.
5. R3: caché de filas visuales con invalidación explícita.

**Salida esperada:** benchmark de interacción con p95 dentro de umbral; si R2/R3 no
mejoran lo suficiente, revisar antes de añadir más complejidad.

### Fase 3 — Medir y decidir (P1/P2)

1. R5: solo si la ráfaga de navegación demuestra cola/CPU excesiva.
2. R7: solo si la contención del lock es frecuente; si no, dejar el ADR como está.
3. R8/R9/R10/R11: limpieza barata, sin cambio de contrato.
4. Actualizar ADR 0011 con cifras reales y estado del objetivo de ~1 s; actualizar ADR
   0006 solo si R7 cambia el timeout.

---

## 8. Qué no refactorizar ahora

- **No migrar `QListWidget` a `QListView` + `QAbstractItemModel`.** Es la reescritura más
  cara del mapa y no hay evidencia de que el widget sea el cuello. Primero R2/R3 y medir.
- **No añadir capas MVC, casos de uso, gestores de estado, buses de eventos ni DI.**
  `window.py` es un orquestador grande, pero sus responsabilidades son concretas; el
  problema es la duplicación de proyecciones, no la falta de capas.
- **No tocar la presentación pura.** `presentation.py` sin Qt, con funciones pequeñas y
  tests, es una de las mejores decisiones del proyecto.
- **No tocar el registro central de acciones/keybindings.** A4 es un caso mal ordenado,
  no un motivo para rediseñarlo.
- **No reescribir `ThumbnailService`.** Es el patrón de referencia: cola sustituible,
  generación, límite de tareas y cierre acotado. R5 debe copiarlo, no inventar otro.
- **No añadir dependencias** (pyobjc, numpy, frameworks async) para ahorrar unas líneas.
- **No paralelizar más la decodificación** ni subir el número de hilos sin medir: 2 hilos
  para originales y 2–4 para miniaturas es una decisión razonable.
- **No hacer asíncrona la escritura de favoritos.** El rollback optimista ya existe;
  bajar el timeout (si se mide) es mucho más simple.
- **No paginar la rejilla.** El populate por lotes es suficiente y el ADR 0011 ya lo
  descartó con motivo.

---

## 9. Anexo: evidencia principal

| Tema | Fichero:línea |
| --- | --- |
| Doble populate | `src/foto/ui/window.py:525`, `554-559` |
| Favorito: actualizaciones y rollback | `src/foto/ui/window.py:2018-2053` |
| `toggle_favorite` devuelve el estado persistido | `src/foto/storage/tags.py:237-269` |
| Panel reconstruido en cada selección | `src/foto/ui/window.py:1414`, `src/foto/ui/panels.py:159-257` |
| Filtro en tres pasadas | `src/foto/ui/window.py:1588-1630`, `793-820`, `1105-1120` |
| Búsqueda viva vs. confirmada | `src/foto/ui/window.py:537`, `671`, `758`, `1632-1635` |
| Overlay de ubicaciones y `help` | `src/foto/ui/window.py:1841-1848` |
| Navegación O(n) | `src/foto/ui/grid.py:262-290`, `372-389` |
| Worker: excepciones y ciclo de vida | `src/foto/ui/app.py:379-395`, `447-463`, `481-492` |
| `osascript` síncrono | `src/foto/ui/app.py:626-658` |
| Cola del visor | `src/foto/ui/window.py:1466-1501`, `src/foto/ui/canvas.py:58-82` |
| Patrón de cola acotada | `src/foto/ui/thumbnails.py:213-315` |
| `busy_timeout` | `src/foto/storage/tags.py:156-173` |
| Banner y recuento de fantasmas | `src/foto/ui/presentation.py:167-207` |
| Grupos antes del primer lote | `src/foto/ui/window.py:643-646` |
| `:sort` repetido | `src/foto/ui/window.py:1740-1765` |
| Apagado y timers | `src/foto/ui/window.py:409-425`, `2055-2067` |
| Test de rollback incompleto | `tests/test_ui_tags.py:334-364` |
| Test de búsqueda a medio populate | `tests/test_ui_progressive.py:168-195` |
| Benchmark: primer píxel y métricas | `scripts/bench_arranque.py:220-250`, `271-273` |
| Objetivo y cifras históricas | `prompts/rendimiento-arranque-ui-25k.md:456-478` |
| Decisión de arranque progresivo | `docs/adr/0011-arranque-progresivo.md` |
| Timeout y escritura de etiquetas | `docs/adr/0006-etiquetas.md:285`, `402` |
