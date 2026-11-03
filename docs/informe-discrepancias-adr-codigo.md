# Informe de discrepancias entre los ADR y el código

- **Fecha de revisión:** 2026-11-02
- **Alcance:** ADR 0001, 0002, 0003 y 0004, todos en estado «aceptada».
- **Base revisada:** worktree actual, con `HEAD` en `5588ac8` y cambios locales ya existentes en `README.md`, `docs/diseno-ui.md` y `src/foto/ui/prototype.py`.
- **Método:** revisión estática de decisiones, implementación y pruebas; ejecución de la suite y de Ruff.

## 1. Resumen ejecutivo

La implementación reproduce gran parte de la arquitectura decidida: identidad por SHA-256, catálogo incremental, deduplicación, ubicación canónica, caché de miniaturas y separación entre indexador y UI. No obstante, se han identificado **17 discrepancias** entre lo aceptado en los ADR y el comportamiento actual:

| Severidad | Número | Criterio |
| --- | ---: | --- |
| Alta | 3 | Pérdida de configuración o ruptura de garantías de escritura/compatibilidad. |
| Media | 10 | Funcionalidad aceptada ausente, incompleta o garantías de robustez incumplidas. |
| Baja | 4 | Desviaciones acotadas, de diagnóstico, presentación u observabilidad. |
| **Total** | **17** | |

Las prioridades son:

1. **Evitar pérdida de configuración:** `save_config()` reescribe el fichero completo y descarta contenido; además, la validación global admite versiones desconocidas.
2. **Hacer efectiva la separación lector/escritor del catálogo:** la UI abre SQLite en lectura/escritura y puede migrarlo; el indexador también abre y migra antes de adquirir el lock.
3. **Completar el comportamiento aceptado del visor:** `:rescan` no es ejecutable, la búsqueda solo usa el nombre y la ordenación no conserva las claves originales del catálogo.
4. **Cerrar desviaciones menores con pruebas de regresión:** bootstrap, `dry-run`, metadatos, errores de caché y presentación de la ubicación canónica.

No se han contado como discrepancias funciones explícitamente pospuestas: XMP, favoritos en XMP, poda de la caché en disco, S3, llavero, IA o edición.

## 2. Validación realizada

| Comprobación | Resultado | Interpretación |
| --- | --- | --- |
| `uv run pytest` | **82 pruebas superadas** | La suite actual está verde, pero no cubre la mayoría de las discrepancias descritas. |
| `uv run ruff check .` | **11 errores** | Hallazgos de estilo ya presentes en el árbol revisado; no se han contado como discrepancias ADR. |
| Cobertura de UI | No hay pruebas que importen `foto.ui` | La carga real del catálogo, los comandos, la búsqueda, la ordenación y los widgets del visor carecen de cobertura directa. |
| Cobertura de miniaturas | Solo helpers de `core/thumbnails.py` | No se ejercitan `ThumbnailCache`, `load_thumbnail()` ni el servicio Qt. |

Las conclusiones sobre discrepancias proceden de la correspondencia estática entre requisito y código. Los tests verdes no demuestran conformidad en los puntos sin cobertura.

## 3. Resumen por ADR

| ADR | Estado de implementación | Altas | Medias | Bajas | Hallazgos |
| --- | --- | ---: | ---: | ---: | --- |
| 0001 — Configuración | Parcial | 2 | 4 | 0 | C01–C06 |
| 0002 — Indexado | Mayoritariamente conforme | 0 | 2 | 2 | I01–I04 |
| 0003 — Visor y catálogo | Parcial | 1 | 3 | 1 | V01–V05 |
| 0004 — Caché de miniaturas | Mayoritariamente conforme | 0 | 1 | 1 | M01–M02 |
| **Total** | | **3** | **10** | **4** | **17** |

## 4. Discrepancias detalladas

### 4.1 ADR 0001 — Almacenamiento de la configuración

#### C01 — Guardar la configuración destruye contenido no relacionado — **Alta**

