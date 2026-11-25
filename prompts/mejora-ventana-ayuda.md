# Mejora de la ventana de ayuda: plan de ejecución

- **Fecha:** 2026-11-25
- **Ámbito:** `src/foto/ui/help.py`, `src/foto/ui/theme.py`, `src/foto/ui/window.py`,
  `src/foto/ui/panels.py`, `src/foto/ui/presentation.py`,
  `src/foto/resources/ayuda.md`, `docs/diseno-ui.md` y tests de UI.
- **Estado:** fases 0, 1 y 2 cerradas y decisiones cerradas. De la fase 3 solo
  entró la ayuda contextual, absorbida en T2.4. Los cambios de la fase 2 están
  en el árbol, pendientes del commit.
- **Objetivo:** mejorar el aspecto y la usabilidad del overlay de ayuda (`?` / `F1` /
  `:help`) sin cambiar su contrato: `ayuda.md` sigue siendo la fuente única del
  contenido y el render sigue siendo propio, con el tema activo.

> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada una:
> márcala `[x]` con la fecha y, si hubo desviaciones del enunciado, añade debajo una
> línea `> Nota (fecha): …`. No reordenes ni borres tareas; si aparece una pendiente
> nueva, añádela al final de su fase con el mismo formato. Una tarea solo está
> cerrada con sus tests en verde y `uv run pytest` + `uv run ruff check .` completos.
> Las tareas de código pueden empezar: la fase 1 está cerrada y la fase 2 lista.

---

## 0. Resumen y estado

- El mayor problema no es de diseño: **las fichas de tecla son invisibles** porque
  comparten color con el fondo del overlay, y **los párrafos se rompen** porque el
  renderer trata cada línea del markdown como un párrafo.
- La fase 1 corrige eso y completa las teclas de scroll. No cambia el contenido.
- La fase 2 añade índice fijo, cabecera, apertura contextual, tamaño responsive,
  tipografía, interlineado y contenido reordenado.
- Se descartan el filtro `/`, el cierre con ratón y la selección de texto con teclado.

| Fase | Estado | Tareas |
| --- | --- | --- |
| 0. Documento, decisiones y punto de retorno | Hecho | T0.1–T0.4 |
| 1. Render y navegación P0 | Hecho | T1.1–T1.5 |
| 2. Índice, cabecera, responsive y contenido | Hecho | T2.1–T2.13 |
| 3. Filtro, ratón y selección | **Descartado** | — |
| 3b. Ayuda contextual | Incluida en T2.4 | — |

---

## 1. Decisiones cerradas (2026-11-25)

| # | Decisión | Elección | Consecuencia |
| --- | --- | --- | --- |
| 1 | Alcance | **Fases 1 y 2**; de la fase 3 solo la ayuda contextual | No hay filtro `/`, ni cierre con ratón, ni selección con teclado |
| 2 | Apertura | **Según la vista actual** | `?`, `F1` y `:help` abren en la sección de la vista; sin contexto, en el inicio |
| 3 | Índice | **Cabecera fija con índice** | El índice no scrollea; el documento conserva su título `# Ayuda` en el cuerpo |
| 4 | Cierre con ratón | **Solo teclado** | No hay «✕» ni cierre al hacer clic fuera; `Esc` y `q` cierran |
| 5 | Ancho | **960 px responsive** | El panel de ubicaciones conserva su máximo de 760 px |
| 6 | Contenido | **Reordenar y condensar** | Orden: Globales, Rejilla, Visor, Panel, Comandos, Ubicaciones |
| 7 | Interlineado | **Aplicar 1,4** | Se aplica con `QTextBlockFormat` después de `setHtml` |
| 8 | Búsqueda | **Sin búsqueda; índice 1–9** | Se descartan el filtro `/` y `QTextDocument.find` |
| 9 | Documento | **Un solo documento** | Este fichero contiene propuesta, decisiones, tareas y registro |

---

## 2. Comportamiento acordado

### 2.1 Apertura contextual

| Contexto | Sección |
| --- | --- |
| Visor abierto (`_viewer_visible`) | `Visor` |
| Foco en el panel (`panel.scroll.hasFocus()`) | `Panel de metadatos` |
| Foco en la rejilla (`grid.hasFocus()`) | `Rejilla` |
| Sin contexto reconocible | Inicio (sin ancla) |

- `?`, `F1` y `:help` usan el mismo criterio.
- Si el título no existe en `ayuda.md`, se abre en el inicio.
- Con la ayuda ya visible, `?` / `F1` la cierran como hoy; no cambian de sección.

### 2.2 Cabecera fija

```
┌ 1 Globales · 2 Rejilla · 3 Visor · 4 Panel · 5 Comandos · 6 Ubicaciones   Esc/q cerrar ┐
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Ayuda                                                                                    │
│ Todo funciona con el teclado: no hay menús ni ratón necesario…                           │
│ …                                                                                        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- Cabecera: índice generado desde los encabezados `##` y pista `Esc/q cerrar`.
- Sin botones ni «✕»: el cierre es solo por teclado.
- El cuerpo conserva el título `# Ayuda`; la cabecera no lo duplica.
- El índice no scrollea; el cuerpo sí.

