# Diseño de la interfaz de usuario

- **Estado:** vigente para el MVP
- **Fecha:** 2026-10-26
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
3. **Una ventana, tres regiones.** Contenido, panel de metadatos y barra de
   estado. Nada de pestañas, barras de herramientas ni ventanas secundarias
   (salvo el selector nativo de carpetas).
4. **La foto es la interfaz.** El chrome no compite con la imagen: sin bordes
   decorativos, sombras ni degradados.
5. **Una sola acción por tecla.** Ninguna tecla cambia de significado según el
   contexto salvo lo documentado en este fichero.
6. **Nada destructivo en el MVP.** No hay borrar, mover ni sobrescribir. Las
   valoraciones y favoritos se escriben en XMP y son reversibles.
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

La rejilla y el visor no son modos de entrada: comparten el modo Normal y la
barra sigue mostrando `NORMAL`. El visor es la vista individual con tira de
miniaturas; se abre con `Enter`/`o` y se cierra con `q` o `Esc`.

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

El mapa de teclas vivirá en un único módulo de la UI y será la fuente de verdad
para la ayuda integrada, que se genera a partir de él. Un test detectará teclas
duplicadas o acciones sin documentar. Las teclas distinguen mayúsculas solo
donde se indica (`G`, `J`, `K`); las secuencias como `g g` admiten hasta 500 ms
entre pulsaciones.

### Atajos globales

| Tecla | Acción |
| --- | --- |
| `?` / `F1` | Mostrar u ocultar la ayuda de atajos |
| `:` | Abrir la línea de comandos |
| `/` | Abrir la búsqueda |
| `i` | Mostrar u ocultar el panel de metadatos |
| `Tab` / `Shift+Tab` | Pasar el foco al panel siguiente / anterior |
| `F11` | Pantalla completa |
| `Esc` | Cancelar, cerrar o retroceder |
| `Ctrl+q` / `⌘Q` | Salir de la aplicación |

### Rejilla

| Tecla | Acción |
| --- | --- |
| `h` `j` `k` `l` o flechas | Mover el cursor por la rejilla |
| `g g` | Primera foto |
| `G` | Última foto |
| `Ctrl+d` / `Ctrl+u` | Media página abajo / arriba |
| `PgDn` / `PgUp` | Una página abajo / arriba |
| `Home` / `End` | Primera / última foto |
| `Enter` u `o` | Abrir la foto en el visor |
| `f` | Alternar favorito |
| `1`–`5` | Asignar valoración |
| `0` | Quitar la valoración |
| `q` | Sin efecto; sugiere `Ctrl+q` en la barra de estado |

El cursor se mueve al instante, sin esperar a que cargue la miniatura. En los
extremos de la rejilla no hay salto circular: el cursor se detiene y la barra de
estado lo indica de forma discreta.

### Visor

| Tecla | Acción |
| --- | --- |
| `j` / `k` o flechas | Siguiente / anterior foto |
| `h` / `l` | Desplazar la imagen en horizontal cuando hay zoom |
| `J` / `K` | Desplazar la imagen en vertical cuando hay zoom |
| `+` / `-` | Acercar / alejar |
| `z` | Alternar entre ajustar a la ventana y 100 % |
| `g g` / `G` | Primera / última foto |
| `i` | Mostrar u ocultar metadatos |
| `f` | Alternar favorito |
| `1`–`5` | Asignar valoración |
| `0` | Quitar la valoración |
| `q` | Volver a la rejilla |
| `Esc` | Restablecer zoom; si ya está ajustado, volver a la rejilla |

`j`/`k` siempre recorren la secuencia; `h`/`l` se mueven en horizontal y
`J`/`K` en vertical. Esa separación se mantiene en todas las vistas: en la
rejilla, `h`/`l` cambian de columna; en el visor con zoom, desplazan la imagen.
En el visor sin zoom, `h`/`l` y `J`/`K` no desplazan la imagen ni cambian de
significado.

Al llegar al final de la secuencia, la navegación se detiene; no hay salto
circular.

El visor incluye una tira de una sola fila con las miniaturas visibles. `j`/`k`
y las flechas actualizan la foto grande y la selección de la tira, que se
autocentra. Un clic en una miniatura selecciona esa foto. Al salir, la rejilla
queda en la foto que se estaba viendo. La tira no es una vista aparte: forma
parte del mismo visor.

