# Diseño de la interfaz de usuario

- **Estado:** vigente para el MVP
- **Fecha:** 2026-10-29
- **Ámbito:** interfaz de `foto` en Omarchy (Arch + Hyprland/Wayland) y macOS.
  Complementa a `README.md` y a `docs/adr/0001-configuracion.md`; no decide
  almacenamiento ni modelo de datos.
- **Audiencia:** quien implemente, revise o amplíe la UI.

## Objetivo

Una interfaz que desaparezca: la foto manda y todo lo demás ocupa el mínimo
espacio posible. Sencilla, clara y limpia, con el teclado como vía principal y
el ratón como alternativa, nunca como requisito.

Este documento fija las reglas básicas. Si una pantalla nueva las incumple, o se
cambia la regla aquí o no se implementa.

## Principios

1. **Teclado primero.** Toda acción debe poder ejecutarse sin ratón. El ratón
   acelera, no habilita.
2. **Atajos de vim.** Navegación con `h j k l`, `g g`/`G`, `Ctrl+d`/`Ctrl+u`,
   búsqueda con `/` y comandos con `:`. Sin `INSERT`: el texto solo se escribe
   en los modos explícitos de búsqueda y comando.
3. **Una ventana, cuatro regiones.** Zona de rejilla, zona de estado, panel de
   metadatos y panel de foto. La zona de rejilla y la zona de estado están
   siempre visibles; el panel de metadatos y el panel de foto pueden
   mostrarse u ocultarse, y al hacerlo solo cambia la dimensión de la zona de
   rejilla, nunca su comportamiento. Nada de pestañas, barras de herramientas
   ni ventanas secundarias (salvo el selector nativo de carpetas).
4. **La foto es la interfaz.** El chrome no compite con la imagen: sin bordes
   decorativos, sombras ni degradados.
5. **Una sola acción por tecla.** Ninguna tecla cambia de significado según el
   contexto salvo lo documentado en este fichero.
6. **Nada destructivo en el MVP.** No hay borrar, mover ni sobrescribir. Los
   favoritos se escriben en XMP y son reversibles.
7. **Silencio visual.** Un único color de acento, tres niveles de texto y
   espacio en blanco como separador principal.
8. **Nada bloquea.** Indexar o generar miniaturas ocurre fuera del hilo de UI;
   nunca aparece un diálogo modal por una operación rutinaria.
9. **Integración con el entorno.** La app se comporta como una ventana más de
   Hyprland: redimensionable, sin decoración propia y sin atajos globales.
10. **Español en la UI.** Etiquetas, mensajes y ayuda en español, en minúscula
    de frase. Los identificadores del código siguen en inglés.

## Integración con Omarchy y macOS

- **Gestor de ventanas en mosaico.** La ventana funciona a cualquier tamaño a
  partir de 640×480. No hay tamaño fijo, `always-on-top`, ni diálogos flotantes
  propios que rompan el mosaico.
- **Sin decoración de cliente.** Se usa la decoración del compositor. En macOS
  se usa la barra de menús nativa; en Linux no hay barra de menús: los comandos
  viven en `:` y en la ayuda (`?`).
- **Atajos globales.** `foto` no registra atajos globales. Esas teclas pertenecen
  a Omarchy/Hyprland. Las teclas de una sola letra solo funcionan con la ventana
  enfocada.
- **Tema del sistema.** Se respeta la paleta de Qt (claro u oscuro) y la fuente
  del sistema. No se empaquetan fuentes ni se impone un tema propio. El lienzo
  del visor es la única superficie con un gris neutro explícito.
- **Wayland y HiDPI.** Escalado fraccional sin asumir X11. Nada de coordenadas
  globales de pantalla ni capturas de escritorio.
- **Una instancia.** Abrir otra foto o biblioteca reutiliza la ventana; no se
  abren ventanas nuevas. La ruta se puede pasar por CLI (`foto <ruta>`).

## Modelo de interacción

### Modos

| Modo | Indicador en la barra de estado | Entrada | Salida |
| --- | --- | --- | --- |
| Normal | `NORMAL` | Al arrancar o al cerrar un overlay | — |
| Búsqueda | `/texto` | `/` | `Enter` confirma, `Esc` cancela y restaura |
| Comando | `:texto` | `:` | `Enter` ejecuta, `Esc` cancela |
| Ayuda | `AYUDA` | `?` o `F1` | `Esc` o `q` |

