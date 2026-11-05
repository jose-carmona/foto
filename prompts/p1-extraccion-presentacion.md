# Prompt: extraer la lógica de presentación pura a `foto.ui.presentation`

> Documento para revisar antes de ejecutar. Tras la revisión, se enviará tal
> cual al agente que aplique los cambios.

## Objetivo

Aplicar exclusivamente la recomendación **P1.4** de `docs/informe-ui.md` (§4):
extraer la lógica de presentación pura que hoy vive atrapada en
`src/foto/ui/window.py` a módulos **sin Qt**, de forma que pueda probarse sin
importar PySide6.

Concretamente:

1. Crear `src/foto/ui/models.py` (sin Qt) con el modelo `Photo`.
2. Crear `src/foto/ui/presentation.py` (sin Qt) con `photo_accessible_name`,
   `photo_search_text`, `photo_sort_value`, `sorted_photos`, `photo_group`,
   `vertical_row_target`, `is_group_start`, `is_group_end`, las constantes que
   consumen (`MONTH_NAMES`, `DEFAULT_SORT`, `SORT_KEYS` y los códigos de tecla
   de grupo) y la aritmética del token de color atenuado (`muted_rgb`).
3. Dejar `window.py` como consumidor: importa de los módulos nuevos y conserva
   solo los adaptadores Qt (`_muted_from_palette`, `_help_muted`).
4. Actualizar todos los puntos de importación (`app.py`, `prototype.py`,
   tests): **sin re-exports** en `window.py`.
5. Reorganizar la suite: nuevo `tests/test_presentation.py` sin Qt con las
   pruebas puras movidas, pruebas unitarias nuevas y un test guardián que
   impida que los módulos puros vuelvan a importar Qt.
6. Actualizar `AGENT.md`, `README.md` y `docs/informe-ui.md` de forma acotada.

Es un refactor de movimiento: **mover, no reescribir**. No cambia ningún
comportamiento visible, ningún atajo, ningún comando y ningún color renderizado.

---

## Decisiones vinculantes y fuera de alcance

Estas decisiones ya están tomadas. No las reabras durante la ejecución:

- **`Photo` va a `foto.ui/models.py`, no a `presentation.py` ni a `core`.**
  La capa de presentación no debe conocer el modelo, solo consumirlo; y el
  informe P1.6 ya contempla un `models.py`, así que se pre-crea su ubicación
  final para no moverlo dos veces. No muevas `Photo` a `foto.core`: el modelo
  de UI no es lógica de negocio y allí no puede depender de textos en español.
- **Sin re-exports.** `window.py` no re-exporta `Photo` ni las funciones
  movidas: una sola ruta canónica de import. Todos los importadores viven en
  el repo (`app.py`, `prototype.py`, `tests/`) y se actualizan.
- **Cuerpos literales.** Las funciones se mueven con su cuerpo, docstring y
  comentarios intactos, salvo dos adaptaciones expresamente previstas:
  - `is_group_start`/`is_group_end` pasan a comparar con constantes enteras
    propias del módulo (ver Tarea 2);
  - `_muted_from_palette` y `_help_muted` se convierten en adaptadores Qt que
    delegan la aritmética en `muted_rgb` (ver Tarea 3).
  No «mejores» algoritmos, no renombres, no cambios de firmas.
- **`Command`, `COMMANDS` y `viewer_key_action` se quedan en `window.py`.**
  El vocabulario de comandos y la traducción de teclas son territorio de P1.5
  (unificar el mapeo de teclas), no de P1.4.
- **`_luminance`, `_contrast_ratio`, `_accent_for` y `error_color` se quedan
  en `window.py`.** Centralizar los tokens de color es P1.7. Solo se extrae la
  lógica del color atenuado, que es la duplicada entre `_muted_from_palette` y
  `_help_muted` (§3.2.E del informe).
