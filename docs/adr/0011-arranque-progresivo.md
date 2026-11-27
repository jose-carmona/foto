# ADR 0011 — Arranque progresivo del visor: ventana primero, catálogo después

- **Estado:** aceptada
- **Fecha:** 2026-11-19
- **Decide:** Jose
- **Ámbito:** arranque de la UI con bibliotecas grandes (25k+ fotos). Complementa
  al ADR 0003 (consumo del índice), que no cambia en lo que respecta a *qué* lee
  el visor ni *desde dónde*: solo decide *cuándo* y *en qué hilo*.

## Contexto

Tras el paso 1 de quick wins, el arranque con 25 000 fotos sigue siendo
síncrono: `MainWindow.__init__` abre el catálogo, carga fotos, etiquetas y
ubicaciones, materializa los objetos de UI y popular la rejilla **antes** del
primer `window.show()`. Aunque el trabajo total haya bajado, mientras sea
síncrono el usuario ve un proceso congelado durante varios segundos.

## Decisión

### 1. La ventana se muestra inmediatamente, con estado de carga

`main()` crea la `MainWindow` vacía y la muestra antes de que exista ningún
dato. Durante la carga la rejilla muestra un texto discreto («Cargando el
catálogo…») reutilizando el estado vacío existente: sin spinner, sin
animaciones, conforme a `docs/diseno-ui.md` (§ Disciplina visual: las
miniaturas pendientes usan un bloque neutro, no un spinner). La barra de
estado fija el mismo aviso con el patrón de texto persistente (`set_pinned`)
y lo retira al terminar la carga.

El guard de catálogo ausente o de error fatal de arranque conserva el mensaje
y el cierre con código 1, pero ocurre **tras** el `show()`: la señal de fallo
del worker muestra el mismo `QMessageBox.critical` y termina la aplicación
con `QApplication.exit(1)`. Es una desviación consciente del flujo anterior
(antes el guard era previo al show): a cambio la ventana aparece siempre en
cuestiones de décimas, y el caso de error es la excepción.

### 2. Un hilo worker con `QThread` + objeto worker

La carga del catálogo corre en un hilo worker: un `QObject` worker
(`ui/app.py`, `LibraryLoadWorker`) movido a un `QThread` propio y corto
(`started` → `worker.load`). No se usa el `QThreadPool` global: la carga es
una operación única y larga, no una cola de tareas cortas, y un hilo dedicado
hace trivial la propiedad y la cancelación.

### 3. Comunicación por señales, solo datos

El worker entrega el resultado por señales Qt: `loaded(fotos, etiquetas,
ubicaciones)` o `failed(mensaje)`. **Ningún objeto Qt cruza el hilo**: lo que
viaja son datos puros (`Photo`, `Tag`, `LocationStatus` —dataclasses sin
Qt—). Los iconos y las miniaturas siguen naciendo y viviendo en el hilo de
UI, en el servicio de miniaturas (`ui/thumbnails.py`, ADR 0004), que no se
toca: el worker no toca disco más allá de leer el catálogo.

### 4. Propiedad del catálogo durante la carga

La conexión solo-lectura (`mode=ro`, sin lock de escritor) la abre y la
cierra el worker, íntegramente dentro de su hilo: es el modelo actual del
visor (ADR 0003). La conexión RW de etiquetas/favoritos (`connect_read_write`)
sigue abriéndose en el hilo de UI y siendo propiedad de la ventana: es el
escritor único del visor (ADR 0006) y no participa en la carga.

### 5. Cancelación cooperativa

Cerrar la aplicación a mitad de carga debe salir sin traceback y sin dejar el
hilo colgado. Como la lectura es solo-lectura y no deja escrituras pendientes,
bastaría descartar el resultado («o ignorado»); se hace un poco mejor con
**cancelación cooperativa**: `load_from_catalog` y `load_library` aceptan un
`should_continue: Callable[[], bool]` puro que comprueban por foto y entre
fases, y cortan con la excepción de control `LibraryLoadCancelled` (definida
en `core/library.py`; no es un error del catálogo y nadie la muestra al
usuario). El cierre —el `closeEvent` de la ventana **y también**
`aboutToQuit`, que Qt emite en toda salida del bucle (`Ctrl+q`, `:q`, ⌘Q, que
no pasan por el `closeEvent`)— marca la cancelación y espera al hilo con un
límite generoso: el corte ocurre en la iteración siguiente, así que la espera
es de milisegundos salvo que el hilo esté dentro de un `fetchall` (fracción
de segundo con 25k). El mismo apagado de fondo cubre el servicio de
miniaturas y el pool del visor (espera acotada a sus tareas activas), y las
tareas (`QRunnable`) emiten bajo guardia: si su objeto de señales ya murió
porque superaron la espera del apagado, el resultado es descartable y el
cierre sigue silencioso.

