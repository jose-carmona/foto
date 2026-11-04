# Prompt: refabricación visual de la visualización de fotos

> Documento para revisar antes de ejecutar. Tras la revisión, se enviará tal
> cual al agente que haga la refabricación.

## Objetivo

Refabricar la composición visual de la ventana del visor de `foto`
(`src/foto/ui/`, principalmente `prototype.py` y `app.py`) para que esté formada
por **cuatro regiones con papeles claros**:

1. **Zona de rejilla** — rejilla de miniaturas.
2. **Zona de estado** — barra de estado y línea de comandos.
3. **Panel de metadatos** — ficha lateral derecha.
4. **Panel de foto** — visualización completa de la foto seleccionada
   (es el visor actual: se abre con `Enter`/`o`, se cierra con `q`/`Esc`;
   conserva su comportamiento actual, solo cambia su composición).

El cambio es **únicamente visual y composicional**. No hay cambios funcionales:
ni atajos nuevos, ni comandos nuevos, ni cambios de semántica de navegación.

## Invariantes no negociables

Estas reglas valen en **todos** los estados y deben cumplirse siempre:

1. La **zona de rejilla** y la **zona de estado** están siempre visibles.
2. La **zona de estado** tiene altura fija de **28 px** y ocupa el ancho
   completo de la ventana, siempre en la franja inferior.
3. El **panel de metadatos** y el **panel de foto** pueden mostrarse u
   ocultarse; esos son los dos únicos cambios de composición posibles.
4. Al mostrarse u ocultarse cualquiera de los dos paneles, lo que se
   redimensiona es la **zona de rejilla** (y el panel de foto cuando aparece).
   Nunca cambia la zona de estado.
5. La rejilla **funciona exactamente igual en cualquier estado**: misma
   navegación, mismos grupos y cabeceras, mismo scroll horizontal y vertical,
   misma selección y foco. Mostrar u ocultar paneles **solo cambia su
   dimensión**; no cambia su comportamiento ni sus métricas internas.
6. El foco, la selección y el cursor son los mismos en la rejilla sea cual sea
   el estado; cambiar de estado no mueve el cursor.
7. **La foto seleccionada en la rejilla siempre queda visible.** Si la
   geometría cambia —porque se muestre u oculte un panel o porque cambie el
   tamaño de la ventana—, al terminar la operación la rejilla se ajusta con
   `EnsureVisible` para que la miniatura del cursor esté dentro de la zona
   visible, con el desplazamiento mínimo (sin autocentrarla). Eso incluye la
   vista de una única fila: la miniatura actual debe quedar a la vista en la
   fila.

## Geometría (valores ya establecidos, no se inventan otros)

- Miniatura: 400 × 247 px; celda con separación de 16 px → **416 × 263 px**
  (`ITEM_W`/`ITEM_H`).
- Cabecera de grupo: **52 px** (`GROUP_HEADER_H`).
- Zona de estado: **28 px** (`BAR_H`).
- Ancho fijo del panel de metadatos: **426 px** (`PANEL_W`).
- Margen exterior de la rejilla: **16 px** (`GRID_MARGIN`).
- Ventana mínima: 640 × 480.

## Los cuatro estados de la ventana

### Estado de arranque (solo rejilla + estado)

Solo visibles zona de rejilla y zona de estado.

```
┌─────────────────────────────────────────────┐
│  zona de rejilla  (ancho completo)          │
│  con su margen exterior de 16 px            │
├─────────────────────────────────────────────┤
│  zona de estado    (28 px)                  │
└─────────────────────────────────────────────┘
```

- La rejilla ocupa todo el alto disponible, con varias filas, cabeceras de
  grupo y scroll vertical.
- **Este es el estado de arranque: la ventana siempre empieza aquí.** No se
  persiste entre sesiones qué paneles estaban abiertos.

### Estado 2: solamente panel de metadatos

