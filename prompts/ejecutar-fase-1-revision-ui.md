# Prompt — Fase 1 (P0) de la revisión de UI: correcciones

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> el alcance. La misión no es decidir ni rediseñar: es **ejecutar la fase 1
> del plan recomendado** en
> [`prompts/revision-ui-arquitectura-rendimiento.md`](revision-ui-arquitectura-rendimiento.md)
> (§ 7), es decir, cerrar los cinco defectos P0 (**A1–A5**) con el código más
> simple posible y tests de regresión.
>
> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada
> una: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si aparece una pendiente nueva, añádela al final de la fase con el mismo
> formato. Una tarea solo está cerrada con sus tests en verde y
> `uv run pytest` + `uv run ruff check .` completos.

## 0. Misión

Corregir los cinco hallazgos P0 de la revisión, sin tocar las mejoras de
latencia P1/P2 (R1–R11) ni abrir refactorizaciones estructurales:

| Tarea | Hallazgo | Resultado esperado |
| --- | --- | --- |
| T1.1 | A1 — favorito con proyecciones divergentes | Una sola ruta actualiza todas las proyecciones; el rollback revierte todo; la UI sigue el estado persistido |
| T1.2 | A2 — worker con excepción no capturada | Cualquier fallo de carga llega por `failed`; el `QThread` termina en éxito y en fallo; las esperas se registran |
| T1.3 | A3 — búsqueda viva vs. confirmada | `_filter_text` es la única fuente del filtro efectivo; la búsqueda viva sobrevive al populate y cancelar restaura la confirmada |
| T1.4 | A4 — `?` secuestrado por ubicaciones | Con el panel de ubicaciones abierto, `?` lo cierra y abre la ayuda; Esc solo cierra |
| T1.5 | A5 — apagado con timers y esperas a ciegas | El cierre detiene `_visible_timer` y `_g_timer`, bloquea trabajo tardío y registra esperas agotadas |
| T1.6 | Verificación y cierre | Suite completa y ruff en verde; diff limitado a lo previsto; notas de desviación registradas |

**Criterio de éxito de la fase:** ningún estado divergente conocido, ningún
cuelgue silencioso en «Cargando…», cierre limpio por `Ctrl+q`, `:q`, ⌘Q y
`closeEvent`, y ninguna regresión en la suite.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, convenciones, comandos de verificación
   y prohibición de `git commit`/`push` sin permiso explícito.
2. `prompts/revision-ui-arquitectura-rendimiento.md` — en especial § 1
   (resumen), § 3 (criterios de diseño), § 4 (A1–A5 con evidencia y pruebas
   de aceptación), § 7 (fase 1) y § 8 (qué no refactorizar).
3. `docs/adr/0011-arranque-progresivo.md` — arranque progresivo, worker de
   carga, populate por lotes y cierre limpio.
4. `docs/adr/0006-etiquetas.md` — favoritos, escritura RW, rollback optimista
   y `busy_timeout` (solo como contexto; R7 queda fuera).
5. Código real, localizando por **nombre de función** (las líneas del informe
   son una foto del 2026-11-21 y pueden haberse desplazado):
   - `src/foto/ui/window.py`: `__init__` (estado de búsqueda, timers,
     `_loader_pool`), `_populate_range`, `_finish_populate`,
     `_cancel_populate`, `_apply_filter`, `_matches_filter`,
     `_commit_search`, `_cancel_prompt`, `_handle_escape`, `_set_tag_filter`,
     `_sort_grid`, `_dispatch`, `_on_current_changed`, `toggle_panel`,
     `_toggle_favorite`, `shutdown_background_work`, `closeEvent`,
     `_schedule_visible_thumbnails`, `_request_visible_thumbnails`,
     `_clear_g_pending`.
   - `src/foto/ui/app.py`: `LibraryLoadWorker.load`, `_start_library_load`,
     `_on_library_loaded`, `_on_library_failed`,
     `_shutdown_background_work`, `main` (solo lectura; R6 queda fuera).
   - `src/foto/storage/tags.py`: `toggle_favorite` (devuelve el estado
     persistido y lanza `CatalogError`).
   - `src/foto/ui/panels.py`: `MetadataPanel.set_photo`.
   - `src/foto/ui/presentation.py`: `with_favorite_tag`,
     `photo_search_text`, `photo_accessible_name`.