La rejilla y el panel de foto no son modos de entrada: comparten el modo Normal
y la barra sigue mostrando `NORMAL`. El panel de foto es la vista individual;
la rejilla queda como una única fila debajo y se abre con `Enter`/`o` y se
cierra con `q` o `Esc`.

Reglas:

- Solo puede haber un overlay abierto a la vez. Abrir otro cierra el anterior.
- Mientras se escribe en búsqueda o comando, las teclas de una letra se
  escriben; no se interpretan como atajos. Solo se interpretan `Esc` y `Enter`
  y, en la línea de comandos, además `Tab` y `↑`/`↓`. Los atajos con modificador
  (`Ctrl+q`, `⌘Q`) siguen activos.
- La barra de estado muestra siempre el modo en texto, nunca solo con color.
- `Esc` nunca cierra la aplicación. Su orden es: cerrar overlay → restablecer
  zoom → volver a la rejilla. En la rejilla, limpia la búsqueda activa.
- `q` cierra el visor o un overlay. En la rejilla no hace nada; la aplicación se
  cierra con `Ctrl+q` (o `⌘Q` en macOS) o con `:q`.
- No hay modo inserción. Escribir texto requiere `/` o `:` de forma explícita.

La ayuda integrada vive en un único documento markdown,
`src/foto/resources/ayuda.md`, empaquetado con la aplicación; el overlay
(`?` / `F1` / `:help`) lo lee y lo renderiza con el tema activo. Los atajos del
documento son la fuente de verdad de lo documentado, y un test comprueba que
cada atajo básico de la rejilla está en la ayuda. Las teclas distinguen
mayúsculas solo donde se indica (`G`, `H`, `J`, `K`, `L`); las secuencias con
prefijo `g` (`g g`, `g m`) admiten hasta 500 ms entre pulsaciones.

### Atajos globales

| Tecla | Acción |
| --- | --- |
| `?` / `F1` | Mostrar u ocultar la ayuda de atajos |
| `↑` / `↓` o `j` / `k` | Mover el scroll de la ayuda |
| `:` | Abrir la línea de comandos |
| `/` | Abrir la búsqueda |
| `i` | Mostrar u ocultar el panel de metadatos |
| `g m` | Abrir la ubicación de la foto en el navegador (Google Maps) |
| `Tab` / `Shift+Tab` | Conmutar el foco entre la rejilla y el panel de metadatos |
| `Esc` | Cancelar, cerrar o retroceder |
| `Ctrl+q` / `⌘Q` | Salir de la aplicación |

`g m` abre el punto GPS de la foto con el cursor; si la foto no tiene
coordenadas, la barra de estado lo indica y no se abre nada. La `m` sola,
sin el prefijo `g`, queda reservada y no hace nada.

### Rejilla

| Tecla | Acción |
| --- | --- |
| `h` `j` `k` `l` o flechas | Mover el cursor por la rejilla |
| `g g` | Primera foto |
| `G` | Última foto |
| `0` / `$` | Primera / última foto del grupo |
| `Ctrl+d` / `Ctrl+u` | Media página abajo / arriba |
| `PgDn` / `PgUp` | Una página abajo / arriba |
| `Home` / `End` | Primera / última foto |
| `Enter` u `o` | Abrir la foto en el visor |
| `f` | Alternar favorito |
| `g m` | Abrir la ubicación de la foto en el navegador (Google Maps) |
| `q` | Sin efecto; sugiere `Ctrl+q` en la barra de estado |

Los dígitos ``1``–``5`` no tienen función asignada y quedan fuera de servicio:
no hacen nada. Las únicas combinaciones con modificador que actúan son
``Ctrl+d``/``Ctrl+u`` (media página) y ``Ctrl+q``/``⌘Q`` (salir): cualquier otra
tecla que llegue con ``Ctrl`` o ``⌘`` pulsados se anula, igual que en la fila
del visor y el visor a pantalla completa.

Si el foco recae en la ventana sin widget intermedio (fallback), la rejilla
manda: los mismos atajos de navegación de esta tabla se aplican desde ahí.

