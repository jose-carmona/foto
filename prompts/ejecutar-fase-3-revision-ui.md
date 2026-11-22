# Prompt — Fase 3 (P2) de la revisión de UI: limpieza barata, solo R8/R9/R10/R11

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> el alcance. La misión no es rediseñar ni reabrir las fases 1 y 2: es
> **ejecutar la fase 3 del plan recomendado** en
> [`prompts/revision-ui-arquitectura-rendimiento.md`](revision-ui-arquitectura-rendimiento.md)
> (§ 7) **recortada por decisión del usuario**: únicamente los hallazgos
> **R8, R9, R10 y R11** («limpieza barata, sin cambio de contrato»), con el
> código más simple posible, tests de regresión y una única medición
> antes/después para R9.
>
> **Descartados.** El resto de los pasos de la fase 3 queda **descartado, no
> pospuesto**: R5 (cola del visor), R7 (`busy_timeout` del favorito) y las
> actualizaciones de ADRs (0011 con cifras, 0006 si R7 cambiara el timeout)
> **no se ejecutan**. No los hagas aunque veas que están pendientes; solo el
> usuario puede reincorporarlos.
>
> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada
> una: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si aparece una pendiente nueva, añádela al final de la fase con el mismo
> formato. Una tarea solo está cerrada con sus tests en verde y
> `uv run pytest` + `uv run ruff check .` completos.

## 0. Misión

Cuatro limpiezas de pocas líneas, sin cambios de contrato:

| Tarea | Hallazgo | Resultado esperado |
| --- | --- | --- |
| T3.1 | R8 — `_ghost_counts` recorre todas las fotos aunque no falte nada | Con todas las ubicaciones disponibles, el banner ni siquiera recorre las fotos |
| T3.2 | R9 — grupos materializados antes del primer lote | El grupo se calcula dentro de `_populate_range`; el primer lote navegable llega antes; medición antes/después |
| T3.3 | R10 — `:sort` con la misma clave repuebla toda la rejilla | Repetir `:sort <misma clave>` no repobla; cambiar de clave sí |
| T3.4 | R11 — factorías `_photo` duplicadas en los tests | Una única factoría `make_photo` en `tests/ui_factories.py`; los nueve ficheros migrados; suite sin cambios de comportamiento |
| T3.5 | Verificación y cierre | Suite y ruff en verde; diff acotado; cifras y notas registradas |

**Criterio de éxito de la fase:** las cuatro tareas cerradas con tests en
verde, sin cambios de contrato público, con la medición del primer lote de R9
registrada antes/después y el diff limitado a los ficheros del mapa de
cambios (§ 7).

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, convenciones, comandos de verificación
   y prohibición de `git commit`/`push` sin permiso explícito.
2. `prompts/revision-ui-arquitectura-rendimiento.md` — en especial § 3
   (criterios de diseño), § 4 (R8, R9, R10 y R11 con evidencia y pruebas de
   aceptación) y § 7 (fase 3). El § 8 (qué no refactorizar) sigue vigente.
3. `prompts/ejecutar-fase-2-revision-ui.md` (§ 9) — contexto de lo que la
   fase 2 ya cambió: `_apply_filter` es ahora de una pasada, existe
   `_filter_text` como única fuente del filtro (A3), el panel oculto no se
   reconstruye (R4) y la activación de macOS usa `QProcess` (R6).
4. Código real, localizando por **nombre de función** (las líneas del informe
   son una foto del 2026-11-21 y las de abajo del 2026-11-22; pueden
   desplazarse):
   - `src/foto/ui/presentation.py`: `_ghost_counts` (~167),
     `location_banner_text` (~185), `location_banner_parts` (~210),
     `unavailable_locations`, `photo_group` (~448).
   - `src/foto/ui/window.py`: `_populate_grid` (~622), `_populate_range`
     (~661), `_populate_next_batch`, `_finish_populate`, `_PopulateState`
     (~145), `_sort_grid` (~1770), `_sort_key`.
   - Ficheros de tests con factoría `_photo` propia (§ 5, T3.4).