- **Decisión:** la aplicación debe poder reescribir `config.toml` sin perder el resto del contenido y sin reformatear indiscriminadamente (`docs/adr/0001-configuracion.md:55-59,129-131`).
- **Código:** `save_config()` genera desde cero un documento que solo contiene `version` y `[library]`, y reemplaza el fichero completo (`src/foto/storage/config.py:88-105`). Se invoca al elegir o cambiar de biblioteca (`src/foto/ui/app.py:210-221,256-263`).
- **Impacto:** se pierden comentarios, secciones desconocidas, claves adicionales y formato. Una versión antigua puede borrar configuración introducida por una versión posterior.
- **Cobertura:** no hay pruebas directas de `save_config()` ni de preservación.
- **Resolución:** actualizar `[library].path` de forma conservadora, conservar contenido desconocido y añadir pruebas de idempotencia, comentarios, claves y secciones adicionales.

#### C02 — La configuración global no valida versiones ni parte del esquema — **Alta**

- **Decisión:** debe existir validación/migración desde la primera versión; una versión desconocida o un fichero inválido debe producir un error explícito (`docs/adr/0001-configuracion.md:134-137`).
- **Código:** `load_config()` solo comprueba `isinstance(version, int)`, por lo que acepta `0`, valores negativos, `99` y booleanos. Si `[library]` no es una tabla o `path` no es texto, ambos se ignoran silenciosamente (`src/foto/storage/config.py:60-85`).
- **Impacto:** una configuración incompatible puede tratarse como válida y después reescribirse, agravando C01. Los errores de tipo pueden desviar el flujo al diálogo de primera ejecución en vez de informar del problema.
- **Cobertura:** no existen equivalentes globales a las pruebas de versión futura y TOML inválido que sí tiene `library.toml` (`tests/test_library_bootstrap.py:53-83`).
- **Resolución:** validar `type(version) is int`, aceptar únicamente versiones soportadas, validar los tipos de `[library]` y `path`, y elevar `ConfigError` con casos de regresión.

#### C03 — La UI no aplica toda la precedencia ni comparte un resolvedor — **Media**

- **Decisión:** la biblioteca se resuelve como `--library` → `FOTO_LIBRARY` → `[library].path` → diálogo, y la precedencia debe implementarse en un solo sitio (`docs/adr/0001-configuracion.md:108-125`).
- **Código:** el indexador resuelve los tres primeros niveles en `src/foto/indexer/cli.py:108-117`; la UI solo llama a `load_config()` y usa `config.library_path` (`src/foto/ui/app.py:234-246`). No consulta `FOTO_LIBRARY` y no reutiliza el resolvedor del indexador.
- **Impacto:** `FOTO_LIBRARY` no funciona para `foto`; los dos ejecutables pueden abrir bibliotecas distintas con el mismo entorno y evolucionar con reglas divergentes.
- **Cobertura:** `tests/test_cli.py:100-137` cubre la precedencia solo para `foto-index`.
- **Resolución:** extraer un resolvedor común que reciba overrides explícitos y lo consuman ambos ejecutables; dejar el diálogo como último paso exclusivamente interactivo. Conviene aclarar en el ADR si la UI debe exponer también los flags de CLI.

#### C04 — La UI no carga ni valida `library.toml` — **Media**

- **Decisión:** la biblioteca es autodescriptiva y `LibraryConfig` debe construirse al arrancar y pasarse explícitamente (`docs/adr/0001-configuracion.md:61-90,134-147`).
- **Código:** la UI solo importa `thumbs_dir` del módulo de biblioteca (`src/foto/ui/app.py:15-18`) y abre directamente el catálogo (`src/foto/ui/app.py:108-154`). `load_library()` solo se alcanza desde `ensure_library()`, usado por el indexador (`src/foto/storage/library.py:137-184`).
- **Impacto:** el visor puede abrir un catálogo aunque falte el descriptor, esté corrupto, tenga un UUID inválido o declare una versión futura. La UI y el indexador no validan los mismos invariantes.
- **Cobertura:** el descriptor se prueba de forma aislada, pero no durante el arranque de la UI.
- **Resolución:** cargar `LibraryConfig` en modo lectura al abrir o cambiar de biblioteca en la UI, mostrar errores explícitos y pasar el modelo validado al contexto que lo necesite. La UI no debe crear el descriptor.