### Búsqueda

- `/` abre un filtro incremental por nombre de fichero sobre la vista actual.
- El texto se escribe en la barra de estado, junto al indicador de modo.
- `Enter` confirma y devuelve el foco a la rejilla o al visor.
- `Esc` cancela y restaura el estado anterior.
- Una búsqueda vacía elimina el filtro. Al reabrir `/` se muestra el filtro
  activo para editarlo.
- No hay `n`/`N`: el filtro oculta las fotos que no coinciden, y `j`/`k`
  recorren los resultados.
- La búsqueda por metadatos, fechas o ubicación llega en la fase 2; este
  documento solo fija el comportamiento del filtro del MVP.

### Línea de comandos

`:`, autocompletado con `Tab`, historial con `↑`/`↓`, ejecución con `Enter` y
cancelación con `Esc`. Todo comando debe aparecer en la ayuda.

Vocabulario mínimo:

| Comando | Acción |
| --- | --- |
| `:q`, `:quit` | Salir de la aplicación |
| `:open [ruta]` | Abrir una carpeta o biblioteca; sin ruta, selector nativo |
| `:rescan` | Volver a indexar la biblioteca actual |
| `:sort date`, `:sort name`, `:sort camera` | Cambiar el orden de la rejilla |
| `:help` | Abrir la ayuda |

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

- Clic: mover el cursor. Doble clic: abrir en el visor.
- En el visor, un clic en una miniatura de la tira cambia la foto actual. La
  rueda recorre la tira en horizontal.
- Rueda: desplazar la rejilla o hacer zoom con `Ctrl`/`⌘` + rueda.
- Clic derecho: menú contextual con las acciones de la foto bajo el cursor. Es
  un atajo, no la única vía: todo lo que ofrece está en el teclado.
- Ninguna acción puede depender de `hover`, arrastre o gesto. Los controles
  ocultos hasta pasar el ratón están prohibidos.

## Composición visual

### Anatomía de la ventana

```
┌ foto — Fotos ───────────────────────────────────────────────┐
│                                                             │
│   ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐                   │
│   │       │ │       │ │       │ │       │                   │
│   └───────┘ └───────┘ └───────┘ └───────┘                   │
│   ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐                   │
│   │       │ │       │ │       │ │       │                   │
│   └───────┘ └───────┘ └───────┘ └───────┘                   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ NORMAL        248 fotos · 12 favoritas                      │
└─────────────────────────────────────────────────────────────┘
```

```
┌ foto — Fotos ───────────────────────────────────────────────┐
│                                          │                  │
│                                          │  Fecha           │
│              [ fotografía ]              │  24 oct 2026     │
│                                          │                  │
│                                          │  Cámara          │
├──────────────────────────────────────────┤  Fujifilm X100V  │
│ [mini] [mini] [mini] [mini] [mini] ...   │                  │
├──────────────────────────────────────────┴──────────────────┤
│ NORMAL        14 de 248 · DSCF1234.RAF                      │
└─────────────────────────────────────────────────────────────┘
```

El diagrama del visor muestra el panel abierto; por defecto arranca oculto y se
alterna con `i`. La tira inferior pertenece al visor y ocupa el mismo ancho que
la foto, excluyendo el panel de metadatos.

- **Contenido:** rejilla o visor. Ocupa todo el espacio libre. En el visor, la
  foto y la tira de miniaturas comparten ese ancho; la tira queda pegada al
  borde inferior.
- **Panel de metadatos:** columna a la derecha, 280–360 px, bajo demanda en la
  rejilla y en el visor. Arranca oculto. Si la ventana mide menos de 900 px,
  aparece como capa temporal sobre el contenido y se cierra con `Esc`.
- **Barra de estado:** 28 px, borde superior de 1 px, texto de 11–12 px. Muestra
  el modo, el recuento y los mensajes efímeros. No duplica los metadatos del
  panel.

### Rejilla

- Las miniaturas fluyen de izquierda a derecha, fila a fila, con ajuste al ancho de
  la ventana (sin rejilla fija de columnas).
- Tamaño de miniatura: 400 × 247 px (proporción áurea). Recorte centrado para
  llenar el recuadro sin deformar la imagen. Sin marco ni borde alrededor de la
  miniatura.
- Separación de 16 px entre miniaturas; margen exterior según composición.
- Favorito y valoración se muestran como insignias discretas en una esquina,
  sobre un velo semitransparente para garantizar legibilidad sobre cualquier
  foto. Nunca aparecen solo al pasar el ratón.