### 2.3 Teclas de la ayuda

| Tecla | Acción |
| --- | --- |
| `↑` / `↓` o `j` / `k` | Una línea arriba / abajo |
| `PgDn` / `PgUp` | Una página abajo / arriba |
| `Espacio` / `Shift+Espacio` | Una página abajo / arriba |
| `Home` / `End` | Principio / final de la ayuda |
| `Ctrl+d` / `Ctrl+u` | Media página abajo / arriba |
| `1`–`9` | Saltar a la sección N |
| `Esc` / `q` | Cerrar la ayuda |

- El paso de línea sale de las métricas de la fuente del documento, no de 40 px
  fijos.
- El comportamiento debe ser el mismo con el foco en el overlay y con el foco
  dentro del navegador tras un clic. La solución más simple es que el navegador no
  reciba foco; si se mantiene, hay que enrutar sus teclas al mismo handler.

### 2.4 Tamaño, tipografía y render

- Geometría de la ayuda: ancho `min(960, contenido - 64)`, alto
  `min(820, contenido - 64)`, mínimos 320×260. El panel de ubicaciones sigue con
  máximo 760.
- Escala: `h1` 20 px/600, `h2` 15 px/600, cuerpo 13 px, cabecera de tabla 12 px/600
  en `fg.muted`.
- Interlineado 1,4 en todos los bloques; si las tablas se desbordan, aplicar solo a
  párrafos y encabezados.
- Fichas: fondo derivado de la superficie del overlay, texto `WindowText`, mono 12 px
  y contraste ≥ 4,5:1 en claro y oscuro.
- Párrafos: líneas consecutivas se unen; una línea en blanco separa párrafos.
- Tabla: sin líneas internas, teclas ~38 % y acciones ~62 %, cabecera atenuada.
- Reglas de sección con `Midlight`, no con `Mid`.

### 2.5 Contenido

- Orden: Globales, Rejilla, Visor, Panel de metadatos, Comandos, Ubicaciones.
- La introducción añade que `q` cierra la ayuda y resume las teclas de navegación
  internas (`PgUp`/`PgDn`, `Home`/`End`, `Espacio`, `Ctrl+d`/`Ctrl+u`, `1`–`9`).
- «Ubicaciones» se condensa a 2–3 frases y su tabla.
- «Panel de metadatos» reduce la prosa a una línea de contexto y su tabla.
- `docs/diseno-ui.md` documenta las teclas nuevas, la apertura contextual, la
  cabecera fija y el máximo de 960 px.

### 2.6 Descartado por decisión

- **Filtro `/` y búsqueda interna:** con seis secciones, el índice `1`–`9` es
  suficiente.
- **Cierre con ratón («✕» y clic fuera):** la ayuda sigue siendo teclado-primero; la
  pista de cierre va en la cabecera y en la barra de estado.
- **Selección de texto con teclado:** no entra en esta entrega; la selección con
  ratón se mantiene.

---

## 3. Diagnóstico que motiva el plan

### 3.1 Defectos

| # | Hallazgo | Evidencia | Dónde |
| --- | --- | --- | --- |
| D1 | **Las fichas de tecla son invisibles** | `_help_chip()` usa `QPalette.AlternateBase` como fondo; `HelpOverlay` pinta su propia `Window` con ese mismo `AlternateBase`. Claro: `#fafafa` sobre `#fafafa`. Oscuro: `#383838` sobre `#383838`. | `help.py:76-84`, `help.py:222-224` |
| D2 | **Cada línea del markdown es un párrafo** | `render_help` añade un bloque `paragraph` por cada línea no vacía. Los párrafos escritos a mano se rompen en 3–6 párrafos con huecos y frases partidas. | `help.py:173-191` |
| D3 | **Espacios dobles alrededor de las fichas** | `_help_inline` une los trozos con `" ".join(...)` y la ficha ya lleva `nbsp` de padding: «Esc  siempre», «Ctrl+q  sale». | `help.py:92-99` |
| D4 | **Jerarquía tipográfica fuera de escala** | `h1` a 26 px y `h2` a 20 px; la guía fija títulos de 15 y 20 px. Cada encabezado lleva un `hr` con `Mid`; la cabecera «Tecla/Acción» compite con el título. | `help.py:102-109`, `help.py:128-142` |
| D5 | **Tamaño fijo poco adaptable** | 760×708 siempre. En 4K se queda pequeño; en 640×480 deja 388 px de alto. | `window.py:1080-1087` |
| D6 | **Navegación de scroll incompleta** | Con el foco en el overlay solo funcionan ↑/↓ y j/k, con paso fijo de 40 px. `PgDn`, `PgUp`, `Home`, `End`, `Espacio` y `Ctrl+d/u` no hacen nada. | `help.py:267-284` |
| D7 | **Sin índice ni orientación** | Seis secciones y ~2,5 visibles por pantalla; no hay forma de saltar ni de saber dónde se está. | `ayuda.md`, `help.py` |
| D8 | **Sin cierre visible en el overlay** | El cierre depende de la barra de estado, que además cambia entre «Esc/q cerrar» y «Esc cerrar». | `panels.py:564-575`, `panels.py:665-679` |
| D9 | **Semántica de accesibilidad pobre** | Encabezados como `<p>` estilados, cabeceras de tabla como `<td><b>` y `body` sin nombre accesible. | `help.py:102-142`, `help.py:230-243` |
| D10 | **Contenido desequilibrado** | «Ubicaciones» es un manual de ~150 palabras en medio de una ayuda de atajos; «Panel de metadatos» tiene prosa larga. | `ayuda.md` |