La fila del visor es la misma tabla en otra geometría; cuando su fila está
abierta añade el paneo y el zoom, que se describen en «Visor». El panel de
metadatos, con el foco, combina scroll propio con las teclas de la rejilla
(ver «Panel de metadatos» más abajo).

### Panel de metadatos

El panel no es un modo de entrada: comparte el modo Normal. Con el foco,
las flechas y `PgUp`/`PgDn`/`Home`/`End` hacen scroll local del contenido y
`Esc`/`Tab` devuelven el foco a la vista (conmutador rejilla ⇄ panel). El resto
de teclas operan la rejilla y el visor igual que en modo normal: `h` `j` `k` `l`
mueven el cursor (sin tocar el scroll del panel), `0`/`$` van a los bordes del
grupo, `Enter`/`o` abren el visor, `+`/`-`/`z` manejan el zoom solo con el
visor abierto, `f` alterna favorito, `:` `/` `?` abren comandos, búsqueda y
ayuda, y `q` cierra el visor o sugiere `Ctrl+q`. Las flechas laterales (`←`,
`→`) no hacen nada. Los dígitos `1`–`5` tampoco.

La ficha agrupa los metadatos de la foto: captura, exposición, archivo y
ubicación. En esta última, el lugar resuelto por el indexador se muestra en
formato `Municipio, País` (con las partes disponibles si falta alguna),
seguido del detalle que guardó el servicio —calle, comarca, código postal y
la dirección completa— y de las coordenadas EXIF exactas, que son la
referencia de `g m`. Sin lugar resuelto la ficha no muestra fila alguna
añadida.

El cursor se mueve al instante, sin esperar a que cargue la miniatura. En los
extremos de la rejilla no hay salto circular: el cursor se detiene y la barra de
estado lo indica de forma discreta.

### Visor

Con el panel de foto abierto, la zona de rejilla queda reducida a la altura de
un título de grupo (52 px) más una única fila de celdas (263 px). Es la misma
rejilla de la vista principal, solo recortada: mismo widget, misma navegación,
mismos grupos, mismas métricas y mismo scroll; lo único que cambia es su
dimensión. No es una vista aparte.

Reglas de la fila:

- Es la misma `PhotoGrid` que la vista principal, recortada a una fila de
  alto; no es una vista aparte.
- Encima de la fila se muestra el título del grupo de la foto con el cursor,
  con la tipografía de las cabeceras de la rejilla. La fila puede cruzar de un
  grupo a otro mientras avanza; el título cambia siempre al grupo de la
  miniatura con el cursor, igual que al recorrer la rejilla a pantalla
  completa.
- Se ve una fila a la vez porque la zona de rejilla queda a la altura exacta
  de una fila; su desplazamiento vertical sigue existiendo y la fila visible
  es la que contiene el cursor (`EnsureVisible`, desplazamiento mínimo).
- El foco y la selección están en la fila. La miniatura actual se resalta; la
  foto grande no lleva anillo de foco ni se resalta.
- Al cerrar el panel de foto, la rejilla queda en la foto que se estaba
  viendo.
- La navegación es la de la rejilla y usa `EnsureVisible`: la fila se desplaza
  lo mínimo para dejar la miniatura a la vista, sin autocentrarla.
- Al cerrar el visor, la rejilla principal queda en la foto que se estaba
  viendo.

| Tecla | Acción |
| --- | --- |
| `h` / `l` o `←` / `→` | Foto anterior / siguiente |
| `j` / `k` o `↓` / `↑` | Avanzar / retroceder un bloque de columnas visibles |
| `g g` / `G` | Primera / última foto |
| `0` / `$` | Primera / última foto del grupo |
| `Ctrl+d` / `Ctrl+u` | Media página abajo / arriba |
| `PgDn` / `PgUp` | Una página abajo / arriba |
| `Home` / `End` | Primera / última foto |
| `H` / `J` / `K` / `L` | Desplazar la imagen con zoom: izquierda, abajo, arriba, derecha |
| `+` / `-` | Acercar / alejar |
| `z` | Alternar entre ajustar a la ventana y 100 % |
| `i` | Mostrar u ocultar metadatos |
| `f` | Alternar favorito |
| `g m` | Abrir la ubicación de la foto en el navegador (Google Maps) |
| `q` | Volver a la rejilla |
| `Esc` | Restablecer zoom; si ya está ajustado, volver a la rejilla |