6. Tests de referencia:
   - `tests/test_ui_tags.py` (favoritos, panel, rollback).
   - `tests/test_ui_progressive.py` (worker, populate por lotes, cierre).
   - `tests/test_ui_locations.py` (overlay de ubicaciones).
   - `tests/test_ui_search.py` (filtro y recuentos).
   - `tests/test_ui_documented_features.py` (contrato de teclas
     documentadas).
   - `tests/conftest.py` (plataforma `offscreen` y utilidades).

## 2. Estado de partida y línea base

Antes de cambiar nada:

1. `git status --short` y `git diff --stat`: identifica el trabajo sin
   commit. **No lo reviertas, no lo commitees.**
2. `uv run pytest` y `uv run ruff check .`: anota el número de tests y el
   estado. Si la línea base ya está roja, **para y repórtalo** antes de
   atribuir el fallo a esta fase.
3. Ejecuta también los tests de UI afectados para tener una referencia
   rápida:
   `uv run pytest tests/test_ui_tags.py tests/test_ui_progressive.py tests/test_ui_locations.py tests/test_ui_search.py`.

## 3. Reglas de trabajo (no negociables)

1. **Solo A1–A5.** R1–R11, benchmarks y ADRs quedan fuera. Si una corrección
   P0 obliga a tocar una línea de una mejora P1, hazlo solo si es
   imprescindible y regístralo como nota.
2. **Menos código, no más capas.** Nada de controladores, gestores de estado,
   buses de eventos, modelos observables ni inyección de dependencias.
3. **Sin dependencias nuevas.** Qt, `sqlite3`, `logging` y la biblioteca
   estándar bastan.
4. **Conservar lo que funciona:** presentación pura sin Qt, registro central
   de acciones/keybindings, miniaturas perezosas, caches acotadas, populate
   por lotes y patrón de cola de `ThumbnailService`.
5. **Tests primero o a la vez que el cambio.** Extiende los tests indicados;
   no los debilites para que pasen. Si un test existente falla porque el
   contrato cambia de verdad, explica por qué en la nota de la tarea.
6. **Una sola fuente de verdad por dato.** Si una `Photo` cambia, todas sus
   proyecciones se actualizan desde un único helper. Si el filtro cambia, hay
   un único texto efectivo.
7. **Errores explícitos.** Nada de `except: pass`; en el límite de un hilo de
   fondo, convertir la excepción en señal y registrarla.
8. **Estilo del proyecto:** mensajes de UI en español, docstrings breves en
   español, `pathlib`, type hints y ruff limpio.
9. **Sin `git commit`/`push`** sin permiso explícito.

## 4. Decisiones ya tomadas (no se discuten)

1. **A1:** un método privado `_apply_photo_update(photo)` aplica la foto a
   `photos`, `_search_index`, ítem de rejilla, panel (si visible), lienzo (si
   es la actual) y filtro (si hay filtro activo). No se convierte `Photo` en
   observable ni se añaden señales por campo.
2. **A1:** se consume el `bool` que devuelve `toggle_favorite`; si difiere
   del estado optimista, la UI se corrige con `with_favorite_tag(photo,
   persisted)`. El rollback usa el mismo helper.
3. **A2:** en `LibraryLoadWorker.load` se captura `sqlite3.Error`
   explícitamente y, en el límite del worker, cualquier `Exception` con
   `logger.exception`; ambos emiten `failed` salvo cancelación. El hilo se
   termina tras `loaded`/`failed` y las esperas acotadas registran si
   expiran.
4. **A3:** `self._filter_text` es el texto efectivo del filtro (vivo o
   confirmado). `self.search_text` sigue siendo **solo** la consulta
   confirmada. No se añade una señal nueva ni se consulta el widget del
   prompt.
5. **A4:** `help` con ubicaciones abierto cierra ubicaciones y **continúa**
   al handler de ayuda; `escape` y `close` solo cierran. No se introduce una
   pila de overlays.
6. **A5:** `_closing` marca el apagado; los timers se detienen y el trabajo
   que llegue tarde sale por un guard. Las esperas (`waitForDone`,
   `thread.wait`) se comprueban y se registran con `logger.warning`.
7. **No** migrar `QListWidget` a `QListView`/`QAbstractItemModel`, **no**
   hacer asíncrona la escritura de favoritos, **no** reescribir
   `ThumbnailService` y **no** tocar el registro de acciones.