5. Tests de referencia:
   - `tests/test_presentation.py` (banner de ubicaciones, funciones puras).
   - `tests/test_ui_locations.py` (banner integrado en la ventana).
   - `tests/test_ui_grid_groups.py` (grupos, cabeceras y caché de la fase 2).
   - `tests/test_ui_progressive.py` (populate por lotes, `:sort` a medio
     populate, factoría `_photo` de referencia en las líneas ~37-54).
   - `tests/test_ui_commands.py` (comandos `:` incluido `:sort`).

## 2. Estado de partida y línea base

Antes de cambiar nada:

1. `git status --short` y `git diff --stat`: identifica el trabajo sin
   commit. **No lo reviertas, no lo commitees.** Las fases 1 (A1–A5) y 2
   (R1, R4, R6, R2, R3) están cerradas y commiteadas; no las reabras.
2. `uv run pytest` y `uv run ruff check .`: anota el número de tests y el
   estado. La fase 2 cerró con **1018 passed**; si la línea base ya está
   roja, **para y repórtalo** antes de atribuir el fallo a esta fase.
3. Ejecuta los tests afectados para tener una referencia rápida:

   ```sh
   uv run pytest tests/test_presentation.py tests/test_ui_locations.py \
     tests/test_ui_progressive.py tests/test_ui_grid_groups.py \
     tests/test_ui_commands.py
   ```

4. Confirma por lectura que R8, R9, R10 y R11 siguen presentes en el código
   actual:
   - **R8:** `location_banner_text` llama a `_ghost_counts(photos)` en la
     primera línea, antes de mirar `unavailable_locations`.
   - **R9:** `_populate_grid` construye
     `groups = [photo_group(photo, self._sort_key) for photo in ordered]`
     antes del primer lote y lo guarda en `_PopulateState.groups`.
   - **R10:** `_sort_grid` no compara `key` con `self._sort_key`; siempre
     llama a `_populate_grid`.
   - **R11:** siguen existiendo factorías `def _photo(` duplicadas en los
     ficheros de UI del T3.4.

   Si alguno ya no se reproduce, **no lo inventes**: documenta la evidencia
   y la desviación antes de continuar.

**Criterio de cierre:** línea base registrada y alcance confirmado.

---

## 3. Reglas de trabajo (no negociables)

1. **Solo R8, R9, R10 y R11.** R5, R7 y las actualizaciones de ADRs están
   **descartados**: no se implementan ni se deja «medio trabajo». Si una
   tarea obliga a tocar una línea de otra mejora, hazlo solo si es
   imprescindible y regístralo como nota.
2. **Sin cambios de contrato.** El `bool` de `_populate_grid`, las firmas
   públicas de presentación y el comportamiento observable del `:sort`
   cambiando de clave se mantienen. Solo cambia lo que las tareas piden.
3. **Menos código, no más capas.** Nada de cachés nuevas, observables,
   gestores de estado ni abstracciones para problemas de una línea.
4. **Sin dependencias nuevas** y sin tocar la naturaleza pura (sin Qt) de
   `presentation.py`: R8 es solo reordenar guardas.
5. **Tests primero o a la vez que el cambio.** Extiende los tests indicados;
   no los debilites para que pasen. R11 no debe cambiar el comportamiento de
   ningún test: si migrar una factoría obliga a alterar aserciones, eso es
   una desviación que se documenta.
6. **Estilo del proyecto:** mensajes de UI en español, docstrings breves en
   español, `pathlib`, type hints y ruff limpio.
7. **Sin `git commit`/`push`** sin permiso explícito.

## 4. Decisiones ya tomadas (no se discuten)

