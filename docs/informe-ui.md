# Informe de evaluación del código UI de `foto` (fototeca)

**Ámbito revisado:** `src/foto/ui/` (`window.py`, `app.py`, `prototype.py`,
`thumbnails.py`) + `src/foto/core/thumbnails.py` (uso por la UI) y documentación
de referencia (`AGENT.md`, `README.md`, `docs/diseno-ui.md`, ADRs 0001–0004,
`tests/`).

**Stack:** Python 3.12 + PySide6 (Qt). Lenguaje de identificadores en inglés;
textos de UI en español.

**Estado del documento:** revisión 3 (2026-11-05). Informe de arquitectura,
compresión y mantenibilidad. Esta revisión incorpora las decisiones de producto
de §0, actualiza todas las referencias de línea al código actual y retira los
hallazgos que dejaron de existir al aplicarlas. El código revisado tiene
3.453 líneas en `window.py` y 292 en `app.py`.

**Aplicación del P0 (2026-11-05):** se ejecutaron las tres recomendaciones P0
de §4. Aplica los cambios de documentación (§3.2.I, §3.2.L) y añade los tests
de contrato e interacción; los hallazgos resueltos quedan marcados en §3.2.I,
§3.2.L, §5 y §6. Favoritos en XMP y menú contextual siguen pendientes/aplazados.

**Aplicación del P1.4 (2026-11-05):** la lógica de presentación pura salió de
`window.py` hacia dos módulos sin Qt: `ui/models.py` (el modelo `Photo`) y
`ui/presentation.py` (orden, grupos, documento de búsqueda, navegación
vertical, teclas de grupo y la aritmética del token `fg.muted`). Sin
re-exports: `app.py`, `prototype.py` y los tests importan por la ruta canónica.
La suite pura es `tests/test_presentation.py` (33 tests, con un guardián que
impide volver a importar Qt) y la equivalencia del color atenuado queda fijada
en `tests/test_ui_muted_color.py`; en total 301 tests (151 de UI).

---

## 0. Decisiones de producto aplicadas

Esta revisión registra cuatro decisiones: tres retiradas ya ejecutadas y un
aplazamiento explícito. No son propuestas: el código y la documentación de
producto reflejan el estado posterior. Los identificadores exactos que no deben
reaparecer están enumerados en `tests/test_ui_retired_features.py`; este informe
los describe por función para no reintroducirlos en la documentación.

1. **Comando de apertura de biblioteca: retirado por completo.** No queda
   handler en `COMMANDS` (`window.py:319-326`) ni en `_run_command`
   (`window.py:3131-3160`), no existe el callback de apertura que antes se
   pasaba a la ventana, no hay sugerencia en el estado vacío y no aparece en
   `docs/diseno-ui.md` ni en `src/foto/resources/ayuda.md`. Abrir otra
   biblioteca deja de ser una acción
   del visor: el catálogo se actualiza al reiniciar o mediante `foto-index`
   desde shell.
2. **Atajo de pantalla completa: retirado.** No hay captura de esa tecla ni
   llamada al modo de pantalla completa en `window.py`/`app.py`; tampoco aparece
   en la ayuda ni en el diseño. La pantalla completa no forma parte del MVP.
3. **Tira de miniaturas histórica: eliminada.** Se han borrado la clase de la
   tira, su dataclass de elemento, sus constantes, el parámetro que la activaba,
   las APIs de tira de `PhotoViewer`, la reconstrucción interna y el slot que
   escuchaba su selección. `PhotoViewer` (`window.py:845`) contiene solo
   `PhotoCanvas`. Con `grid_row=True` —app real y demo— la fila inferior sigue
   siendo la misma `PhotoGrid` recortada a una fila; con `grid_row=False` el
   visor ocupa toda la ventana y la rejilla se oculta, sin tira propia.
4. **Menú contextual de ratón: aplazado.** No se implementa en esta fase. Queda
   registrado como tarea pendiente al final de la Fase 1 del roadmap
   (`README.md:72`), y `diseno-ui.md:260-262` lo describe como comportamiento
   objetivo, no como función disponible. El teclado sigue cubriendo todas las
   acciones; no hay `QMenu` ni `contextMenuEvent` todavía.

La comprobación automática de las tres retiradas se ejecuta en
`tests/test_ui_retired_features.py` y escanea `src/foto/ui/*.py`,
`docs/diseno-ui.md` y `src/foto/resources/ayuda.md`. La suite de UI completa
pasa con **158 tests** (los 67 originales más el contrato de comandos y
atajos y las pruebas de interacción del P0, aplicado el 2026-11-05).

---

## Resumen ejecutivo

La UI de `foto` es un **prototipo funcional bien diseñado y disciplinado**, con
una separación de capas correcta en la dirección crítica (`core`/`storage` no
importan Qt), navegación por teclado, miniaturas asíncronas con caché acotada en
memoria y una documentación de diseño muy detallada.

