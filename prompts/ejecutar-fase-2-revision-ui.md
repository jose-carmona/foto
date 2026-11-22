# Prompt — Fase 2 (P1) de la revisión de UI: latencia con cambios pequeños

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> el alcance. La misión no es rediseñar ni reabrir la fase 1: es **ejecutar la
> fase 2 del plan recomendado** en
> [`prompts/revision-ui-arquitectura-rendimiento.md`](revision-ui-arquitectura-rendimiento.md)
> (§ 7), es decir, cerrar **R1, R4, R6, R2 y R3** con el código más simple
> posible, tests de regresión y una medición de interacción antes/después de
> R2/R3.
>
> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada
> una: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si aparece una pendiente nueva, añádela al final de la fase con el mismo
> formato. Una tarea solo está cerrada con sus tests en verde y
> `uv run pytest` + `uv run ruff check .` completos.

## 0. Misión

Mejorar la latencia percibida sin añadir capas ni reescribir la rejilla:

| Tarea | Hallazgo | Resultado esperado |
| --- | --- | --- |
| T2.1 | R1 — doble populate en el constructor | La ventana popula una sola vez; la primera selección sigue siendo correcta |
| T2.2 | R4 — panel de metadatos reconstruido aunque esté oculto | Navegar con el panel oculto no reconstruye nada; al abrirlo (i/Tab) muestra la foto actual |
| T2.3 | R6 — dos `osascript` síncronos en el hilo de UI | Una sola activación, asíncrona, sin bloquear el arranque ni dejar procesos zombis |
| T2.4 | Medición de interacción | El benchmark mide j/k y filtro con p50/p95/máx y umbrales, antes de tocar R2/R3 |
| T2.5 | R2 — filtro en tres pasadas completas | Una sola pasada; `setHidden` solo cuando el valor cambia; cabeceras y caché de visibles en el mismo bucle |
| T2.6 | R3 — j/k recalculan toda la geometría | Filas visuales y centros cacheados por generación de layout, con invalidación explícita |
| T2.7 | Verificación y cierre | Suite y ruff en verde; benchmark final con p95 dentro de umbral; diff acotado y cifras registradas |

**Criterio de éxito de la fase:** la navegación con `j`/`k` y el filtro
incremental tienen p95 < 50 ms sobre 25k fotos en la medición de referencia (o
queda documentado, con perfil, por qué no se alcanza y qué se descarta tocar);
el panel oculto no se reconstruye; el arranque no bloquea con `osascript`; no
hay regresiones en la suite.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, convenciones, comandos de verificación
   y prohibición de `git commit`/`push` sin permiso explícito.
2. `prompts/revision-ui-arquitectura-rendimiento.md` — en especial § 1
   (resumen), § 3 (criterios de diseño), § 4 (R1, R4, R6, R2 y R3 con
   evidencia y pruebas de aceptación), § 5 (hipótesis y umbrales), § 6
   (benchmark), § 7 (fase 2) y § 8 (qué no refactorizar).
3. `docs/adr/0011-arranque-progresivo.md` — populate por lotes, primera
   selección diferida y cierre limpio (solo como contexto; la fase 1 ya lo
   cerró).
4. Código real, localizando por **nombre de función** (las líneas del informe
   son una foto del 2026-11-21 y pueden haberse desplazado):
   - `src/foto/ui/window.py`: `__init__` (las dos llamadas a
     `_populate_grid`), `_populate_grid`, `_populate_range`,
     `_finish_populate`, `_apply_filter`, `_matches_filter`,
     `_update_group_headers`, `_refresh_visible_cache`, `_on_current_changed`,
     `_focus_panel`, `toggle_panel`, `_restore_after_populate`,
     `_apply_photo_update`.
   - `src/foto/ui/grid.py`: `__init__`, `resizeEvent`,
     `update_header_widths`, `set_visible_rows`, `append_visible_rows`,
     `retheme`, `_visual_rows`, `_row_center_x`, `move_selection`,
     `_move_vertical`.
   - `src/foto/ui/app.py`: `main` (bloque de activación de macOS).
   - `scripts/bench_arranque.py`: construcción de la biblioteca sintética,
     modos (a)/(b)/(c) y reporte de tiempos.
5. Tests de referencia:
   - `tests/test_ui_progressive.py` (populate por lotes, primera selección,
     `MainWindow` y worker).
   - `tests/test_ui_search.py` (filtro, recuentos y conjunto visible).
   - `tests/test_ui_interaction.py` (panel, foco y Tab).
   - `tests/test_ui_grid_groups.py` (navegación y geometría de la rejilla).
   - `tests/conftest.py` (plataforma `offscreen` y utilidades).

## 2. Estado de partida y línea base

Antes de cambiar nada:

1. `git status --short` y `git diff --stat`: identifica el trabajo sin
   commit. **No lo reviertas, no lo commitees.** La fase 1 (A1–A5) está
   cerrada y commiteada; no la reabras.
2. `uv run pytest` y `uv run ruff check .`: anota el número de tests y el
   estado. Si la línea base ya está roja, **para y repórtalo** antes de
   atribuir el fallo a esta fase.
3. Ejecuta los tests de UI afectados para tener una referencia rápida:

   ```sh
   uv run pytest tests/test_ui_progressive.py tests/test_ui_search.py \
     tests/test_ui_interaction.py tests/test_ui_grid_groups.py
   ```

4. Localiza las dos llamadas a `_populate_grid` en `__init__`, la llamada a
   `panel.set_photo` en `_on_current_changed`, los dos `subprocess.run` de
   `app.py` y las tres pasadas de `_apply_filter`. Confirma por lectura que
   R1, R4, R6, R2 y R3 siguen presentes en el código actual. Si alguno ya no
   se reproduce, **no lo inventes**: documenta la evidencia y la desviación
   antes de continuar.

**Criterio de cierre:** línea base registrada y alcance confirmado.

---

## 3. Reglas de trabajo (no negociables)

1. **Solo R1, R4, R6, R2 y R3.** R5, R7, R8, R9, R10 y R11 quedan fuera. Si
   una tarea obliga a tocar una línea de otra mejora, hazlo solo si es
   imprescindible y regístralo como nota.
2. **Menos código, no más capas.** Nada de controladores, gestores de estado,
   buses de eventos, modelos observables ni inyección de dependencias.
3. **Sin dependencias nuevas.** Qt, la biblioteca estándar y `QTest` (ya
   presente con PySide6) bastan.
4. **Conservar lo que funciona:** presentación pura sin Qt, registro central
   de acciones/keybindings, miniaturas perezosas, caches acotadas, populate
   por lotes y patrón de cola de `ThumbnailService`.
5. **Medir antes de optimizar R2/R3.** El benchmark de interacción se añade
   antes de tocar el filtro o la geometría, y se registra la línea base. Sin
   esa cifra no se puede afirmar que la fase mejora algo.
6. **Tests primero o a la vez que el cambio.** Extiende los tests indicados;
   no los debilites para que pasen. Si un test existente falla porque el
   contrato cambia de verdad, explica por qué en la nota de la tarea.
7. **Una sola fuente de verdad por dato.** El filtro efectivo sigue siendo
   `_filter_text` (A3); la visibilidad, las cabeceras y la caché de visibles
   se calculan en una sola pasada; la geometría visual tiene una sola caché
   invalidada en puntos explícitos.
8. **Estilo del proyecto:** mensajes de UI en español, docstrings breves en
   español, `pathlib`, type hints y ruff limpio.
9. **Sin `git commit`/`push`** sin permiso explícito.

## 4. Decisiones ya tomadas (no se discuten)

1. **R1:** se elimina la primera llamada a `_populate_grid(photos)` del
   constructor (la que ignora el resultado y ocurre antes de conectar
   señales) y se conserva la segunda, que ya contempla el camino síncrono y
   el diferido. **No se cambia el contrato `bool` de `_populate_grid` en esta
   fase**; la mejora de firma con `on_done` que sugiere el informe queda para
   otra limpieza.
2. **R4:** `_on_current_changed` solo llama a `panel.set_photo` si
   `self.panel_visible`. Al abrir el panel se refresca con la foto actual,
   tanto por `toggle_panel` (ya existe desde la fase 1) como por
   `_focus_panel` (Tab). Las escrituras al panel oculto de
   `_apply_filter`/`_restore_after_populate` (`show_empty`) también se evitan.
   No se actualizan etiquetas en sitio ni se convierte el panel en
   observable.
3. **R6:** la activación de macOS se hace con un único helper que llama a
   `raise_()`, `activateWindow()` y `QProcess.startDetached("osascript", …)`
   una sola vez tras `show()`. Se elimina el `QTimer.singleShot(50, _focus)`
   duplicado y el `import subprocess` de `app.py`. No se añade pyobjc ni se
   bloquea el hilo de UI.
4. **R2:** `_apply_filter` calcula en **una sola pasada** la visibilidad, las
   filas visibles, las claves, las posiciones, los grupos visibles y las
   cabeceras. `setHidden` solo se llama si el valor cambia. Si
   `_update_group_headers` y `_refresh_visible_cache` quedan sin llamadores,
   se eliminan; no se mantienen dos caminos para el mismo dato.
5. **R3:** `PhotoGrid` cachea las filas visuales, la geometría
   `(top, centros_x)` y el índice de fila visual por fila visible. La caché se
   invalida en `set_visible_rows`, `append_visible_rows`, `resizeEvent`,
   `retheme` y cuando `update_header_widths` cambia el ancho. No se invalida
   por scroll: el agrupado y las distancias relativas no cambian al
   desplazarse. **No** se migra a `QListView`/`QAbstractItemModel`.