### 3.2 Restricciones técnicas comprobadas

- Qt rich text **sí** aplica `background-color` en un `<span>` inline, pero **ignora**
  `padding`, `border` y `border-radius` en ese contexto. La ficha puede tener fondo y
  texto monoespaciado, pero será plana; el aire horizontal se consigue con `nbsp`.
- Qt rich text **ignora** `line-height` en el CSS inline. Hay que aplicar 1,4 con
  `QTextBlockFormat.setLineHeight` después de `setHtml`.
- `<h2 style="...">` y `<th align="left">` sí funcionan y permiten mantener la
  semántica sin perder el control visual.

---

## 4. Tareas

### Fase 0 — Documento, decisiones y punto de retorno

- [x] **T0.1 — Redactar la propuesta y crear el punto de retorno.**
  - **Fecha:** 2026-11-25.
  - **Resultado:** `prompts/mejora-ventana-ayuda.md` con diagnóstico, propuesta y
    decisiones abiertas.
  - **Punto de retorno:** commit `51c1c4a` (`añade propuesta de mejora de la
    ventana de ayuda`).
  - **Hecho cuando:** el documento existe y el árbol queda limpio tras el commit.

- [x] **T0.2 — Cerrar decisiones y convertir el documento en plan accionable.**
  - **Fecha:** 2026-11-25.
  - **Resultado:** § 1 con las nueve decisiones cerradas, § 2 con el comportamiento
    acordado, § 4 con las tareas de las fases 1 y 2 y el registro de avance.
  - **Hecho cuando:** no quedan decisiones abiertas y lo descartado está registrado.

- [x] **T0.3 — Revisión y visto bueno del plan.**
  - **Fecha:** 2026-11-25.
  - **Responsable:** proyectista.
  - **Resultado:** visto bueno; autoriza empezar la fase 1.
  - **Bloqueaba:** T1.1. Ya desbloqueada.

- [x] **T0.4 — Commit del plan actualizado.**
  - **Fecha:** 2026-11-25.
  - **Hecho cuando:** el proyectista autoriza el commit, el mensaje es corto e
    imperativo y `git status` queda limpio.
  - **Resultado:** plan actualizado commiteado; el hash queda en el historial de git.

### Fase 1 — Render y navegación P0