Desde el punto de vista de **mantenibilidad**, el principal riesgo sigue siendo
`src/foto/ui/window.py`: un archivo monolítico de 3.453 líneas que hace de
ventana, modelo, controlador, navegador, gestor de caché, renderizador de ayuda y
gestor de tema a la vez. La lógica de teclado está repartida en varios sitios y
la densidad señal/ruido es baja en algunas zonas.

La revisión cruzada con el código mantiene tres puntos que la primera versión
no recogía, ya sin las funciones retiradas en §0:

1. **Alcance planificado, no implementado**: el menú contextual del ratón está
   documentado en el diseño y aplazado al final de la Fase 1 del roadmap; no
   debe presentarse como una función disponible.
2. **La suite de pruebas está infravalorada**: no es cierto que solo se prueben
   funciones puras. Varios tests instancian `PhotoWindow` real con
   `QT_QPA_PLATFORM=offscreen`; la suite de UI pasa con **158 tests**, incluidos
   los de regresión de funciones retiradas. Eso sí: requieren PySide6, no son
   tests “sin Qt” (resuelto en el P0: la documentación ya no describe la suite
   completa como “sin Qt”).
3. **La documentación sigue desalineada en varios puntos**: los favoritos en
   XMP se presentan como parte del MVP aunque solo viven en memoria (decisión
   pospuesta al final de la Fase 1). Los otros dos desajustes del informe
   anterior —la suite descrita como “sin Qt” y el silencio sobre el acceso a
   disco para derivados— se corrigieron en el P0, con tests de regresión.
   El fragmento corrupto que tenía `diseno-ui.md` en la
   revisión anterior ya está corregido.

**Veredicto:** arquitectura sólida en la dirección de dependencias; código
funcional y probado, pero con deuda de concentración de responsabilidades y con
una brecha residual entre lo documentado y lo implementado. Las decisiones de §0
acotan el alcance y eliminan la parte más visible de esa brecha. Antes de
escalar la UI conviene cerrar el resto y contener `window.py`.

---

## Alcance y método de revisión

Se revisaron los fuentes de la UI y los documentos que las rigen:

- `src/foto/ui/window.py` — ventana, widgets, navegación, visor, ayuda, paneles.
- `src/foto/ui/app.py` — punto de entrada Qt, carga del catálogo, integración con
  `ThumbnailService`.
- `src/foto/ui/thumbnails.py` — generación asíncrona y caché persistente de miniaturas.
- `src/foto/ui/prototype.py` — demo con datos falsos.
- `src/foto/core/thumbnails.py` — geometría y nombres de caché (puro, sin Qt).
- `src/foto/resources/ayuda.md` — documento de atajos empaquetado.
- `AGENT.md`, `README.md`, `docs/diseno-ui.md`, `docs/adr/0001–0004`.
- `tests/` — suite pytest, incluidos los tests de UI con plataforma offscreen y
  el test de regresión de funciones retiradas.

Método:

- Se comprobaron las referencias de línea y los tamaños de archivo tras aplicar
  las decisiones de §0.
- Se siguió el flujo de comandos y atajos desde la documentación hasta su
  implementación (`COMMANDS`, `_run_command`, `keyPressEvent`, `QAction`).
- Se ejecutó la suite de UI: `uv run pytest -q tests/test_ui_*.py` → **158
  tests superados**.
- Se verificó el contrato de documentación con los tests del P0:
  `tests/test_ui_documented_features.py` (comandos y atajos) y
  `tests/test_ui_interaction.py` (carga/fallo de imagen, prompts y foco).
- Se ejecutó `uv run ruff check src/foto/ui tests` → sin avisos.
- Se distingue expresamente entre “sin pantalla” (offscreen) y “sin Qt”: la
  suite de UI no necesita un servidor gráfico, pero sí importa PySide6.

---

## 1. Arquitectura

### 1.1 Capas y separación de responsabilidades

```
UI (Qt/PySide6)  ──►  Núcleo Python (core/storage)  ──►  SQLite catálogo + XMP (futuro)
```

La regla no negociable 5 de `AGENT.md` es que **`core/` y `storage/` no importan
Qt**; esa dirección sí se cumple. `window.py` no importa `storage`, y
`app.py` actúa como composition root: es el punto donde la UI se encuentra con
el catálogo y la configuración.

Conviene matizar dos afirmaciones de la primera versión:

- **La UI sí importa `core`.** `window.py:75` importa
  `foto.core.thumbnails.eviction_candidates` y `ui/thumbnails.py:15` importa
  `foto.core.thumbnails`. No es una violación de la regla —ese módulo es puro,
  sin Qt—, pero “la UI no toca `core`” es literalmente falso.
