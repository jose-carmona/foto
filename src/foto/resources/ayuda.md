# Ayuda

Todo funciona con el teclado: no hay menús ni ratón necesario. `Esc` siempre
retrocede un paso —cierra ayuda, visor o línea de comandos, y además
restablece el zoom— y `Ctrl+q` sale de la aplicación. `q` también cierra esta
ayuda.

Dentro de la ayuda, `↑` / `↓` o `j` / `k` mueven una línea, `PgUp` / `PgDn` y
`Espacio` una página, `Ctrl+d` / `Ctrl+u` media página, `Home` / `End` van a
los extremos y `1`–`9` saltan a la sección con ese número del índice.

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
| `t` | Mostrar u ocultar los rectángulos de etiquetas con región |
| `0` / `$` | Primera / última foto del grupo |
| `g g` / `G` | Primera / última foto |
| `q` | Volver a la rejilla |

El paneo solo tiene efecto con zoom. `t` dibuja solo el borde de las etiquetas
con región; el tooltip de cada rectángulo da el nombre, que también está en la
sección «Etiquetas» del panel. En la rejilla sin visor, `t` no hace nada.

## Panel de metadatos

Muestra los datos de la foto seleccionada —las etiquetas, como chips, con el
favorito primero—; con el foco en el panel, las teclas de la rejilla siguen
moviendo el cursor y solo las flechas desplazan el panel.

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

## Ubicaciones

La fototeca puede repartirse en varios discos (`[[library.locations]]` en
`config.toml`, ADR 0010). Si un disco no está a mano —un USB desmontado, por
ejemplo—, la ventana lo avisa con una tarjeta sobre la rejilla que se cierra
con `Esc` —también con la «✕», o sola a los 30 segundos—: esas fotos se marcan
con el icono de aviso, sus metadatos siguen visibles y favoritar o filtrar por
etiqueta sigue funcionando, pero abrirlas exige el fichero. Al volver a montar
el disco, reabre `foto` para refrescar el estado.

| Comando | Acción |
| --- | --- |
| `:locations` | Estado de las ubicaciones (también con un clic en el banner) |