#### C05 — El fallback de creación de `library.toml` no es atómico — **Media**

- **Decisión:** la configuración se publica atómicamente para evitar ficheros parciales tras un corte (`docs/adr/0001-configuracion.md:132-133`).
- **Código:** la vía normal escribe un temporal y lo publica mediante hard link, pero el fallback para sistemas sin hard links crea directamente el destino con `O_EXCL` y escribe allí el contenido (`src/foto/storage/library.py:102-128`).
- **Impacto:** en exFAT, SMB u otros sistemas contemplados por el propio código, un fallo durante la escritura puede dejar visible un `library.toml` vacío o parcial.
- **Cobertura:** `tests/test_library_bootstrap.py:86-98` comprueba el fallback exitoso, no un fallo a mitad de escritura ni concurrencia.
- **Resolución:** no escribir el contenido inicial directamente en el destino; combinar un temporal completo con publicación atómica y un mecanismo de exclusión que evite sobrescrituras concurrentes.

#### C06 — El serializador no escapa rutas TOML — **Media**

- **Decisión:** la ruta debe persistirse correctamente en un TOML legible y editable (`docs/adr/0001-configuracion.md:43-59,138-139`).
- **Código:** la ruta se interpola directamente entre comillas (`src/foto/storage/config.py:95-99`), sin escapar comillas, barras invertidas ni caracteres de control.
- **Impacto:** nombres válidos en Linux o macOS pueden producir TOML inválido o una ruta distinta al volver a leerla.
- **Cobertura:** no hay pruebas de ida y vuelta de `save_config()`/`load_config()` con caracteres especiales.
- **Resolución:** usar serialización TOML correcta y probar comillas, backslash, Unicode y caracteres de control, coordinado con la escritura conservadora de C01.

### 4.2 ADR 0002 — Indexado de la fototeca

#### I01 — `extension` no forma parte del modelo ni de la reextracción — **Media**

- **Decisión:** `PhotoMetadata` incluye tipo y extensión (`docs/adr/0002-indexado.md:220-227`), y el esquema contiene `photos.extension` (`:74-96`).
- **Código:** `PhotoMetadata` tiene `file_type`, pero no `extension` (`src/foto/core/metadata.py:13-33`). La extensión se deriva del nombre de la primera ruta que inserta el hash (`src/foto/core/indexing.py:289-294,317-322`), y `update_metadata()` no la actualiza (`src/foto/storage/catalog.py:425-451`).
- **Impacto:** el valor puede depender del orden de recorrido cuando copias idénticas tienen extensiones distintas y puede quedar histórico después de cambios de ubicaciones. El contrato de metadatos queda incompleto.
- **Cobertura:** no hay aserciones sobre `PhotoMetadata.extension` o `photos.extension` en los tests de metadatos, duplicados o `--full`.
- **Resolución:** decidir primero si la extensión es un metadato intrínseco del contenido o un dato derivado de una ubicación. Añadirla al extractor y a la actualización, o enmendar el ADR y definir una regla determinista para duplicados.

#### I02 — Se configura y migra SQLite antes de adquirir el lock — **Media**

- **Decisión:** debe existir un único escritor protegido por `index.lock`; las migraciones se aplican al abrir (`docs/adr/0002-indexado.md:245-257`).
- **Código:** la CLI ejecuta `Catalog.open()` antes de adquirir el lock (`src/foto/indexer/cli.py:188-212`). La apertura activa WAL y puede crear o migrar el esquema y hacer `commit` (`src/foto/storage/catalog.py:114-126,176-209`).
- **Impacto:** un proceso que finalmente devuelve código 3 puede haber modificado previamente el catálogo. La garantía de escritor único no cubre inicialización ni migraciones.
- **Cobertura:** `tests/test_cli.py:140-152` solo comprueba el código de salida con un catálogo válido; no verifica ausencia de efectos previos ni concurrencia durante migraciones.
- **Resolución:** adquirir el lock antes de cualquier apertura de catálogo que pueda escribir, cerrar el catálogo antes de liberar el lock y añadir una regresión con lock ocupado y esquema pendiente de migrar.