6. **Medición:** se añade al benchmark existente un modo `--interaccion`
   limitado a j/k y filtro, que es lo que la fase 2 necesita para validar
   R2/R3. El visor, el RSS, el arranque en frío y la tabla completa de
   umbrales de § 6 siguen en la fase 3. Umbral de referencia: **p95 < 50 ms**
   para ambas interacciones sobre 25k fotos.
7. **No** se toca el registro de acciones, la presentación pura, el populate
   por lotes, `ThumbnailService`, los ADRs ni la escritura de favoritos.

---

## 5. Tareas

### T2.0 — Línea base y reproducción (obligatoria)

- [x] Ejecutar la verificación de § 2 y anotar aquí el estado inicial
  (número de tests, fallos preexistentes, `git status`).
  > Línea base (2026-11-22): `git status` limpio salvo el propio fichero del
  > prompt sin commitear; `uv run pytest` → **1006 passed**; `uv run ruff
  > check .` → limpio. Ningún fallo preexistente.
- [x] Confirmar por lectura que R1, R4, R6, R2 y R3 siguen presentes. Si
  alguno no se reproduce, documenta la evidencia y la desviación antes de
  continuar.
  > Evidencia (2026-11-22): R1 — dos llamadas a `_populate_grid(photos)` en
  > `PhotoWindow.__init__` (la primera en línea 532, la segunda en 561). R4 —
  > `panel.set_photo(photo)` incondicional en `_on_current_changed`;
  > `panel.show_empty("Sin coincidencias")` en `_apply_filter` y
  > `show_empty("Sin selección")` en `_restore_after_populate`, ambos sin
  > guardar `panel_visible`. R6 — dos `subprocess.run` de `osascript` síncronos
  > en `main` de `app.py` (uno directo y otro dentro de `_focus` diferido con
  > `QTimer.singleShot(50, _focus)`). R2 — `_apply_filter` recorre
  > `grid.count()` y llama después a `_update_group_headers` (dos pasadas más
  > sobre la lista) y `_refresh_visible_cache` (otra). R3 — `_move_vertical`
  > llama a `_visual_rows()` y `_row_center_x` (con `visualItemRect` por celda)
  > en cada pulsación. Los cinco se reproducen.

**Criterio de cierre:** línea base registrada y alcance confirmado.

---

### T2.1 — R1: un solo populate en el constructor

- [x] En `PhotoWindow.__init__`, borrar la primera llamada
  `self._populate_grid(photos)` (la que ignora el resultado, antes de conectar
  `currentItemChanged`, `itemDoubleClicked`, etc.). Conservar la segunda, que
  usa el `bool` para diferir `_select_first_visible` en el populate por lotes.
- [x] No mover `_update_empty_state` ni reordenar el resto del constructor
  salvo que un test demuestre que hace falta. El objetivo es eliminar el
  trabajo duplicado, no reescribir el arranque.
- [x] Verificar que no queda ninguna otra ruta que popule dos veces la misma
  lista en el constructor.

- [x] **Test** en `tests/test_ui_progressive.py`:
  `test_constructor_populates_the_grid_once`: envolver
  `PhotoWindow._populate_grid` con un espía, construir una ventana con
  `FIVE_PHOTOS` y comprobar que se llamó **una sola vez**. Mantener
  `test_small_library_populates_synchronously` y los tests de primera
  selección en verde.

**Criterio de cierre:** el constructor popula una vez; la primera selección y
los recuentos no cambian; el populate por lotes sigue difiriendo el `on_done`.

---

### T2.2 — R4: el panel oculto no se reconstruye

- [x] En `_on_current_changed`, sustituir la llamada incondicional
  `self.panel.set_photo(photo)` por:

  ```python
  if self.panel_visible:
      self.panel.set_photo(photo)
  ```

- [x] En `_focus_panel` (Tab), después de poner `panel_visible = True` y
  llamar a `_layout_content()`, refrescar el contenido con la foto actual:

  ```python
  self.panel.set_photo(self._current_photo())
  ```

  `toggle_panel` ya lo hace desde la fase 1; no dupliques una tercera ruta.
- [x] En `_apply_filter`, cuando no queden visibles, llamar a
  `panel.show_empty("Sin coincidencias")` solo si `self.panel_visible`.
  En `_restore_after_populate`, hacer lo mismo con
  `panel.show_empty("Sin selección")`. Al abrir el panel, `set_photo` ya
  reconstruye el estado correcto.
- [x] No tocar `_apply_photo_update`: ya actualiza el panel solo si está
  visible.

