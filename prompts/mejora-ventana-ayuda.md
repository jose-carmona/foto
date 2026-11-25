# Mejora de la ventana de ayuda: propuesta para discutir

- **Fecha:** 2026-11-25
- **Ámbito:** `src/foto/ui/help.py`, `src/foto/ui/theme.py`, `src/foto/ui/window.py`,
  `src/foto/ui/panels.py`, `src/foto/resources/ayuda.md`, `docs/diseno-ui.md` y tests de UI.
- **Estado:** propuesta. **No se ha modificado código de la aplicación.**
- **Objetivo:** mejorar el aspecto y la usabilidad del overlay de ayuda (`?` / `F1` /
  `:help`) sin cambiar su contrato: `ayuda.md` sigue siendo la fuente única del
  contenido y el render sigue siendo propio, con el tema activo.

> Este documento es la base de discusión, no un prompt de ejecución. Las decisiones
> abiertas están en § 8 y deben cerrarse antes de convertirlo en tareas. Las
> referencias a líneas corresponden al estado del árbol en la fecha indicada.

---

## 0. Resumen

- El mayor problema no es de diseño: **las fichas de tecla son invisibles** porque
  comparten color con el fondo del overlay, y **los párrafos se rompen** porque el
  renderer trata cada línea del markdown como un párrafo.
- Con tres arreglos de bajo riesgo (fichas, párrafos y espacios) y las teclas de
  scroll completas, la ayuda cambia de aspecto y de uso sin tocar el contenido.
- Encima de eso, la propuesta añade índice `1`–`9`, cabecera con cierre, tamaño
  responsive y, opcionalmente, filtro `/`.
- Hay ocho decisiones abiertas en § 8; ninguna bloquea la fase 1.

---

## 1. Cómo se ha evaluado

Se ha renderizado el overlay con la plataforma `offscreen` en dos tamaños y dos temas:

| Caso | Geometría del overlay | Observación |
| --- | --- | --- |
| 1280×800, oscuro | `(260, 32, 760, 708)` | Caben ~2,5 secciones; el documento tiene 6 |
| 1280×800, claro | `(260, 32, 760, 708)` | Mismo resultado; las fichas tampoco se ven |
| 640×480, oscuro | `(32, 32, 576, 388)` | Solo ~6 filas de tabla visibles |

También se ha comprobado el comportamiento del teclado con el foco en el overlay y
con el foco dentro del `QTextBrowser` tras un clic, y se ha probado qué CSS inline
acepta Qt en rich text.

---

## 2. Diagnóstico

### 2.1 Defectos que explican el aspecto actual