#### I03 — Una ruta con error de `stat` se añade a `seen` — **Baja**

- **Decisión:** tras errores de lectura/hash solo se añade la ruta a `seen` si el `stat` funcionó (`docs/adr/0002-indexado.md:158-172`).
- **Código:** un fallo de `entry.stat()` genera `ScanIssue(kind="file")` (`src/foto/core/scanner.py:117-123`), y el orquestador añade esa ruta a `seen` incondicionalmente (`src/foto/core/indexing.py:215-227`).
- **Impacto:** es una desviación conservadora: evita un falso `missing`, pero conserva como `present` una ubicación que no pudo verificarse y retrasa la reconciliación.
- **Cobertura:** hay pruebas de errores de listado y hash, no de fallo de `stat` sobre una ubicación conocida.
- **Resolución:** elegir y documentar una política coherente: mantener el comportamiento conservador y enmendar el ADR, u omitir la ruta de `seen` y marcar el recorrido como parcial para impedir reconciliaciones inseguras.

#### I04 — `--dry-run` omite una validación estructural de `.foto` — **Baja**

- **Decisión:** el dry-run simula la ejecución sin escribir; los errores de configuración, esquema o raíz son fatales (`docs/adr/0002-indexado.md:204-213,262-263`).
- **Código:** `ensure_library()` retorna anticipadamente en dry-run antes de comprobar si `.foto` existe pero no es un directorio (`src/foto/storage/library.py:152-162`).
- **Impacto:** si `.foto` es un fichero, la simulación puede informar de éxito mientras la ejecución real devuelve error. No hay escritura ni riesgo para originales; el problema es de diagnóstico.
- **Cobertura:** `tests/test_cli.py:179-190` solo prueba una raíz válida sin `.foto`.
- **Resolución:** adelantar la validación estructural al retorno de dry-run y exigir código 2 tanto en simulación como en ejecución real.

### 4.3 ADR 0003 — Consumo del índice desde el visor

#### V01 — La UI abre el catálogo en lectura/escritura y puede crearlo o migrarlo — **Alta**

- **Decisión:** el catálogo debe abrirse en modo solo lectura; solo `foto-index` lo crea o regenera (`docs/adr/0003-consumo-del-indice-desde-el-visor.md:23-30,71-75,112-125`).
- **Código:** `load_photos()` comprueba `exists()` y usa `Catalog.open()` (`src/foto/ui/app.py:108-125`), que ejecuta `sqlite3.connect()` sin `mode=ro` y siempre configura y migra (`src/foto/storage/catalog.py:114-138,176-209`).
- **Impacto:** no existe una garantía técnica de solo lectura; la UI puede activar WAL o migrar un esquema antiguo. Si el fichero desaparece entre la comprobación y la conexión, SQLite puede crear uno nuevo e inicializarlo como vacío.
- **Cobertura:** no hay pruebas de `load_photos()` ni de una apertura específica para lectores.
- **Resolución:** separar las aperturas de escritor y lector. El lector debe usar URI `mode=ro`, opcionalmente `PRAGMA query_only=ON`, validar la versión sin migrar y no activar WAL. No usar modo `immutable`, porque `:rescan` debe observar nuevos commits.

#### V02 — `:rescan` está implementado internamente, pero es inalcanzable — **Media**

- **Decisión:** `:rescan` recarga los datos desde el catálogo, sin indexar ni recorrer el filesystem (`docs/adr/0003-consumo-del-indice-desde-el-visor.md:71-75`).
- **Código:** `MainWindow._rescan()` hace la recarga correcta (`src/foto/ui/app.py:175-188,226-231`), pero `COMMANDS` no contiene `rescan` (`src/foto/ui/prototype.py:303-309`) y `_run_command()` no lo despacha (`:2918-2937`). El callback registrado en `:2033-2034` no se invoca.
- **Impacto:** el usuario recibe «comando desconocido» y debe reiniciar la aplicación para recargar el índice.
- **Cobertura:** no hay pruebas de comandos ni de recarga.
- **Resolución:** añadir `rescan` al vocabulario, autocompletado, ayuda y dispatcher; probar que recarga SQLite sin llamar al indexador ni a `list_images()`.

