# Prompt — Rectángulos de etiquetas en el visor con la tecla `t`

> Decisiones del proyectista de 2026-11-24. Este documento es autocontenido:
> fija el comportamiento, los ficheros a tocar, las reglas de dibujo y la
> verificación. La misión es **implementar**, no estudiar.

## 0. Misión

Cuando una foto está abierta en el **panel de foto** (el visor), la tecla `t`
activa/desactiva una superposición sobre la imagen con un **rectángulo por
cada etiqueta asignada que tenga región**. Al pasar el ratón por un rectángulo,
su tooltip muestra el nombre de la etiqueta. En la rejilla de miniaturas `t`
no hace nada.

La entrega es de UI y de carga de datos: el esquema ya guarda las regiones
(`photo_tags.region_x/y/w/h`, ADR 0006) y la fase `apple-import` ya las escribe
(ADR 0009), pero hoy **no se cargan en el modelo de UI** y el ADR 0006 dice
explícitamente que el visor no pinta regiones. Esta entrega cierra ese hueco
sin tocar el indexador, la transferencia ni los originales.

Resumen: una sola consulta de carga ampliada, una regla pura de filtrado y
geometría, una tecla en los contextos del visor, un pintado en `PhotoCanvas` y
su documentación.

## 1. Decisiones vinculantes (no renegociar sin volver a preguntar)

1. **Ámbito de la tecla.** `t` actúa solo con el visor abierto, en los cuatro
   contextos que entonces despachan acciones: lienzo (`VIEWER`), fila de
   miniaturas (`GRID_VIEWER`), panel de metadatos (`PANEL`) y fallback de la
   ventana (`WINDOW`). En la rejilla sin visor (`GRID`) la tecla sigue sin
   estar enlazada y no hace nada. El handler comprueba además
   `self._viewer_visible`, porque la tabla de `WINDOW` no distingue estados.
2. **Qué se dibuja.** Solo asignaciones con las cuatro coordenadas de región
   no nulas. Cuentan `status='confirmed'` y `status='suggested'`; `rejected`
   nunca. No hay filtro por `kind`: sirven personas, lugares, eventos o
   etiquetas libres con región. Las asignaciones de foto completa (región
   `NULL`), incluida `fav`, **no** se dibujan.
3. **Qué se ve.** Únicamente el borde del rectángulo. **No se dibuja texto**
   sobre la foto. El nombre de la etiqueta aparece como tooltip al situar el
   cursor sobre el rectángulo. El tooltip es información complementaria: el
   nombre sigue disponible por teclado en la sección «Etiquetas» del panel de
   metadatos; ninguna acción depende de `hover`.
4. **Geometría.** La región es proporcional `[0,1]` sobre la imagen orientada
   tal como se muestra (EXIF aplicado), con origen arriba-izquierda, `x` a la
   derecha e `y` hacia abajo (ADR 0006, Decisión 7). El rectángulo escala y se
   desplaza con la foto en `+`/`-`, `z`, `H`/`J`/`K`/`L` y al redimensionar;
   el grosor del trazo se mantiene fijo en pantalla. Sin inversión del eje `y`.
5. **Preview.** Mientras `PhotoCanvas` muestra la miniatura de preview
   (`preview=True`, recortada a 400×247) **no se pintan rectángulos**. Aparecen
   en cuanto se dibuja la imagen completa decodificada, para no desplazarlos
   respecto del recorte.
6. **Estado del interruptor.** Un único booleano de sesión, apagado por
   defecto. Sobrevive al cambio de foto y a cerrar/reabrir el visor; se pierde
   al reiniciar la aplicación. No se persiste en `config.toml` ni en
   `QSettings` (prohibido por ADR 0001).
7. **Sin regiones.** Si la foto actual no tiene ninguna etiqueta visible con
   región, `t` muestra un mensaje **efímero** en la barra
   («sin etiquetas con región en esta foto», ~3 s). El interruptor queda
   activo: las fotos siguientes sí mostrarán sus rectángulos. No es un error.
8. **Carga.** La región viaja en el modelo desde el arranque: se amplía
   `TagAssignment` y `PhotoTag` y se selecciona en `assignments_by_hash`. Una
   sola consulta; la ventana nunca ejecuta SQL.