La navegación de la fila es la de la rejilla: `h`/`l` mueven una miniatura,
`j`/`k` saltan un bloque de columnas visibles y `g g`/`G`, `Home`/`End`,
`PgUp`/`PgDn` y `Ctrl+d`/`Ctrl+u` recorren la secuencia con la misma semántica.
El grupo también delimita aquí: `0`/`$` dejan el cursor en la primera o última
miniatura del grupo al que pertenece la foto actual.

El paneo de la imagen ampliada usa las mayúsculas `H`, `J`, `K`, `L`. Sin zoom no
desplazan nada. La miniatura actual se resalta en la fila; la foto grande no lleva
anillo de foco ni se resalta.

Al llegar al final de la secuencia, la navegación se detiene; no hay salto
circular.

La fila queda pegada al borde inferior del espacio disponible y muestra la
secuencia con las mismas métricas que la rejilla: miniaturas de 400 × 247 px,
separación de 16 px y celdas de 416 × 263 px. El scroll y la rueda se comportan
igual que en la rejilla completa. Un clic en una miniatura selecciona esa foto.

### Búsqueda

- `/` abre un filtro incremental que examina **el índice**, no solo el nombre
  de fichero: nombre, ruta relativa (carpeta y ubicación canónica), réplicas,
  cámara, objetivo, tipo de fichero, fecha y el resto de campos indexados del
  catálogo (ver ADR 0003).
- El filtrado se hace en memoria sobre la lista cargada del catálogo, con una
  entrada por foto (por hash); las réplicas no crean entradas adicionales.
- El texto se escribe en la barra de estado, junto al indicador de modo.
- `Enter` confirma y devuelve el foco a la rejilla o al visor.
- `Esc` cancela y restaura el estado anterior.
- Una búsqueda vacía elimina el filtro. Al reabrir `/` se muestra el filtro
  activo para editarlo.
- No hay `n`/`N`: el filtro oculta las fotos que no coinciden, y `j`/`k`
  recorren los resultados.

### Línea de comandos

`:`, autocompletado con `Tab`, historial con `↑`/`↓`, ejecución con `Enter` y
cancelación con `Esc`. Todo comando debe aparecer en la ayuda.

Vocabulario mínimo:

| Comando | Acción |
| --- | --- |
| `:q`, `:quit` | Salir de la aplicación |
| `:sort date`, `:sort camera`, `:sort location` | Cambiar el orden de la rejilla (por defecto, fecha) |
| `:help` | Abrir la ayuda |

No existe `:rescan`: según ADR 0003 (enmendado), la reindexación y la
sincronización del índice se ejecutan desde shell, servicio o cron con
`foto-index`, y el visor muestra el catálogo actualizado al reiniciarse o al
abrir otra biblioteca.

Regla de extensibilidad: cualquier acción nueva que no tenga una tecla propia
debe ser alcanzable por `:`. No se añaden botones para compensar.

### Teclas reservadas

No se usan todavía, pero quedan reservadas para no romper la coherencia cuando
lleguen sus funciones:

| Tecla | Uso futuro |
| --- | --- |
| `u` / `Ctrl+z` | Deshacer |
| `r` | Rotar |
| `e` | Editar |
| `x`, `v`, `Espacio` | Selección múltiple y modo visual (fase 2) |
| `y` | Copiar ruta o metadatos |
| `s` | Guardar o sincronizar, si alguna vez existe |

`Espacio` no se asigna en el MVP.

### Ratón

- Clic: mover el cursor. Doble clic: abrir en el panel de foto.
- Con el panel de foto abierto, un clic en una miniatura de la fila cambia la
  foto actual.
- Rueda: desplazar la rejilla, igual con el panel de foto abierto.
  `Ctrl`/`⌘` + rueda sobre la foto grande hace zoom.
- Clic derecho: menú contextual con las acciones de la foto bajo el cursor. Es
  un atajo, no la única vía: todo lo que ofrece está en el teclado. Pendiente
  para más adelante: queda al final de la Fase 1 del roadmap y todavía no está
  implementado.