- **No partir `window.py` más allá de lo pedido.** Nada de `theme.py`,
  `keybindings.py`, `panels.py` ni `navigation.py`: eso es P1.6 y se hará
  después, con esta base ya colocada.
- **Sin ADR nuevo** y sin cambios en `docs/diseno-ui.md`: es un refactor
  interno de la capa UI que no altera ninguna regla de diseño.
- **Favoritos en XMP y menú contextual:** siguen pospuestos/aplazados al final
  de la Fase 1. No los toques.
- **Funciones retiradas:** no reintroduzcas nada de lo cubierto por
  `tests/test_ui_retired_features.py`. Ese test escanea `src/foto/ui/*.py`,
  así que los módulos nuevos quedan bajo su vigilancia automáticamente.
- **Sin dependencias nuevas** (regla 6 de `AGENT.md`): nada de `pytest-qt` ni
  librerías de color.

Convenciones de siempre: identificadores en inglés; comentarios, docstrings y
textos de UI en español; type hints en todas las funciones públicas;
`from __future__ import annotations`; `ruff` con line-length 100.

---

## Lectura obligatoria antes de tocar nada

Lee completos y contrasta con el código:

1. `AGENT.md` — reglas no negociables, estructura, tests y flujo de trabajo.
2. `docs/informe-ui.md` — §3.2.C, §3.2.E, §3.3, §4 (P1.4), §5 y §6.
3. `src/foto/ui/window.py` — el bloque de modelo y helpers puros
   (líneas ~112–150 y ~252–474 del estado actual), `_help_muted` (~1946) y los
   puntos de uso: `viewer_key_action` (~477–499), `PhotoGrid.move_selection`
   (~1060, ~1135), `PhotoWindow.__init__` (~2239), `_rebuild_grid` (~2435),
   `_update_row_header` (~2488), los límites de grupo (~2849), `_apply_filter`
   (~3078), `_run_command` (~3143) y `_toggle_favorite` (~3391).
4. `src/foto/ui/app.py` — importación de `Photo` (~22) y `_to_ui_photo` (~87).
5. `src/foto/ui/prototype.py` — importación y `demo_thumbnails` (~65).
6. `tests/test_ui_grid_navigation.py` — se mueve entero; es la referencia de
   las pruebas sin Qt.
7. `tests/test_ui_search.py`, `tests/test_ui_grid_groups.py` — mezclan pruebas
   puras y de ventana: solo se mueven las puras.
8. `tests/test_ui_retired_features.py` — patrón de test guardián por escaneo
   de fuentes, y límites que no deben romperse.

Antes de modificar, comprueba el estado del repositorio y ejecuta la suite
completa. No hagas `git commit`, `git push` ni cambios de configuración de git.

---

## Estado de partida

- `uv run pytest -q` pasa completo; la suite de UI son **158 tests**
  (`uv run pytest -q tests/test_ui_*.py`).
- `uv run ruff check .` pasa sin avisos.
- `window.py` tiene 3.453 líneas y concentra el ~84 % de `src/foto/ui/`.
- No existen `src/foto/ui/models.py` ni `src/foto/ui/presentation.py`.
- Los tests de lógica pura (`vertical_row_target`, `photo_search_text`,
  `is_group_start`/`is_group_end`) importan hoy desde `foto.ui.window`, que
  arrastra PySide6: se ejecutan sin pantalla, pero no sin Qt.

---

## Tarea 1 — Crear `src/foto/ui/models.py`

Módulo sin Qt que solo contiene el modelo de UI de una foto:

- `Photo`: el `dataclass(frozen=True)` actual de `window.py` (~252–276), con
  sus campos, su propiedad `key` y su docstring, copiados literales.
- Docstring de módulo en español explicando su papel: modelo inmutable de una
  foto del catálogo tal como la consume la UI (ADR 0003), deliberadamente en
  la capa de UI y no en `core`.
- Imports permitidos: `dataclasses`, `pathlib`. Nada más.

`presentation.py` y `window.py` importarán `Photo` de aquí.

---