9. **Sin escritura.** `t` solo conmuta estado de UI: no escribe en
   `photo_tags`, no toca `photos`/`locations`, no modifica originales y no
   cambia el resultado de `foto-export`/`foto-import`.
10. **Documentación.** Se enmienda el ADR 0006, se actualizan
    `docs/diseno-ui.md`, `src/foto/resources/ayuda.md` y los tests de
    inventario de teclas. **Quedan fuera `README.md` y `AGENT.md`** en esta
    entrega (decisión del proyectista).

## 2. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables (especialmente 1, 5, 6 y 7), stack y
   convenciones; `README.md` — alcance y arquitectura.
2. `docs/diseno-ui.md` — modos, contextos de teclado, composición del visor,
   color/contraste, ratón y lista de verificación.
3. `docs/adr/0006-etiquetas.md` — Decisión 3 (esquema y `CHECK` de región),
   Decisión 6 (`confirmed` + `suggested`, nunca `rejected`) y Decisión 7
   (coordenadas; la frase «el visor no pinta regiones» queda enmendada).
   `docs/adr/0009-indexado-apple-photos.md` — origen de las regiones faciales.
4. Código real: `src/foto/storage/tags.py` (`TagAssignment`,
   `assignments_by_hash`), `src/foto/storage/catalog.py` (`tag_assignments`),
   `src/foto/core/library.py` (`CatalogPhoto`), `src/foto/ui/app.py`
   (`Photo.from_catalog`), `src/foto/ui/models.py` (`Photo`, `PhotoTag`),
   `src/foto/ui/presentation.py` (`visible_*`, `tag_chips`,
   `photo_search_text`), `src/foto/ui/keybindings.py` (contextos, `KEY_T`,
   `bind_common`, `bind_viewer`), `src/foto/ui/window.py` (`_actions`,
   `_dispatch`, `_show_viewer_photo`, `_apply_photo_update`, `_toggle_favorite`,
   `set_photos`), `src/foto/ui/canvas.py` (`paintEvent`, `set_image`,
   `keyPressEvent`), `src/foto/ui/theme.py` (`_accent_for`, `muted_color`,
   `QPalette`).
5. Tests cercanos: `tests/test_tags.py`, `tests/test_ui_tags.py`,
   `tests/test_keybindings.py`, `tests/test_ui_key_inventory.py`,
   `tests/test_ui_documented_features.py`, `tests/test_ui_help.py`,
   `tests/test_presentation.py`, `tests/ui_factories.py`.

Comprueba el estado del repositorio y ejecuta la verificación disponible
antes de empezar. No hagas `git commit`, `git push` ni cambios de configuración
de git sin confirmación explícita.

## 3. Datos y reglas puras (sin Qt)

- `photo_tags` ya tiene las cuatro columnas `REAL` y su `CHECK` en el esquema
  v7. **No hay migración nueva**, no se toca `SCHEMA_VERSION`.
- `storage/tags.py`:
  - ampliar `TagAssignment` con `region: Region | None = None`, añadido al
    final para no romper constructores existentes. `Region` es el alias
    inmutable `tuple[float, float, float, float]` (`(x, y, w, h)`); defínelo
    aquí, sin importar Qt ni `foto.ui`.
  - `assignments_by_hash` añade `pt.region_x, pt.region_y, pt.region_w,
    pt.region_h` al `SELECT` y un segundo criterio estable de orden
    (`pt.id`) tras `t.name`: una misma persona puede tener varias caras en
    una foto y el orden debe ser determinista para el tooltip y el pintado.
  - El valor `None` en las cuatro columnas se convierte en `region=None`.
    No se valida de nuevo el rango: ya lo garantiza el `CHECK` del esquema.
- `ui/models.py`: `PhotoTag` gana el mismo campo opcional al final;
  `Photo.from_catalog` copia `tag.region`. `Photo.favorite` y el resto de
  propiedades no cambian.
- `ui/presentation.py` (reglas puras, testeadas sin Qt):
  - `visible_tag_regions(photo) -> tuple[tuple[str, Region], ...]`: nombre y
    región de las asignaciones con región no nula y `status` en
    `('confirmed', 'suggested')`. Sin filtro por `kind`, sin `rejected`, sin
    deduplicar caras distintas.
  - `tag_rect_pixels(region, width, height) -> tuple[float, float, float,
    float]`: convierte `(x, y, w, h)` a `(left, top, right, bottom)` en
    coordenadas de la imagen mostrada; recorta a `[0, ancho] × [0, alto]` y
    devuelve `(0, 0, 0, 0)` si el destino está vacío. Es la única aritmética
    de la región; `PhotoCanvas` solo la transforma en `QRectF`.
  - `photo_search_text` y `tag_chips` **no cambian**: una región no añade
    texto buscable ni cambia los chips del panel.