- [x] **Tests** en `tests/test_ui_interaction.py`:
  1. `test_hidden_panel_is_not_rebuilt_on_navigation`: con el panel oculto,
     instalar un espía sobre `window.panel.set_photo` **después** de construir
     la ventana, cambiar de foto actual (`grid.setCurrentItem`) y comprobar
     que el espía no se llama.
  2. `test_opening_panel_shows_current_photo`: navegar con el panel oculto,
     llamar a `toggle_panel()` y comprobar que `set_photo` recibe la foto
     actual (no la primera).
  3. `test_tab_opening_panel_refreshes_the_hidden_panel`: navegar con el panel
     oculto, abrirlo con `_focus_panel()` (o Tab) y comprobar que `set_photo`
     recibe la foto actual.
  4. `test_visible_panel_is_still_updated_on_navigation`: con el panel
     visible, navegar y comprobar que `set_photo` sí se llama con la foto
     nueva.

**Criterio de cierre:** navegar con el panel oculto no crea widgets; abrirlo
por cualquier vía muestra la foto actual; con el panel visible el
comportamiento no cambia.

---

### T2.3 — R6: activación de macOS sin bloquear

- [x] En `src/foto/ui/app.py`, extraer un helper module-level (por ejemplo
  `_activate_window(window, *, macos: bool)`) que haga:

  ```python
  window.raise_()
  window.activateWindow()
  if not macos:
      return
  QProcess.startDetached(
      "osascript",
      [
          "-e",
          'tell application "System Events" to set frontmost of '
          f'(first process whose unix id is {os.getpid()}) to true',
      ],
  )
  ```

  `startDetached` no espera, no deja zombis y no bloquea el hilo de UI. Si la
  versión de PySide6 devuelve una tupla, ignórala.
- [x] En `main`, llamar al helper **una sola vez** tras `window.show()` en
  ambas ramas (`darwin` y resto). En macOS, conservar
  `QApplication.processEvents()` antes de la activación si el orden actual lo
  necesita; eliminar el `QTimer.singleShot(50, _focus)` y el segundo
  `subprocess.run`.
- [x] Eliminar `import subprocess` de `app.py` si no se usa en ningún otro
  sitio. Añadir `QProcess` al import de `PySide6.QtCore`.
  > Nota (2026-11-22): también se retira `QTimer` del import de `QtCore`,
  > porque el `singleShot(50, _focus)` era su único uso en `app.py` y ruff
  > habría marcado el import como sin uso.

- [x] **Tests** en `tests/test_ui_progressive.py` (sección de `MainWindow`):
  1. `test_macos_activation_does_not_block`: sustituir
     `app_module.QProcess` por un falso que registre llamadas, invocar
     `_activate_window(widget, macos=True)` y comprobar que hay exactamente
     una llamada a `startDetached` con programa `osascript` y el PID actual.
  2. `test_activation_skips_osascript_off_macos`: con `macos=False`, el falso
     no recibe ninguna llamada.
  3. Los tests de arranque y cierre existentes siguen pasando.

**Criterio de cierre:** no queda ningún `subprocess.run` en el camino de
arranque; la activación se intenta una sola vez y no bloquea.

---

### T2.4 — Medición de interacción y línea base de R2/R3

Esta tarea se ejecuta **después** de R1/R4/R6 y **antes** de tocar R2/R3. Así
la línea base de R2/R3 queda aislada de las mejoras pequeñas.

- [x] Añadir `--interaccion` a `scripts/bench_arranque.py`. Sin el flag, el
  comportamiento actual (a)/(b)/(c) no cambia.
- [x] Con el flag, tras el populate completo de una `MainWindow` de 25k:
  - pulsar **100** `j`/`k` con `QTest.keyClick` sobre la rejilla, alternando
    ambos y partiendo de una foto intermedia;
  - aplicar **50** filtros de 1–3 letras sobre nombres sintéticos (`0`, `00`,
    `000`, `1`, `12`, `IMG`…), volviendo a `""` al final;
  - medir cada evento como `time.perf_counter()` antes de la llamada +
    `app.processEvents()` después, y reportar **p50, p95 y máximo** (no solo
    mínimo/mediana);
  - repetir la secuencia 3 rondas (o `--repeticiones`) y agregar las muestras;
  - imprimir una tabla `OK`/`FALLA` con el umbral **p95 < 50 ms** para j/k y
    para filtro;
  - comprobar antes de medir que `window.grid._visual_rows()` no es `None`;
    si la geometría no es fiable en `offscreen`, imprímelo y no presentes la
    cifra de j/k como válida.
  > Nota (2026-11-22): la comprobación de geometría fiable se hace una vez
  > antes de medir (no cambia entre rondas) y el veredicto de j/k pasa a
  > `n/a (geometría no fiable)` si `_visual_rows()` es `None`. La ronda
  > cierra con un evento de filtro `""` para dejar la biblioteca completa.