| # | Hallazgo | Evidencia | Dónde |
| --- | --- | --- | --- |
| D1 | **Las fichas de tecla son invisibles** | `_help_chip()` usa `QPalette.AlternateBase` como fondo; `HelpOverlay` pinta su propia `Window` con ese mismo `AlternateBase`. Claro: `#fafafa` sobre `#fafafa`. Oscuro: `#383838` sobre `#383838`. | `help.py:76-84`, `help.py:222-224` |
| D2 | **Cada línea del markdown es un párrafo** | `render_help` añade un bloque `paragraph` por cada línea no vacía. Los párrafos escritos a mano se rompen en 3–6 párrafos con huecos y frases partidas («Con el foco en el panel, las teclas» / «de la rejilla siguen operando…»). | `help.py:173-191` |
| D3 | **Espacios dobles alrededor de las fichas** | `_help_inline` une los trozos con `" ".join(...)` y la ficha ya lleva `nbsp` de padding: «Esc  siempre», «Ctrl+q  sale». | `help.py:92-99` |
| D4 | **Jerarquía tipográfica fuera de escala** | `h1` a 26 px y `h2` a 20 px; la guía fija títulos de 15 y 20 px. Cada encabezado lleva un `hr` con `Mid`, demasiado fuerte; la cabecera «Tecla/Acción» va en negrita a 13 px y compite con el título. | `help.py:102-109`, `help.py:128-142` |
| D5 | **Tamaño fijo poco adaptable** | 760×708 siempre. En 4K se queda pequeño; en 640×480 deja 388 px de alto. | `window.py:1080-1087` |
| D6 | **Navegación de scroll incompleta e inconsistente** | Con el foco en el overlay solo funcionan ↑/↓ y j/k, con paso fijo de 40 px. `PgDn`, `PgUp`, `Home`, `End`, `Espacio` y `Ctrl+d/u` no hacen nada. Con el foco dentro del navegador (tras un clic) sí funcionan, pero las flechas pasan a 20 px. | `help.py:267-284` |
| D7 | **Sin índice ni orientación** | Seis secciones y ~2,5 visibles por pantalla; no hay forma de saltar ni de saber dónde se está. | `ayuda.md`, `help.py` |
| D8 | **Sin cierre visible en el overlay** | El cierre depende de la barra de estado («Esc/q cerrar»), que además cambia a «Esc cerrar» según la ruta (`set_help_mode` vs `refresh_right`). El banner de ubicaciones sí tiene «✕». | `panels.py:564-575`, `panels.py:665-679` |
| D9 | **Semántica de accesibilidad pobre** | Encabezados como `<p>` estilados, cabeceras de tabla como `<td><b>` y `body` sin nombre accesible. El lector de pantalla no puede navegar por secciones ni por cabeceras. | `help.py:102-142`, `help.py:230-243` |
| D10 | **Contenido desequilibrado** | «Ubicaciones» es un manual de ~150 palabras en medio de una ayuda de atajos; «Panel de metadatos» tiene prosa larga. El valor de la ayuda son las tablas. | `ayuda.md` |

### 2.2 Restricciones técnicas comprobadas

- Qt rich text **sí** aplica `background-color` en un `<span>` inline, pero **ignora**
  `padding`, `border` y `border-radius` en ese contexto. La ficha puede tener fondo y
  texto monoespaciado, pero será plana; el aire horizontal se consigue con `nbsp`,
  como ya hace `_help_chip_pad`.
- Qt rich text **ignora** `line-height` en el CSS inline. Si se quiere el 1,4 de la
  guía, hay que aplicarlo con `QTextBlockFormat.setLineHeight` después de `setHtml`.
- `<h2 style="...">` y `<th align="left">` sí funcionan y permiten mantener la
  semántica sin perder el control visual.

---

## 3. Objetivos y no objetivos

### Objetivos

1. Que las fichas de tecla se lean como fichas y la tabla se escanee de un vistazo.
2. Que los párrafos del documento se lean como párrafos, no como líneas sueltas.
3. Que la ayuda se pueda recorrer entera con el teclado de forma predecible.
4. Que en ventanas pequeñas siga siendo usable y en pantallas grandes no se quede
   diminuta.
5. Mantener `ayuda.md` como fuente única y el render propio, sin dependencias nuevas.

### No objetivos

- No se propone cambiar el modelo de modos ni el contrato de `?`/`F1`/`:help`.
- No se propone empaquetar fuentes, iconos ni imágenes para las fichas.
- No se propone reescribir el documento como HTML a mano.
- No se propone añadir animaciones ni sombras (la guía las descarta).
- No se propone tocar el indexador, el catálogo ni la lógica de negocio.

---

## 4. Propuesta estética

### 4.1 P0 — corregir lo que ya está roto

**Fichas visibles y con contraste**

- Añadir a `theme.py` un token de superficie de ficha, p. ej.
  `help_chip_surface(widget) -> QColor`, derivado de la paleta del overlay con un
  delta de luminancia mínimo y texto `WindowText`. El widget no escribe colores
  literales: consume la capa de tokens, como el resto de la UI.
- Valores orientativos, no vinculantes: en claro, superficie del overlay `#fafafa` y
  ficha `#e8e8e8`; en oscuro, `#383838` y `#4b4b4b`. El texto debe mantener 4,5:1
  sobre la ficha.