- Al estar seleccionada, solo se añade un leve sombreado al recuadro de la miniatura
  (sin recolorizarla ni cambiar su color).
- Barra de desplazamiento fina (8 px), sin flechas, visible cuando hay desborde.

### Visor

- La imagen se ajusta entera al espacio disponible con 8 px de margen, centrada
  y sin recortar. Si la proporción no llena el ancho, quedan franjas del lienzo;
  nunca se recorta la foto para llenarlo.
- La tira inferior mide 120 px de alto: miniaturas de 96 px de alto (~155 px de
  ancho), separación de 8 px y barra de desplazamiento horizontal fina de 8 px.
  Usa el mismo recorte centrado, las mismas insignias y el mismo sombreado de
  selección que la rejilla.
- La tira muestra solo las fotos visibles bajo el filtro activo. La foto actual
  queda seleccionada y la tira se autocentra al navegar.
- Si el filtro deja la tira vacía, el visor se cierra y la rejilla muestra el
  estado vacío. Si se estaba escribiendo una búsqueda, el foco permanece en la
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
  (por ejemplo, `:open` para abrir una biblioteca).
- Las miniaturas pendientes usan un bloque neutro, no un spinner.

### Color y tipografía

Los colores salen siempre de una capa de tokens construida sobre `QPalette`.
Ningún widget escribe un color literal; así el tema claro y el oscuro funcionan
sin casos especiales.

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

- **Favoritos y valoraciones:** actualización optimista de la insignia y
  escritura inmediata en el XMP. Si la escritura falla, se revierte el cambio y
  se muestra un error persistente. Nunca se falla en silencio.
- **Mensajes:** los éxitos y las acciones habituales aparecen en la barra de
  estado durante unos 3 s. Los errores permanecen hasta que el usuario los
  descarta con `Esc`.
- **Errores:** explican qué pasó y qué hacer, sin códigos crípticos. No usan
  diálogos modales.
- **Indexado:** el progreso se muestra en la barra de estado con recuento. La UI
  sigue navegable con lo ya indexado.
- **Confirmaciones:** solo para acciones destructivas. En el MVP no hay ninguna.
- **Preferencias de UI:** el estado del panel de metadatos y el tamaño de la
  rejilla se recuerdan entre sesiones. La configuración se carga y se guarda
  fuera de la UI, según `docs/adr/0001-configuracion.md`.

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
- La generación inicial de miniaturas de la rejilla todavía es síncrona al abrir
  la carpeta; es deuda conocida de rendimiento.
- Un cambio de foto en caché se percibe inmediato; si aún no está, se mantiene
  la miniatura, nunca un hueco blanco.

## Accesibilidad

- Todas las funciones, incluidas las de la ayuda y la línea de comandos, se
  manejan con teclado.
- Foco visible con anillo de 2 px y separación de 2 px. Nunca se elimina el
  indicador de foco.
- Nombres accesibles en widgets propios y en las miniaturas.
- Texto escalable hasta el 200 % sin recortes ni solapamientos.
- Sin dependencia de animación, `hover` ni gestos.
- El zoom del visor no puede ser la única forma de leer un metadato: el panel
  debe ser texto seleccionable y escalable.

## Lista de verificación

Una pantalla o funcionalidad de UI no se considera terminada hasta que:

- [ ] Se puede usar entera sin ratón.
- [ ] Todos sus atajos aparecen en la ayuda (`?`) y en este documento.
- [ ] La ayuda se genera desde el mapa de teclas, no se escribe a mano.
- [ ] No hay teclas duplicadas ni significados nuevos sin documentar.
- [ ] Funciona a 640×480 y a 1920×1080 en una ventana en mosaico.
- [ ] El panel de metadatos se comporta bien en ventanas estrechas.
- [ ] Se ve correctamente en tema claro y oscuro.
- [ ] El foco es visible y el contraste cumple.
- [ ] No hay diálogos modales para acciones rutinarias.
- [ ] No se bloquea el hilo de UI ni se muestra un spinner bloqueante.
- [ ] Los errores se muestran y el estado queda consistente.
- [ ] La UI no contiene lógica de negocio ni acceso directo a disco.

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
- Mover la generación inicial de miniaturas de la rejilla fuera del hilo de UI.