---

## 5. Tareas

### T1.0 — Línea base y reproducción (obligatoria)

- [x] Ejecutar la verificación de § 2 y anotar aquí el estado inicial
  (número de tests, fallos preexistentes, `git status`).
  > Nota (2026-11-22): línea base en verde: 991 tests pasan, `uv run ruff
  > check .` limpio. `git status --short` solo mostraba este prompt sin
  > seguimiento; el árbol de la aplicación estaba limpio.
- [x] Reproducir, al menos por lectura y con un test cuando sea barato, los
  cinco hallazgos A1–A5. Si alguno no se reproduce en el código actual,
  **no lo inventes**: documenta la evidencia y la desviación antes de
  continuar.
  > Nota (2026-11-22): A1–A5 se reprodujeron por lectura y quedaron cubiertos
  > por los tests de regresión nuevos o extendidos; no hubo desviaciones de
  > alcance.

**Criterio de cierre:** línea base registrada y alcance confirmado.

---

### T1.1 — A1: favorito con una sola ruta de actualización

- [x] **Helper único.** Añadir `_apply_photo_update(self, photo: Photo)` cerca
  de `_toggle_favorite`, con esta responsabilidad:

  ```python
  def _apply_photo_update(self, photo: Photo) -> None:
      """Aplica una ``Photo`` a todas sus proyecciones (A1)."""
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
      if self._filter_text or self._tag_filter:  # ver nota de orden
          self._apply_filter(self._filter_text)
      else:
          self.grid.viewport().update()
  ```

  > **Nota de orden:** si T1.1 se ejecuta antes de T1.3, `_filter_text` aún
  > no existe. Usa `self.search_text` en esas dos líneas y T1.3 las cambia a
  > `_filter_text`. No dupliques el helper ni dejes una segunda ruta.

- [x] **Consumir el estado persistido.** `_toggle_favorite` queda, en esencia:

  ```python
  updated = with_favorite_tag(photo, not photo.favorite)
  self._apply_photo_update(updated)
  if self.tag_connection is None or photo.photo_hash is None:
      self.status.show_message(message(updated), 2000)
      return
  try:
      persisted = toggle_favorite(
          self.tag_connection, photo.photo_hash, now=datetime.now(UTC).isoformat()
      )
  except CatalogError as exc:
      self._apply_photo_update(photo)  # rollback completo, una sola ruta
      self.status.show_error(str(exc))
      return
  final = updated
  if persisted != updated.favorite:
      final = with_favorite_tag(photo, persisted)
      self._apply_photo_update(final)
  self.status.show_message(message(final), 2000)
  ```

  El mensaje («favorito» / «sin favorito») debe reflejar el estado final, no
  el optimista. El rollback ya no repite listas de campos.

- [x] **Panel oculto.** En `toggle_panel`, al abrir, refrescar el contenido con
  la foto actual: `self.panel.set_photo(self._current_photo())`. Es necesario
  para cerrar A1 (el panel oculto no se actualiza en cada selección) y no
  contradice R4: R4 añadirá no reconstruirlo durante la navegación.

- [x] **Tests** en `tests/test_ui_tags.py`:
  1. `test_favorite_updates_all_projections`: con panel visible y un filtro
     de búsqueda activo que dependa de la etiqueta (p. ej. `favorita`),
     alternar favorito y comprobar `photos`, `_search_index`,
     `FAVORITE_ROLE`, `viewer.canvas`, chips del panel y conjunto visible.
  2. Extender `test_favorite_write_failure_reverts_and_reports` para
     comprobar también `_search_index`, chips del panel y conjunto visible,
     no solo `photos`/ítem/lienzo.
  3. `test_favorite_follows_persisted_state`: cargar la ventana con
     `a.jpg` favorita, borrar la asignación `fav` por fuera de la UI, alternar
     y comprobar que la UI termina en el estado que devolvió
     `toggle_favorite` (favorita), no en el optimista.
  4. `test_favorite_with_hidden_panel_refreshes_on_open`: alternar con el
     panel oculto, abrir el panel y comprobar que el chip muestra el estado
     nuevo.

  > Nota (2026-11-22): T1.1 y T1.3 se ejecutaron juntas, así que el helper usa
  > `_filter_text` desde el principio. `test_favorite_updates_all_projections`
  > comprueba las proyecciones en dos fases (sin filtro para el panel y con
  > filtro `fav` para el conjunto visible) porque, al desmarcar la única
  > coincidencia, el panel pasa a «Sin coincidencias».