#### V03 — La búsqueda solo examina el nombre de fichero — **Media**

- **Decisión:** `/` filtra por nombre, ruta relativa, cámara, objetivo o cualquier campo indexado, en memoria y una vez por hash (`docs/adr/0003-consumo-del-indice-desde-el-visor.md:62-69,120`).
- **Código:** `_apply_filter()` reduce el filtro a `needle in photo.name.lower()` (`src/foto/ui/prototype.py:2855-2864`).
- **Impacto:** no se puede buscar por carpeta, ruta canónica, réplica, cámara, objetivo ni otros metadatos presentes en SQLite.
- **Cobertura:** no hay pruebas de búsqueda o conteos de la UI.
- **Resolución:** construir un documento de búsqueda normalizado desde los datos del catálogo, conservando una entrada por hash, y probar metadatos, rutas canónicas y réplicas.

#### V04 — La ordenación usa texto de presentación, no las claves del catálogo — **Media**

- **Decisión:** solo se ordena por `captured_at` descendente o `camera_model`, con ausentes al final y desempate por nombre (`docs/adr/0003-consumo-del-indice-desde-el-visor.md:67`).
- **Código:** `_format_taken()` trunca la fecha a minutos (`src/foto/core/library.py:97-104`) y `_format_camera()` concatena fabricante y modelo (`:115-117`). `photo_sort_value()` ordena esos textos (`src/foto/ui/prototype.py:330-347`).
- **Impacto:** fotos dentro del mismo minuto se desempatan por nombre en vez de por segundos/fracción; el fabricante puede alterar el orden que correspondería al modelo. Sí se conservan los modos `date`/`camera`, los ausentes al final y el desempate por nombre.
- **Cobertura:** no hay pruebas de precisión temporal, modelos, ausentes o desempates.
- **Resolución:** conservar campos crudos `captured_at` y `camera_model` en el modelo de UI, separados de sus representaciones formateadas, y ordenar por ellos.

#### V05 — La barra de estado descarta la ubicación canónica — **Baja**

- **Decisión:** la ubicación canónica se muestra abajo a la derecha (`docs/adr/0003-consumo-del-indice-desde-el-visor.md:39,117-118`).
- **Código:** los llamadores pasan `canonical_rel_path` (`src/foto/ui/prototype.py:2885-2888`), pero `StatusBar.set_counts()` ignora ese parámetro y compone el texto solo con `filename` (`:1732-1736`).
- **Impacto:** la ruta canónica solo es visible en el panel de metadatos, no en la barra de estado.
- **Cobertura:** no hay pruebas del contenido de `StatusBar`.
- **Resolución:** usar `canonical_rel_path or filename` al construir el texto y cubrir selección, filtros y recargas.

### 4.4 ADR 0004 — Caché persistente de miniaturas

#### M01 — Algunas excepciones de caché pueden impedir devolver la miniatura — **Media**

- **Decisión:** un fallo de lectura o escritura de caché no es fatal; la miniatura se genera y devuelve igualmente (`docs/adr/0004-cache-de-miniaturas.md:55-69`).
- **Código:** en lectura, el `try` solo protege `path.stat()`; `QImage(...)` queda fuera. En escritura solo se captura `OSError` (`src/foto/ui/thumbnails.py:92-127`). Una excepción de un binding o plugin Qt llegaría a `ThumbnailTask.run()` y produciría `failed` (`:175-186`).
- **Impacto:** una excepción de lectura evita la regeneración; una excepción al guardar puede hacer perder incluso una imagen ya generada correctamente. Los fallos habituales devueltos como `QImage` nulo o `save() == False` sí están tratados.
- **Cobertura:** no hay tests de `ThumbnailCache` ni excepciones de Qt.
- **Resolución:** contener las excepciones previstas de Qt/E/S dentro de la frontera de caché, limpiar temporales en `finally` y mantener separados los errores del original.