- Mantener la monoespaciada a 12 px y el `nbsp` de padding. Aceptar la ficha plana:
  Qt no permite radios inline y la guía prohíbe radios mayores de 6 px.

**Párrafos de verdad**

- Agrupar líneas consecutivas hasta una línea en blanco y emitir un solo `<p>` por
  bloque. Las tablas y los encabezados siguen cortando el bloque.
- Añadir un margen inferior de 10–12 px por párrafo para conservar el aire que hoy
  dan, por accidente, los párrafos troceados.
- Si en el futuro se quiere un salto duro dentro de un párrafo, documentar la sintaxis
  (por ejemplo, dos espacios finales) en lugar de depender del ancho de línea.

**Espacios correctos**

- Cambiar `" ".join(pieces)` por `"".join(pieces)` en `_help_inline`; los espacios
  del documento ya están donde deben. Añadir un test que compruebe que no hay doble
  espacio ni espacio antes de puntuación alrededor de una ficha.

**Tests de regresión**

- Ficha: el color de fondo difiere del fondo del overlay y el contraste del texto es
  ≥ 4,5:1 en claro y oscuro.
- Párrafos: un párrafo del markdown con líneas consecutivas genera un único `<p>`.
- Espacios: `render_help("texto `Esc` siempre")` no contiene `Esc  ` ni `  siempre`.

### 4.2 P1 — jerarquía y ritmo

**Escala tipográfica**

| Elemento | Actual | Propuesto |
| --- | --- | --- |
| Título del documento (`#`) | 26 px / 600 | 20 px / 600 |
| Sección (`##`) | 20 px / 600 | 15 px / 600 |
| Cuerpo | 13 px | 13 px |
| Cabecera de tabla | 13 px / 700 | 12 px / 600 en `fg.muted` |

- Regla de separación con `Midlight` en lugar de `Mid`, o solo bajo el título. La
  guía pide líneas solo cuando aportan estructura; seis reglas fuertes compiten con
  las tablas.
- Espaciado: 20–24 px entre secciones, 8 px bajo el encabezado, 12 px entre
  párrafos, 6–8 px por fila.
- Interlineado 1,4 aplicado con `QTextBlockFormat` si se decide asumir el coste; el
  CSS no basta.

**Tabla**

- Columna de teclas ~38 % y de acciones ~62 %, alineación superior, sin líneas
  internas. Mantener el ancho global de la columna de teclas (ya existe) para que
  todas las tablas queden alineadas.
- Los separadores «/» y «o» entre fichas, en `fg.muted`, para que las teclas
  destaquen sin convertirse en decoración.
- Cabecera de tabla en `fg.muted` y 12 px: deja de competir con el título.

**Cabecera fija del overlay**

```
┌ Ayuda ───────────────────────────────── Esc/q cerrar  ✕ ┐
│ 1 Globales  2 Rejilla  3 Visor  4 Panel  5 Comandos      │
│ 6 Ubicaciones                                            │
├──────────────────────────────────────────────────────────┤
│ Globales                                                 │
│ Funcionan en cualquier vista.                            │
│                                                          │
│ Tecla            Acción                                  │
│ ? / F1           Mostrar u ocultar esta ayuda            │
│ :                Línea de comandos                       │
│ …                                                        │
└──────────────────────────────────────────────────────────┘
```

- Título + pista de cierre + «✕» de 24×24 con tooltip «Cerrar (Esc)» y nombre
  accesible, como el banner de ubicaciones. El «✕» emite una señal que la ventana
  conecta a `_toggle_help`, para que también oculte el blocker y restaure el estado.
- La cabecera no scrollea; el `QTextBrowser` queda debajo.

### 4.3 P2 — tamaño y acabado