- [x] **T1.1 — Fichas de tecla visibles y con contraste.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/theme.py`, `src/foto/ui/help.py`,
    `tests/test_ui_help.py`.
  - **Cambio:** añadir a `theme.py` un token `help_chip_surface(...)` que derive la
    superficie de la ficha de la superficie del overlay (claro: oscurecer; oscuro:
    aclarar). `_help_chip()` lo consume y usa `WindowText` para el texto. Mantener la
    monoespaciada de 12 px y el padding con `nbsp`.
  - **Resultado:** en claro y oscuro la ficha se distingue del fondo; el texto
    mantiene 4,5:1.
  - **Tests:** aplicar `apply_light_palette` y `apply_dark_palette`, renderizar la
    ayuda, extraer el `background-color` y el `color` de una ficha y comprobar que el
    fondo difiere del fondo del overlay y que el contraste es ≥ 4,5:1.
  - **Hecho cuando:** los tests nuevos pasan en ambos temas.

- [x] **T1.2 — Párrafos agrupados y con aire.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** en `render_help`, acumular líneas consecutivas hasta una línea en
    blanco, una tabla o un encabezado; emitir un solo `<p>` por bloque. Añadir
    `margin-bottom: 12px` al párrafo.
  - **Resultado:** un párrafo escrito en varias líneas se lee como un párrafo; no hay
    frases partidas ni huecos accidentales.
  - **Tests:** `render_help("l1\nl2\n\nl3")` contiene `l1 l2` en un solo párrafo y
    `l3` en otro; no contiene `l1</p><p>l2`.
  - **Hecho cuando:** el documento real renderiza la introducción y «Ubicaciones»
    como párrafos completos.

- [x] **T1.3 — Espacios de ficha sin duplicar ni chocar con puntuación.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** sustituir `" ".join(pieces)` por `"".join(pieces)`. Ajustar el padding
    de la ficha según el contexto: sin `nbsp` final antes de `, . ; : ! ? ) »` y sin
    `nbsp` inicial después de `( [ { «`. Mantener el padding en tablas y en los
    extremos de una celda.
  - **Resultado:** «Esc siempre», no «Esc  siempre»; «config.toml, ADR», no
    «config.toml , ADR».
  - **Tests:** `render_help("texto `Esc` siempre")` no contiene `Esc\u00a0\u00a0  `;
    `render_help("texto `config.toml`, ADR")` no contiene `\u00a0</span>,`.
  - **Hecho cuando:** los tests nuevos pasan y `_help_cell_width` sigue midiendo las
    tablas sin partir filas.

- [x] **T1.4 — Teclas de scroll completas y con paso consistente.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** ampliar `HelpOverlay.keyPressEvent` con `PgUp`/`PgDn`,
    `Home`/`End`, `Espacio`/`Shift+Espacio` y `Ctrl+d`/`Ctrl+u`. Calcular el paso de
    línea con `QFontMetrics` del documento y la página con `bar.pageStep()`.
  - **Resultado:** todas las teclas de § 2.3 funcionan; el paso no cambia según dónde
    esté el foco.
  - **Tests:** con la ayuda abierta y el foco en el overlay, cada tecla mueve la barra
    en la cantidad esperada; `Home` lleva al mínimo y `End` al máximo. Si se mantiene
    el foco en el navegador, repetir el test tras un clic y comprobar el mismo paso.
  - **Nota:** en una prueba offscreen, un `QTextBrowser` con `NoFocus` sigue
    permitiendo la selección con ratón; si se elige esa vía, añadir una comprobación
    de que seleccionar texto no devuelve el foco al navegador.
  - **Hecho cuando:** los tests cubren todas las teclas y no hay regresión de j/k.
  - **Resultado:** el cuerpo usa `Qt.NoFocus`; las teclas llegan siempre al overlay y
    un clic sobre el texto no le devuelve el foco.

- [x] **T1.5 — Verificación de la fase 1.**
  - **Fecha:** 2026-11-25.
  - **Comandos:** `uv run pytest tests/test_ui_help.py tests/test_ui_styles.py`,
    `uv run pytest`, `uv run ruff check .`.
  - **Revisión visual:** capturas en claro y oscuro a 1280×800 y 640×480; comprobar
    fichas, párrafos, espacios y scroll.
  - **Hecho cuando:** suite y ruff en verde y las capturas no muestran regresiones.
  - **Nota:** registrar el resultado en § 7.
  - **Resultado:** las tres órdenes en verde. Las fichas se distinguen en ambos temas,
    los párrafos se leen completos, no hay espacios duplicados y el scroll responde;
    la geometría sigue siendo la de la fase 2 (760×708 y 576×388).
  - **Nota (2026-11-25):** el fixture `qapp` de `tests/test_ui_help.py` aplica
    `_apply_system_font` y es autouse: sin una `QApplication` con fuente resuelta, Qt
    aborta al medir las fichas en la plataforma `offscreen` y el comando por módulos
    del plan no arrancaba.

### Fase 2 — Índice, cabecera, responsive y contenido

- [x] **T2.1 — Modelo puro de secciones y anclas.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** añadir `HelpSection` (anchor, title) y
    `help_sections(markdown) -> tuple[HelpSection, ...]`; las anclas son `s1`, `s2`, …
    en orden de los encabezados `##`. `render_help` inserta
    `<a name="sN"></a>` antes del encabezado correspondiente.
  - **Resultado:** el índice y el render comparten el mismo orden; las anclas son
    estables.
  - **Tests:** `help_sections` devuelve los títulos en orden y `render_help` contiene
    las mismas anclas. No se indexa el `# Ayuda` inicial.
  - **Hecho cuando:** los tests puros pasan sin Qt y el HTML contiene las anclas.
  - **Resultado:** `HelpSection` y `help_sections` comparten con el parser el
    recorte de títulos, de modo que índice y anclas coinciden 1:1; el HTML lleva
    `<a name="sN">` antes de cada `##`.

- [x] **T2.2 — Cabecera fija con índice y pista de cierre.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `src/foto/ui/theme.py`,
    `tests/test_ui_help.py`.
  - **Cambio:** añadir a `HelpOverlay` un `QWidget` de cabecera con un `QLabel` de
    índice (`1 Globales · 2 Rejilla · …`) y otro con `Esc/q cerrar`. El índice se
    genera desde `self.sections`, usa `ui_font(12)` y `set_muted`; el hint va a la
    derecha. La cabecera no scrollea; el `QTextBrowser` queda debajo.
  - **Resultado:** el índice siempre visible; sin botones ni «✕».
  - **Tests:** la cabecera contiene los títulos de todas las secciones; no hay
    `QToolButton` de cierre; el índice tiene nombre accesible.
  - **Hecho cuando:** a 960 y a 576 px el índice se lee (puede ocupar dos líneas) y no
    tapa el cuerpo.
  - **Resultado:** índice atenuado con `wordWrap` (a 576 px ocupa dos líneas) y
    pista a la derecha; debajo, un filete de 1 px en `Midlight`. `theme.py` no
    necesitó cambios («QSS de la cabecera si hace falta» no hizo falta).

- [x] **T2.3 — Teclas 1–9 para saltar a secciones.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** en `keyPressEvent`, si la tecla es un dígito `1`–`9` sin
    `Ctrl`/`Alt`/`Meta`, saltar a la sección N con `body.scrollToAnchor(anchor)`.
    Ignorar dígitos fuera de rango. Guardar la sección actual en
    `HelpOverlay.current_section` para los tests.
  - **Resultado:** `1`–`6` llevan a las seis secciones; `7`–`9` no hacen nada.
  - **Tests:** pulsar `2` deja `current_section == "Rejilla"` y la barra por encima del
    mínimo; `Home` vuelve al inicio.
  - **Hecho cuando:** los tests pasan con la ayuda abierta desde la ventana.
  - **Resultado:** ``1`–`6`` saltan, ``7`–`9`` se ignoran y `Home` vuelve al
    principio; el salto también funciona con la ayuda abierta desde la ventana.
  - **Nota (2026-11-25):** el salto exige además el dígito sin `Shift` (el
    enunciado solo excluía `Ctrl`/`Alt`/`Meta`): `Shift+dígito` es otro carácter
    en la mayoría de las distribuciones.

- [x] **T2.4 — Apertura contextual según la vista.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/presentation.py`, `src/foto/ui/window.py`,
    `src/foto/ui/help.py`, `tests/test_presentation.py`, `tests/test_ui_help.py`.
  - **Cambio:** añadir a `presentation.py` una función pura
    `help_section_for_context(*, viewer_visible, panel_focused, grid_focused) -> str | None`
    con el mapeo de § 2.1. En `_toggle_help`, calcular la sección y llamar a
    `help_overlay.show_section(...)` después de `show()`/`raise_()`/`setFocus()`.
    `:help` usa el mismo camino porque llama a `_toggle_help`.
  - **Resultado:** el visor abre en «Visor», el panel en «Panel de metadatos», la
    rejilla en «Rejilla» y sin contexto en el inicio.
  - **Tests:** unitarios del mapeo puro; integración con foco en rejilla y panel.
    Para el visor, usar el mapeo puro o abrir el visor con el fixture existente.
  - **Hecho cuando:** los tres contextos abren en su sección y un título inexistente
    cae al inicio.
  - **Nota:** si `scrollToAnchor` no posiciona en el primer `show`, aplicar la sección
    pendiente en `showEvent` o con `QTimer.singleShot(0, …)`; verificar en tests con
    `processEvents`.
  - **Resultado:** el contexto se lee antes de mover el foco al overlay y el salto
    se aplica con `QTimer.singleShot(0, …)`; sin contexto (o con título
    inexistente) se restablece el scroll al inicio, también al reabrir.

- [x] **T2.5 — Barra de estado en modo AYUDA.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/panels.py`, `src/foto/ui/window.py`,
    `tests/test_ui_status_bar.py`, `tests/test_ui_help.py`.
  - **Cambio:** `set_help_mode(enabled, sections=0)` muestra en el área izquierda
    `j/k · PgUp/PgDn · Home/End · 1–N secciones` y en la derecha `Esc/q cerrar`.
    Unificar `refresh_right` para que el texto derecho sea siempre `Esc/q cerrar` en
    modo ayuda. Al salir, restaurar el mensaje anterior y el color normal.
  - **Resultado:** la ayuda enseña sus propias teclas sin depender de la cabecera; no
    queda el texto «Esc cerrar» suelto.
  - **Tests:** activar el modo ayuda, comprobar ambos textos; llamar a `refresh_right`
    y comprobar que no cambia; salir y comprobar que se restaura el mensaje.
  - **Hecho cuando:** los tests pasan y el mensaje no conserva el color de un error
    anterior.
  - **Resultado:** la pista izquierda entra en `_apply_message_text` (el error sigue
    ganando) y `refresh_right` deja el cierre unificado; al salir se restauran el
    mensaje y el color normal.

- [x] **T2.6 — Geometría responsive de 960 px.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/window.py`, `tests/test_ui_help.py`,
    `tests/test_ui_locations.py` si cubre geometría.
  - **Cambio:** parametrizar `_center_overlay` con `max_width` y `max_height`; la
    ayuda usa `max_width=960` y `max_height=820`; el panel de ubicaciones conserva
    760. Mantener la fórmula de márgenes de 64 px y los mínimos actuales.
  - **Resultado:** a 1280×800 la ayuda mide 960 de ancho; a 640×480, 576; a 1920×1080
    no supera 960×820.
  - **Tests:** redimensionar la ventana y comprobar `help_overlay.width()` y que el
    panel de ubicaciones sigue con su máximo.
  - **Hecho cuando:** los tests pasan y no hay recortes en 640×480.
  - **Resultado:** 960 a 1280×800, 576 a 640×480 y máximo 820 de alto a
    1920×1080; `test_ui_locations.py` no cubre geometría y no necesitó cambios.

- [x] **T2.7 — Escala tipográfica, reglas y tabla.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** `h1` 20 px/600, `h2` 15 px/600; cabecera de tabla 12 px/600 en
    `fg.muted`; reglas con `Midlight`; espaciado de § 2.4. Usar `<th>` para la
    cabecera y `<td>` para las filas.
  - **Resultado:** el título no domina la pantalla; las tablas se escanean y la
    cabecera no compite con el título.
  - **Tests:** el HTML contiene `font-size:20px`, `font-size:15px`, `<th` y el color
    atenuado; la fila de teclas conserva un único ancho de columna.
  - **Hecho cuando:** los tests pasan y la captura a 640×480 sigue siendo legible.
  - **Resultado:** escala aplicada y cabecera de tabla alineada a la izquierda,
    atenuada; la columna de teclas conserva su único ancho.
  - **Nota (2026-11-25):** el `<hr>` pinta con `background-color`, no con `color`:
    el QSS del cuerpo (`color: palette(window-text)`) hacía que Qt ignorara el
    color inline de la regla y la pintara negra. El token sigue siendo
    `Midlight`. Además, `th` lleva `align="left"` porque Qt centra su contenido
    por defecto.

- [x] **T2.8 — Interlineado 1,4.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** tras `setHtml`, recorrer los bloques del documento y aplicar
    `QTextBlockFormat.setLineHeight(140, ProportionalHeight)`. Reaplicar en cada
    `_render` y conservar el scroll en `retheme`.
  - **Resultado:** el texto respira como pide la guía; las tablas no se desbordan.
  - **Tests:** el primer bloque tiene `lineHeight() == 140` y el tipo proporcional.
  - **Hecho cuando:** el test pasa y una captura confirma que las tablas siguen
    completas. Si alguna tabla se desborda, aplicar solo a párrafos y encabezados y
    anotarlo.
  - **Resultado:** se aplica a todos los bloques (incluidos los de las celdas) en
    cada `_render`; `retheme` conserva el scroll y las tablas quedan completas en
    las capturas.

- [x] **T2.9 — Reordenar y condensar `ayuda.md`; actualizar `docs/diseno-ui.md`.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/resources/ayuda.md`, `docs/diseno-ui.md`,
    `tests/test_ui_help.py`, `tests/test_ui_documented_features.py`,
    `tests/test_ui_key_inventory.py`.
  - **Cambio:** orden de § 2.5; introducción con `q` y las teclas internas;
    «Ubicaciones» y «Panel de metadatos» condensados. En `docs/diseno-ui.md`, añadir
    las teclas de § 2.3, la apertura contextual, la cabecera fija y el máximo de
    960 px.
  - **Resultado:** la ayuda responde primero «¿qué tecla pulso?»; todas las teclas
    nuevas están en los tres sitios (código, guía y `ayuda.md`).
  - **Tests:** las secciones siguen existiendo; todos los comandos documentados siguen
    en la ayuda; las teclas nuevas aparecen en `ayuda.md` y en `docs/diseno-ui.md`.
  - **Hecho cuando:** suite completa en verde y el documento se lee de arriba abajo
    sin prosa sobrante.
  - **Resultado:** orden Globales → Rejilla → Visor → Panel → Comandos →
    Ubicaciones; introducción con `q` y las teclas internas; «Ubicaciones» en tres
    frases y «Panel» en una línea de contexto. Los tests de contrato
    (`test_ui_documented_features.py`, `test_ui_key_inventory.py`) no necesitaron
    cambios: las tablas que escanean no se tocaron.

- [x] **T2.10 — Semántica accesible.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** usar `<h1>`/`<h2>` para el título y las secciones, `<th>` para la
    cabecera de tabla y `body.setAccessibleName("atajos de teclado")`; nombre
    accesible para el índice de la cabecera.
  - **Resultado:** el lector de pantalla encuentra encabezados y cabeceras; el cuerpo
    tiene nombre.
  - **Tests:** el HTML contiene `<h1`, `<h2` y `<th`; `body.accessibleName()` es el
    esperado. Actualizar `test_rejected_syntax_stays_out_of_the_renderer`, que hoy
    espera `T</p>`, a la etiqueta de encabezado que se use.
  - **Hecho cuando:** los tests pasan y no se pierde el estilo visual.
  - **Resultado:** `<h1>`/`<h2>` conservan la escala de T2.7; el cuerpo se llama
    «atajos de teclado» y el índice «índice de la ayuda»; el test de sintaxis
    rechazada espera `T</h1>`.

- [x] **T2.11 — Verificación de la fase 2.**
  - **Fecha:** 2026-11-25.
  - **Comandos:** `uv run pytest`, `uv run ruff check .`.
  - **Revisión visual:** 640×480, 1280×800 y 1920×1080, claro y oscuro. Comprobar
    índice, teclas `1`–`6`, apertura contextual desde rejilla/panel/visor, cierre con
    `Esc`/`q`, ausencia de «✕» y de cierre al hacer clic fuera, y que no hay recortes.
  - **Documentación:** `docs/diseno-ui.md` y `ayuda.md` actualizados.
  - **Hecho cuando:** suite y ruff en verde; capturas revisadas; `git diff` limitado a
    los ficheros de § 6.
  - **Resultado:** 1179 tests y ruff en verde; `git diff` limitado a `help.py`,
    `window.py`, `panels.py`, `presentation.py`, `ayuda.md`, `diseno-ui.md` y los
    tres ficheros de tests (`theme.py` no necesitó cambios). Capturas revisadas a
    1280×800, 640×480 y 1920×1080 en claro y oscuro.
  - **Nota (2026-11-25):** la revisión visual se hizo sobre capturas de la
    plataforma `offscreen`; las teclas (`1`–`6`, `Esc`/`q`, scroll) se verifican
    con los tests de `test_ui_help.py`, no de forma interactiva.

- [x] **T2.12 — `:help` abre al inicio (Globales), no en la sección de la rejilla.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/window.py`, `docs/diseno-ui.md`,
    `tests/test_ui_help.py`.
  - **Cambio:** `_toggle_help` gana el parámetro `contextual=True`; el comando
    `:help` llama con `contextual=False` y abre sin ancla. Ajusta § 2.1: `?` y
    `F1` siguen el criterio contextual, `:help` no.
  - **Resultado:** `:help` abre en el inicio aunque la rejilla haya recobrado el foco.
  - **Nota (2026-11-25):** en cocoa, `_enter` cierra el prompt antes de emitir
    el comando y el foco vuelve a la rejilla; `_toggle_help` interpretaba ese
    foco heredado como contexto y abría en «Rejilla». En `offscreen` la
    restauración de foco no ocurre, por eso las capturas de T2.11 no lo vieron.
    El test simula el estado de cocoa (rejilla con foco al ejecutar el comando)
    para no depender de la plataforma.

- [x] **T2.13 — El texto del cuerpo no hereda el color QSS al cambiar de tema.**
  - **Fecha:** 2026-11-25.
  - **Ficheros:** `src/foto/ui/help.py`, `tests/test_ui_help.py`.
  - **Cambio:** párrafos y celdas de tabla llevan su color inline (igual que
    encabezados y fichas) y `HelpOverlay.retheme` llama a `reapply_style(self.body)`
    para re-parsear `HELP_BODY_QSS`.
  - **Resultado:** tras un cambio oscuro→claro en vivo, el cuerpo no se queda con
    el texto blanco del tema anterior; el asa de la scrollbar tampoco.
  - **Nota (2026-11-25):** los párrafos eran el único texto sin color explícito y
    dependían de `color: palette(window-text)` del QSS; Qt no reinterpreta las
    funciones `palette(...)` al cambiar la paleta (mecanismo ya documentado en
    `reapply_style`). Las capturas de T2.11 no lo vieron porque cada captura
    arrancaba con su tema ya aplicado; el fallo requiere rethemear en vivo.

---

## 5. Criterios de aceptación globales

- [x] En claro y oscuro, las fichas se distinguen del fondo y el texto mantiene
      4,5:1.
- [x] Un párrafo del markdown con líneas consecutivas se renderiza como un solo
      párrafo; no hay frases partidas.
- [x] No hay espacios dobles ni espacios antes de puntuación alrededor de fichas.
- [x] Con el foco en el overlay funcionan ↑/↓, j/k, PgUp/PgDn, Home/End,
      Espacio/Shift+Espacio y Ctrl+d/Ctrl+u, con el mismo paso.
- [x] `1`–`9` saltan a las secciones existentes y las anclas son estables.
- [x] La ayuda abre en la sección de la vista actual (rejilla, panel o visor) y en el
      inicio si no hay contexto.
- [x] La cabecera fija muestra el índice y `Esc/q cerrar`; no hay «✕» ni cierre con
      ratón.
- [x] El overlay se ve bien a 640×480 y a 1920×1080; el ancho máximo es 960 px y el
      panel de ubicaciones conserva 760.
- [x] El interlineado es 1,4 y las tablas no se desbordan.
- [x] `?`, `F1`, `:help`, `Esc` y `q` se comportan como se documenta.
- [x] `docs/diseno-ui.md` y `src/foto/resources/ayuda.md` actualizados con las teclas
      nuevas.
- [x] `uv run pytest` y `uv run ruff check .` en verde.

---

## 6. Ficheros y tests afectados

| Fichero | Cambio previsto |
| --- | --- |
| `src/foto/ui/help.py` | Fichas, párrafos, espacios, scroll, secciones/anclas, cabecera, índice, tipografía, interlineado, semántica |
| `src/foto/ui/theme.py` | Token de superficie de ficha y QSS de la cabecera si hace falta |
| `src/foto/ui/window.py` | Geometría responsive y apertura contextual |
| `src/foto/ui/panels.py` | Pista de la barra de estado en modo AYUDA; unificar el texto derecho |
| `src/foto/ui/presentation.py` | `help_section_for_context` puro |
| `src/foto/resources/ayuda.md` | Reordenar/condensar y documentar las teclas nuevas |
| `docs/diseno-ui.md` | Teclas de la ayuda, apertura contextual, cabecera y tamaño |
| `tests/test_ui_help.py` | Fichas, párrafos, espacios, scroll, secciones, índice, cabecera, contexto, tipografía, interlineado |
| `tests/test_ui_status_bar.py` | Pista y textos del modo AYUDA |
| `tests/test_presentation.py` | Mapeo puro de contexto |
| `tests/test_ui_documented_features.py` | Comandos y secciones documentados |
| `tests/test_ui_key_inventory.py` | Si el inventario de teclas de la ventana cambia |
| `tests/test_ui_locations.py` | Geometría del panel de ubicaciones, si aplica |

---

## 7. Registro de avance

| Fecha | Tarea | Nota |
| --- | --- | --- |
| 2026-11-25 | T0.1 | Propuesta redactada; punto de retorno `51c1c4a`. |
| 2026-11-25 | T0.2 | Decisiones cerradas; documento convertido en plan accionable. |
| 2026-11-25 | T0.3 | Visto bueno del proyectista; autoriza la fase 1. |
| 2026-11-25 | T0.4 | Plan actualizado commiteado. |
| 2026-11-25 | T1.1 | Token `help_chip_surface`; fichas visibles con contraste ≥ 4,5:1 en claro y oscuro. |
| 2026-11-25 | T1.2 | Líneas consecutivas se unen en un párrafo con `margin-bottom: 12px`. |
| 2026-11-25 | T1.3 | Unión sin espacios añadidos y relleno contextual ante puntuación. |
| 2026-11-25 | T1.4 | Teclas de scroll completas; cuerpo con `Qt.NoFocus` para no perder el foco. |
| 2026-11-25 | T1.5 | Suite y ruff en verde; capturas 1280×800 y 640×480 en claro y oscuro revisadas. |
| 2026-11-25 | T2.1 | `HelpSection` y `help_sections`; anclas `s1`…`s6` compartidas por índice y render. |
| 2026-11-25 | T2.2 | Cabecera fija: índice atenuado y pista `Esc/q cerrar`, sin botones; filete en Midlight. |
| 2026-11-25 | T2.3 | Dígitos 1–6 saltan a sección; 7–9 y dígitos con modificadores se ignoran. |
| 2026-11-25 | T2.4 | `help_section_for_context` puro; apertura en Visor/Panel/Rejilla o al inicio con singleShot(0). |
| 2026-11-25 | T2.5 | Modo AYUDA con teclas y salto 1–N; `Esc/q cerrar` unificado y color restaurado al salir. |
| 2026-11-25 | T2.6 | `_center_overlay` parametrizado: ayuda 960×820; ubicaciones conserva 760. |
| 2026-11-25 | T2.7 | h1 20 px, h2 15 px, `<th>` 12 px en fg.muted alineado; reglas en Midlight con `background-color`. |
| 2026-11-25 | T2.8 | Interlineado 1,4 proporcional bloque a bloque tras `setHtml`; tablas sin desbordes. |
| 2026-11-25 | T2.9 | `ayuda.md` reordenado y condensado; guía con teclas, contexto, cabecera y 960 px. |
| 2026-11-25 | T2.10 | `h1`/`h2`/`th` semánticos; nombres accesibles en cuerpo e índice. |
| 2026-11-25 | T2.11 | 1179 tests y ruff en verde; capturas de tres tamaños en claro y oscuro revisadas. |
| 2026-11-25 | T2.12 | `:help` abre al inicio (Globales); el foco heredado del prompt ya no cuenta como contexto. |
| 2026-11-25 | T2.13 | Color explícito en cada bloque y `reapply_style` del cuerpo en `retheme`: sin texto blanco tras oscuro→claro. |

---

## 8. Alternativas técnicas descartadas

- **Fichas con imágenes generadas.** Permitirían esquinas redondeadas y bordes, pero
  pierden el texto accesible, no escalan con la fuente y añaden caché y pintura.
- **Tabla HTML con celdas de ficha.** Permitiría fondo y borde por celda, pero
  convierte cada fila en una tabla anidada y complica el reflujo en ventanas
  estrechas.
- **Reescribir la ayuda en HTML.** Rompe la regla de que `ayuda.md` es la fuente
  única y obliga a mantener dos formatos.
- **Usar `setMarkdown` de Qt.** Pierde el formato de los spans de código y no se
  puede estilar; es la razón por la que existe el renderer propio.
- **Un `QListView` o un widget por fila.** Da más control visual, pero multiplica el
  código y el coste de accesibilidad para una pantalla de solo lectura.
- **Filtro `/` o `QTextDocument.find`.** Descartado por decisión: el índice `1`–`9`
  cubre la navegación de seis secciones.
- **Cierre con ratón y selección con teclado.** Descartados por decisión: la ayuda
  sigue siendo teclado-primero y la selección con ratón se mantiene.