- **La UI hace acceso directo a disco.** `ImageLoadTask` decodifica originales
  con `QImageReader` (`window.py:546-563`) y `ThumbnailCache` lee y escribe
  ficheros (`thumbnails.py:92-127`). Esto choca con la lista de verificación de
  `docs/diseno-ui.md:541` (“La UI no contiene lógica de negocio ni acceso
  directo a disco”); si se acepta como excepción deliberada para derivados,
  debe documentarse así.

| Elemento | Rol |
|---|---|
| `Photo` (`window.py:253`) | Modelo inmutable (`frozen dataclass`) con clave por ruta/hash. |
| `PhotoWindow` (`window.py:2219`) | Orquestador: cuatro regiones, estado, navegación, acciones. |
| `PhotoGrid` (`window.py:872`) | `QListWidget` que sirve de rejilla y de fila del visor (misma vista, distinta geometría). |
| `PhotoViewer` (`window.py:845`) | Contenedor del visor; solo aloja `PhotoCanvas`, sin tira propia. |
| `PhotoCanvas` (`window.py:566`) | Lienzo del visor con zoom/paneo; no decodifica: solo pinta. |
| `ImageLoadTask` (`window.py:537`) | Decodificación del original en un `QThreadPool` de 2 hilos. |
| `ThumbnailService` (`thumbnails.py:189`) | Generación de miniaturas visibles en pool acotado (3 hilos), caché en disco atómica. |
| `MetadataPanel` (`window.py:1287`) | Ficha de metadatos, solo lectura, sin lógica de negocio. |
| `StatusBar` (`window.py:1619`) + `PromptEdit` (`window.py:1511`) | Línea de comandos `:`, búsqueda `/`, ayuda `?`, mensajes. |

### 1.2 Modelo de interacción

Se respeta `docs/diseno-ui.md` en lo esencial: una ventana, cuatro regiones
(rejilla, estado, metadatos, foto), teclado primero, atajos vim (`hjkl`, `g`,
`G`, `0`, `$`, `Ctrl+d/u`), modos (Normal/Búsqueda/Comando/Ayuda), sin diálogos
modales para acciones rutinarias y arranque con paneles ocultos **en la app
real** (`app.py:174`).

Matices:

- La demo `prototype.py` no pasa `initial_panel_visible`
  (`prototype.py:74`), por lo que usa el valor por defecto `True`
  (`window.py:2230`) y arranca con el panel de metadatos visible. No cumple la
  regla de “arranque siempre con paneles ocultos”, aunque sea una demo.
- El menú contextual del ratón está documentado como objetivo en
  `diseno-ui.md:260-262` y aplazado al final de la Fase 1 del roadmap
  (`README.md:72`). Todavía no existe en el código: no hay `QMenu` ni
  `contextMenuEvent`.
- La fila inferior del visor no es una vista aparte: con `grid_row=True` es la
  propia `PhotoGrid` comprimida, con las mismas cabeceras de grupo y la misma
  navegación.

### 1.3 Flujo de datos y concurrencia

- Lectura del catálogo SQLite en `app.py:load_photos` (`app.py:128`), solo
  lectura y se cierra tras usar.
- Miniaturas visibles generadas en `QThreadPool` (3 hilos), solo para el viewport,
  con descarte por generación al cambiar de vista.
- Imagen grande del visor decodificada en segundo plano (`ImageLoadTask`); se
  muestra la miniatura mientras tanto.
- Caché de originales limitada a 3 pixmaps (`IMAGE_CACHE_SIZE = 3`) con LRU
  sencillo; precarga de vecinos ±1.
- Caché de miniaturas en disco con escritura atómica (`tempfile` + `os.replace`)
  y ventana en memoria de 160 iconos.
- Tema claro/oscuro derivado de `QPalette` y reacción a `colorSchemeChanged`
  (`window.py:2389`).

---

## 2. Tamaño, densidad y caché

### 2.1 Tamaño y concentración

Archivos de UI (aproximado, tras las retiradas de §0 y la extracción del P1.4):

| Archivo | Líneas | Rol principal |
|---|---|---|
| `window.py` | 3.293 | Ventana completa: tema, navegación, visor, ayuda, paneles |
| `app.py` | 292 | Punto de entrada, carga de catálogo, integración con `ThumbnailService` |
| `thumbnails.py` | 291 | Servicio y caché de miniaturas |
| `presentation.py` | 211 | Reglas de presentación puras: orden, grupos, búsqueda, navegación, teclas, `fg.muted` (sin Qt) |
| `prototype.py` | 85 | Demo con datos falsos |
| `models.py` | 41 | Modelo de UI de una foto (`Photo`, sin Qt) |
| `core/thumbnails.py` | 112 | Geometría y nombres de caché (puro, sin Qt) |