1. **R8:** `location_banner_text` calcula primero
   `missing = unavailable_locations(locations)`; si está vacía devuelve
   `None` **sin llamar a `_ghost_counts`**. Con ubicaciones no disponibles,
   el recuento sale de `_ghost_counts` como hoy. `location_banner_parts` ya
   tiene esa guarda: queda el estilo de ambas funciones alineado.
   `_ghost_counts` sigue siendo un helper privado; no se cachea ni se
   restructura.
2. **R9:** el grupo de cada foto se calcula **dentro de `_populate_range`**
   con `photo_group(photo, self._sort_key)`, una vez por foto al insertarla.
   Se elimina el campo `groups` de `_PopulateState`. El coste total es el
   mismo, pero se reparte entre lotes y el primer lote llega antes. No se
   cachean grupos ni se paraleliza. El docstring de `_populate_grid` dice
   hoy «el cálculo de orden y grupos se hace una vez antes del primer
   lote»: actualízalo.
3. **R10:** `_sort_grid` sale pronto si `key == self._sort_key`: muestra el
   mismo mensaje de `_after_sort` (`f"ordenado por {key}"`, 3000 ms) y
   regresa sin tocar `_sort_key`, sin repoblar y sin manipular el cursor.
   Repetir `:sort` a medio populate con la misma clave pasa a ser un no-op:
   el populate en curso conserva su orden (que es el mismo) y sigue
   avanzando; los tests existentes de `:sort` a medio populate usan una
   clave distinta y no se ven afectados.
4. **R11:** una única factoría `make_photo(name, taken, **overrides)` en
   `tests/ui_factories.py`, con los campos por defecto de
   `tests/test_ui_progressive.py` (la forma `**overrides` del informe). Se
   migran los nueve ficheros del informe y se **borran** sus helpers
   locales. Donde los defaults difieran, se adaptan las llamadas con
   `overrides` explícitos para que cada test reciba los mismos valores que
   antes. Fuera de alcance: `test_tags.py` (su `_photo` devuelve una ruta,
   no una `Photo`) y `test_presentation.py` (no está en la lista del
   informe).
5. **Medición de R9:** un script desechable (o una extensión mínima del
   benchmark si resulta trivial) que mida el tiempo entre la llamada a
   `_populate_grid` y el primer lote insertado (`grid.count()` alcanzando el
   tamaño de lote), 3 repeticiones y mediana, en dos escenarios: populate
   inicial y repoblado por `:sort location`. Sin tablas de umbrales ni
   ampliación del benchmark de § 6: solo las cifras antes/después anotadas
   aquí. Los pasos 1–6 de § 6 del informe siguen descartados.
6. **No** se toca el registro de acciones, el patrón de colas de
   `ThumbnailService`, la escritura de favoritos, el contrato de
   `_populate_grid` ni ningún ADR.

---

## 5. Tareas

### T3.0 — Línea base y reproducción (obligatoria)

- [x] Ejecutar la verificación de § 2 y anotar aquí el estado inicial
  (número de tests, fallos preexistentes, `git status`).

  > Nota (2026-11-22): `git status --short` solo muestra el prompt de esta
  > fase sin trackear (`?? prompts/ejecutar-fase-3-revision-ui.md`);
  > `git diff --stat` vacío. `uv run pytest`: 1018 tests collected, todos en
  > verde (coincide con el cierre de la fase 2); `uv run ruff check .`:
  > limpio. Referencia rápida de los ficheros afectados (§ 2.3): 128 passed.