- Geometría responsive: ancho `min(960, 78 % del contenido)`, alto
  `min(820, 82 %)`, mínimos 320×260. En ventanas anchas, limitar la medida del texto
  a ~72 caracteres; las tablas pueden usar todo el ancho.
- `_center_overlay` es compartido con el panel de ubicaciones: parametrizar los
  máximos por overlay en lugar de cambiar el comportamiento de ambos a la vez.
- Borde de 1 px `Midlight` (hoy `Mid`); sin sombra, según la guía.

---

## 5. Propuesta de usabilidad

### 5.1 P0 — navegación completa y predecible

- Añadir a `HelpOverlay.keyPressEvent`:
  - `PgDn` / `PgUp`: una página.
  - `Home` / `End`: principio / final.
  - `Espacio` / `Shift+Espacio`: una página abajo / arriba.
  - `Ctrl+d` / `Ctrl+u`: media página, coherente con la rejilla.
- Calcular el paso de línea con las métricas de la fuente del documento, no con 40 px
  fijos. Así el paso no depende del tamaño de fuente ni del tema.
- Mantener ↑/↓ y j/k como están; son los que ya conoce el usuario de la rejilla.
- Con el foco dentro del `QTextBrowser` tras un clic, el navegador ya scrollea; hay
  que evitar que el paso cambie (20 px frente a 40 px). La forma más simple es que el
  overlay controle siempre el scroll y el navegador no reciba foco, o que ambos usen
  el mismo paso.
- Documentar `q` como cierre de la ayuda en la introducción de `ayuda.md` (ya
  funciona vía `_dispatch`, pero no aparece en el documento).

### 5.2 P1 — orientación

**Índice de secciones con teclas 1–9**

- Generar el índice desde los encabezados del markdown, no escribirlo a mano: una
  función pura `help_sections(markdown) -> list[tuple[str, str]]` en `help.py`, con
  tests sin Qt.
- Añadir anclas estables (`s1`, `s2`, …) a los encabezados y usar
  `body.scrollToAnchor("sN")`.
- Teclas `1`–`9`: saltan a la sección N. Si hay más de 9 secciones, las restantes no
  tienen tecla y se llega con scroll. Documentarlo en `ayuda.md` y en
  `docs/diseno-ui.md`.
- El índice puede ir en la cabecera fija (§ 4.2) o como primera línea del documento.
  La cabecera fija es mejor: sigue visible después de saltar.

**Barra de estado en modo AYUDA**

- El área izquierda hoy queda vacía. Mostrar una pista corta:
  `j/k · PgUp/PgDn · 1–6 secciones`.
- Unificar el texto derecho: `set_help_mode` pone «Esc/q cerrar» y `refresh_right`
  pone «Esc cerrar». Debe haber una sola fuente para el texto del modo.

**Ayuda contextual (opcional)**

- `?` desde el visor podría abrir en «Visor»; desde el panel, en «Panel»; `:help`
  siempre en la introducción.
- Alternativa más predecible: abrir siempre arriba y marcar la sección actual.
- Es una decisión de producto; ver § 8.

### 5.3 P2 — búsqueda y ratón

**Buscar dentro de la ayuda**

- `/` dentro del overlay abre un campo inline sobre el cuerpo. Opciones:
  1. **Filtrar** filas por tecla o descripción, conservando el encabezado de la
     sección. `Esc` limpia y `Enter` salta a la primera coincidencia. Es lo que más
     ahorra scroll, pero exige una función pura de filtrado del markdown y tests.
  2. **Buscar y saltar** con `QTextDocument.find`, sin ocultar nada. Más simple, pero
     con seis secciones y muchas filas se queda corto.
- Si se implementa, el campo debe ser alcanzable con teclado y anunciar «sin
  resultados» en texto, no solo con color.

**Cierre con ratón**

- Clic fuera del overlay (sobre el blocker) para cerrar. Hoy el blocker es un
  `QWidget` sin `mousePressEvent`.
- Solo debe cerrar cuando hay un overlay informativo visible; no cuando el blocker
  está puesto por la línea de comandos.