`window.py` concentra **≈78 % de `src/foto/ui/`** (3.293 de 4.216 líneas, ya
después de ceder el modelo y la presentación pura al P1.4). Es un
“objeto dios” (`God Object`/`God File`): contiene funciones puras de utilidad,
dataclasses, traducción de teclas, widgets Qt, lógica de navegación, renderizado
HTML de ayuda y la lógica de estado de la ventana.

Bloques aproximados dentro de `window.py`:

| Bloque | Líneas aproximadas |
|---|---|
| `PhotoWindow` | ~1.235 |
| `PhotoGrid` | ~320 |
| `StatusBar` | ~300 |
| `MetadataPanel` | ~220 |
| `PhotoCanvas` | ~200 |
| Renderizador de ayuda (`_help_*`, `render_help`) | ~185 |
| `PhotoViewer` | ~27 |

### 2.2 Densidad señal/ruido

Zonas con alta densidad de boilerplate de Qt:

- `PhotoGrid.keyPressEvent` (`window.py:1076`),
  `PanelScroll.keyPressEvent` (`window.py:1207`),
  `viewer_key_action` (`window.py:477`) y
  `PhotoWindow.keyPressEvent` (`window.py:3397`) mapean teclas de forma
  parcialmente duplicada. `PhotoCanvas` reutiliza `viewer_key_action` en
  `window.py:741`; la rejilla y el panel de metadatos tienen su propia copia
  parcial porque sus acciones difieren.
- Los `stylesheets` están embebidos como cadenas CSS en `MetadataPanel`,
  `StatusBar`, `CompletionPopup`, `HelpOverlay`, `PhotoGrid` y `PhotoWindow`.
- `set_photo` en `MetadataPanel` ocupa unas 62 líneas
  (`window.py:1383-1444`) y construye la UI de metadatos manualmente con
  `QGridLayout`.
- La ayuda se renderiza desde `ayuda.md`, pero **el comportamiento de las teclas
  sigue hardcodeado** en los `keyPressEvent`; el test de ayuda comprueba
  fragmentos del HTML, no que cada atajo documentado esté implementado.

Por el lado positivo, funciones como `vertical_row_target`,
`photo_sort_value`, `sorted_photos`, `photo_group`, `is_group_start`,
`is_group_end` y el renderizador de ayuda son pequeñas, puras y bien nombradas.

### 2.3 Caché de miniaturas

- `CACHE_JPEG_QUALITY = 90` en `thumbnails.py:20`; caché derivado nombrado por
  `hash-tamaño-version.jpg`, escritura atómica (`tempfile` + `os.replace`).
- Caché en memoria acotada a 160 iconos (~63 MB según el comentario de
  `window.py:100-102`).
- **Pendiente conocido:** sin poda ni límite de tamaño de caché en disco
  (mencionado en `diseno-ui.md` §Rendimiento percibido y roadmap). Las
  miniaturas huérfanas se acumulan.

---

## 3. Mantenibilidad

### 3.1 Factores favorables (lo que hay que preservar)

1. **Reglas explícitas y mayoritariamente seguidas.** `AGENT.md` documenta
   decisiones, convenciones y flujos; el código usa type hints, `pathlib`,
   dataclasses y errores explícitos. No todas las reglas se cumplen al pie de la
   letra (ver §3.2), pero el marco existe.
2. **Dirección de dependencias correcta.** `core`/`storage`/`indexer` no saben de
   Qt ni de la UI. `app.py` es el composition root; `window.py` no importa
   `storage`.
3. **Datos inmutables.** `Photo` es un `frozen dataclass`; los cambios generan
   copias (`replace` en `_toggle_favorite`), lo que reduce efectos secundarios.
4. **Pruebas puras y de UI offscreen.** Hay tests de lógica pura
   (`vertical_row_target`, `photo_search_text`, caché) y tests que instancian
   `PhotoWindow` con `QT_QPA_PLATFORM=offscreen`
   (`tests/test_ui_grid_groups.py:65-72`,
   `tests/test_ui_viewer_layout.py:70-82`,
   `tests/test_ui_search.py:74-81`, `tests/test_ui_help.py:93-100`). La suite de
   UI pasa con **158 tests**, incluidos los de funciones retiradas y los del
   P0 (contrato de comandos/atajos e interacción). **No son
   tests “sin Qt”**: requieren PySide6, pero no una pantalla.
5. **Ayuda desacoplada como contenido.** `render_help` lee
   `src/foto/resources/ayuda.md` y genera HTML; el texto de los atajos no está
   hardcodeado en la UI. El límite es que el comportamiento de esos atajos sí lo
   está, y el test solo verifica que el HTML conserva ciertos fragmentos.
6. **Caché atómica y acotada en memoria.** Escritura temporal + `os.replace`;
   generación solo de miniaturas visibles; ventana de memoria limitada.
7. **Tema del sistema.** Respeta `QPalette` y `colorScheme` (claro/oscuro), sin
   fuentes empaquetadas ni tema propio impuesto.