**Criterio de cierre:** no queda ninguna actualización manual de favorito
fuera del helper; los tres caminos (éxito, divergencia y rollback) actualizan
las mismas proyecciones; tests verdes.

---

### T1.2 — A2: el worker nunca deja la ventana en «Cargando…»

- [x] **Captura en `LibraryLoadWorker.load`.** Mantener
  `LibraryLoadCancelled` como salida silenciosa y añadir, en este orden:

  ```python
  except sqlite3.Error as exc:
      if not self._cancelled:
          self.failed.emit(str(exc))
      return
  except (OSError, RuntimeError) as exc:
      if not self._cancelled:
          self.failed.emit(str(exc))
      return
  except Exception as exc:  # límite del worker: ninguna excepción escapa
      logger.exception("fallo inesperado al cargar el catálogo")
      if not self._cancelled:
          self.failed.emit(f"Fallo inesperado al cargar el catálogo: {exc}")
      return
  ```

  Añadir `import sqlite3` si falta. No capturar `BaseException`
  (`KeyboardInterrupt`/`SystemExit` deben seguir su curso).

- [x] **Ciclo de vida del hilo.** En `_start_library_load`, conectar también
  `self._loader.loaded` y `self._loader.failed` a `self._loader_thread.quit`
  (además de sus handlers). `quit()` es seguro e idempotente. El camino
  feliz deja de mantener un `QThread` vivo hasta el apagado.

- [x] **Esperas comprobadas.** En `MainWindow._shutdown_background_work`,
  registrar el resultado:

  ```python
  thread.quit()
  if not thread.wait(10_000):
      logger.warning("el worker de carga no terminó en 10 s")
  ```

  No cambies el límite salvo que un test demuestre que es insuficiente.

- [x] **Tests** en `tests/test_ui_progressive.py` (patrón de los tests de
  worker existentes):
  1. `test_worker_reports_sqlite_errors`: `load_library` lanza
     `sqlite3.OperationalError("database is locked")`; comprobar que se emite
     `failed` con el mensaje y no `loaded`.
  2. `test_worker_reports_unexpected_errors`: `load_library` lanza
     `ValueError("boom")`; comprobar `failed` con un mensaje claro y que la
     excepción no escapa.
  3. `test_worker_thread_stops_after_success`: con `MainWindow` y un catálogo
     sembrado, esperar a que la biblioteca cargue y comprobar
     `not window._loader_thread.isRunning()`.
  4. `test_worker_thread_stops_after_failure`: con `MainWindow`, parchear
     `load_library` para lanzar, parchear `QMessageBox.critical` y
     `QApplication.exit` como en
     `test_main_window_reports_a_missing_catalog_after_the_show`, y
     comprobar que el hilo termina.
  5. Los tests de cierre a mitad de carga
     (`test_main_window_close_mid_load_cancels_cleanly`,
     `test_quit_mid_load_cancels_the_worker_about_to_quit`) siguen pasando.

**Criterio de cierre:** ningún fallo del worker deja la UI en «Cargando…»;
el hilo termina en éxito, fallo y cancelación; las esperas agotadas dejan
rastro.

---

### T1.3 — A3: `_filter_text` como única fuente del filtro efectivo

- [x] **Estado.** En `__init__`, junto a `self.search_text = ""`, añadir
  `self._filter_text = ""`. En `set_photos`, donde se resetea
  `self.search_text = ""`, resetear también `self._filter_text = ""`.

- [x] **Punto único.** Al inicio de `_apply_filter(text)`:
  `self._filter_text = text`; después, `needle = text.strip().casefold()`.
  El parámetro sigue siendo la fuente de la llamada, pero el estado queda
  registrado para los caminos que no son el filtro inmediato.

- [x] **Leer el texto efectivo** en:
  - `_populate_range`: `needle = self._filter_text.strip().casefold()` (cada
    lote usa el filtro vigente, incluida la búsqueda viva escrita entre
    lotes).
  - `_finish_populate`: `self._apply_filter(self._filter_text)` (el último
    lote ya no descarta la consulta viva).
  - `_sort_grid` → `_after_sort`: `self._apply_filter(self._filter_text)`.
  - `_set_tag_filter`: `self._apply_filter(self._filter_text)`.