- Ninguna acción puede depender de `hover`, arrastre o gesto. Los controles
  ocultos hasta pasar el ratón están prohibidos.

## Composición visual

### Anatomía de la ventana

La ventana tiene cuatro regiones:

1. **Zona de rejilla.** La rejilla de miniaturas con sus cabeceras de grupo.
   Siempre visible, y con idéntico comportamiento en todos los estados:
   mostrar u ocultar paneles solo cambia su dimensión.
2. **Zona de estado.** Barra inferior, 28 px de alto, siempre visible, siempre
   a ancho completo. Ninguna región la solapa ni la modifica.
3. **Panel de metadatos.** Columna derecha de ancho fijo (426 px), por encima
   de la zona de estado. Se muestra u oculta con `i`; arranca oculto.
4. **Panel de foto.** La foto seleccionada a tamaño completo (el visor). Se
   abre con `Enter`/`o` y se cierra con `q`/`Esc`.

Los cuatro estados resultantes:

**Estado de arranque: solo rejilla + estado.** La ventana siempre arranca así;
no se recuerda entre sesiones qué paneles estaban abiertos.

```
┌─────────────────────────────────────────────┐
│  zona de rejilla  (ancho completo)          │
├─────────────────────────────────────────────┤
│  zona de estado    (28 px)                  │
└─────────────────────────────────────────────┘
```

**Estado 2: solo panel de metadatos.** Ocupa el lateral derecho por encima de
la zona de estado; la rejilla se acoge al ancho restante.

```
┌───────────────────────────────┬─────────────┐
│  zona de rejilla              │ panel de    │
│  (ancho − 426 px)             │ metadatos   │
│                               │ (426 px)    │
├───────────────────────────────┴─────────────┤
│  zona de estado (28 px, ancho completo)     │
└─────────────────────────────────────────────┘
```

**Estado 3: solo panel de foto.** A ancho completo, de arriba a abajo: panel
de foto, título del grupo de la foto con el cursor (52 px) y la zona de
rejilla recortada a una única fila de celdas (263 px).

```
┌─────────────────────────────────────────────┐
│  panel de foto                              │
├─────────────────────────────────────────────┤
│  título del grupo de la foto con el cursor  │  ← 52 px
├─────────────────────────────────────────────┤
│  zona de rejilla: una única fila de celdas  │  ← 263 px
├─────────────────────────────────────────────┤
│  zona de estado (28 px)                     │
└─────────────────────────────────────────────┘
```

**Estado 4: panel de foto + panel de metadatos.** Igual que el estado 3, pero
el panel de metadatos abarca el lateral derecho completo (por encima de la
zona de estado) y el panel de foto, el título de grupo y la fila se acogen al
ancho restante (`ancho − 426 px`).

```
┌───────────────────────────────┬─────────────┐
│  panel de foto                │ panel de    │
│                               │ metadatos   │
├───────────────────────────────┤ (426 px)    │
│  título del grupo             │             │
├───────────────────────────────┤             │
│  fila de miniaturas           │             │
├───────────────────────────────┴─────────────┤
│  zona de estado (28 px, ancho completo)     │
└─────────────────────────────────────────────┘
```

Reglas de la anatomía:

- La rejilla funciona exactamente igual en los cuatro estados: misma
  navegación, mismos grupos y cabeceras, mismo scroll, misma selección. Los
  paneles solo cambian su dimensión.
- La miniatura seleccionada queda siempre a la vista: tras mostrar u ocultar
  un panel o redimensionar la ventana, la rejilla se ajusta con
  `EnsureVisible` en desplazamiento mínimo.
- Mostrar lo oculto y ocultar lo visible de los paneles no mueve el cursor ni
  la selección.
- En el panel de foto, el título de grupo mostrado es el del grupo de la foto
  con el cursor y cambia al navegar, con la tipografía de las cabeceras de la
  rejilla.
- Ventana menor de 900 px de ancho con el panel de metadatos abierto: el
  panel aparece como capa temporal sobre el contenido y se cierra con `Esc`.
- **Zona de estado:** 28 px, borde superior de 1 px, texto de 11–12 px.
  Muestra el modo, el recuento y los mensajes efímeros. No duplica los
  metadatos del panel.

