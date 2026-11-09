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
| `f` | Marcar o desmarcar la foto como favorita |
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

## Panel de metadatos

Muestra los datos de la foto seleccionada. Con el foco en el panel, las teclas
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
| `:help` | Abrir esta ayuda |