- [x] Confirmar por lectura que R8, R9, R10 y R11 siguen presentes. Si
  alguno no se reproduce, documenta la evidencia y la desviación antes de
  continuar.

  > Nota (2026-11-22): los cuatro se reproducen. **R8:**
  > `location_banner_text` (presentation.py:197) llama a
  > `ghosts = _ghost_counts(photos)` antes de mirar
  > `unavailable_locations`. **R9:** `_populate_grid` (window.py:650)
  > construye `groups = [photo_group(photo, self._sort_key) for photo in
  > ordered]` antes del primer lote y lo guarda en `_PopulateState.groups`
  > (window.py:160). **R10:** `_sort_grid` (window.py:1770) no compara
  > `key` con `self._sort_key`; siempre llama a `_populate_grid`. **R11:**
  > `def _photo(` presente en los nueve ficheros del informe (más
  > `test_presentation.py` y `test_tags.py`, ambos fuera de alcance).
  > Desviación menor del enunciado: los tests existentes de `:sort` no
  > viven en `test_ui_commands.py` sino en `test_ui_grid_groups.py`
  > (~224, ~259) y `test_ui_progressive.py` (~263, ~302); los tests nuevos
  > de R10 irán a `test_ui_grid_groups.py`, que es donde vive hoy la
  > prueba de que `:sort` repuebla la rejilla.

**Criterio de cierre:** línea base registrada y alcance confirmado.

---

### T3.1 — R8: el banner no recorre las fotos si no falta nada

- [x] En `location_banner_text`, mover el cálculo del recuento detrás de la
  guarda de ubicaciones, igual que hace `location_banner_parts`:

  ```python
  missing = unavailable_locations(locations)
  if not missing:
      return None
  ghosts = _ghost_counts(photos)
  lines = [
      ...  # bucle sobre `missing`, sin cambios
  ]
  ```

- [x] Mantener el docstring coherente (el «sin fotos no hay recuento» de
  `photos=None` sigue aplicando cuando sí hay ubicaciones caídas).

- [x] **Tests** en `tests/test_presentation.py` (funciones puras, sin Qt):
  1. `test_banner_text_skips_ghost_counts_when_all_available`: envolver
     `presentation._ghost_counts` con un contador (monkeypatch) y llamar a
     `location_banner_text` con todas las ubicaciones disponibles: el
     contador **no se incrementa** y el resultado es `None`.
  2. `test_banner_text_counts_ghosts_only_with_missing_location`: con una
     ubicación activa no disponible y fotos de esa ubicación, el texto
     conserva el recuento correcto y `location_banner_parts` sigue
     devolviendo los mismos dos textos.
  3. Los tests existentes de banner en `test_presentation.py` y
     `test_ui_locations.py` siguen pasando.

**Criterio de cierre:** el caso normal (todo disponible) no paga la pasada
O(n); los textos con ubicaciones caídas no cambian.

---

### T3.2 — R9: grupos por foto dentro del lote, no antes del primer lote

- [x] Eliminar el campo `groups` de `_PopulateState` y su uso en
  `_populate_range`: en el bucle, calcular
  `group_key, group_label = photo_group(photo, self._sort_key)` para la foto
  en curso. `state.current_group` y `state.headers` siguen funcionando igual.
- [x] `_sort_key` no puede cambiar en mitad de un populate (`_sort_grid`
  cancela y repuebla), así que no hace falta congelar la clave en el estado;
  si al leer el código final aparece algún camino que la mute sin repoblar,
  para y repórtalo antes de continuar.
- [x] Actualizar el docstring de `_populate_grid` (hoy dice que orden y
  grupos se calculan una vez antes del primer lote) y el de
  `_populate_range` si menciona el coste de grupos.
- [ ] Medir **antes** del cambio y **después**, con el método de la
  decisión 5 de § 4, y anotar las cifras aquí:
  - populate inicial de 25k;
  - repoblado por `:sort location`.

- [x] **Tests** en `tests/test_ui_progressive.py`:
  1. `test_no_upfront_group_materialization`: con lotes pequeños y populate
     progresivo, envolver `window_module.photo_group` con un contador;
     llamar a `_populate_grid` y comprobar que **antes** de procesar el
     primer lote (`_populate_next_batch` aún no disparado) el contador es
     0, y que tras terminar el populate el total es el número de fotos.
  2. `test_groups_and_headers_survive_mid_populate`: con biblioteca de
     prueba con varios grupos por fecha y por lugar, comprobar cabeceras,
     orden y visibles a mitad de populate y al terminar (los tests
     existentes de grupos y cabeceras deben seguir pasando sin cambios).
  3. Los tests de `:sort` a medio populate (`test_ui_progressive.py` ~263 y
     ~302) siguen pasando.

