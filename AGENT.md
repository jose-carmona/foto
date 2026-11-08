# AGENT.md

Instrucciones para agentes de IA que trabajen en este repositorio.

`foto` es una fototeca local-first en Python + Qt que aspira a sustituir a Apple
Photos. Es una herramienta personal, no un producto: prioriza claridad,
corrección y que la biblioteca sea inspeccionable por su dueño.

Antes de tocar nada, lee `README.md`. Es la fuente de verdad sobre alcance,
arquitectura y roadmap. Para cualquier cambio de interfaz, lee también
`docs/diseno-ui.md`.

## Estado actual

- UI funcional de `foto` en `src/foto/ui/`: la ventana orquestadora
  (`window.py`) monta los widgets enfocados —rejilla (`grid.py`), visor
  (`canvas.py`), paneles de metadatos y estado (`panels.py`) y ayuda
  (`help.py`)— con tokens, tipografía y QSS de los widgets en `theme.py` y el
  vocabulario de comandos de `:` en `commands.py`; demo con datos falsos en
  `prototype.py`.
  El modelo de UI (`Photo`) vive en `ui/models.py`, las
  reglas de presentación pura (orden, grupos, búsqueda, navegación vertical,
  teclas de grupo y la aritmética del color atenuado) en `ui/presentation.py`
  y la tabla unificada de teclas con contexto explícito (`(contexto, tecla,
  modificadores) → acción`, sin Qt) en `ui/keybindings.py`; la ventana ejecuta
  las acciones con un único dispatcher (`_dispatch`) con registro acción →
  handler. Todos son módulos sin Qt que la ventana consume.
- Indexador `foto-index` completo en `src/foto/indexer/` + `core/` + `storage/`:
  catálogo SQLite incremental, hashes SHA-256 de contenido, rutas relativas,
  reconciliación de ausencias, `exiftool` opcional y lock de escritor único.
- El catálogo vive en `<fototeca>/.foto/catalog.sqlite` y es reconstruible. El
  indexador no importa Qt.
- El visor **consume el catálogo** (`ADR 0003`): lee solo desde
  `<fototeca>/.foto/catalog.sqlite` (ubicaciones `present`). Si no existe el
  catálogo, la app avisa y se cierra. Las búsquedas y ordenaciones se hacen
  contra el índice. `foto-index` es independiente (CLI/cron/systemd).
- La imagen grande del visor se decodifica en segundo plano. Las miniaturas
  visibles de la rejilla se generan en un pool de trabajo, pidiendo al lector
  una decodificación ya escalada, y se guardan en una caché persistente en
  `<fototeca>/.foto/thumbs/` indexada por hash de contenido, tamaño y versión.
  La caché es un derivado reconstruible; todavía no tiene poda ni límite de
  tamaño. En memoria solo se mantiene una ventana acotada de miniaturas y la
  navegación usa índices de las filas visibles. XMP no está implementado.
- `tests/` contiene una suite pytest que se ejecuta sin pantalla. Los tests de
  `core/` y `storage/` no necesitan Qt; los de UI importan PySide6 y usan
  `QT_QPA_PLATFORM=offscreen`. `pytest` y `ruff` están en el grupo `dev` de
  `pyproject.toml`.
- El MVP es **visualización y metadatos**, nada más. No adelantes edición,
  álbumes ni IA sin que se pida explícitamente.
- No inventes comandos de instalación, CI ni badges que no existan.

## Reglas no negociables

1. **Nunca modificar, mover ni borrar originales.** Toda escritura va a XMP,
   SQLite o a un nuevo fichero derivado (miniaturas, caché).
2. **XMP es la fuente de verdad.** SQLite es un índice reconstruible; ningún
   dato debe existir solo en la base de datos.
3. **Sin red por defecto.** La única salida permitida es la copia de seguridad a
   S3, y solo si el usuario la ha configurado. Nada de telemetría, analítica ni
   servicios externos.
4. **Multiplataforma real.** Omarchy (Arch + Hyprland/Wayland) y macOS son
   objetivos de primera clase. Aísla cualquier diferencia de sistema en
   módulos concretos, no la disperses por la UI.
5. **La UI no contiene lógica de negocio.** `core/` y `storage/` no deben
   importar Qt.
6. **Sin dependencias nuevas sin justificarlo.** Antes de añadir una librería,
   comprueba que la stdlib o una dependencia existente no bastan.
7. **Teclado primero.** Toda acción de la UI debe poder ejecutarse sin ratón.
   Los atajos siguen `docs/diseno-ui.md`; no inventes teclas ni cambies su
   significado sin actualizar ese documento y la ayuda integrada.

## Stack

- Python 3.12+
- PySide6 (LGPL, coherente con la licencia MIT del proyecto)
- `platformdirs` para ubicar configuración, caché y datos en Linux y macOS
- SQLite (catálogo implementado en `storage/catalog.py`; WAL, `user_version`,
  migraciones y `foreign_keys=ON`)
- Sidecars XMP; `exiftool` para lectura/escritura de metadatos. En el indexador
  actual es opcional y de solo lectura.
- pytest y ruff como dependencias de desarrollo, declaradas en
  `[dependency-groups].dev` de `pyproject.toml`
- `uv` como gestor de entorno y dependencias

Formatos de imagen: JPEG, HEIC y RAW vía `exiftool` + extracción de miniaturas
embebidas. No implementes decodificadores de RAW propios.

## Estructura actual