#### M02 — Los errores de lectura de caché no se registran en depuración — **Baja**

- **Decisión:** los fallos de caché no son fatales y el error se registra en modo depuración (`docs/adr/0004-cache-de-miniaturas.md:67-69`).
- **Código:** `except OSError: return None` y los retornos por `QImage` nulo o tamaño incorrecto no registran nada (`src/foto/ui/thumbnails.py:92-103`). La escritura sí usa `logger.debug()` (`:121-127`).
- **Impacto:** la regeneración funciona en los casos habituales, pero permisos, E/S o corrupción no dejan la traza de diagnóstico prevista.
- **Cobertura:** no hay pruebas de logging o regeneración de caché real.
- **Resolución:** registrar en `DEBUG` errores distintos de una ausencia normal y los datos útiles de corrupción/tamaño, sin elevar el nivel a warning o error.

## 5. Riesgos y precisiones documentales asociados

Estos puntos no se incluyen en el total de 17 discrepancias estrictas.

### R01 — Una recarga puede aceptar resultados antiguos de decodificación

Las tareas del original solo llevan `key` y `path` (`src/foto/ui/prototype.py:493-524`). `set_photos()` limpia cachés pero no invalida las tareas en vuelo (`:2262-2280`), y `_on_image_loaded()` acepta el resultado si la clave sigue existiendo (`:2769-2784`). Tras una recarga, una misma ruta puede quedar asociada a contenido distinto y mostrar la imagen anterior. Es un riesgo de concurrencia relacionado con V02, no una regla textual independiente del ADR. Conviene añadir generación y `photo_hash` a las tareas, como ya hace el servicio de miniaturas.

### D01 — `docs/diseno-ui.md` contradice ADR 0003

El documento de UI limita `/` al nombre y pospone metadatos a fase 2 (`docs/diseno-ui.md:181-192`), mientras ADR 0003 ya exige búsqueda sobre el índice. Además, define `:rescan` como «volver a indexar» (`:194-207`), cuando el ADR lo define como recargar el catálogo. Debe prevalecer el ADR aceptado y actualizarse la guía de UI junto con la implementación.

### D02 — La prohibición de escrituras de ADR 0004 es demasiado absoluta

«La UI no escribe fuera de `.foto/`» (`docs/adr/0004-cache-de-miniaturas.md:71-75`) contradice la escritura de configuración global exigida por ADR 0001. La caché sí cumple su alcance; debe aclararse el texto, por ejemplo: «Dentro de la fototeca, la UI no escribe fuera de `.foto/`; la configuración global se rige por ADR 0001».

### D03 — Definiciones que conviene precisar al modificar ADR 0002

- `duplicate_photos` cuenta hashes con más de una ubicación, incluidas `missing`, no copias adicionales (`src/foto/storage/catalog.py:479-486`).
- `--full` reextrae fotos con una ubicación vista; no puede hacerlo con fotos que solo tienen ubicaciones ausentes (`:403-423`).
- La política para una versión global ausente, actualmente tratada como v1 (`src/foto/storage/config.py:74`), pertenece a ADR 0001 y debería explicitarse al introducir migraciones.

No son necesariamente errores de código, pero sí ambigüedades que dificultan escribir pruebas de contrato.

## 6. Conformidades relevantes

### ADR 0001

- Uso de `platformdirs`, `config.toml`, `pathlib`, expansión de `~` y rutas relativas al fichero (`src/foto/storage/config.py:34-57,78-85`).
- Modelos `AppConfig` y `LibraryConfig` inmutables; configuración fuera de Qt.
- Descriptor por biblioteca con UUID4, versión y validación en el indexador.
- Escritura global mediante temporal y `os.replace`; el problema es la preservación y validación, no la publicación atómica normal.

### ADR 0002