### Rejilla

- Las miniaturas fluyen de izquierda a derecha, fila a fila, con ajuste al ancho de
  la ventana (sin rejilla fija de columnas).
- La rejilla se agrupa según la ordenación activa. `:sort date` agrupa por año y
  mes, del grupo más reciente al más antiguo; `:sort camera` agrupa por cámara,
  en orden alfabético. `:sort location` agrupa por municipio (`country` +
  `municipality` del servicio) con la etiqueta `Municipio, País`, en orden
  país → municipio alfabético ascendente; las fotos sin lugar resuelto quedan
  al final en `Sin ubicación`. Cada grupo muestra una cabecera y, debajo, sus
  miniaturas. Las fotos sin fecha o sin cámara quedan al final en `Sin fecha`
  o `Sin cámara`.
  Un filtro oculta las cabeceras cuyos grupos se quedan sin coincidencias.
- Tamaño de miniatura: 400 × 247 px (proporción áurea). Recorte centrado para
  llenar el recuadro sin deformar la imagen. Sin marco ni borde alrededor de la
  miniatura.
- Separación de 16 px entre miniaturas; margen exterior según composición.
- El favorito se muestra como una insignia discreta en una esquina, sobre un
  velo semitransparente para garantizar legibilidad sobre cualquier foto. Nunca
  aparece solo al pasar el ratón.
- Al estar seleccionada, se añade un leve sombreado al recuadro y un borde fino.
  Con el foco en la rejilla, el borde usa el acento; sin foco, un gris neutro.
  No se recolorea la miniatura.
- Barra de desplazamiento fina (8 px), sin flechas, visible cuando hay desborde.

### Visor

- La imagen se ajusta entera al espacio disponible con 8 px de margen, centrada
  y sin recortar. Si la proporción no llena el ancho, quedan franjas del lienzo;
  nunca se recorta la foto para llenarlo.
- La zona de rejilla recortada (título de grupo + una fila) es la misma rejilla
  con las mismas métricas: celdas de 416 × 263 px (miniatura de 400 × 247 px y
  16 px de separación), disposición a ancho disponible, scrollbars con el mismo
  criterio que la rejilla completa y mismas cabeceras de grupo en su flujo.
  Usa el mismo recorte centrado, las mismas insignias, el mismo sombreado de
  selección y el mismo foco que la rejilla.
- La fila muestra las fotos visibles bajo el filtro activo, con la misma
  ordenación y grupos de la rejilla. La miniatura
  actual queda seleccionada y resaltada; la foto grande no. La rejilla no se
  autocentra: usa `EnsureVisible` para mantener la miniatura a la vista con el
  desplazamiento mínimo.
- Si el filtro deja la fila vacía, el panel de foto se cierra y la rejilla
  muestra el estado vacío. Si se estaba escribiendo una búsqueda, el foco permanece en la
  barra de estado.
- El lienzo es un gris neutro sin tinte, distinto del fondo de la ventana, para
  separar la foto del chrome. Ni negro puro ni blanco puro.
- Zoom con interpolación de alta calidad. Cuando la imagen ampliada supera el
  lienzo se permite el desplazamiento; si cabe, se centra.
- Al cambiar de foto se muestra de inmediato su miniatura y, cuando termina la
  decodificación en segundo plano, la imagen original. Si el original no se
  puede decodificar (RAW/HEIC), se mantiene la miniatura y se avisa en la barra
  de estado. No hay hueco vacío ni spinner.

### Disciplina visual

- Máximo tres niveles de texto por vista: principal, secundario y atenuado.
- Un solo acento, procedente del color de resaltado del sistema. El acento no se
  usa como decoración.
- Sin degradados, sin sombras, sin bordes dobles y sin radios mayores de 6 px.
- El espacio en blanco separa; las líneas solo cuando aportan estructura.
- Iconos monocromos de 16 o 20 px, siempre con nombre accesible y tooltip. Un
  icono nunca es la única etiqueta de una acción principal.
- Nada de animaciones en el cambio de foto. Los overlays y el panel pueden
  animarse 120–160 ms con `ease-out`; se respeta la preferencia de movimiento
  reducido.