## Tarea 2 — Crear `src/foto/ui/presentation.py`

Módulo sin Qt con las reglas de presentación puras. Imports permitidos:
`collections.abc.Sequence` (para `vertical_row_target`) y
`from foto.ui.models import Photo`. Nada más.

Contenido, en un orden legible (agrupa por tema, documenta el módulo con un
docstring que explique que aquí viven las reglas de presentación puras —orden,
grupos, documento de búsqueda, navegación vertical, teclas de grupo y la
aritmética del token fg.muted— y que se prueba sin PySide6):

1. **Nombre accesible y búsqueda** (copiadas literales de `window.py`):
   - `photo_accessible_name` (~279) — incluida por decisión expresa: misma
     familia de «texto de presentación en español» que `photo_group`.
   - `photo_search_text` (~286) — conserva su docstring con la referencia a
     V03 y ADR 0003.
2. **Orden y grupos:**
   - `DEFAULT_SORT`, `SORT_KEYS`, `MONTH_NAMES` (~327–343).
   - `photo_sort_value` (~346), `sorted_photos` (~356), `photo_group` (~401),
     literales. Los nombres de meses en español se quedan aquí, en la capa de
     UI: esa es la decisión explícita que pedía el informe.
3. **Navegación vertical:** `vertical_row_target` (~366), literal.
4. **Teclas de grupo:** `is_group_start` e `is_group_end` (~458–474) con sus
   docstrings, pero comparando con constantes enteras propias del módulo en
   lugar de `Qt.Key_*`:

   ```python
   # Códigos de tecla tal como los entrega QKeyEvent.key(): Qt::Key_0 es 0x30,
   # Qt::Key_Dollar 0x24, Qt::Key_4 0x34 y Qt::Key_5 0x35 (coinciden con ASCII).
   # Duplicados aquí como enteros para no importar Qt desde la capa pura.
   KEY_0 = 0x30
   KEY_DOLLAR = 0x24
   KEY_4 = 0x34
   KEY_5 = 0x35
   ```

   `is_group_end` pasa a usar `KEY_DOLLAR`, `KEY_4` y `KEY_5`;
   `is_group_start`, `KEY_0`. Semántica idéntica: `QKeyEvent.key()` devuelve
   exactamente estos enteros.
5. **Color atenuado, parte pura:** nueva función

   ```python
   def muted_rgb(
       foreground: tuple[int, int, int], background: tuple[int, int, int]
   ) -> tuple[int, int, int]:
   ```

   con la aritmética hoy duplicada entre `_muted_from_palette` y `_help_muted`:

   - `ratio = 0.62` con fondo claro y `0.72` con fondo oscuro;
   - umbral de claridad: `(max(rgb) + min(rgb)) / 2 > 128`, **sin redondear**.
     Es la luminosidad HSL de `QColor.lightness()` en escala 0–255; al no
     redondear, la comparación coincide con la de Qt también en el caso .5
     (Qt redondea 128,5 hacia arriba a 129; ambos dan `> 128` verdadero).
     No uses `round()`: el redondeo bancario de Python divergiría de Qt.
   - interpolación canal a canal:
     `round(bg + (fg - bg) * ratio)` para R, G y B, igual que hoy.

   Docstring en español con el nombre del token (`fg.muted`) y la nota de que
   los adaptadores Qt viven en `window.py`.

---

## Tarea 3 — Dejar `window.py` como consumidor

1. Elimina de `window.py` las definiciones movidas: `Photo`,
   `photo_accessible_name`, `photo_search_text`, `DEFAULT_SORT`, `SORT_KEYS`,
   `MONTH_NAMES`, `photo_sort_value`, `sorted_photos`, `vertical_row_target`,
   `photo_group`, `is_group_start` e `is_group_end`.