- [x] Documentar en el docstring del script qué mide exactamente cada modo
  (evento → selección con `processEvents`, no solo la función interna).
- [x] Ejecutar la línea base y anotarla aquí:

  ```sh
  QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py \
    --fotos 25000 --repeticiones 3 --interaccion
  ```

  > Línea base (2026-11-22): j/k p50 248,8 ms · p95 329,4 ms · máx 352,3 ms;
  > filtro p50 258,2 ms · p95 264,1 ms · máx 267,2 ms; **FALLA** ambos según
  > 50 ms. Condición: sintética, `offscreen`, catálogo caliente, sin
  > originales, geometría fiable, 300 pulsaciones j/k y 153 eventos de
  > filtro (3 rondas).

**Criterio de cierre:** el benchmark mide las dos interacciones con
percentiles y umbral, y la línea base está registrada antes de R2/R3.

---

### T2.5 — R2: filtro en una sola pasada

- [x] Reescribir `_apply_filter` para hacer un único recorrido de
  `self.grid.count()` que calcule a la vez:
  - visibilidad de cada foto (`_matches_filter`, sin reconstruir documentos);
  - `setHidden` **solo si** `item.isHidden() != hidden`;
  - filas visibles (`rows`), claves (`keys`) y posiciones
    (`positions`) para `grid.set_visible_rows` y la caché de navegación;
  - grupos con al menos una foto visible y cabeceras que hay que ocultar o
    mostrar, también con `setHidden` solo si cambia;
  - la primera foto visible para la selección de respaldo.

  Esqueleto de referencia (adáptalo al código real, no lo copies a ciegas):

  ```python
  def _apply_filter(self, text: str) -> None:
      self._filter_text = text
      needle = text.strip().casefold()
      tag_filter = self._tag_filter
      rows: list[int] = []
      keys: list[str] = []
      positions: dict[str, int] = {}
      visible_groups: set[str] = set()
      headers: list[QListWidgetItem] = []
      first_visible: QListWidgetItem | None = None

      for row in range(self.grid.count()):
          item = self.grid.item(row)
          if item is None:
              continue
          if item.data(HEADER_ROLE):
              headers.append(item)
              continue
          photo = self.photos.get(item.data(PHOTO_ROLE))
          visible = photo is None or self._matches_filter(
              photo, needle, tag_filter
          )
          hidden = not visible
          if item.isHidden() != hidden:
              item.setHidden(hidden)
          if not visible:
              continue
          if first_visible is None:
              first_visible = item
          group_key = str(item.data(GROUP_ROLE) or "")
          visible_groups.add(group_key)
          key = item.data(PHOTO_ROLE)
          if key:
              positions[str(key)] = len(keys)
              keys.append(str(key))
              rows.append(row)

      for header in headers:
          hidden = str(header.data(GROUP_ROLE) or "") not in visible_groups
          if header.isHidden() != hidden:
              header.setHidden(hidden)

      self.grid.set_visible_rows(rows)
      self._visible_keys_cache = keys
      self._visible_positions_by_key = positions

      # ... selección, cierre del visor, recuentos, estado vacío y
      # miniaturas, con la misma semántica que antes.
  ```

- [x] Conservar exactamente la semántica posterior: si la foto actual queda
  oculta, seleccionar la primera visible; si no hay ninguna, `setCurrentRow(-1)`
  y `show_empty("Sin coincidencias")` solo con el panel visible; cerrar el
  visor si no queda nada visible; actualizar recuentos, estado vacío y
  miniaturas.
- [x] Absorber `_update_group_headers` y `_refresh_visible_cache` en este
  bucle. Si tras el cambio no tienen llamadores, **eliminarlos** y ajustar el
  docstring de `_populate_range` que los menciona. No dejes dos caminos para
  calcular la caché de visibles.
- [x] No cambiar `_matches_filter`, `_filter_text` ni la regla de composición
  búsqueda AND etiqueta.

- [x] **Tests**:
  1. En `tests/test_ui_search.py`,
     `test_filter_only_changes_visibility_when_needed`: envolver
     `QListWidgetItem.setHidden` con un espía, aplicar un filtro, limpiar el
     registro y reaplicar el mismo filtro; comprobar que no hay llamadas. Un
     cambio de filtro sí debe producir llamadas.
  2. `test_filter_keeps_headers_and_counts_in_one_pass`: con grupos por fecha,
     aplicar un filtro que deje fotos de un solo grupo y comprobar que la
     cabecera correcta está visible, las demás ocultas, y que
     `_visible_keys_cache`/`_visible_count` coinciden con lo visible.
  3. Los tests existentes de `test_ui_search.py`, cabeceras, recuentos y
     cursor siguen pasando.
  4. Volver a ejecutar el benchmark de T2.4 y anotar las cifras de filtro
     después de R2.
     > Después de R2 (2026-11-22): filtro p50 158,5 ms · p95 162,5 ms ·
     > máx 165,8 ms (línea base: p50 258,2 · p95 264,1 · máx 267,2 ms).
     > −39 % en p95; sigue **FALLA** el umbral de 50 ms.