### 6. Populate de la rejilla por lotes

La inserción monolítica de los 25k `QListWidgetItem` se sustituye por lotes
de ~500 ítems procesados con un `QTimer` de disparo cero: tras el primer
lote la rejilla ya es visible y navegable; los siguientes se insertan en
fondo. El cálculo de orden, grupos y visibilidad se hace **una vez antes del
primer lote**. `QListWidget` se conserva: la migración a modelo virtualizado
es el paso 3 del prompt, condicionado y solo de diseño.

Coherencia a medio populate (decisión documentada en el prompt): el índice
`_grid_items`, la caché de visibles y los filtros se mantienen actualizados
**lote a lote** —cada ítem nace ya oculto o visible según el filtro vigente—,
de modo que búsqueda, favoritos y navegación funcionan durante el populate;
al terminar, los pases de siempre normalizan el estado.

### 7. Sin Qt en `core/` ni `storage/`

El worker, el estado de carga y los lotes viven en `ui/`. La única adición a
`core/` es el parámetro `should_continue` y su excepción de control, puros y
sin Qt, al servicio de la cancelación del punto 5.

## Consecuencias

### Positivas

- Time-to-first-pixel de segundos a décimas: la ventana con su estado de
  carga aparece antes de tocar el catálogo.
- El hilo de UI nunca se bloquea más que por los lotes del populate (~25 ms
  por lote de 500) ni por la carga.
- El cierre a mitad de carga es limpio y rápido, con o sin carga en curso.
- `load_from_catalog` sigue siendo la única puerta de lectura del catálogo
  (ADR 0003) y sirve igual para la carga síncrona de antes (tests, scripts)
  y para la progresiva de la UI.

### Negativas

- El arranque pasa a tener dos fases y un estado intermedio visible; los
  tests de ciclo completo necesitan bombear el bucle de eventos.
- Un error fatal de arranque se anuncia tras mostrar la ventana (punto 1).
- La señal entrega la lista completa de fotos: la primera tanda de la rejilla
  espera al `load_library` completo. Repartir la carga en emisiones parciales
  es una mejora futura que no cambia el contrato de señales.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| `QThreadPool` global con tarea única | Propiedad y cancelación más difusas para una operación única y larga; el hilo dedicado es el patrón Qt canónico para esto. |
| Cargar en el hilo de UI pero por trozos con `processEvents` | Seguiría bloqueando el arranque entre trozos y mezcla dos problemas (carga y populate) en el mismo hilo. |
| Repartir la carga en varias señales (fotos, luego etiquetas/ubicaciones) | Complejidad extra en la ventana por una ganancia pequeña; las ubicaciones hacen falta antes del primer lote para el banner (ADR 0010). |
| `QThread` heredado con `run()` sobreescrito | Funciona, pero el objeto worker movido a hilo es el patrón documentado de Qt para cancelar y limpiar con `finished` → `deleteLater`. |
| Paginación de la rejilla | Fuera del objetivo del prompt; el populate completo por lotes es asumible. |

## Relación con otros ADR

- **ADR 0003**: sin cambios en la forma de leer el catálogo (solo lectura,
  sin crear ni regenerar índice); esta decisión solo retrasa y aparta a hilo
  esa lectura.
- **ADR 0004**: el mecanismo de miniaturas no se toca; sigue en el hilo de UI.
- **ADR 0006**: la conexión RW de favoritos conserva su propiedad y semántica.
- **ADR 0010**: el banner de ubicaciones no disponibles se compone al llegar
  los datos del worker, con la misma función pura de presentación.