**Criterio de cierre:** `_PopulateState.groups` no existe; cabeceras y
agrupado intactos; cifras del primer lote antes/después registradas.

> Nota (2026-11-22): código y tests listos; cifras pendientes de anotar.
> El primer intento del script de medición (`scripts/bench_primer_lote.py`,
> desechable y sin commitear) arrancaba el cronómetro después de volver de
> `_populate_grid`, excluyendo justo el coste que R9 mide; corregido para
> que arranque antes de la llamada. El «antes» se mide sobre el código
> previo en un worktree de HEAD y el «después» en el árbol de trabajo.

---

### T3.3 — R10: `:sort` con la misma clave no repuebla

- [x] Al principio de `_sort_grid`, antes de capturar el ítem actual:

  ```python
  if key == self._sort_key:
      self.status.show_message(f"ordenado por {key}", 3000)
      return
  ```

- [x] No tocar el resto de la función: cambiar de clave sigue repoblando,
  restaurando el cursor y reaplicando el filtro como hoy.

- [x] **Tests** en el fichero donde vivan hoy los tests de `:sort`
  (`tests/test_ui_commands.py` o el que corresponda):
  1. `test_same_sort_key_does_not_repopulate`: espía sobre
     `window._populate_grid`; llamar a `_sort_grid(window._sort_key)`
     (p. ej. `"date"` con el orden por defecto) y comprobar que el espía
     **no se llama**, que `_sort_key` no cambia y que el mensaje aparece.
  2. `test_different_sort_key_still_repopulates`: llamar a
     `_sort_grid("camera")` y comprobar que el espía sí se llama.
  3. Los tests existentes de `:sort` (incluido el a medio populate con
     clave distinta) siguen pasando.

**Criterio de cierre:** repetir la misma clave es un no-op barato con
mensaje; cambiar de clave conserva todo el comportamiento actual.

---

### T3.4 — R11: una factoría única para las fotos de los tests

- [x] Crear `tests/ui_factories.py` con `make_photo(name, taken,
  **overrides) -> Photo` copiando los campos por defecto de
  `tests/test_ui_progressive.py` (`name`, `taken`, `camera`, `lens`,
  `focal`, `aperture`, `shutter`, `iso`, `folder`, `fmt`, `dimensions`,
  `size`, `location`, `photo_hash`) y aplicando `overrides` encima. Docstring
  breve en español.
- [x] Migrar los nueve ficheros del informe, borrando su `def _photo(` local
  e importando la factoría (estilo consistente en todos, p. ej.
  `from tests.ui_factories import make_photo`; respeta el que el proyecto ya
  use para módulos compartidos de tests):
  `test_ui_action_registry.py`, `test_ui_documented_features.py`,
  `test_ui_grid_groups.py`, `test_ui_interaction.py`,
  `test_ui_key_inventory.py`, `test_ui_map.py`, `test_ui_progressive.py`,
  `test_ui_search.py`, `test_ui_viewer_layout.py`.
- [x] Ajuste fino por fichero, **sin cambiar el comportamiento de ningún
  test**:
  - donde el helper local tenía defaults distintos (p. ej. `camera`, o
    campos que la factoría única no rellena igual), añadir en las llamadas
    el `override` explícito que reproduzca el valor que el test usaba;
  - `test_ui_action_registry.py` tipa su `_photo` como `object`: verifica
    que sus llamadores solo usan atributos de `Photo` y migrar igual;
  - si algún test depende de constantes acompañantes (p. ej. `NOW`), déjalas
    en su fichero: solo se extrae la factoría.