**Criterio de cierre:** una sola pasada, `setHidden` solo cuando cambia, misma
selección y mismos recuentos; p95 de filtro medido antes/después.

---

### T2.6 — R3: caché de geometría para j/k

- [ ] En `PhotoGrid.__init__`, añadir la caché:

  ```python
  self._visual_rows_cache: list[list[int]] | None = None
  self._visual_geometry_cache: list[tuple[int, tuple[int, ...]]] | None = None
  self._visual_row_by_row: dict[int, int] = {}
  ```

- [x] Añadir `_invalidate_layout_cache()` que ponga las tres a `None`/vacío y
  llamarlo desde:
  - `set_visible_rows`;
  - `append_visible_rows`;
  - `resizeEvent`;
  - `retheme`;
  - `update_header_widths`, cuando el ancho cambie de verdad (antes de
    `doItemsLayout`).

- [x] Añadir un cálculo perezoso con dos métodos: `_visual_layout()` devuelve
  la caché o la construye, y `_compute_visual_layout()` hace el trabajo sin
  cachear. En una sola pasada construye:
  - `visual_rows: list[list[int]]`;
  - `geometry: list[tuple[int, tuple[int, ...]]]` con el `top` de la fila y
    los centros X de sus celdas;
  - `row_by_row: dict[int, int]` de fila visible → índice de fila visual.

  Si la geometría aún no es fiable (`visualItemRect` con ancho/alto ≤ 1),
  devolver `None` sin cachear, igual que hoy.
- [x] Mantener `_visual_rows()` como envoltorio fino de `_visual_layout()`
  (lo usa el benchmark de T2.4 para comprobar que la geometría es fiable) y
  eliminar `_row_center_x`, que deja de ser necesario.
- [x] `_move_vertical` usa la caché: `row_index` sale de
  `_visual_row_by_row` (O(1)), el centro actual sale de `geometry`, y la celda
  destino de la fila objetivo se elige comparando con
  `geometry[target_index][1]` sin volver a llamar a `visualItemRect` por
  celda. `_move_indexed` sigue siendo el respaldo cuando no hay geometría.
- [x] No cachear por scroll ni por selección: solo por layout. No añadir
  `QListView` ni un modelo nuevo.

- [x] **Tests** en `tests/test_ui_grid_groups.py`:
  1. `test_vertical_navigation_reuses_the_layout_cache`: mostrar la ventana,
     procesar eventos, invalidar la caché antes de medir, envolver
     `_compute_visual_layout` con un contador, pulsar `j` dos veces sin
     cambios de layout y comprobar que el cálculo ocurrió una sola vez y que
     el cursor se movió.
  2. `test_layout_cache_is_invalidated_on_resize_filter_and_sort`: tras un
     `resize`, un filtro y un `:sort`, el siguiente `j`/`k` recalcula la caché
     y el destino es correcto.
  3. `test_vertical_navigation_after_filter_uses_the_new_rows`: con un filtro
     activo, `j`/`k` se mueven solo entre las fotos visibles y no entre las
     ocultas.
  4. Los tests de navegación vertical, grupos y filtros siguen pasando.
  5. Volver a ejecutar el benchmark de T2.4 y anotar las cifras de j/k
     después de R3.
     > Después de R3 (2026-11-22): j/k p50 1,8 ms · p95 1,9 ms · máx 218,7 ms
     > (línea base: p50 248,8 · p95 329,4 · máx 352,3 ms). p95 −99 %; **OK**
     > el umbral de 50 ms. El máx aislado (218,7 ms) cae en la primera
     > pulsación tras una ronda de filtro, cuando la rejilla acaba de
     > reacomodar 25k ítems.

**Criterio de cierre:** dos `j` consecutivos sin cambio de layout no
recalculan geometría; resize, filtro, sort y retheme invalidan; los destinos
siguen siendo correctos; p95 de j/k medido antes/después.

---

### T2.7 — Verificación y cierre de la fase

- [x] `uv run pytest` completo en verde.
  > Final (2026-11-22): **1018 passed** (línea base: 1006; +12 tests nuevos).
- [x] `uv run ruff check .` limpio.
- [x] Revisar `git diff --stat` y el diff completo: solo deben aparecer los
  ficheros de implementación, tests y benchmark previstos; ninguna mejora
  R5/R7–R11 colada de rondón.
  > Diff (2026-11-22): `src/foto/ui/window.py`, `src/foto/ui/grid.py`,
  > `src/foto/ui/app.py`, `scripts/bench_arranque.py` y los cuatro ficheros
  > de tests previstos (`test_ui_progressive`, `test_ui_interaction`,
  > `test_ui_search`, `test_ui_grid_groups`). Sin R5/R7–R11.