```
┌───────────────────────────────┬─────────────┐
│  zona de rejilla              │ panel de    │
│  (ancho − 426 px)             │ metadatos   │
│                               │ (426 px)    │
│                               │             │
├───────────────────────────────┴─────────────┤
│  zona de estado (28 px, ancho completo)     │
└─────────────────────────────────────────────┘
```

- El panel de metadatos ocupa el **lateral derecho completo por encima de la
  zona de estado**: desde el borde superior de la ventana hasta donde empieza
  la zona de estado, con ancho fijo de 426 px.
- La **zona de estado no se ve alterada** por el panel de metadatos: conserva
  sus 28 px de alto y su ancho completo de ventana, atravesando toda la
  ventana por debajo.
- La zona de rejilla se acoge al ancho restante (`ancho − 426 px`), pero su
  comportamiento no varía: sigue mostrando filas y grupos como siempre.

### Estado 3: solamente panel de foto

De arriba a abajo, a **ancho completo de ventana**:

```
┌─────────────────────────────────────────────┐
│  panel de foto                              │
│                                             │
├─────────────────────────────────────────────┤
│  título del grupo de la foto con el cursor  │  ← 52 px
├─────────────────────────────────────────────┤
│  zona de rejilla: UNA única fila de celdas  │  ← 263 px
├─────────────────────────────────────────────┤
│  zona de estado (28 px)                     │
└─────────────────────────────────────────────┘
```

- El panel de foto ocupa el ancho **completo** de la ventana y todo el alto
  disponible: `alto − 28 (estado) − 52 (título) − 263 (fila)`.
- La zona de rejilla queda reducida a la altura exacta de un **título de grupo
  (52 px) + una única fila de miniaturas (263 px)**. Es la misma rejilla de
  siempre, solo recortada: ni es un filmstrip ni un widget aparte.
- El **título del grupo** es el del grupo de la foto con el cursor y **cambia
  al navegar**, igual que caben los títulos de las cabeceras de la rejilla
  completa. La fila contigua puede cruzar de un grupo a otro; el título
  mostrado sigue siempre reflejando el grupo actual.
- La rejilla conserva toda su semántica: se ve una fila a la vez, pero su
  desplazamiento vertical sigue existiendo — al navegar, la fila visible es la
  que contiene el cursor y el resto de filas queda fuera de la vista.

### Estado 4: panel de foto + panel de metadatos

Igual que el estado 3, pero el panel de metadatos ocupa el lateral derecho
completo (de borde superior a zona de estado, 426 px) y el panel de foto, el
título de grupo, la fila de la rejilla y sus márgenes se acogen al ancho
restante (`ancho − 426 px`).

- El panel de metadatos **abarca tanto el panel de foto como la fila** de la
  rejilla, no solo uno de ellos.

## Reglas de transición entre estados

- Mostrar/ocultar el panel de foto **no cambia el comportamiento de la
  rejilla**; solo cambia la dimensión de la zona de rejilla (pasa de varias
  filas a una fila de alto). La foto que se estaba viendo, el cursor y la
  selección quedan como estaban.
- Mostrar/ocultar el panel de metadatos tampoco cambia nada más que el ancho
  disponible de la zona de rejilla y del panel de foto.
- Los paneles se muestran y ocultan con los mecanismos actuales:
  - `Enter` / `o` muestra el panel de foto; `q` / `Esc` lo oculta (con la
    semántica de zoom actual de `Esc`).
  - `i` muestra/oculta el panel de metadatos.
  - `Tab` / `Shift+Tab` mueven el foco; `Esc` nunca cierra la aplicación.
- Al cerrar el panel de foto, la rejilla queda en la foto que se estaba viendo.
- Si el filtro deja la fila vacía, el panel de foto se cierra y la rejilla
  muestra el estado vacío, como hoy.
- Ventana estrecha (< 900 px de ancho) con metadatos visibles: mantén el
  comportamiento actual de panel como capa temporal sobre el contenido.

## Aspecto visual

La refabricación es ante todo visual. Aplícalo así:

- **La foto manda.** El chrome no compite con la imagen: sin bordes
  decorativos, sombras ni degradados añadidos.