### 3.2 Riesgos y deuda técnica

#### A. `window.py` como objeto dios (riesgo alto)

Hace demasiado: temas, modelos, navegación, visor, panel, estado, búsqueda,
comandos, ayuda, caché de miniaturas. Cualquier cambio de navegación o de tema
toca el mismo archivo gigante, lo que aumenta el riesgo de regresiones y dificulta
la revisión de PRs.

#### B. Lógica de teclado duplicada (riesgo alto)

El mapeo de teclas aparece en al menos cuatro sitios
(`viewer_key_action`, `PhotoGrid.keyPressEvent`, `PanelScroll.keyPressEvent`,
`PhotoWindow.keyPressEvent`) más el dispatch en `_on_action`
(`window.py:3173`) / `_on_viewer_action` (`window.py:3255`). Añadir o cambiar una
tecla requiere editar varios lugares y actualizar `diseno-ui.md` + `ayuda.md`.

Matiz importante: **no toda la duplicación es accidental**. La misma tecla
significa cosas distintas según el contexto:

- `j`/`k` mueven una fila en la rejilla, pero navegan foto anterior/siguiente en
  el visor.
- `H`/`J`/`K`/`L` hacen paneo solo con zoom.
- En el panel de metadatos, las flechas hacen scroll, no selección.
- Con un prompt o la ayuda abiertos, las teclas de una letra se escriben o se
  ignoran.

La solución no es una tabla global sin contexto, sino un modelo de acciones por
modo/widget y una tabla de traducción compartida donde el contexto sea explícito.

#### C. Lógica de presentación mezclada con UI (riesgo medio)

`photo_search_text`, `sorted_photos`, `photo_group`, `photo_sort_value` viven en
`window.py`. Son reglas de presentación/búsqueda y no dependen de Qt, pero están
atrapadas en un módulo que importa PySide6. La regla 5 de `AGENT.md` (“la UI no
contiene lógica de negocio”) se estira aquí.

Sin embargo, moverlas sin más a `foto.core` es arriesgado: `photo_group` devuelve
nombres de meses en español y trabaja sobre `Photo`, un modelo definido en la UI.
La opción más limpia es un módulo puro **`foto.ui.presentation`** (sin Qt) que
pueda probarse sin PySide6, o mover también el modelo `Photo` si se quiere ir a
`core`. En cualquier caso, deberían incluirse otros helpers puros atrapados en
`window.py`: `vertical_row_target`, `is_group_start`, `is_group_end` y la lógica
de color atenuado.

Resuelto en el P1.4 (2026-11-05): se optó por el módulo puro `foto.ui.presentation`
(sin Qt) y el modelo `Photo` se quedó en la capa de UI, en `foto.ui.models`, tal
como razonaba este hallazgo. Se movieron también `vertical_row_target`,
`is_group_start`/`is_group_end` (con constantes de tecla enteras propias) y la
aritmética del color atenuado (`muted_rgb`). Se prueba sin PySide6 en
`tests/test_presentation.py`, con test guardián que impide que los módulos puros
vuelvan a importar Qt o la ventana.

#### D. Estilos incrustados dispersos (riesgo medio)

CSS embebido en cadenas en múltiples widgets. `diseno-ui.md:427-429` dice que
“ningún widget escribe un color literal” y que los colores salen de una capa de
tokens construida sobre `QPalette`.

Matiz: `apply_light_palette`/`apply_dark_palette` **son** esa capa de tokens; que
contengan literales hexadecimales no es la violación. Las violaciones reales son:

- `error_color` (`window.py:246-251`), que devuelve rojos literales.
- `_accent_for` (`window.py:145-150`), con `#5c9cf5` embebido.
- Estilos de `StatusBar` que inyectan `error_color(...).name()` en cadenas CSS.
- Cada panel re-aplica su propio `styleSheet` en `retheme`, en lugar de recibir
  tokens de un único punto.

#### E. Duplicación de helpers de tema (riesgo bajo-medio)

`_muted_from_palette` (`window.py:129`) y `_help_muted` (`window.py:1946`)
calculan el mismo color atenuado con lógica idéntica; una devuelve `QColor` y la
otra una cadena hexadecimal. Es un caso claro de extracción trivial.

Resuelto en el P1.4 (2026-11-05): la aritmética vive en `muted_rgb`
(`ui/presentation.py`) y ambas funciones son ahora adaptadores Qt que delegan en
ella; `tests/test_ui_muted_color.py` fija, con las paletas clara y oscura, que el
color renderizado no cambia ni un canal.

#### F. Favoritos solo en memoria (riesgo bajo por MVP, pero contradice la documentación)