- [x] Ejecutar el benchmark final completo y anotar las cifras:

  ```sh
  QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py \
    --fotos 25000 --repeticiones 3 --interaccion
  ```

  > Final (2026-11-22): j/k p50 1,8 ms · p95 2,1 ms · máx 221,4 ms (**OK**);
  > filtro p50 164,2 ms · p95 169,4 ms · máx 461,6 ms (**FALLA**).
  > Comparación con la línea base de T2.4: j/k p95 329,4 → 2,1 ms (−99 %,
  > R3); filtro p95 264,1 → 169,4 ms (−36 %, R2: tres pasadas → una y
  > `setHidden` solo si cambia).
- [x] Si R2 o R3 no mejoran lo suficiente, **no añadas más complejidad**:
  registra el dato, revisa si el cuello está en el repintado o en otra capa y
  deja constancia de qué se descarta. El objetivo de la fase es medir y
  decidir, no reescribir la rejilla.
  > **Decisión sobre R2 (2026-11-22):** el p95 del filtro no alcanza 50 ms.
  > Perfil con `cProfile` sobre 25k (biblioteca sintética, offscreen): el
  > cuello **no es el repintado** (`processEvents` tras `_apply_filter`:
  > 0,1–5 ms) sino el bucle Python↔Qt por ítem: ~100 000 llamadas
  > `QListWidgetItem.data` (4 por foto: `HEADER_ROLE`, `PHOTO_ROLE` dos
  > veces, `GROUP_ROLE`), 25 000 `grid.item`, 25 000 `isHidden` y 25 000
  > `Photo.key` por pasada (~155–170 ms en total, estable). Bajar de ahí
  > exigiría bien un modelo paralelo de roles en Python puro —segunda fuente
  > de verdad, prohibida por la regla 7—, bien migrar a
  > `QListView`/`QAbstractItemModel` —fuera de alcance por § 8—, bien tocar
  > `_matches_filter`/`Photo.key` (excluido por la tarea T2.5). Se descarta
  > tocar nada de eso en esta fase; queda para la fase 3 «medir y decidir»,
  > con este perfil como punto de partida. R3 sí cumple el umbral.
- [x] Smoke manual si hay entorno gráfico:
  - arrancar `uv run foto` sobre una biblioteca indexada;
  - navegar con `j`/`k` con el panel oculto y abrirlo con `i` y Tab;
  - escribir en `/` y comprobar que el filtro responde;
  - cambiar de orden con `:sort date` y repetir la navegación;
  - en macOS, comprobar que la ventana se activa al arrancar sin retardo
    perceptible.
  > Limitación documentada (2026-11-22): el smoke interactivo no se puede
  > ejecutar sin un operador delante de la ventana (este agente no puede
  > manipular la UI real de la sesión); lanzar `uv run foto` sobre la
  > biblioteca real del usuario abriría una ventana no solicitada. La
  > suite `offscreen` y el benchmark cubren navegación, filtro, panel,
  > `:sort` y la activación (`_activate_window` con `QProcess.startDetached`
  > verificado con falso en test).
- [x] Actualizar este fichero: marcar T2.0–T2.7 con fecha, añadir notas de
  desviación y un párrafo de estado final.
- [x] No hacer commit ni push sin permiso explícito.

**Criterio de cierre:** suite y ruff en verde, diff acotado, benchmark final
con p95 y comparación registrada, y decisión explícita sobre R2/R3 si no se
alcanza el umbral.

---

## 6. Tabla de aceptación global

| ID | Comportamiento | Test / evidencia |
| --- | --- | --- |
| R1 | El constructor popula una sola vez | `test_constructor_populates_the_grid_once` |
| R1 | La primera selección sigue siendo correcta | tests de populate/primera selección existentes |
| R4 | Navegar con el panel oculto no lo reconstruye | `test_hidden_panel_is_not_rebuilt_on_navigation` |
| R4 | Abrir con `i` muestra la foto actual | `test_opening_panel_shows_current_photo` |
| R4 | Abrir con Tab refresca el panel oculto | `test_tab_opening_panel_refreshes_the_hidden_panel` |
| R4 | Con panel visible la navegación sigue actualizando | `test_visible_panel_is_still_updated_on_navigation` |
| R6 | La activación de macOS no bloquea ni duplica | `test_macos_activation_does_not_block` |
| R6 | Fuera de macOS no se lanza `osascript` | `test_activation_skips_osascript_off_macos` |
| R2 | Reaplicar el mismo filtro no llama a `setHidden` | `test_filter_only_changes_visibility_when_needed` |
| R2 | Cabeceras, claves y recuentos salen de una pasada | `test_filter_keeps_headers_and_counts_in_one_pass` |
| R3 | Dos `j` sin cambio de layout no recalculan | `test_vertical_navigation_reuses_the_layout_cache` |
| R3 | Resize, filtro y sort invalidan la caché | `test_layout_cache_is_invalidated_on_resize_filter_and_sort` |
| R2/R3 | p95 < 50 ms sobre 25k | `bench_arranque.py --interaccion` |