2. Añade los imports de `foto.ui.models` y `foto.ui.presentation`, pero solo
   de los nombres que `window.py` usa realmente (ruff marcará los que sobren;
   p. ej. `photo_sort_value` y `MONTH_NAMES` no se usan directamente en la
   ventana). Los puntos de uso internos no cambian: `viewer_key_action`,
   `PhotoGrid`, `_rebuild_grid`, `_apply_filter`, `_run_command`,
   `_toggle_favorite`, etc. siguen llamando a los mismos nombres.
3. Convierte los dos adaptadores de color atenuado en delegados de `muted_rgb`
   (resuelve la duplicación §3.2.E):
   - `_muted_from_palette(palette)` extrae los RGB de `WindowText` y `Window`
     y devuelve `QColor(*muted_rgb(foreground, background))`.
   - `_help_muted()` hace lo mismo sobre la paleta de la aplicación y
     devuelve `QColor(...).name(QColor.HexArgb)`, como hoy.
   - `muted_color(widget)` se queda como está (ya delega en
     `_muted_from_palette`).
   El color renderizado debe ser **idéntico** al de antes, canal a canal.
4. Se quedan en `window.py`, sin tocar: `Command`, `COMMANDS`,
   `viewer_key_action`, `_luminance`, `_contrast_ratio`, `_accent_for`,
   `error_color`, `default_thumbnail`, `ui_font`, las constantes de geometría
   (`THUMB_W`, `ITEM_H`, …), los roles de Qt y todo el resto de widgets.
5. Ajusta el docstring de módulo de `window.py` con una línea que sitúe el
   modelo y la lógica de presentación pura en `models.py`/`presentation.py`.
   Nada más.

---

## Tarea 4 — Actualizar `app.py` y `prototype.py`

- `src/foto/ui/app.py`: importa `Photo` desde `foto.ui.models`; el resto del
  bloque (`THUMB_W`, `THUMB_H`, `PhotoWindow`, `apply_theme`, `ui_font`)
  sigue viniendo de `foto.ui.window`. `_to_ui_photo` no cambia.
- `src/foto/ui/prototype.py`: `Photo` desde `foto.ui.models` y
  `sorted_photos` desde `foto.ui.presentation`; `PhotoWindow`, `apply_theme`
  y `default_thumbnail` siguen desde `foto.ui.window`.

Tras esta tarea, `grep -rn "from foto.ui.window import" src/foto` no debe
mostrar ninguna importación de los símbolos movidos.

---

## Tarea 5 — Reorganizar la suite

### 5.1. Nuevo `tests/test_presentation.py` (sin Qt)

Docstring de módulo en español: es la suite de la capa de presentación pura;
no importa PySide6 ni crea `QApplication`. Contenido:

1. **Movidas desde `tests/test_ui_grid_navigation.py`** (el fichero se elimina
   al quedar vacío; conserva en la sección el docstring que documenta la
   regresión original del movimiento vertical con filas parciales):
   - importa `vertical_row_target` desde `foto.ui.presentation`;
   - sustituye `ITEM_H` (constante de widget que vive en `window.py`) por una
     constante local del test, p. ej. `ROW_H = 263  # THUMB_H + GRID_GAP`,
     con un comentario que explique que cualquier altura constante vale.
2. **Movidas desde `tests/test_ui_search.py`** (solo las dos pruebas puras del
   documento de búsqueda): importan `Photo` desde `foto.ui.models` y
   `photo_search_text` desde `foto.ui.presentation`, con una fábrica local de
   fotos si la necesitan. El resto del fichero no se toca.
3. **Movidas desde `tests/test_ui_grid_groups.py`** (solo las dos pruebas puras
   de detección de teclas), convertidas a enteros sin `QKeyEvent`:
   `is_group_start(KEY_0, False)`, `is_group_end(KEY_DOLLAR, False)`,
   `is_group_end(KEY_4, True)`, `is_group_end(KEY_5, True)`,
   `not is_group_end(KEY_4, False)`, etc. Añade un caso que fije la
   equivalencia con ASCII (`ord("0")`, `ord("$")`, `ord("4")`, `ord("5")`) y
   un comentario que recuerde que son los valores de `Qt::Key_*`. Las pruebas
   con `PhotoWindow` real se quedan en su fichero.