- **Tokens de color de `docs/diseno-ui.md`.** Ningún color literal en widgets:
  `bg.window`, `bg.panel`, `bg.canvas`, `fg.primary`, `fg.muted`, `border`,
  `accent`, `error`, construidos sobre `QPalette` para que los temas claro y
  oscuro funcionen sin casos especiales.
- **Delimitación de regiones.** Las cuatro regiones se distinguen con los
  medios ya prescritos: fondo del lienzo del panel de foto (`bg.canvas`, gris
  neutro sin tinte), fondo de panel (`bg.panel`) para el panel de metadatos y
  la zona de estado, separadores de **1 px** (`border`) entre regiones y
  espacio en blanco como separador principal.
- **Zona de estado.** Texto de 11–12 px, indicador de modo siempre en texto,
  borde superior de 1 px. No duplica los metadatos del panel.
- **Título del grupo de la fila.** Misma tipografía que las cabeceras de la
  rejilla (15 px, peso 600) y alineado a la izquierda; es un rótulo, no un
  botón.
- **Disciplina visual completa de `docs/diseno-ui.md`:** máximo tres niveles
  de texto, un solo acento, radios ≤ 6 px, rejilla de 8 px, iconos monocromos
  con nombre accesible, sin animaciones en el cambio de foto y respetando la
  preferencia de movimiento reducido en las transiciones que ya la tienen
  (120–160 ms, `ease-out`).
- **Foco y accesibilidad.** Anillo de foco de 2 px visible en la rejilla y en
  el panel de metadatos; el lienzo del panel de foto no recibe indicador de
  foco (el foco vive en la miniatura actual de la fila), como hoy.
- **Miniaturas.** Mismas métricas, mismo recorte centrado, mismas insignias y
  mismo sombreado de selección en la rejilla completa y en la fila única. Los
  huecos pendientes son bloques neutros, nunca spinners.

## Actualización de la documentación

- `docs/diseno-ui.md` pasa a describir **cuatro regiones** con estos nombres
  (zona de rejilla, zona de estado, panel de metadatos, panel de foto), la
  geometría de los cuatro estados y la regla de «la rejilla solo cambia de
  dimensión». Actualiza también la anatomía ASCII y la sección del visor.
- `src/foto/resources/ayuda.md` refleja la misma terminología si menciona el
  visor o los paneles.
- La suite `tests/` debe seguir en verde con pytest y ruff sin cambios de
  semántica; si algún test describe la composición antigua (p. ej. geometría
  del visor), actualízalo junto al cambio y explica por qué.

## Criterios de verificación

- Verificar los cuatro estados manualmente en `uv run foto`:
  1. arranque: solo rejilla + estado;
  2. `i`: metadatos a la derecha, rejilla al ancho restante;
  3. `Enter`: panel de foto a ancho completo, una fila + título de grupo
     debajo;
  4. `i` + `Enter`: metadatos abarcando todo el derecho, el resto al ancho
     restante.
- Y en los cuatro estados:
  - La navegación de la rejilla (`h j k l`, `g g`/`G`, `0`/`$`, `Ctrl+d`/`u`,
    `PgDn`/`PgUp`, `Home`/`End`) se comporta idéntico.
  - El título de grupo de la fila refleja el grupo del cursor.
  - La miniatura seleccionada queda visible dentro de la zona de rejilla
    después de mostrar/ocultar paneles y de redimensionar la ventana.
  - La zona de estado no cambia de tamaño ni de sitio.
  - Cerrar el panel de foto deja la rejilla en la foto vista.
- Ventana a 640 × 480 y a 1920 × 1080, sin solapamientos ni huecos.
- `uv run pytest` y `uv run ruff check .` en verde.

## Fuera de alcance

- Cambios de atajos, comandos o semántica de interacción.
- Persistir el estado de los paneles entre sesiones.
- Todo lo marcado como «Fuera de alcance» en `docs/diseno-ui.md` y en
  `AGENT.md` (edición, álbumes, IA, etc.).
- Índice, catálogo, indexador y caché de miniaturas: no importan Qt y no se
  tocan.