- [x] **Resetear por los caminos de cancelación/confirmación**, que siguen
  pasando explícitamente el texto confirmado o vacío:
  - `_commit_search`: `self._apply_filter(self.search_text)`.
  - `_cancel_prompt("search")`: `self._apply_filter(self.search_text)`.
  - `_handle_escape` al limpiar la búsqueda: `self._apply_filter("")`.
  - Cualquier otro llamador de `_apply_filter` que represente «volver al
    filtro confirmado» debe pasar `self.search_text`, no `_filter_text`.

- [x] **No cambiar el significado de `search_text`.** Sigue siendo la consulta
  confirmada con Enter; la usa el prompt como texto inicial y la barra para
  el mensaje de «búsqueda limpiada». No la actualices desde el texto vivo.

- [x] **Tests**:
  1. En `tests/test_ui_progressive.py`,
     `test_live_search_survives_populate`: con lotes de 2, esperar a que haya
     al menos 2 ítems, llamar **solo** a `window._apply_filter("a.jpg")` (sin
     tocar `window.search_text`), bombear hasta el final y comprobar que los
     visibles siguen siendo los de la consulta viva y que
     `window.search_text == ""`.
  2. `test_cancel_search_restores_committed_filter`: confirmar `a.jpg`, aplicar
     en vivo `b.jpg`, cancelar con `_cancel_prompt("search")` y comprobar que
     vuelve el conjunto de `a.jpg` y que `_filter_text == "a.jpg"`.
  3. `test_sort_mid_populate_keeps_live_filter`: a medio populate, aplicar un
     filtro vivo, ejecutar `_sort_grid("camera")` y comprobar que al terminar
     siguen visibles solo las fotos de la consulta viva.
  4. `test_tag_filter_mid_populate_keeps_live_filter`: análogo con
     `_set_tag_filter("fav")` sobre fotos con favorito, si el fixture lo
     permite; si no, cubrirlo con una ventana pequeña y etiquetas.
  5. El test existente `test_search_works_mid_populate` sigue pasando (fija el
     camino de la consulta confirmada).

**Criterio de cierre:** no queda ninguna lectura de `search_text` para decidir
la visibilidad durante el populate ni al terminar; escribir sin confirmar ya
no parpadea ni se pierde al llegar el último lote.

---

### T1.4 — A4: `?` abre la ayuda con ubicaciones abierto

- [x] **Repartidor.** En `_dispatch`, separar «cerrar» de «abrir ayuda»:

  ```python
  if self.locations_overlay.isVisible():
      if action in ("escape", "close"):
          self._toggle_locations()
          return
      if action != "help":
          return
      self._toggle_locations()  # cierra ubicaciones y sigue al handler de ayuda
  ```

  Después, el flujo continúa hacia el handler de `help` (que abre la ayuda).
  `escape` y `close` conservan el comportamiento actual: cierran ubicaciones y
  salen. No añadas una pila de overlays.

- [x] **Tests** en `tests/test_ui_locations.py`:
  1. `test_help_opens_over_locations`: abrir ubicaciones con
     `_run_command("locations")`, despachar `help` y comprobar que la ayuda
     está visible y ubicaciones cerrado.
  2. `test_escape_closes_locations_without_opening_help`: abrir ubicaciones,
     despachar `escape` y comprobar que se cierran y la ayuda sigue oculta.
  3. El test de contrato de teclas documentadas
     (`tests/test_ui_documented_features.py`) sigue pasando: `?` es «abrir
     ayuda» en todas las situaciones documentadas.

**Criterio de cierre:** `?` ya no se pierde con el overlay de ubicaciones;
Esc no abre nada por accidente.

---

### T1.5 — A5: apagado sin timers vivos ni esperas a ciegas

- [x] **Estado de cierre.** En `__init__`, `self._closing = False`. En
  `shutdown_background_work`, ponerlo a `True` **antes** de parar nada. No lo
  reseteas: el apagado es terminal.

- [x] **Parar todo.** `shutdown_background_work` debe detener, como mínimo:

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

  Añadir `import logging` y `logger = logging.getLogger(__name__)` en
  `window.py` si no existen. Mantener la idempotencia: llamarlo dos veces no
  debe fallar ni reactivar nada.