4. **Pruebas unitarias nuevas** para lo que hoy solo se ejercita a través de
   la ventana:
   - `sorted_photos`: por fecha (más reciente primero), por cámara, las fotos
     sin fecha o sin cámara al final, y desempate por nombre con `casefold`.
   - `photo_sort_value`: la tupla `(True, …)`/`(False, …)` que empuja los
     vacíos al final en ambos ordenes.
   - `photo_group`: etiqueta `"mayo de 2024"` con clave `"2024-05"`;
     `"Sin cámara"` y `"Sin fecha"` vacíos; fecha malformada (p. ej.
     `"2024-xx-01"`) que cae a la propia fecha como etiqueta; mes fuera de
     rango (p. ej. `"2024-13-01"`) que también cae a la fecha.
   - `photo_accessible_name`: con y sin favorito.
   - `muted_rgb`: ratio 0.62 con fondo claro y 0.72 con fondo oscuro
     (canales exactos), simetría de la interpolación y la frontera
     `(max + min) / 2` en 128 (oscuro) frente a 128.5 (claro).
5. **Test guardián de pureza**, al estilo de `test_ui_retired_features.py`:
   - escaneo de fuentes de `src/foto/ui/models.py` y
     `src/foto/ui/presentation.py` (casefold) sin ninguno de estos tokens:
     `pyside`, `qtcore`, `qtgui`, `qtwidgets`, `foto.ui.window`,
     `foto.ui.thumbnails`, `foto.ui.app`, `foto.ui.prototype`;
   - una prueba que importe `foto.ui.presentation` en un subproceso
     (`sys.executable -c "..."`) y compruebe que ningún módulo de
     `sys.modules` empieza por `PySide6`; cubre también `models.py` por
     transitividad. No depende de `QT_QPA_PLATFORM`.

### 5.2. Ajustes en los ficheros existentes

- `tests/test_ui_grid_navigation.py`: se elimina (todo su contenido se ha
  movido). Comprueba con `git status` que no queda huérfano de contenido.
- `tests/test_ui_search.py`: quita las dos pruebas puras movidas; importa
  `Photo` desde `foto.ui.models` y `PhotoWindow` desde `foto.ui.window`.
- `tests/test_ui_grid_groups.py`: quita las dos pruebas puras movidas y el
  import de `QKeyEvent` si queda sin uso; importa `Photo` desde
  `foto.ui.models` e `is_group_start`/`is_group_end` ya no desde `window`
  (las pruebas de ventana no las necesitan directamente).
- Ninguna aserción de las pruebas movidas se modifica: solo su ubicación, sus
  imports y las constantes de tecla/altura señaladas arriba.

### 5.3. Prueba de equivalencia del color atenuado (offscreen)

Crea un fichero pequeño de UI (p. ej. `tests/test_ui_muted_color.py`) que
compruebe, con `QT_QPA_PLATFORM=offscreen` y las paletas clara y oscura de
`apply_light_palette`/`apply_dark_palette`, que:

- `_muted_from_palette(palette)` coincide canal a canal con
  `QColor(*muted_rgb(foreground, background))` construido de la misma paleta;
- `_help_muted()` (con la paleta de la aplicación fijada a cada tema) devuelve
  el mismo color que `_muted_from_palette`, en su forma `HexArgb`.

Es la red de seguridad de que la extracción no cambia ni un canal.

---

## Tarea 6 — Documentación

Acotada, sin ADR y sin tocar `docs/diseno-ui.md`:

- `AGENT.md`:
  - «Estado actual»: añade o ajusta una viñeta que sitúe el modelo (`Photo`) en
    `ui/models.py` y las reglas de presentación pura (orden, grupos, búsqueda,
    navegación) en `ui/presentation.py`, ambos sin Qt.
  - árbol de «Estructura actual»: el comentario de `ui/` pasa a mencionar
    ventana, modelo y presentación, demo y miniaturas.
  - sección «Tests»: donde dice que la geometría y los nombres de la caché de
    miniaturas se prueban sin Qt, añade que la lógica de presentación de la UI
    también se prueba sin Qt (`tests/test_presentation.py`).
- `README.md`:
  - el comentario de `ui/` en el árbol (línea ~139) y el párrafo de la Fase 1
    (~66–69) mencionan que la lógica de presentación pura vive en módulos sin
    Qt dentro de `src/foto/ui/`. Una frase, sin arquitectura nueva.

---

## Criterios de aceptación

- `uv run pytest -q` pasa completo y el total de tests **aumenta** respecto a
  158 (se mueven los existentes y se añaden los nuevos; ninguno se pierde).
- `uv run pytest -q tests/test_presentation.py` pasa **sin importar PySide6**
  en ese fichero: no hay `QT_QPA_PLATFORM` ni `QApplication` en él.
- `uv run ruff check .` pasa.
- `tests/test_ui_retired_features.py` sigue pasando.
- El test guardián de pureza (escaneo + subproceso) pasa y fallaría si alguien
  añadiese `import PySide6` o `from foto.ui.window import ...` a
  `models.py`/`presentation.py`.
- `grep -rn "from foto.ui.window import" src tests` no muestra importaciones
  de `Photo` ni de ninguna función movida: no hay re-exports.
- En `window.py` no queda ninguna definición de los símbolos movidos
  (`Photo`, `photo_search_text`, `sorted_photos`, `photo_group`,
  `photo_sort_value`, `vertical_row_target`, `is_group_start`, `is_group_end`,
  `photo_accessible_name`, `MONTH_NAMES`, `DEFAULT_SORT`, `SORT_KEYS`).
- La prueba de equivalencia del color atenuado pasa para los temas claro y
  oscuro: el render es idéntico al anterior.
- No hay dependencias nuevas, no hay ADR nuevo y `docs/diseno-ui.md` no cambia.
- No se ha tocado el comportamiento de favoritos, ni el menú contextual, ni
  las funciones retiradas, ni ningún atajo o comando.

---

## Actualización del informe

Al terminar, actualiza `docs/informe-ui.md` de forma acotada, como se hizo con
el P0:

- Añade al encabezado una línea «Aplicación del P1.4 (fecha)» resumiendo el
  movimiento a `ui/models.py` y `ui/presentation.py` y el nuevo test guardián.
- §3.2.C y §3.2.E: márcalos como resueltos, conservando el razonamiento.
- §2.1: actualiza el recuento de líneas de `window.py` (con `wc -l` real) y
  añade las filas de `models.py` y `presentation.py`.
- §4: marca la recomendación P1.4 como aplicada.
- §5: ajusta la fila sobre la regla 5 («lógica de presentación en la UI» ya
  resuelta; siguen pendientes XMP y el aislamiento de plataforma).
- No reescribas el informe completo ni cambies sus conclusiones de arquitectura.

---

## Entregables

- `src/foto/ui/models.py` y `src/foto/ui/presentation.py` nuevos, sin Qt.
- `src/foto/ui/window.py` aligerado y consumiendo los módulos nuevos.
- `src/foto/ui/app.py` y `src/foto/ui/prototype.py` con imports actualizados.
- `tests/test_presentation.py` nuevo (pruebas movidas + unitarias + guardián),
  `tests/test_ui_grid_navigation.py` eliminado, `tests/test_ui_search.py` y
  `tests/test_ui_grid_groups.py` adelgazados, y la prueba offscreen de
  equivalencia del color atenuado.
- `AGENT.md`, `README.md` y `docs/informe-ui.md` actualizados de forma acotada.
- Resumen final con los comandos ejecutados (`pytest`, `ruff`, `wc -l`,
  comprobaciones `grep`) y su resultado.