`_toggle_favorite` actualiza `self.photos` pero no persiste en XMP
(`window.py:3383-3395`). `AGENT.md:33` reconoce que XMP no está implementado,
pero `README.md:46` incluye los favoritos en XMP dentro del MVP y
`diseno-ui.md:462-464` exige escritura inmediata en XMP con reversión si falla.
No es solo deuda de MVP: es una **inconsistencia entre documentos y código** que
el informe anterior presentaba como algo “consciente y documentado”. La decisión
sobre qué hacer con los favoritos queda pospuesta al final de la Fase 1; no se
resuelve en el P0.

#### G. Caché de miniaturas sin poda (riesgo bajo-medio)

Conocido y documentado como pendiente; puede crecer sin límite en bibliotecas
grandes.

#### H. Menú contextual aplazado (riesgo bajo)

`diseno-ui.md:260-262` describe un menú contextual con las acciones de la foto
bajo el cursor. No hay `QMenu` ni `contextMenuEvent` en `window.py`/`app.py`
todavía. No es una brecha de documentación: la decisión de §0 lo sitúa al final
de la Fase 1 del roadmap (`README.md:72`) y el diseño lo marca como pendiente.
El riesgo es solo de expectativa si alguien lo lee como una función ya
disponible; el teclado cubre todas las acciones mientras tanto.

#### I. Documentación desalineada (riesgo medio)

Pendientes (fuera del P0):

- `AGENT.md:33` dice que XMP no está implementado; `README.md:46` y
  `diseno-ui.md:462-464` prometen favoritos en XMP dentro del MVP. La decisión
  de alcance está pospuesta al final de la Fase 1 del roadmap.
- `diseno-ui.md:473-476` exige arranque con ambos paneles ocultos; la demo no lo
  cumple porque usa el valor por defecto de `PhotoWindow`.

Resuelto en el P0 (2026-11-05):

- `README.md` y `AGENT.md` ya no describen toda la suite como “sin Qt”:
  distinguen los tests de `core/`/`storage/` (sin Qt) de los de UI (PySide6 con
  `QT_QPA_PLATFORM=offscreen`, es decir, sin pantalla).
- `diseno-ui.md` conserva la regla “sin lógica de negocio” y documenta la
  excepción de acceso directo a disco (decodificación de originales y caché de
  miniaturas), confinada en `ImageLoadTask` y `ui/thumbnails.py`.
- El fragmento corrupto que tenía `diseno-ui.md` en la revisión 2 sigue
  corregido; no queda deuda de ese tipo.
- Los comandos y atajos documentados tienen ahora tests de regresión que
  detectan los desajustes (`tests/test_ui_documented_features.py`).

#### J. Código específico de plataforma disperso en la UI (riesgo medio)

La regla 4 de `AGENT.md` pide aislar las diferencias de sistema en módulos
concretos. Sin embargo, `_CTRL_MASK` está en `window.py:86` y el bloque de
activación de ventana en macOS con `osascript` está en `app.py:244-283`. Es deuda
de plataforma dentro de la UI, no solo de `core`/`storage`.

#### K. Diálogos modales en el arranque (riesgo bajo-medio)

`app.py` usa `QMessageBox.critical` en `161`, `209`, `230` y `242`.
`diseno-ui.md:468-469` dice que los errores no usan diálogos modales y
`diseno-ui.md:538` lo repite en la lista de verificación. Puede argumentarse que
los errores fatales de arranque son una excepción razonable, pero el informe debe
discutirlo, no afirmar sin matices que se respeta la regla.

#### L. Acceso directo a disco desde la UI (riesgo medio)

`ImageLoadTask` (`window.py:537-563`) decodifica originales desde el hilo de
trabajo y `ThumbnailCache` (`thumbnails.py:92-127`) lee y escribe en
`.foto/thumbs/`. La lista de verificación de `diseno-ui.md:541` decía que la UI
no debe tener acceso directo a disco. Resuelto en el P0 (2026-11-05): la
excepción está documentada en `diseno-ui.md` (lista de verificación y
Rendimiento percibido) y el acceso sigue confinado a `ui/thumbnails.py` y a la
tarea de carga, no repartido por la ventana.

### 3.3 Duplicaciones detectadas (resumen)

| ¿Qué? | Dónde | Impacto |
|---|---|---|
| Mapeo de teclas vim | `viewer_key_action`, `PhotoGrid`, `PanelScroll`, `PhotoWindow` | Alto: cada nueva tecla toca 4+ sitios |
| Dispatch de acciones | `_on_action` vs `_on_viewer_action` | Medio: vocabularios en gran parte disjuntos; no son “casi idénticos” |
| Color atenuado `fg.muted` | `_muted_from_palette` vs `_help_muted` | Bajo |
| Estilos de barra de scroll | `MetadataPanel`, `HelpOverlay`, `PhotoGrid`, `StatusBar` | Medio |
| Colores de tema literales | `error_color`, `_accent_for`, estilos inline de `StatusBar` | Medio |
| Documentación vs implementación | favoritos XMP, tests “sin Qt”, acceso a disco | Alto: el usuario y el mantenedor reciben información falsa |