```
foto/
├── docs/
│   ├── adr/          # decisiones de arquitectura
│   └── diseno-ui.md  # reglas de interfaz
├── src/foto/
│   ├── core/         # hashing, scanner, metadatos, indexado
│   ├── indexer/      # CLI foto-index y __main__
│   ├── storage/      # catálogo SQLite, exiftool, lock y config TOML
│   └── ui/           # ventana orquestadora, widgets (rejilla, visor,
│                     # paneles, ayuda), tokens y QSS de widgets, comandos,
│                     # modelo y presentación, demo y miniaturas
├── tests/            # suite pytest sin pantalla
└── README.md
```

Comandos disponibles:

```sh
uv sync
uv run foto
uv run foto-index --library ~/Fotos
uv run foto-index --library ~/Fotos --dry-run --json
uv run python -m foto.indexer --library ~/Fotos --help
PYTHONPATH=src .venv/bin/python -m foto.ui.prototype
uv run pytest
uv run ruff check .
```

## Convenciones de código

- Código e identificadores en inglés; comentarios, docstrings, documentación y
  textos visibles de la UI en español.
- Type hints en todas las funciones públicas.
- Usa `pathlib` para todas las rutas y `platformdirs` para localizar los
  directorios de configuración, caché y datos del usuario. Nunca concatenes
  strings para construir rutas.
- `dataclasses` para modelos de datos; evita dicts sueltos cruzan módulos.
- Errores explícitos: excepciones propias en `core`, nunca `except: pass`.
- Operaciones de disco potencialmente lentas (indexar, miniaturas) fuera del
  hilo de UI.
- Nada de estado global mutable; la configuración se pasa explícitamente.

## Configuración

Decisión completa en `docs/adr/0001-configuracion.md`. Resumen operativo:

- **Global del usuario:** `config.toml` en `platformdirs.user_config_dir("foto")`.
  Formato TOML, legible y editable a mano.
- **De la biblioteca:** `<fototeca>/.foto/library.toml`, junto al catálogo SQLite
  y la caché de miniaturas. La biblioteca se describe a sí misma.
- **Secretos:** llavero del sistema o variables de entorno. Nunca en `config.toml`.
- **Precedencia:** `--library` → `FOTO_LIBRARY` → `config.toml` → diálogo de
  primera ejecución. Para el fichero de config: `--config` → `FOTO_CONFIG` →
  `platformdirs`.
- **Requisitos:** lectura con `tomllib`, escritura atómica, campo `version` con
  migración, errores explícitos y `pathlib`.
- **Prohibido:** `.env` como fuente de verdad y `QSettings`. La carga y la
  escritura de configuración no viven en la UI.

## Indexador y catálogo

Decisión completa en `docs/adr/0002-indexado.md`. Resumen operativo:

- **Identidad:** `sha256:<64 hex>` del contenido. Una fila en `photos` por hash y
  N filas en `locations` por ruta relativa POSIX.
- **Escrituras:** solo en `<fototeca>/.foto/`. Nunca se modifican, mueven ni
  borran originales. El catálogo es un índice reconstruible.
- **Incremental:** firma `(size, mtime_ns, inode, device)`; el hash solo se
  calcula para altas y cambios. `--full` fuerza un rehash completo y reextrae
  metadatos. El resumen expone `hashed_files` y `metadata_extracted` para que ese
  trabajo sea visible aunque `unchanged` no cambie.
- **Ausencias:** se marcan `missing` por reconciliación con la tabla temporal
  `seen`; un recorrido parcial o una raíz ausente no marcan nada. `--prune` solo
  borra filas del catálogo.
- **Metadatos:** EXIF de la imagen, normalizados; `exiftool` es opcional, de solo
  lectura, sin shell y por lotes. Sin él quedan `pending`.
- **Concurrencia:** WAL, `foreign_keys=ON`, `busy_timeout` y lock exclusivo en
  `.foto/index.lock`. Un solo escritor; el futuro lector no debe bloquearse.
- **Sin Qt:** `core/`, `storage/` e `indexer/` no importan `foto.ui` ni PySide6.

## Tests

- `pytest` para todo; los tests de `core/` y `storage/` no deben necesitar Qt ni
  pantalla.
- Usa `tmp_path` y fixtures con ficheros de imagen mínimos en lugar de imágenes
  reales grandes.
- La suite cubre hash e identidad, indexado incremental, borrados y `--prune`,
  recorrido parcial, metadatos, catálogo, bootstrap, lock y CLI; la geometría y
  los nombres de la caché de miniaturas, así como la lógica de presentación de
  la UI (`tests/test_presentation.py`), se prueban sin Qt.
- Todo bug corregido lleva su test de regresión.

## Flujo de trabajo

- Cambios pequeños y revisables. Nada de refactors masivos no pedidos.
- Si implementas una funcionalidad del roadmap, marca su casilla en el
  `README.md` y ajusta la documentación afectada.
- No hagas `git commit`, `git push` ni cambios de configuración de git sin
  confirmación explícita.
- Mensajes de commit cortos, en imperativo y en español (p. ej.
  `añade indexado de carpetas`).
- Si una decisión de diseño no está clara, pregunta antes de asumirla.

## Datos y privacidad

- No incluyas fotos, rutas personales, bases de datos ni sidecars XMP en el
  repositorio. Añade los patrones necesarios a `.gitignore` cuando aparezcan.
- `.foto/` está ignorado: contiene catálogos y locks generados por el indexador.
- Credenciales de S3 vía variables de entorno o llavero del sistema, nunca en
  el repo ni en ficheros de configuración versionados.
- Los logs no deben volcar rutas completas de la biblioteca salvo en modo
  depuración explícito.