- SHA-256 en streaming con prefijo, doble firma y un reintento.
- Esquema `photos`/`locations`/`scan_runs` esencialmente idéntico al decidido.
- Rutas relativas POSIX, exclusión de `.foto` y symlinks, deduplicación por contenido.
- Incremental, `--full`, copia en memoria para dry-run, `seen` temporal por lotes, ausencias lógicas y `--prune`.
- `exiftool` opcional, sin shell, por lotes, de solo lectura y con errores por fichero.
- CLI, códigos de salida, logging y separación de Qt mayoritariamente alineados.

### ADR 0003

- Índice obligatorio, mensaje de error y salida no cero si falta; no hay fallback al filesystem.
- Consulta limitada a ubicaciones `present` (`src/foto/storage/catalog.py:495-525`).
- Deduplicación por hash, elección lexicográfica de la canónica y réplicas en el panel (`src/foto/core/library.py:136-190`).
- Conteos sobre una entrada por hash; la UI no invoca `foto-index`.
- Originales decodificados en segundo plano.

### ADR 0004

- Caché en `.foto/thumbs/`, JPEG calidad 90, orientación EXIF, recorte y tamaño exacto.
- Clave por hash, tamaño y versión, con normalización segura del nombre (`src/foto/core/thumbnails.py`).
- Validación de aciertos, regeneración de entradas inválidas y escritura atómica con `os.replace`.
- Lectura/escritura y decodificación en workers; generación bajo demanda de la zona visible.
- La poda en disco sigue pendiente de forma explícita y no constituye una discrepancia.

## 7. Plan de acción

### Principios de ejecución

- Corregir primero integridad y garantías de escritura; después completar comportamiento y, por último, observabilidad.
- Mantener cambios pequeños y acompañar cada corrección de una prueba de regresión.
- No cambiar una decisión aceptada silenciosamente: cuando la mejor solución sea distinta del texto actual, actualizar el ADR y registrar la regla elegida.
- No introducir indexado desde la UI, escritura en originales ni dependencias nuevas sin justificación.

### Fase 1 — Integridad de configuración y catálogo — **Prioridad P0**

| Acción | Hallazgos | Entregable y criterio de aceptación |
| --- | --- | --- |
| Separar apertura SQLite de lector y escritor | V01 | `Catalog.open_read_only()` usa `mode=ro`, no migra ni activa WAL y valida el esquema. La UI lo utiliza. Tests: no crea un fichero ausente, no altera `user_version`, rechaza escrituras y no migra esquemas antiguos. |
| Adquirir el lock antes de abrir el catálogo para escritura | I02 | Ninguna ejecución real configura o migra SQLite antes de poseer `index.lock`. Test con lock ocupado y `user_version=0`: código 3 y esquema/journal sin cambios. |
| Hacer conservadora y válida la persistencia global | C01, C02, C06 | Se conservan comentarios, claves desconocidas y secciones; se validan versión y tipos; las rutas hacen round-trip TOML. Tests de preservación, versión futura/booleana, esquema inválido, escaping e idempotencia. |
| Restaurar atomicidad del bootstrap sin hard links | C05 | Un fallo a mitad de escritura no deja descriptor parcial; dos creadores concurrentes terminan con un único ID válido y ninguno sobrescribe al otro. |

**Salida de fase:** la UI es técnicamente incapaz de escribir en el catálogo, el indexador protege todas sus escrituras SQLite con el lock y ninguna operación normal destruye configuración existente.

### Fase 2 — Contrato funcional del visor y resolución de biblioteca — **Prioridad P1**