---

## 4. Recomendaciones priorizadas

### P0 — Cerrar la brecha entre documentación y producto

1. **Alinear `README.md`/`AGENT.md` y la lista de verificación de
   `diseno-ui.md`.** Corregir “sin Qt” por “sin pantalla” y documentar la
   excepción de acceso directo a disco para los derivados. La decisión sobre los
   favoritos queda fuera de este P0: está pospuesta al final de la Fase 1.
2. **Añadir tests de regresión para comandos y atajos documentados.** El test de
   funciones retiradas ya cubre las tres retiradas de §0. Falta el caso general:
   que cada comando de `diseno-ui.md`/`ayuda.md` exista en `COMMANDS` o en
   `_run_command`, y que cada atajo básico documentado tenga un
   `keyPressEvent` que lo produzca.
3. **Ampliar la suite offscreen existente, no sustituirla.** Ya hay tests que
   instancian `PhotoWindow`; faltan rutas críticas como carga/fallo de imagen,
   prompt de búsqueda/comando y gestión de foco. No hace falta `pytest-qt` para
   esto: `QApplication` con `QT_QPA_PLATFORM=offscreen` ya funciona y evita una
   dependencia nueva (regla 6 de `AGENT.md`).

### P1 — Contener la deuda estructural

4. **Extraer la lógica de presentación pura a `foto.ui.presentation`.** Mover
   `photo_search_text`, `sorted_photos`, `photo_group`, `photo_sort_value`,
   `vertical_row_target`, `is_group_start`, `is_group_end` y la lógica de color
   atenuado. Mantener el modelo `Photo` en la UI o moverlo también, pero no
   dejar funciones que devuelven textos en español dentro de `core` sin una
   decisión explícita. El objetivo es poder probarlas sin importar PySide6.
   **Aplicada el 2026-11-05:** el modelo vive en `ui/models.py` y las reglas en
   `ui/presentation.py`, ambos sin Qt; los importadores usan la ruta canónica y
   `tests/test_presentation.py` las prueba sin PySide6.
5. **Unificar el mapeo de teclas con contexto explícito.** Un módulo/tabla que
   traduzca `(modo/widget, tecla, modificadores)` → acción, y que
   `PhotoGrid`, `PanelScroll`, `PhotoCanvas` y `PhotoWindow` consulten. No
   fusionar `_on_action` y `_on_viewer_action` a ciegas: sus vocabularios son en
   gran parte distintos. Si se unifican, hacerlo como registro de comandos con
   acciones por contexto, no como un `if` gigante.
6. **Partir `window.py` por responsabilidades.** Módulos separados:
   `theme.py` (paletas y tokens), `models.py` (`Photo`),
   `navigation.py` (movimiento de cursor, `vertical_row_target`),
   `canvas.py` (`PhotoCanvas`), `panels.py` (`MetadataPanel`, `StatusBar`),
   `help.py` (renderizador), `keybindings.py` (tabla de teclas). `PhotoWindow`
   se queda como orquestador delgado. Hacerlo después de los tests de P0 para
   tener red de seguridad.
7. **Centralizar estilos y tokens.** Mantener `apply_*_palette` como capa de
   tokens, pero mover `error_color` y `_accent_for` allí y evitar que cada panel
   re-aplique su propio `styleSheet` en `retheme`.
8. **Añadir pruebas de interacción para las rutas críticas de `PhotoWindow`.**
   La base ya existe (offscreen); extenderla a visor, prompt, ayuda, búsqueda y
   gestión de foco. No es necesario montar `pytest-qt`.
9. **Poda de caché de miniaturas en disco.** Límite de tamaño y limpieza de
   huérfanos (pendiente en el roadmap y `diseno-ui.md`).

### P2 — Opcional / evolutivo

10. **Evaluar modelo/vista (`QAbstractListModel` + delegate) si `QListWidget`
    empieza a pesar**, como ya se señala en `diseno-ui.md` Pendiente.
11. **Favoritos en XMP:** la decisión de alcance queda pospuesta al final de la
    Fase 1. Si se aprueba, persistirlos respetando “XMP es la fuente de verdad”
    y la actualización optimista con reversión.
12. **Extraer la construcción de metadatos** de `MetadataPanel.set_photo` a un
    renderizador de filas independiente.
13. **Implementar el menú contextual de ratón** cuando se retome el final de la
    Fase 1. Hasta entonces se mantiene como tarea pendiente en `README.md:72` y
    como objetivo marcado en `diseno-ui.md:260-262`; no debe anunciarse como
    disponible.

---

## 5. Registro de verificación