- Sin cambios en `core/indexing.py`, `transfer/`, `storage/catalog.py` más allá
  del delegado ya existente, ni en la fase `apple-import`.

## 4. Tecla, acción y dispatcher

- `ui/keybindings.py` ya define `KEY_T` (0x54). Añadir filas con acción
  `toggle_tag_overlay`, sin modificadores, en `VIEWER`, `GRID_VIEWER`,
  `PANEL` y `WINDOW`. **No añadirla a `GRID`**: `bind_common` la usan GRID,
  GRID_VIEWER y WINDOW, así que la fila nueva se registra por separado para
  los dos últimos, no dentro de `bind_common`.
- La regla única de modificadores ya anula `Ctrl+t`/`⌘t`; cuando haya
  otro overlay abierto, el guardia existente de `_dispatch` ignora `t`
  igual que ignora `i` o `f`.
- `ui/window.py`: registrar `registry["toggle_tag_overlay"] =
  self._toggle_tag_overlay` junto a `favorite` y `panel`. El handler:
  1. si no hay visor abierto o no hay foto actual, no hace nada;
  2. conmuta `self._tag_overlay_visible`;
  3. sincroniza el lienzo y muestra uno de los mensajes efímeros:
     «etiquetas sobre la foto», «etiquetas ocultas» o, si no hay regiones
     visibles en esa foto, «sin etiquetas con región en esta foto»;
  4. oculta cualquier tooltip abierto al desactivar.
- No se añade comando `:` ni se cambia el significado de otra tecla. `t`
  queda documentada como tecla del visor; en rejilla sigue muerta.

## 5. Pintado y tooltip en `PhotoCanvas`

- Estado propio: `_tag_regions` (tupla de `(nombre, Region)`) y
  `_hover_tag` (índice o `None`). El booleano de encendido lo posee la ventana
  y se entrega al lienzo; el lienzo no decide cuándo está activo.
- API nueva: `set_tag_regions(regions)` y
  `set_tag_overlay_visible(visible)`; `clear()` también limpia regiones y
  tooltip. La ventana sincroniza las regiones en `_show_viewer_photo`
  **antes de su `return` anticipado** (misma foto con imagen ya cargada) y en
  `_apply_photo_update` cuando la foto actual es la del visor; en
  `set_photos` se limpian como ya se limpia el lienzo.
- `paintEvent`: reutilizar el `target` (`QRectF`) que ya calcula para
  `drawPixmap`; para cada región visible, `painter.drawRect` sobre
  `QRectF(target.x() + x*target.width(), target.y() + y*target.height(),
  w*target.width(), h*target.height())`. Solo cuando
  `_tag_overlay_visible`, `not _preview` y haya pixmap válido. Dibujar los
  rectángulos **después de la foto y antes del chip de favorito y del anillo
  de foco**, para que el chrome siga encima. Sin `setBrush`; trazo de 2 px
  fijo.
- Color: publicar en `theme.py` `accent_color(widget, background)` envolviendo
  el `_accent_for` ya existente, y pasarlo el canvas con `_canvas_color()`
  como fondo. Ningún color literal en el widget; mismo resultado en tema claro
  y oscuro, con contraste ≥ 3:1 sobre el gris del lienzo.
- Tooltip: `mouseMoveEvent` calcula en qué rectángulo cae el cursor (usando la
  misma geometría que el pintado), muestra `QToolTip.showText` con el nombre
  en la posición global del evento y recuerda cuál está activo;
  `leaveEvent`, el cambio de foto, el resize, la desactivación de `t` y
  `clear()` lo ocultan. En solapes, gana la primera región del orden
  determinista de `visible_tag_regions`. Con el overlay apagado no hay
  tooltip. El lienzo mantiene su nombre accesible («foto actual»).
- Redimensionar, hacer zoom y panear ya llaman a `update()`; basta con que el
  pintado lea la geometría vigente en cada `paintEvent`. No se guarda una
  geometría cacheada que pueda quedarse obsoleta.