| Acción | Hallazgos | Entregable y criterio de aceptación |
| --- | --- | --- |
| Compartir la resolución de biblioteca | C03 | Un único resolvedor sin Qt implementa la precedencia; `foto` respeta `FOTO_LIBRARY` y el diálogo solo actúa como último nivel. Tests unitarios para overrides y origen de la ruta. |
| Validar el descriptor al abrir desde la UI | C04 | La UI carga `LibraryConfig` sin crearlo y rechaza descriptor ausente/corrupto/versión futura con mensaje explícito. Tests de arranque y cambio de biblioteca. |
| Conectar `:rescan` y mostrar la canónica | V02, V05 | `:rescan` está en ayuda/autocompletado/dispatcher, recarga solo SQLite y la barra muestra la ruta canónica. Tests de comando, recarga y widget. |
| Separar datos crudos de presentación para buscar y ordenar | V03, V04 | El modelo conserva `captured_at`, `camera_model` y campos indexados. Búsqueda por ruta/réplica/cámara/objetivo y ordenación con segundos/modelos/ausentes/desempates tienen pruebas. |
| Invalidar decodificaciones antiguas al recargar | R01 | Las tareas llevan generación y hash; resultados de una generación anterior se descartan. Test con recarga mientras una decodificación está en vuelo. |
| Alinear la guía de UI | D01 | `docs/diseno-ui.md` describe la búsqueda aceptada y `:rescan` como recarga del índice; la ayuda integrada coincide. |

**Salida de fase:** las operaciones visibles del visor coinciden con ADR 0003 y ambos ejecutables resuelven y validan la biblioteca de forma consistente.

### Fase 3 — Semántica de indexado y robustez de caché — **Prioridad P2**

| Acción | Hallazgos | Entregable y criterio de aceptación |
| --- | --- | --- |
| Resolver el contrato de `extension` | I01 | Decisión explícita sobre contenido frente a ubicación; código y ADR convergentes. Tests de extracción, `--full` y duplicados con extensiones distintas. |
| Alinear errores de `stat` y reconciliación | I03 | Política conservadora documentada y probada; ningún error de acceso produce un falso `missing`. |
| Hacer fiel la validación en dry-run | I04 | `.foto` como fichero devuelve código 2 con y sin `--dry-run`, sin crear ni modificar nada. |
| Contener fallos y mejorar trazas de caché | M01, M02 | Excepciones de lectura/escritura no impiden devolver una miniatura generada; temporales limpiados y errores registrados solo en `DEBUG`. |
| Precisar textos ambiguos | D02, D03 | ADR 0004 delimita escrituras dentro de la biblioteca; ADR 0001/0002 documentan las políticas que se decidan al implementar. |

**Salida de fase:** las desviaciones restantes quedan corregidas o convertidas en decisiones explícitas, sin diferencias ocultas entre documentación y comportamiento.

### Fase 4 — Verificación y cierre

1. Añadir pruebas de integración del visor para apertura de catálogo, duplicados, `missing`, réplicas, mapeo completo, comandos, búsqueda y ordenación. Mantener `core/` y `storage/` sin Qt; las pruebas de widgets pueden ejecutarse con Qt en modo `offscreen`.
2. Añadir pruebas de concurrencia para lock/migración, inicialización del descriptor, lectura con escritor WAL y recargas con trabajos en vuelo.
3. Ampliar cobertura de regresiones ya identificadas aunque el código principal sea conforme: hash inestable, `SIGINT`/código 130, recuperación de `scan_runs`, lotes de `seen`, timestamps de ausencias y aciertos/corrupción de caché.
4. Ejecutar `uv run pytest` y `uv run ruff check .`. Para usar Ruff como puerta de cierre, resolver primero o acotar los 11 hallazgos actuales, que son independientes de este informe.
5. Cerrar cada hallazgo solo cuando exista una prueba o una comprobación reproducible y la documentación afectada esté actualizada.

## 8. Orden recomendado de implementación

1. **V01 + I02:** establecer la frontera lector/escritor de SQLite.
2. **C01 + C02 + C06 + C05:** proteger la configuración frente a pérdida o corrupción.
3. **C03 + C04:** unificar resolución y validación de bibliotecas.
4. **V02 + V03 + V04 + V05 + R01:** completar el contrato del visor y actualizar su documentación.
5. **I01 + I03 + I04 + M01 + M02:** cerrar desviaciones acotadas y robustecer pruebas.
6. **D01–D03:** dejar los ADR y la guía de UI coherentes con las decisiones finales.

Este orden reduce primero el riesgo de pérdida o escritura indebida y evita construir nuevas pruebas de UI sobre una apertura de catálogo que todavía permite migraciones.