- [x] **Guards de trabajo tardío.** Salir pronto si `self._closing` al inicio
  de:
  - `_populate_next_batch`;
  - `_schedule_visible_thumbnails` (evita reactivar `_visible_timer` después
    del apagado);
  - `_request_visible_thumbnails`;
  - `_clear_g_pending`.

  No hace falta un guard en cada slot: estos son los que pueden rearrancar
  trabajo o tocar la rejilla tras el cierre.

- [x] **MainWindow.** En `_shutdown_background_work`, comprobar el resultado
  de `thread.wait(10_000)` y dejar un `logger.warning` si expira (además de
  lo indicado en T1.2). Mantener el orden actual: cancelar worker, parar
  miniaturas y llamar a `super().shutdown_background_work()`.

- [x] **Tests** en `tests/test_ui_progressive.py`:
  1. Extender `test_close_mid_populate_leaves_no_live_timers` para comprobar
     también `not window._visible_timer.isActive()` y
     `not window._g_timer.isActive()`.
  2. `test_shutdown_is_idempotent` (añadirlo si no existe): llamar dos veces
     a `shutdown_background_work` y comprobar que no lanza, que los timers
     siguen parados y que `_closing` es `True`.
  3. `test_late_populate_batch_after_shutdown_is_ignored`: con `_closing`
     activo, invocar `_populate_next_batch`, `_schedule_visible_thumbnails`,
     `_request_visible_thumbnails` y `_clear_g_pending`; comprobar que no
     cambian la rejilla, no reactivan timers y no lanzan.
  4. Cerrar con el visor abierto y una decodificación en curso no debe
     producir avisos de Qt ni tracebacks (puede ser una comprobación manual
     documentada si el test es inestable en `offscreen`).

  > Nota (2026-11-22): el punto 4 quedó como comprobación manual no ejecutada
  > (el agente no tiene entorno gráfico). La ruta subyacente ya está cubierta
  > por `test_image_task_discards_the_result_when_the_source_is_gone` y
  > `test_quit_shuts_the_thumbnail_service_down_about_to_quit`.

**Criterio de cierre:** `Ctrl+q`, `:q`, ⌘Q y `closeEvent` dejan el mismo
estado: sin timers activos, sin populate en curso, sin tareas del visor
pendientes y con las esperas agotadas registradas.

---

### T1.6 — Verificación y cierre de la fase

- [x] `uv run pytest` completo en verde.
- [x] `uv run ruff check .` limpio.
- [x] Revisar `git diff --stat` y el diff completo: solo deben aparecer los
  ficheros de implementación y tests previstos; ninguna mejora R1–R11
  colada de rondón.
- [x] Smoke manual en macOS si hay entorno gráfico:
  - arrancar `uv run foto` sobre una biblioteca indexada;
  - marcar/desmarcar favorito con el panel visible y oculto;
  - abrir ubicaciones (`:locations`) y pulsar `?`;
  - cerrar con `Ctrl+q`, `:q` y ⌘Q;
  - forzar un fallo de carga (catálogo ilegible o parche temporal) y
    comprobar que aparece un error, no «Cargando…» eterno.
  > Nota (2026-11-22): smoke manual no ejecutado: el agente trabaja sin
  > entorno gráfico. Los cinco recorridos tienen cobertura automática en
  > `offscreen` (favoritos con panel visible/oculto, ayuda sobre ubicaciones,
  > cierre en todas las vías y fallo de carga del worker).
- [x] Actualizar este fichero: marcar T1.0–T1.6 con fecha, añadir notas de
  desviación y un párrafo de estado final.
- [x] No hacer commit ni push sin permiso explícito.

**Criterio de cierre:** suite y ruff en verde, diff acotado, smoke sin
sorpresas y registro de la fase completo.

---

## 6. Tabla de aceptación global