## 6. Ciclo de vida y mensajes

- `PhotoWindow.__init__`: `self._tag_overlay_visible = False`. No se lee ni se
  escribe configuración; reiniciar la app lo devuelve a `False`.
- Cambiar de foto con `j`/`k`, `h`/`l`, `g g`, `G`, `0`/`$`, `PgUp`/`PgDn` o
  clic en la fila conserva el estado y muestra las regiones de la foto nueva
  en cuanto tenga imagen completa.
- Cerrar el visor con `q`/`Esc` conserva el estado; volver a abrirlo muestra
  las regiones si estaban activas. `Esc` mantiene su orden actual (overlay →
  zoom → rejilla): `t` no se engancha en esa cadena.
- Fotos duplicadas por hash comparten las mismas asignaciones y, por tanto,
  los mismos rectángulos en todas sus copias.
- Foto no disponible (ubicación ausente, ADR 0010): el visor no se abre, así
  que `t` no actúa; no hace falta caso especial.
- Sin foto actual: `t` no hace nada, sin mensaje.

## 7. Diseño esperado por módulo (orientativo, no literal)

| Fichero | Cambio |
| --- | --- |
| `src/foto/storage/tags.py` | `Region`, `TagAssignment.region`, `SELECT` y orden estable en `assignments_by_hash`; documentación del módulo actualizada (ya no dice que la región no se carga). |
| `src/foto/core/library.py` | Sin cambios de código: `CatalogPhoto.tags` ya transporta `TagAssignment`. |
| `src/foto/ui/app.py` | Sin cambios si `Photo.from_catalog` ya copia el campo; verificar la fábrica. |
| `src/foto/ui/models.py` | `PhotoTag.region` opcional y copia en `from_catalog`. |
| `src/foto/ui/presentation.py` | `visible_tag_regions` y `tag_rect_pixels`; nada más. |
| `src/foto/ui/keybindings.py` | Cuatro filas de `KEY_T` → `toggle_tag_overlay`. |
| `src/foto/ui/window.py` | Booleano de sesión, handler, registro en `_actions`, sincronización de regiones y mensajes. |
| `src/foto/ui/canvas.py` | Estado, pintado, tooltip y limpieza. |
| `src/foto/ui/theme.py` | `accent_color(widget, background)` público sobre `_accent_for`. |
| `docs/adr/0006-etiquetas.md` | Nota de revisión que enmienda la Decisión 7. |
| `docs/diseno-ui.md` y `src/foto/resources/ayuda.md` | Tecla `t` y descripción del overlay. |

## 8. Tests

### Sin Qt ni pantalla

- `tag_rect_pixels`: `(0.1, 0.2, 0.3, 0.4)` sobre `1000 × 500` da
  `(100, 100, 400, 300)`; `y` crece hacia abajo; recorte a bordes; destino
  vacío; `w/h` positivos.
- `visible_tag_regions`: incluye `confirmed` y `suggested` con región;
  excluye `rejected`, región `None` y foto sin etiquetas; no filtra por
  `kind`; conserva el orden y las caras distintas de una misma persona.
- `PhotoTag.region` por defecto `None`; `Photo.from_catalog` y
  `with_favorite_tag` conservan la región del resto de etiquetas;
  `photo_search_text`/`tag_chips` no cambian por tener región.
- `assignments_by_hash`: sembrar por SQL dos asignaciones de la misma persona
  con regiones distintas y una sin región; comprobar los valores, el orden por
  `pt.id` y que el hash vecino no aparece.

### Con Qt (`QT_QPA_PLATFORM=offscreen`)

- `key_action` devuelve `toggle_tag_overlay` para `t` en `VIEWER`,
  `GRID_VIEWER`, `PANEL` y `WINDOW`; `None` en `GRID`, con Shift, con Ctrl y
  con ⌘. Ninguna fila ambigua nueva.
- Los cuatro contextos emiten la acción por teclado real y el visor reacciona;
  con el visor cerrado, `t` desde panel/fallback no cambia nada.
- Respeto de guardias: con ayuda o prompt abiertos, `t` no conmuta (ya lo
  hace `_dispatch`).
- Estado de sesión: activar, navegar a otra foto, cerrar y reabrir el visor;
  el rectángulo sigue visible. Reiniciar la ventana lo deja apagado.