- Los estados vacíos explican qué hacer y ofrecen el atajo correspondiente
  (por ejemplo, `?` para ver los atajos disponibles).
- Las miniaturas pendientes usan un bloque neutro, no un spinner.

### Color y tipografía

Los colores salen siempre de una capa de tokens construida sobre `QPalette`.
Ningún widget escribe un color literal; así el tema claro y el oscuro funcionan
sin casos especiales.

Los estilos estructurales del mismo modo: la capa de tokens y las plantillas
QSS de los widgets viven en `src/foto/ui/theme.py`; los widgets aplican
consumiendo `theme` y re-aplican vía `theme.reapply_style` al cambiar el tema.

| Token | Uso |
| --- | --- |
| `bg.window` | Fondo de la ventana y de la rejilla |
| `bg.panel` | Panel de metadatos y barra de estado |
| `bg.canvas` | Lienzo neutro del visor |
| `fg.primary` | Texto y títulos |
| `fg.muted` | Etiquetas, ayudas y estados secundarios |
| `border` | Separadores de 1 px |
| `accent` | Cursor, foco, favorito y elementos activos |
| `error` | Solo errores |

Requisitos:

- Contraste mínimo 4,5:1 en texto normal y 3:1 en texto grande y elementos de
  interfaz. El foco también debe alcanzar 3:1.
- Tipografía del sistema. Escala base de 13 px; mínimo 11 px; títulos de 15 y
  20 px. Interlineado 1,4.
- Peso 400 y 600, nada intermedio. Sin mayúsculas sostenidas salvo siglas y el
  indicador de modo (`NORMAL`, `AYUDA`).
- Cifras tabulares en los metadatos (ISO, apertura, velocidad, focal).
- El estado nunca se comunica solo con color: siempre hay texto, icono o forma.

### Espaciado y formas

- Rejilla base de 8 px: 4, 8, 12, 16, 24, 32.
- 16 px de margen exterior, 8 px entre miniaturas, 12 px dentro del panel.
- Radio de 4 px en campos y 6 px en miniaturas; nada más redondeado.
- Altura mínima de un objetivo interactivo: 24×24 px; recomendado 32×32.

## Comportamiento y feedback

- **Favoritos:** actualización optimista de la insignia y escritura inmediata en
  el XMP. Si la escritura falla, se revierte el cambio y se muestra un error
  persistente. Nunca se falla en silencio.
- **Mensajes:** los éxitos y las acciones habituales aparecen en la barra de
  estado durante unos 3 s. Los errores permanecen hasta que el usuario los
  descarta con `Esc`.
- **Errores:** explican qué pasó y qué hacer, sin códigos crípticos. No usan
  diálogos modales.
- **Indexado:** el progreso se muestra en la barra de estado con recuento. La UI
  sigue navegable con lo ya indexado.
- **Confirmaciones:** solo para acciones destructivas. En el MVP no hay ninguna.
- **Preferencias de UI:** la ventana siempre arranca en el mismo estado: solo
  zona de rejilla y zona de estado, con ambos paneles ocultos. El estado de
  los paneles no se recuerda entre sesiones. La configuración se carga y se
  guarda fuera de la UI, según `docs/adr/0001-configuracion.md`.

## Rendimiento percibido

- La imagen original del visor se decodifica en un hilo de trabajo; la miniatura
  se muestra mientras tanto y el resultado se convierte a pixmap en el hilo de
  UI.
- El cursor y los atajos responden en el mismo ciclo de teclado, sin esperar a
  la imagen.
- Caché de las tres últimas imágenes originales y precarga de las fotos anterior
  y siguiente cuando no hay trabajo pendiente.
- Al cambiar de foto, un resultado antiguo no reemplaza a la foto actual; si
  llega, se guarda en caché o se descarta.
- Las miniaturas de la rejilla se generan en segundo plano, solo para la zona
  visible, y se piden al lector ya escaladas. Se guardan en una caché persistente
  en `<fototeca>/.foto/thumbs/` indexada por hash de contenido, tamaño y versión;
  las escrituras son atómicas y un fallo de caché no impide mostrarla.
- Un cambio de foto en caché se percibe inmediato; si aún no está, se mantiene
  la miniatura, nunca un hueco blanco.