| ID | Comportamiento | Test de referencia |
| --- | --- | --- |
| A1 | Favorito actualiza todas las proyecciones | `test_favorite_updates_all_projections` |
| A1 | Fallo de escritura revierte todas las proyecciones | `test_favorite_write_failure_reverts_and_reports` (extendido) |
| A1 | La UI sigue el estado persistido | `test_favorite_follows_persisted_state` |
| A1 | Panel oculto se refresca al abrir | `test_favorite_with_hidden_panel_refreshes_on_open` |
| A2 | `sqlite3.Error` llega por `failed` | `test_worker_reports_sqlite_errors` |
| A2 | Excepción inesperada llega por `failed` | `test_worker_reports_unexpected_errors` |
| A2 | El hilo termina tras éxito y tras fallo | `test_worker_thread_stops_after_success` / `_failure` |
| A3 | La búsqueda viva sobrevive al populate | `test_live_search_survives_populate` |
| A3 | Cancelar restaura la consulta confirmada | `test_cancel_search_restores_committed_filter` |
| A3 | `:sort`/`:tag` a medio populate conservan la viva | `test_sort_mid_populate_keeps_live_filter` / `test_tag_filter_mid_populate_keeps_live_filter` |
| A4 | `?` abre ayuda sobre ubicaciones | `test_help_opens_over_locations` |
| A4 | Esc solo cierra ubicaciones | `test_escape_closes_locations_without_opening_help` |
| A5 | El cierre para `_visible_timer` y `_g_timer` | `test_close_mid_populate_leaves_no_live_timers` (extendido) |
| A5 | El apagado es idempotente | `test_shutdown_is_idempotent` |
| A5 | El trabajo tardío se ignora | `test_late_populate_batch_after_shutdown_is_ignored` |

## 7. Mapa de cambios previsto

| Fichero | Zona | Tarea |
| --- | --- | --- |
| `src/foto/ui/window.py` | `_apply_photo_update`, `_toggle_favorite`, `toggle_panel` | T1.1 |
| `src/foto/ui/app.py` | `LibraryLoadWorker.load`, `_start_library_load`, `_shutdown_background_work` | T1.2 |
| `src/foto/ui/window.py` | `__init__`, `set_photos`, `_apply_filter`, `_populate_range`, `_finish_populate`, `_sort_grid`, `_set_tag_filter` | T1.3 |
| `src/foto/ui/window.py` | `_dispatch` | T1.4 |
| `src/foto/ui/window.py` | `__init__`, `shutdown_background_work`, guards de populate/miniaturas | T1.5 |
| `src/foto/ui/app.py` | `_shutdown_background_work` (log de `thread.wait`) | T1.5 |
| `tests/test_ui_tags.py` | favoritos y panel | T1.1 |
| `tests/test_ui_progressive.py` | worker, búsqueda viva, cierre | T1.2, T1.3, T1.5 |
| `tests/test_ui_locations.py` | ayuda sobre ubicaciones | T1.4 |

## 8. Fuera del alcance de esta fase

- **R1–R11 completos**: doble populate, filtro en una pasada, caché de filas
  visuales, panel oculto durante la navegación, cola del visor, `osascript`
  asíncrono, `busy_timeout`, `_ghost_counts`, grupos antes del primer lote,
  `:sort` repetido y factorías de tests. La fase 2/3 los abordará con sus
  propias mediciones.
- **Benchmarks nuevos** (`--interaccion`, p95, RSS) y actualización de cifras
  de arranque: son parte de la fase 3, no de esta corrección.
- **Migrar `QListWidget` a `QListView`**, añadir MVC/casos de uso/gestores de
  estado/DI o dependencias nuevas.
- **Escritura asíncrona de favoritos**: el rollback optimista se mantiene.
- **Cambiar el contrato de teclas documentadas** o el registro central de
  acciones: A4 es un caso mal ordenado, no un rediseño.
- **Reescribir `ThumbnailService`** o el populate por lotes.
- **Actualizar ADRs** salvo que una desviación obligue a una nota mínima; en
  ese caso, regístrala también en este prompt.

## 9. Estado final de la fase

> **Cerrada el 2026-11-22.** `uv run pytest`: 1006 tests en verde (991 en la
> línea base + 15 nuevos de regresión). `uv run ruff check .`: limpio. El
> diff se limita a `src/foto/ui/window.py`, `src/foto/ui/app.py` y los tres
> ficheros de tests previstos (más este registro); ninguna mejora R1–R11 ni
> ADR tocado. Sin commit ni push.
>
> **Desviaciones:** T1.1 y T1.3 se hicieron juntas, así que el helper de
> favorito usa `_filter_text` desde el principio. El test de proyecciones
> del favorito comprueba el panel sin filtro y el conjunto visible con filtro
> `fav`, porque desmarcar la única coincidencia deja el panel vacío. El
> smoke manual en macOS no se ejecutó por falta de entorno gráfico; queda
> cubierto por la suite `offscreen`. No hay pendientes P0 abiertos.