- [x] **No tocar** `test_tags.py` ni `test_presentation.py`.

- [x] **Verificación de aceptación:**
  1. `grep -n "def _photo(" tests/` ya no muestra ninguno de los nueve
     ficheros migrados (y sigue mostrando el de `test_tags.py`, intacto).
  2. `uv run pytest` completo: mismos tests que la línea base, todos en
     verde, **sin aserciones modificadas**; si alguna hubo que tocar,
     documentar por qué en una nota.
  3. `uv run ruff check .` limpio.

**Criterio de cierre:** una sola factoría, los nueve ficheros migrados, la
suite sin cambios de comportamiento y ruff limpio.

> Nota (2026-11-22): desviaciones menores, sin cambios de comportamiento
> observable: (1) en `test_ui_search.py` el helper viejo derivaba
> `photo_hash` de `abs(hash(name)) % 100000` —aleatorio por
> `PYTHONHASHSEED`, irreproducible—; las llamadas migradas usan el
> `photo_hash=f"sha256:{name}"` de la factoría y ningún test lo observa (la
> búsqueda no indexa el hash). (2) Los campos derivados que sí importan
> (`folder` y `canonical_rel_path`, que ejercitan la búsqueda por ruta) se
> reproducen con overrides explícitos en cada llamada. (3) `test_ui_map.py`
> mapeaba `lat=`/`lon=` a `gps_latitude`/`gps_longitude`: sus dos call
> sites usan ahora esas claves. (4) `test_ui_action_registry.py` perdía el
> tipado `-> object` de su helper: la factoría compartida devuelve `Photo`,
> que es lo que los call sites ya usaban.

---

### T3.5 — Verificación y cierre de la fase

- [ ] `uv run pytest` completo en verde (anotar el número final y la
  comparación con la línea base; R11 no debe añadir tests, R8/R9/R10 sí).
- [ ] `uv run ruff check .` limpio.
- [ ] Revisar `git diff --stat` y el diff completo: solo deben aparecer los
  ficheros del mapa de cambios (§ 7); nada de R5/R7 ni de las
  actualizaciones de ADRs coladas de rondón.
- [ ] Confirmar que las cifras de T3.2 (primer lote antes/después) están
  anotadas en la tarea, incluido el escenario `:sort location`.
- [ ] Smoke manual si hay entorno gráfico: arrancar `uv run foto` sobre una
  biblioteca indexada, probar `:sort location`, repetir `:sort location`
  (no debe reordenar con parpadeo) y comprobar que el banner de ubicaciones
  se comporta como antes. Si no es ejecutable sin operador, documentar la
  limitación como hizo la fase 2.
- [ ] Actualizar este fichero: marcar T3.0–T3.5 con fecha, añadir notas de
  desviación y un párrafo de estado final (§ 9).
- [ ] No hacer commit ni push sin permiso explícito.

**Criterio de cierre:** suite y ruff en verde, diff acotado, cifras
registradas y registro de la fase al día.

---

## 6. Tabla de aceptación global

| ID | Comportamiento | Test / evidencia |
| --- | --- | --- |
| R8 | Todas las ubicaciones disponibles: `_ghost_counts` no se llama | `test_banner_text_skips_ghost_counts_when_all_available` |
| R8 | Con ubicación caída, el recuento sigue siendo correcto | `test_banner_text_counts_ghosts_only_with_missing_location` |
| R9 | Nada se materializa de grupos antes del primer lote | `test_no_upfront_group_materialization` |
| R9 | Grupos y cabeceras intactos a medio populate | `test_groups_and_headers_survive_mid_populate` + tests existentes |
| R9 | Primer lote medido antes/después (y con `:sort location`) | cifras anotadas en T3.2 |
| R10 | `:sort` con la misma clave no repuebla y muestra mensaje | `test_same_sort_key_does_not_repopulate` |
| R10 | `:sort` con clave distinta repobla como hoy | `test_different_sort_key_still_repopulates` |
| R11 | Una sola factoría, nueve ficheros, sin cambios de comportamiento | `grep` + suite completa en verde |