| Afirmación / hallazgo | Evidencia |
|---|---|
| Las tres funciones retiradas en §0 no reaparecen | `tests/test_ui_retired_features.py` pasa y escanea `src/foto/ui/*.py`, `docs/diseno-ui.md` y `src/foto/resources/ayuda.md`. |
| “La UI no toca `core`/`storage`” | Falso en sentido literal: `window.py:75` y `ui/thumbnails.py:15` importan `core.thumbnails`; `app.py:17-20` importa `storage`. La regla real (core/storage no importan Qt) sí se cumple. |
| “Los tests no cubren `PhotoWindow`” | Falso: `tests/test_ui_grid_groups.py:65-72`, `tests/test_ui_viewer_layout.py:70-82`, `tests/test_ui_search.py:74-81` y `tests/test_ui_help.py:93-100` instancian la ventana. |
| “Tests sin Qt” | Falso: importan PySide6 y usan `QApplication` con `QT_QPA_PLATFORM=offscreen`. Son tests sin pantalla, no sin Qt. |
| Suite de UI | `uv run pytest -q tests/test_ui_*.py` → 158 tests superados. |
| Comandos y atajos documentados se implementan | `tests/test_ui_documented_features.py`: vocabulario bidireccional (documentación ⇄ `COMMANDS`), ejecución real de comandos y tabla explícita de atajos con teclas documentadas cubiertas. |
| Rutas críticas de `PhotoWindow` offscreen | `tests/test_ui_interaction.py`: carga y fallo de `ImageLoadTask` (`loaded`/`failed`, precarga, sin reintentos), prompts de búsqueda/comando (filtrar, completar, historial, `Esc`) y foco entre rejilla, visor, panel y prompts. |
| Documentación “sin Qt” corregida | `README.md`/`AGENT.md` describen la suite sin pantalla y distinguen los tests sin Qt de `core/`/`storage/`; los desajustes tienen test de regresión. |
| Acceso directo a disco documentado como excepción | `docs/diseno-ui.md` §Lista de verificación y §Rendimiento percibido: la regla “sin lógica de negocio” se mantiene y el acceso se limita a decodificar originales y a la caché de miniaturas, confinado en `ImageLoadTask` y `ui/thumbnails.py`. |
| “El proyecto cumple sus reglas no negociables hoy” | Parcial: se cumple la dirección de dependencias y la regla 5 dejó de estirarse con el P1.4 (la lógica de presentación pura vive en `ui/presentation.py` y el modelo en `ui/models.py`, sin Qt); siguen pendientes la persistencia XMP de favoritos y el aislamiento de plataforma. El acceso directo a disco queda documentado como excepción acotada (P0). |
| Menú contextual | Aplazado, no implementado: `diseno-ui.md:260-262` lo describe como objetivo y `README.md:72` lo registra al final de la Fase 1; no hay `QMenu` ni `contextMenuEvent` todavía. |
| Documentación “excelente” | Matizable: el desajuste sobre tests y acceso a disco quedó resuelto en el P0; sigue pendiente la decisión sobre favoritos en XMP (pospuesta al final de la Fase 1). |
| `MetadataPanel.set_photo` ~80 líneas | Son ~62 líneas (`window.py:1383-1444`). |
| `apply_*_palette` viola “ningún color literal” | Interpretación incorrecta: esas funciones son la capa de tokens. Las violaciones reales están en `error_color` (`window.py:246-251`) y `_accent_for` (`window.py:145-150`). |
| `_on_action` y `_on_viewer_action` “casi idénticos” | Exagerado: sus vocabularios son en gran parte disjuntos; solo coinciden en `g`, favoritos, ayuda, búsqueda y panel. |

---

## 6. Conclusión

El código UI de `foto` es **funcional, consistente con buena parte de sus reglas
y bien documentado en intención**. Sus puntos fuertes (dirección de dependencias,
inmutabilidad, concurrencia no bloqueante, ayuda desde archivo, tests offscreen)
dan una base sólida.

El principal obstáculo a la mantenibilidad a medio plazo sigue siendo la
**concentración en `window.py`** y la **duplicación de la lógica de teclado**.
Tras el P0 (2026-11-05) el riesgo inmediato ya no son las funciones retiradas
en §0 ni los comandos y atajos desalineados (hay tests de contrato), ni los
tests “sin Qt” ni el silencio sobre el acceso a disco (corregidos y
documentados): queda la **brecha de los favoritos en XMP**, cuya decisión de
alcance está pospuesta al final de la Fase 1. El menú contextual no cuenta
como brecha: está aplazado explícitamente al final de la Fase 1 del roadmap.
Mantener los tests de regresión es más urgente y más barato que partir el
archivo.

Las recomendaciones P0 son cambios acotados que no alteran la arquitectura y
eliminan información engañosa. Las P1 son refactors internos que sí conviene
hacer, pero después de tener una red de tests que cubra comandos, atajos y rutas
críticas de la ventana.