## 7. Mapa de cambios previsto

| Fichero | Zona | Tarea |
| --- | --- | --- |
| `src/foto/ui/window.py` | `__init__` (primera llamada a `_populate_grid`) | T2.1 |
| `src/foto/ui/window.py` | `_on_current_changed`, `_focus_panel`, `_apply_filter`, `_restore_after_populate` | T2.2 |
| `src/foto/ui/app.py` | helper de activación, `main`, imports | T2.3 |
| `scripts/bench_arranque.py` | `--interaccion`, percentiles y umbrales | T2.4 |
| `src/foto/ui/window.py` | `_apply_filter`; retirada de `_update_group_headers` y `_refresh_visible_cache` | T2.5 |
| `src/foto/ui/grid.py` | caché de layout, `_move_vertical`, invalidación | T2.6 |
| `tests/test_ui_progressive.py` | R1 y R6 | T2.1, T2.3 |
| `tests/test_ui_interaction.py` | R4 | T2.2 |
| `tests/test_ui_search.py` | R2 | T2.5 |
| `tests/test_ui_grid_groups.py` | R3 | T2.6 |
| `prompts/ejecutar-fase-2-revision-ui.md` | registro de la fase | todas |

## 8. Fuera del alcance de esta fase

- **R5** (cola del visor), **R7** (`busy_timeout` del favorito) y
  **R8–R11** (`_ghost_counts`, grupos antes del primer lote, `:sort`
  repetido, factorías de tests). Son fase 3.
- **Benchmark completo de § 6**: visor, RSS, arranque en frío, tabla completa
  de umbrales y renombrado de «primer píxel». La fase 2 solo añade j/k y
  filtro para validar R2/R3.
- **Migrar `QListWidget` a `QListView`/`QAbstractItemModel`**, añadir
  MVC/casos de uso/gestores de estado/DI o dependencias nuevas.
- **Cambiar el contrato de `_populate_grid`** (`on_done=None`) o reordenar el
  constructor más allá de borrar el populate duplicado.
- **Actualizar ADRs** con las cifras: la fase 3 actualiza ADR 0011 (y ADR
  0006 solo si R7 cambia el timeout). Si una desviación obliga a una nota
  mínima, regístrala también en este prompt.
- **Tocar el registro de acciones, la presentación pura, el populate por
  lotes o `ThumbnailService`.**

## 9. Estado final de la fase

> **Cerrada el 2026-11-22.** Tests: 1006 en la línea base → **1018 passed**
> al final (12 nuevos: 1 de R1, 4 de R4, 2 de R6, 2 de R2, 3 de R3); un
> único `uv run pytest` completo en verde y `uv run ruff check .` limpio.
> Benchmark (`bench_arranque.py --fotos 25000 --repeticiones 3
> --interaccion`, offscreen, catálogo caliente, sin originales): **j/k**
> p50 248,8 → 1,8 ms · p95 329,4 → 2,1 ms · máx 352,3 → 221,4 ms
> (**OK**, p95 < 50 ms gracias a R3); **filtro** p50 258,2 → 164,2 ms ·
> p95 264,1 → 169,4 ms · máx 267,2 → 461,6 ms (**FALLA** el umbral de
> 50 ms pese a R2). Desviaciones menores: `QTimer` retirado del import de
> `QtCore` en `app.py` (su único uso era el `singleShot` eliminado); la
> comprobación de geometría fiable del benchmark se hace una vez antes de
> medir y la ronda cierra con un filtro `""`. **Decisión sobre R2:** el
> perfil (`cProfile`, 25k) muestra que el cuello no es el repintado sino el
> bucle Python↔Qt por ítem (~100k `data()`, 25k `item()`/`isHidden()`/
> `Photo.key` por pasada); reducirlo exige un modelo paralelo sin Qt
> (prohibido, regla 7), migrar a `QListView`/modelo (fuera de alcance, § 8)
> o tocar `_matches_filter`/`Photo.key` (excluido por T2.5): nada se toca y
> queda para la fase 3. Smoke manual no ejecutable sin operador (limitación
> documentada en T2.7); la suite y el benchmark cubren navegación, filtro,
> panel y activación. No hubo `git commit` ni `git push`.