## 7. Mapa de cambios previsto

| Fichero | Zona | Tarea |
| --- | --- | --- |
| `src/foto/ui/presentation.py` | `location_banner_text` | T3.1 |
| `src/foto/ui/window.py` | `_PopulateState`, `_populate_grid`, `_populate_range` (docstrings) | T3.2 |
| `src/foto/ui/window.py` | `_sort_grid` | T3.3 |
| `tests/ui_factories.py` | nueva factoría `make_photo` | T3.4 |
| 9 ficheros `tests/test_ui_*.py` | retirada de `_photo` locales | T3.4 |
| `tests/test_presentation.py` | tests de R8 | T3.1 |
| `tests/test_ui_progressive.py` | tests de R9 (y migración de factoría) | T3.2, T3.4 |
| `tests/test_ui_commands.py` (o donde vivan los tests de `:sort`) | tests de R10 | T3.3 |
| script de medición desechable (o `scripts/bench_arranque.py` si es trivial) | primer lote antes/después | T3.2 |
| `prompts/ejecutar-fase-3-revision-ui.md` | registro de la fase | todas |

## 8. Fuera del alcance de esta fase

- **Descartados por decisión del usuario (no ejecutar):**
  - **R5** — cola del visor sin sustitución ni descarte temprano.
  - **R7** — `busy_timeout` del toggle de favorito (ADR 0006 se queda como
    está).
  - **Actualización de ADRs** — ni ADR 0011 con cifras del objetivo de ~1 s
    ni ningún otro.
  - **Pasos 1–6 de § 6 del informe** — ampliaciones del benchmark («primer
    píxel» real, arranque en frío, percentiles globales, visor, RSS,
    umbrales). La única medición de esta fase es la del primer lote de T3.2.
- **R2/R3 de la fase 2:** la decisión sobre el filtro (p95 > 50 ms por el
  bucle Python↔Qt) queda registrada en
  `prompts/ejecutar-fase-2-revision-ui.md` § 9; esta fase no la retoma.
- **Cambios de contrato:** firma `on_done` de `_populate_grid`, semántica
  observable del `:sort` con clave distinta, textos de UI existentes.
- **Lo prohibido por § 8 del informe:** migrar a `QListView`/modelo, añadir
  capas MVC/gestores de estado/DI, reescribir `ThumbnailService`, tocar el
  registro de acciones, paralelizar decodificación, hacer asíncrona la
  escritura de favoritos, paginar la rejilla y añadir dependencias.
- **Otros ficheros de tests:** `test_tags.py` y `test_presentation.py`
  conservan sus helpers tal cual.

## 9. Estado final de la fase

> **Abortada a medias el 2026-11-22 por decisión del usuario** («Por esta
> interacción es OK. Fin»). El commit `e0128eb` queda como estado válido:
> **1024 passed** (línea base 1018 + 6: 2 de R8, 2 de R9, 2 de R10) y
> `uv run ruff check .` limpio, con R8, R10 y R11 cerradas y R9 cerrada en
> código y tests. La **medición antes/después de R9 quedó a medias**: el
> script desechable (corregido tras detectar que su primer cronómetro
> excluía el coste que se mide) dio el «antes» del populate inicial
> (**mediana 157,2 ms**, muestras 759,4 · 149,4 · 157,2) sobre el código
> previo en un worktree de `c7820d2`; el «antes» de `:sort location` y el
> «después» completo no llegaron a ejecutarse. Queda pendiente, para cuando
> el usuario lo pida: las cifras de T3.2, el smoke manual y este § 9
> completo. Sin `git push`; sin cambios pendientes en el árbol de trabajo
> salvo esta nota.