- Pintado: pixmap sintético y una región sembrada; al activar `t` aparecen
  píxeles del color de acento en el rectángulo esperado y al desactivar
  desaparecen; lo mismo en tema claro y oscuro; el chip de favorito sigue
  encima.
- Preview: `set_image(..., preview=True)` no pinta; `set_image` con la imagen
  completa sí.
- Zoom/paneo: tras `zoom_in`, `toggle_zoom`, `pan_by` y un resize, la
  geometría calculada coincide con la transformación del `target`; el ancho
  de trazo no escala.
- Tooltip: `mouseMoveEvent` dentro del rectángulo muestra el nombre; fuera,
  al cambiar de foto y al apagar `t` se oculta; ninguna acción se dispara por
  el hover.
- Sin regiones: mensaje efímero en la barra, sin rectángulos, interruptor
  activo; la foto siguiente con región sí pinta.
- Catálogo real de prueba: foto con `photo_tags` con región (sembrada o
  importada), visor abierto, `t` pinta y **el catálogo queda byte a byte
  igual**; una pasada de `foto-index` con la app abierta no altera el overlay.
- Inventario documental: `ayuda.md` contiene `t` y los tests de ayuda y
  teclas siguen en verde.

Todo bug corregido lleva su test de regresión.

## 9. Verificación

1. `uv sync` — sin dependencias nuevas.
2. `uv run pytest` y `uv run ruff check .` — suite completa en verde, sin
   pantalla.
3. `uv run foto` sobre la biblioteca de prueba con caras importadas: abrir
   una foto en el visor, pulsar `t`, comprobar que aparece un rectángulo por
   cara, que el tooltip da el nombre, que `+`/`-`/`z`/`H`/`J`/`K`/`L` lo
   siguen, que al cambiar de foto sigue activo y que en la rejilla `t` no
   hace nada. Repetir en tema claro y oscuro y a 640×480.
4. `uv run foto-export --dry-run` antes y después: `t` no cambia metadatos.
5. **Criterio de fin:** rectángulos y tooltip correctos solo con el visor
   abierto, estado de sesión, modelo con región cargada al arrancar, docs y
   tests al día, cero cambios en indexador/transfer y originales intactos.

## 10. Documentación

| Fichero | Cambio |
| --- | --- |
| `docs/adr/0006-etiquetas.md` | Nota de revisión (2026-11-24) con origen «prompt de rectángulos en el visor»: la Decisión 7 pasa a decir que la región **sí** se carga en el modelo y el visor **sí** pinta el borde con `t` y tooltip con el nombre. La definición `[0,1]`, el eje `y`, el todo-o-nada y las reglas de estado no cambian. |
| `docs/diseno-ui.md` | Fila de `t` en la tabla del visor; nota en rejilla y panel de que solo actúa con el visor abierto; párrafo en «Visor» con borde de acento, sin texto sobre la foto, tooltip, geometría con zoom/paneo, oculto durante el preview y alternativa de teclado en el panel. Sin teclas duplicadas. |
| `src/foto/resources/ayuda.md` | Fila de `t` en «Visor» y una frase sobre el tooltip y la no acción en rejilla. El renderer solo entiende encabezados, tablas de dos columnas y fichas de código: no usar listas ni negritas. |
| `tests/test_keybindings.py`, `test_ui_key_inventory.py`, `test_ui_documented_features.py`, `test_ui_help.py` | Inventario y documentación de la tecla al día. |

## 11. Fuera de alcance

- Crear, editar, borrar o fusionar regiones o etiquetas; asignar etiquetas
  desde el visor; confirmar/rechazar sugerencias.
- Dibujar nombres sobre la imagen, rectángulos para etiquetas sin región,
  `fav` a pantalla completa ni indicadores de confianza/origen en el overlay.
- Persistir el estado de `t`, añadir comandos `:`, atajos globales o cambios
  en el orden de `Esc`.
- Tocar el pipeline del indexador, `foto-export`/`foto-import`, la fase
  `apple-import`, el esquema del catálogo, `README.md` ni `AGENT.md`.
- Dependencias nuevas, detección de caras, ML, edición de imagen, vídeo o
  geocodificación.
- `git commit`, `git push` o cambios de configuración de git sin permiso
  explícito.