**Accesibilidad**

- Usar `<h2>` para secciones y `<th>` para cabeceras de tabla.
- `body.setAccessibleName("atajos de teclado")`.
- Valorar `TextSelectableByKeyboard` para copiar una tecla o un comando sin ratón,
  con cuidado de no romper j/k ni el foco.
- Probar el overlay al 200 % de escala de texto: hoy todo usa píxeles fijos
  (`ui_font(13)`), así que la ayuda hereda la limitación general de la UI.

---

## 6. Contenido de `ayuda.md`

La ayuda debe responder primero «¿qué tecla pulso?» y dejar la prosa en segundo plano.

- **Reordenar por frecuencia:** Globales, Rejilla, Visor, Panel de metadatos,
  Comandos, Ubicaciones. «Ubicaciones» es un caso avanzado y hoy interrumpe el flujo.
- **Condensar «Ubicaciones»:** 2–3 frases y la tabla de `:locations`; el detalle de
  discos ausentes ya está en `docs/diseno-ui.md` y en el ADR 0010.
- **Acortar «Panel de metadatos»:** una línea de contexto y la tabla; el detalle de
  chips y tooltips puede quedar en una nota breve.
- **Introducción:** añadir que `q` también cierra la ayuda y que `?`/`F1` la
  alternan. Mantener la explicación de `Esc` y `Ctrl+q`.
- **Evitar líneas largas a mano:** con el renderer corregido, un párrafo puede
  escribirse en varias líneas sin romperse; aun así, párrafos de 2–3 líneas se leen
  mejor en 576 px de ancho.
- No cambiar el orden de las tablas de teclas sin actualizar `docs/diseno-ui.md` y
  los tests que comprueban que cada atajo documentado aparece en la ayuda.

---

## 7. Plan por fases y criterios de aceptación

| Fase | Contenido | Riesgo | Impacto |
| --- | --- | --- | --- |
| 1 | D1–D3 + D6: fichas, párrafos, espacios y teclas de scroll; tests de regresión | Bajo; no cambia contenido | Alto: se ve inmediatamente |
| 2 | D4, D5, D7–D10: tipografía, tamaño responsive, índice, cabecera/cierre, barra de estado, semántica y contenido | Medio | Alto: la ayuda pasa de «documento» a «herramienta» |
| 3 | Filtro `/`, ayuda contextual, cierre con ratón y selección con teclado | Alto; opcional | Medio |

### Criterios de aceptación

- [ ] En claro y oscuro, las fichas se distinguen del fondo y el texto mantiene
      4,5:1.
- [ ] Un párrafo del markdown con líneas consecutivas se renderiza como un solo
      párrafo; no hay frases partidas.
- [ ] No hay espacios dobles ni espacios antes de puntuación alrededor de fichas.
- [ ] Con el foco en el overlay funcionan ↑/↓, j/k, PgUp/PgDn, Home/End,
      Espacio/Shift+Espacio y Ctrl+d/Ctrl+u, con el mismo paso.
- [ ] El índice salta a todas las secciones y las anclas son estables.
- [ ] El overlay se ve bien a 640×480 y a 1920×1080, y no supera el 82 % de alto.
- [ ] `?`, `F1`, `:help`, `Esc`, `q` y el «✕» cierran/abren como se documenta.
- [ ] `uv run pytest` y `uv run ruff check .` en verde.
- [ ] `docs/diseno-ui.md` y `src/foto/resources/ayuda.md` actualizados con las teclas
      nuevas.

---

## 8. Decisiones abiertas

Estas son las preguntas que hay que cerrar antes de ejecutar. Ninguna bloquea la
fase 1.

1. **Alcance del rediseño.** ¿Solo arreglos visuales (fase 1), o también índice y
   cabecera (fase 2), o el paquete completo con filtro (fase 3)?