- La UI solo toca disco en dos puntos acotados: la decodificación de
  originales, que vive en la tarea de carga del visor (`ImageLoadTask`), y la
  caché de miniaturas, que vive en `ui/thumbnails.py`. Ese acceso a disco no se
  reparte por la ventana ni por el resto de widgets.
- La caché de miniaturas es un derivado reconstruible: borrar
  `<fototeca>/.foto/thumbs/` solo obliga a regenerarla. Todavía no se poda ni
  tiene un límite de tamaño, así que las fotos borradas o cambiadas pueden dejar
  ficheros huérfanos.
- En memoria se conserva una ventana acotada de miniaturas (160 iconos). Al
  alejarse, las más antiguas se descartan y se vuelven a pedir desde la caché en
  disco si hace falta. La navegación usa índices de las filas visibles en vez de
  recorrer toda la rejilla.

Presupuestos orientativos en una biblioteca local:

- primera ventana visible en menos de 500 ms;
- primeras miniaturas visibles en menos de 300 ms;
- arranque en caliente con caché en menos de 500 ms;
- ningún bloqueo del hilo de UI superior a 50 ms.

## Accesibilidad

- Todas las funciones, incluidas las de la ayuda y la línea de comandos, se
  manejan con teclado.
- Foco visible con anillo de 2 px y separación de 2 px. Nunca se elimina el
  indicador de foco. En el visor, el foco se muestra en la miniatura actual de la
  fila; el lienzo de la foto grande no recibe foco.
- Nombres accesibles en widgets propios y en las miniaturas.
- Texto escalable hasta el 200 % sin recortes ni solapamientos.
- Sin dependencia de animación, `hover` ni gestos.
- El zoom del visor no puede ser la única forma de leer un metadato: el panel
  debe ser texto seleccionable y escalable.

## Lista de verificación

Una pantalla o funcionalidad de UI no se considera terminada hasta que:

- [ ] Se puede usar entera sin ratón.
- [ ] Todos sus atajos aparecen en la ayuda (`?`), en este documento y en
      `src/foto/resources/ayuda.md`.
- [ ] La ayuda se lee del documento markdown empaquetado y se renderiza, no se
      escribe a mano en el código.
- [ ] No hay teclas duplicadas ni significados nuevos sin documentar.
- [ ] Funciona a 640×480 y a 1920×1080 en una ventana en mosaico.
- [ ] El panel de metadatos se comporta bien en ventanas estrechas.
- [ ] Se ve correctamente en tema claro y oscuro.
- [ ] El foco es visible y el contraste cumple.
- [ ] No hay diálogos modales para acciones rutinarias.
- [ ] No se bloquea el hilo de UI ni se muestra un spinner bloqueante.
- [ ] Los errores se muestran y el estado queda consistente.
- [ ] La UI no contiene lógica de negocio. El acceso directo a disco se limita a
      la decodificación de originales del visor y a la caché de miniaturas.

## Fuera de alcance

- Edición de imagen, álbumes, caras, OCR y publicación (fases posteriores).
- Selección múltiple y modo visual.
- Temas propios, plugins de UI y personalización de la disposición.
- Notificaciones del sistema para acciones rutinarias.
- Barra de herramientas, pestañas y ventanas múltiples.

## Pendiente

- Validar en Hyprland el comportamiento del panel de metadatos en ventanas
  estrechas y con escalado fraccional.
- Decidir si el estado del panel y el tamaño de celda se guardan por biblioteca
  o de forma global, y definir su sección `[ui]` en `config.toml`.
- Definir los campos exactos del panel cuando esté cerrado el modelo de
  metadatos.
- Elegir el mecanismo de movimiento reducido (preferencia de la app, del sistema
  o ambos) al implementar la capa de tema.
- Definir el modo visual y el uso de `Espacio` cuando se diseñe la selección
  múltiple de la fase 2.
- Validar RAW/HEIC mediante miniaturas embebidas y medir la memoria de la caché
  de originales; si hace falta, cargar una versión intermedia para el visor.
- Evaluar un modelo/vista si el número de `QListWidgetItem` empieza a pesar; la
  memoria de miniaturas y la navegación ya están acotadas.
- Podar las miniaturas huérfanas y medir el coste de la caché persistente en disco.
