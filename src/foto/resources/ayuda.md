# Ayuda

Todo funciona con el teclado: no hay menús ni ratón necesario. `Esc` siempre
retrocede un paso —cierra ayuda, visor o línea de comandos, y además
restablece el zoom— y `Ctrl+q` sale de la aplicación.

## Globales

Funcionan en cualquier vista.

| Tecla | Acción |
| --- | --- |
| `?` / `F1` | Mostrar u ocultar esta ayuda |
| `:` | Línea de comandos |
| `/` | Buscar fotos |
| `i` | Mostrar u ocultar el panel de metadatos |
| `f` | Marcar o desmarcar la foto como favorita (se guarda en el catálogo) |
| `g m` | Abrir la ubicación de la foto en el navegador (Google Maps) |
| `Tab` / `Shift+Tab` | Mover el foco entre la rejilla y el panel |
| `Ctrl+q` / `⌘Q` | Salir de la aplicación |

## Rejilla

Es el catálogo de fotos agrupadas por mes o cámara.

| Tecla | Acción |
| --- | --- |
| `Enter` / `o` | Abrir la foto en el visor |
| `h` `j` `k` `l` / flechas | Mover el cursor |
| `0` / `$` | Primera / última foto del grupo |
| `g g` / `G` / `Home` / `End` | Primera / última foto |
| `PgDn` / `PgUp` | Una página abajo / arriba |
| `Ctrl+d` / `Ctrl+u` | Media página abajo / arriba |

## Visor

Se abre con `Enter` sobre cualquier foto. La rejilla queda debajo como una
única fila para saltar a otra foto con el mismo cursor.

| Tecla | Acción |
| --- | --- |
| `j` / `k` o flechas | Foto siguiente / anterior |
| `h` / `H` | Desplazar la imagen a la izquierda |
| `l` / `L` | Desplazar la imagen a la derecha |
| `J` / `K` | Desplazar la imagen abajo / arriba |
| `+` / `-` | Acercar / alejar la imagen |
| `z` | Ajustar a la ventana / 100 % |
| `0` / `$` | Primera / última foto del grupo |
| `g g` / `G` | Primera / última foto |
| `q` | Volver a la rejilla |

El paneo solo tiene efecto con zoom.

## Ubicaciones

La fototeca puede repartirse en varios discos (`[[library.locations]]` en
`config.toml`, ADR 0010). Si uno no está conectado —por ejemplo un USB
desmontado— la ventana lo avisa con una tarjeta sobre la rejilla (icono de
advertencia, la ruta afectada y cuántas fotos no se ven). La app es
teclado primero: la tarjeta se cierra con `Esc` —también con la «✕», y se
cierra sola a los 30 segundos— y el panel de ubicaciones está a un
`:locations` de distancia. Esas fotos se marcan en la rejilla solo con el
mismo icono de aviso, y muestran sus datos del catálogo (fecha, cámara,
etiquetas, favorito) aunque el fichero no esté a mano; la miniatura sale de
la caché si existe. Favoritar y filtrar por etiqueta siguen funcionando:
todo son metadatos. Abrir la foto en el visor, que exige el fichero, queda
desactivado con un mensaje claro. Al volver a montar el disco, reabre
`foto` para refrescar el estado.

| Comando | Acción |
| --- | --- |
| `:locations` | Estado de las ubicaciones (también con un clic en el banner) |

## Panel de metadatos

Muestra los datos de la foto seleccionada. Las etiquetas se muestran como
chips —el favorito, como un chip carmesí con un corazón— y el detalle de cada
una (sugerida, machine learning) se ve en el tooltip. Con el foco en el panel,
las teclas
de la rejilla siguen operando la rejilla; solo las flechas desplazan el panel.

| Tecla | Acción |
| --- | --- |
| `↑` / `↓` | Desplazar el panel |
| `PgDn` / `PgUp` | Una página del panel abajo / arriba |
| `Home` / `End` | Principio / final del panel |
| `Tab` / `Shift+Tab` | Devolver el foco a la rejilla |

## Comandos

Pulsa `:` y escribe el comando. `Tab` autocompleta, `↑` / `↓` recorren el
historial, `Enter` ejecuta y `Esc` cancela.

| Comando | Acción |
| --- | --- |
| `:q` / `:quit` | Salir de la aplicación |
| `:sort` date / camera / location | Ordenar por fecha / cámara / ubicación |
| `:tag` | Filtrar por etiqueta (`:tag <nombre>`, repita o deje vacío para quitar el filtro). La app abre con el filtro `fav` aplicado si hay favoritas |
| `:locations` | Estado de las ubicaciones de la fototeca |
| `:help` | Abrir esta ayuda |