2. **Apertura contextual.** ¿`?` debe abrir en la sección de la vista actual o
   siempre en la introducción? La primera es más útil; la segunda, más predecible.
3. **Índice.** ¿Cabecera fija con índice y «✕», o índice como primera línea del
   documento para no añadir chrome? La cabecera fija es más útil; el documento
   queda más limpio sin ella.
4. **Cierre con ratón.** ¿Se añade el «✕» y el clic fuera, o se mantiene la ayuda
   estrictamente teclado-primero con la pista de la barra de estado?
5. **Ancho.** ¿Se amplía el overlay a 960 px como máximo (y se parametriza también
   el panel de ubicaciones), o se mantiene 760 px para no cambiar la composición?
6. **Contenido.** ¿Se reordena y condensa `ayuda.md` en esta entrega, o se deja el
   texto intacto y solo se corrige el render? Reordenar es barato, pero cambia la
   ayuda que el usuario ya conoce.
7. **Interlineado.** ¿Se aplica 1,4 con `QTextBlockFormat` (más código, más aire) o
   se acepta el interlineado por defecto de Qt?
8. **Filtro.** ¿Merece la pena el filtro `/` para seis secciones, o el índice 1–9 es
   suficiente? El filtro es la mejora más potente para documentos largos, pero
   también la más costosa de mantener.

---

## 9. Alternativas descartadas

- **Fichas con imágenes generadas.** Permitirían esquinas redondeadas y bordes, pero
  pierden el texto accesible, no escalan con la fuente y añaden caché y pintura. No
  compensa para una ficha de tecla.
- **Tabla HTML con celdas de ficha.** Permitiría fondo y borde por celda, pero
  convierte cada fila en una tabla anidada y complica el reflujo en ventanas
  estrechas.
- **Reescribir la ayuda en HTML.** Rompe la regla de que `ayuda.md` es la fuente
  única y obliga a mantener dos formatos.
- **Usar `setMarkdown` de Qt.** Pierde el formato de los spans de código y no se
  puede estilar; es la razón por la que existe el renderer propio.
- **Un `QListView` o un widget por fila.** Da más control visual, pero multiplica el
  código y el coste de accesibilidad para una pantalla de solo lectura.

---

## 10. Ficheros y tests afectados

| Fichero | Cambio previsto |
| --- | --- |
| `src/foto/ui/help.py` | Renderer de párrafos, fichas, índice/anclas, teclas de scroll, cabecera opcional |
| `src/foto/ui/theme.py` | Token de superficie de ficha y, si aplica, QSS de la cabecera |
| `src/foto/ui/window.py` | Geometría responsive, señal de cierre, apertura contextual opcional |
| `src/foto/ui/panels.py` | Pista de la barra de estado en modo AYUDA; unificar el texto derecho |
| `src/foto/resources/ayuda.md` | Reordenar/condensar, documentar teclas nuevas |
| `docs/diseno-ui.md` | Tabla de teclas de la ayuda y criterios de la ventana |
| `tests/test_ui_help.py` | Fichas, párrafos, espacios, índice, teclas de scroll |
| `tests/test_ui_key_inventory.py` | Si las teclas nuevas entran en el inventario de la ventana |
| `tests/test_ui_documented_features.py` | Si se añaden comandos o se reordena el documento |

---

## 11. De propuesta a ejecución

Cuando § 8 esté cerrado:

1. Convertir las fases 1–3 en tareas `T1.1`, `T1.2`, … con resultado esperado y
   tests, siguiendo el formato de los prompts de `prompts/`.
2. Empezar por la fase 1: es independiente del contenido y se puede revisar con dos
   capturas (claro/oscuro) y la suite en verde.
3. No mezclar la fase 1 con el rediseño de la cabecera: si algo falla, el diff debe
   poder revertirse sin perder los arreglos de render.
4. Mantener `ayuda.md` como fuente única; cualquier tecla nueva se documenta en los
   tres sitios: código, `docs/diseno-ui.md` y `ayuda.md`.
