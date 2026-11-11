# foto

Una fototeca local-first para sustituir a Apple Photos, escrita en Python + Qt.

`foto` nace como herramienta personal: quiero gestionar mi archivo fotográfico sin
depender de iCloud, del ecosistema Apple ni de una base de datos opaca. Los
originales no se tocan, los metadatos viven en ficheros estándar y todo funciona
sin conexión.

> **Estado:** Fase 1 (visualización y metadatos) casi completa. `foto` consume
> el catálogo SQLite: rejilla agrupada por fecha, cámara o lugar, visor con
> zoom y paneo, panel de metadatos con el lugar resuelto, búsqueda incremental,
> ordenación por fecha/cámara/lugar, ayuda integrada y temas claro/oscuro.
> `foto-index` es un pipeline de fases (`scan` + `geocode`) que identifica las
> fotos por el hash de su contenido, mantiene un catálogo SQLite incremental y
> cachea las miniaturas en `<fototeca>/.foto/thumbs/`. Falta: favoritos
> persistidos en XMP (el atajo `f` alterna en memoria), poda de la caché de
> miniaturas y menú contextual.

## Objetivo

Sustituir a Apple Photos en mi flujo diario manteniendo lo que sí funciona bien:
una biblioteca visual rápida, metadatos legibles y copias de seguridad
automáticas. La diferencia es que aquí la biblioteca es mía y puedo abrirla,
inspeccionarla y migrarla con herramientas estándar.

## Principios

- **Local-first.** La biblioteca vive en disco. La red es opcional, salvo una
  única llamada activada por defecto: la geocodificación inversa de las fotos
  con GPS contra OpenStreetMap, que se puede desactivar sin tocar código.
- **Originales intactos.** `foto` nunca modifica ni borra los ficheros
  originales; los cambios se guardan como metadatos.
- **Metadatos de usuario en el catálogo.** Las etiquetas y los favoritos viven
  en el catálogo SQLite, que es su fuente de verdad
  ([ADR 0006](docs/adr/0006-etiquetas.md)); la exportación a XMP queda como
  futurible y la importación no existe.
- **Multiplataforma real.** Desarrollo y uso diario en Omarchy (Arch Linux +
  Hyprland/Wayland), con soporte de primera clase en macOS.
- **Sin telemetría.** No sale del equipo nada que no sea explícito: las copias
  de seguridad que yo configure y las consultas de geocodificación inversa
  (Nominatim), limitadas a las fotos con GPS y desactivables.
- **Componentes reemplazables.** El catálogo es reconstruible en lo que deriva
  del contenido (fotos, ubicaciones, metadatos, lugares); las etiquetas y los
  favoritos no se regeneran desde disco, así que exigen copia de seguridad
  (Fase 3).

## Alcance del MVP

La primera versión se centra en **ver y entender** las fotos, no en editarlas.

- Abrir una carpeta o biblioteca y mostrar una rejilla de miniaturas.
- Vista individual con zoom y navegación entre fotos.
- Lectura de metadatos: fecha de captura, cámara, objetivo, ISO, apertura,
  velocidad, distancia focal y geolocalización.
- Favoritos, escritos en el XMP correspondiente.
- Catálogo SQLite para búsquedas y metadatos, y caché de miniaturas en disco,
  regenerables desde los originales y, para los metadatos de usuario, desde los
  XMP.

Fuera del MVP, por ahora: edición, álbumes, reconocimiento de caras y cualquier
forma de publicación o compartición.

## Roadmap

- [ ] **Fase 1 — Visualización y metadatos** (MVP)
  - [x] Indexado incremental de carpetas con catálogo SQLite, hashes de
    contenido y metadatos EXIF normalizados
    - [x] Pipeline de fases del indexador con geocodificación inversa de las
      fotos con GPS ([ADR 0005](docs/adr/0005-fases-y-geocodificacion.md))
  - [x] Generación y caché de miniaturas desde el catálogo
    - [x] Generación asíncrona de las miniaturas visibles y decodificación escalada
    - [x] Caché persistente en `<fototeca>/.foto/thumbs/`
    - [ ] Poda y límite de tamaño de la caché
  - [ ] Endurecer el rendimiento para bibliotecas grandes
    - [x] Ventana de miniaturas en memoria y navegación con índices O(1)
    - [ ] Evaluar un modelo/vista si el número de elementos empieza a pesar
  - [x] Integración del visor con el catálogo ([ADR 0003](docs/adr/0003-consumo-del-indice-desde-el-visor.md))
  - [x] Búsqueda incremental `/` contra el índice (nombre, ruta, cámara,
    metadatos, lugar…) con cancelación con `Esc`
  - [x] Barra de comandos `:` con autocompletado (`Tab`) e historial (`↑`/`↓`)
  - [x] Ordenación y grupos por fecha, cámara y lugar
    (`:sort date|camera|location`); dentro de cada grupo, de más nuevo a más
    viejo
  - [x] Panel de metadatos con el lugar resuelto (municipio/región/país y la
    dirección completa que devolvió el servicio)
  - [x] Ayuda integrada con `?` / `F1` / `:help` (`src/foto/resources/ayuda.md`)
  - [x] Temas claro y oscuro con retema en caliente según el `colorScheme` del sistema
  - [x] Abrir la ubicación de la foto en el navegador con `g m` (Google Maps)
  - [x] Tabla de teclas unificada con contextos (`ui/keybindings.py`) y lógica
    de presentación pura sin Qt (`ui/presentation.py`, `ui/models.py`)
  - [x] Ventana partida en módulos por responsabilidades: `window.py` orquesta
    `grid.py`, `canvas.py`, `panels.py`, `help.py` y `theme.py`; estilos
    centralizados en `theme.py` y vocabulario de comandos en `commands.py`
  - [ ] Favoritos persistidos en XMP (el atajo `f` alterna en memoria;
    decisión de alcance pospuesta al final de la Fase 1)
  - [ ] Menú contextual de ratón con las acciones de la foto (pendiente para más adelante)
- [ ] **Fase 2 — Organización**
  - Álbumes manuales e inteligentes
  - Búsqueda y filtros por fecha, cámara, etiquetas y ubicación
    - [x] Buscar por lugar y ordenar con `:sort location` en el visor
    - [x] Buscador por texto libre sobre fecha, cámara, metadatos y lugar
- [ ] **Fase 3 — Protección de datos**
  - Copia de seguridad a disco local
  - Copia de seguridad a S3 (opcional y configurable)
  - Verificación de integridad de la biblioteca
- [ ] **Fase 4 — Edición no destructiva**
  - Recorte, rotación, exposición, color
- [ ] **Fase 5 — Migración e IA local**
  - Importación de una biblioteca existente de Apple Photos sin perder metadatos
  - Caras, escenas y OCR ejecutados en local

## Arquitectura

```
┌───────────────────┐        ┌────────────────────┐
│ UI (Qt / PySide6) │        │ foto-index (CLI)   │
└─────────┬─────────┘        └─────────┬──────────┘
          │                            │
┌─────────▼────────────────────────────▼─────────┐
│ Núcleo (Python)                                │
│ recorrido, hashing, metadatos, indexado        │
└─────────┬────────────────────────────┬─────────┘
          │                            │
      SQLite (índice)          XMP sidecars (futuro)
          │
    originales en disco
          │
    copias: disco local + S3
```

- **UI:** Qt mediante PySide6 (LGPL, compatible con una licencia MIT). Teclado
  primero, atajos de vim y lenguaje visual sobrio; reglas en
  `docs/diseno-ui.md`.
- **Indexador:** ejecutable `foto-index`, sin Qt, que es un **pipeline de
  fases**: `scan` (recorrido, hashing, metadatos y reconciliación) y `geocode`
  (geocodificación inversa de las fotos con GPS contra Nominatim, con caché
  por celda, throttle de 1 petición/s y reanudación ante rechazos
  ([ADR 0005](docs/adr/0005-fases-y-geocodificacion.md))). Se puede lanzar
  desde bash, cron o un `systemd` oneshot.
- **Catálogo:** SQLite como índice rápido de búsqueda y metadatos normalizados.
  Es reconstruible desde los originales; si se borra, la siguiente pasada lo
  regenera. El visor lee también de él el lugar resuelto (`place_*`, con el
  `address` completo que devolvió el servicio), con el que busca por
  municipio/región/país y ordena con `:sort location`.
- **Miniaturas:** derivados JPEG en `<fototeca>/.foto/thumbs/`, nombrados por
  hash de contenido, tamaño y versión. También son reconstruibles y nunca
  sustituyen al original; la caché todavía no se poda automáticamente. En
  memoria solo se conserva una ventana acotada alrededor de lo visible.
- **Metadatos:** EXIF leídos con `exiftool` (opcional) y normalizados en SQLite.
  Los sidecars XMP serán la fuente de verdad de los metadatos de usuario en una
  fase posterior.
- **Copias de seguridad:** destino local y, opcionalmente, S3.
- **Configuración:** TOML en el directorio de configuración del usuario; lo
  específico de cada biblioteca vive en `<fototeca>/.foto/`. Ver
  `docs/adr/0001-configuracion.md`, `docs/adr/0002-indexado.md`,
  `docs/adr/0003-consumo-del-indice-desde-el-visor.md`,
  `docs/adr/0004-cache-de-miniaturas.md` y
  `docs/adr/0005-fases-y-geocodificacion.md`.

Estructura actual:

```
foto/
├── docs/
│   ├── adr/        # decisiones de arquitectura
│   └── diseno-ui.md
├── prompts/        # prompts de trabajo por entrega
├── src/foto/
│   ├── core/       # hashing, recorrido, metadatos, geocodificación e indexado
│   ├── indexer/    # CLI foto-index, python -m foto.indexer y Nominatim
│   ├── storage/    # SQLite, exiftool, lock, biblioteca y configuración TOML
│   ├── ui/         # ventana orquestadora, widgets, teclas, comandos, temas,
│   │               # ayuda, demo y miniaturas
│   └── resources/  # ayuda integrada (markdown empaquetado)
├── tests/          # suite pytest que se ejecuta sin pantalla
└── README.md
```

## Documentación

- `docs/diseno-ui.md` — reglas de interfaz: teclado primero, atajos de vim,
  composición visual y accesibilidad.
- `docs/adr/` — decisiones de arquitectura:
  - `0001-configuracion.md` — configuración global y de la biblioteca.
  - `0002-indexado.md` — identidad por hash, catálogo SQLite e indexado
    incremental.
  - `0003-consumo-del-indice-desde-el-visor.md` — el visor consume el índice,
    búsquedas/ordenaciones contra el catálogo y requisito del índice existente.
  - `0004-cache-de-miniaturas.md` — caché derivada en `.foto/thumbs/`, clave por
    hash de contenido, tamaño y versión, y escritura atómica.
  - `0005-fases-y-geocodificacion.md` — pipeline de fases del indexador y
    geocodificación inversa con Nominatim, con caché por celda y reanudación.

## UI

`foto` abre la última biblioteca configurada o pide una carpeta y consume el
catálogo ([ADR 0003](docs/adr/0003-consumo-del-indice-desde-el-visor.md)). Si no
existe `catalog.sqlite`, avisa y se cierra. La rejilla agrupa las miniaturas por
año y mes, por cámara o por lugar (municipio, país) según la ordenación
activa. Pide solo las miniaturas visibles a un pool de trabajo y las guarda en
`<fototeca>/.foto/thumbs/`; el visor decodifica el original en segundo plano.
La búsqueda `/` y las ordenaciones `:sort` se aplican contra lo cargado del
índice; `g m` abre la ubicación en el navegador y `?` abre la ayuda integrada.

```sh
uv sync
uv run foto
```

`PYTHONPATH=src .venv/bin/python -m foto.ui.prototype` abre la demo con fotos
falsas para revisar la interfaz sin tocar discos.

## Indexador

`foto-index` es un proceso de una sola pasada, independiente de Qt, que recorre
la biblioteca y mantiene `<fototeca>/.foto/catalog.sqlite`. Identifica cada foto
por el SHA-256 de sus bytes: dos copias idénticas son una foto con dos
ubicaciones. No modifica, mueve ni borra originales; solo escribe dentro de
`.foto/`.

```sh
uv sync
uv run foto-index --library ~/Fotos
uv run foto-index --library ~/Fotos --json
uv run foto-index --library ~/Fotos --dry-run --verbose
uv run foto-index --library ~/Fotos --prune --prune-after 30
uv run foto-index --library ~/Fotos --full
uv run foto-index --library ~/Fotos --skip geocode
```

También se puede lanzar como módulo: `python -m foto.indexer --library ~/Fotos`.

Opciones principales:

| Opción | Comportamiento |
| --- | --- |
| `--library PATH` | Raíz de la biblioteca; máxima precedencia. |
| `--config PATH` | Fichero de configuración global. |
| `--full` | Rehashea y reextrae todo; reintenta también los lugares en `error`. |
| `--skip FASE` | Excluye una fase del pipeline (`scan`, `geocode`); repetible. |
| `--prune` | Borra ubicaciones `missing` antiguas y fotos huérfanas del catálogo. |
| `--prune-after DAYS` | Antigüedad mínima para `--prune`; 30 por defecto. |
| `--dry-run` | No escribe nada ni toca la red; simula sobre una copia en memoria. |
| `--json` | Resumen JSON en stdout, segmentado por fases; los logs van a stderr. |
| `--quiet` / `--verbose` | Menos o más detalle en los logs. |

El resumen de texto distingue lo que se ha procesado de lo que ha cambiado, y
segmenta el trabajo por fase:

```text
Indexado full: ok
  escaneados=6 sin_cambios=6 nuevas=0 modificadas=0 ausentes=0
  fotos=6 ubicaciones=6 duplicadas=0 metadatos_extraidos=6 pendientes_metadatos=0
  ficheros_hasheados=6 bytes_hasheados=5098349 errores=0 duración=0.319s
  geocode: candidatas=6 geocodificadas=6 con_error=0 cacheadas=0 peticiones=4
```

`sin_cambios` significa que el contenido no cambió; `ficheros_hasheados` confirma
que `--full` leyó esos bytes y `metadatos_extraidos` cuenta las fotos cuyos
metadatos se han vuelto a escribir en esta pasada. Con `--json` aparecen como
`hashed_files` y `metadata_extracted`, y el JSON trae un objeto `phases` con el
estado y los contadores de cada fase (incluida la geocodificación). Con
`--skip geocode` aparece `geocode: omitida`, y `--dry-run` reporta
`geocode: candidatas=N celdas_a_pedir=M (sin red)`.

La geocodificación es la única red del indexador: consulta Nominatim con un
User-Agent identificativo, como máximo una petición por segundo, y guarda la
jerarquía país/región/municipio en el catálogo, junto con el nombre visible y
el `address` completo del servicio, con una caché por celda de 3
decimales. Si el servicio rechaza peticiones o la red falla, la fase se detiene
sin marcar error en ninguna foto; la siguiente pasada continúa por las
`pending` sin repetir lo ya resuelto. Se desactiva con `--skip geocode` o con
`[geocoding] enabled = false` en `config.toml`, que además acepta `endpoint`,
`language`, `user_agent`, `request_interval_seconds` y `timeout_seconds`.

La biblioteca se resuelve con la precedencia del ADR 0001: `--library` →
`FOTO_LIBRARY` → `[library].path` de `config.toml`. El fichero de configuración,
con `--config` → `FOTO_CONFIG` → `platformdirs`.

Códigos de salida: `0` éxito, `1` error no fatal o recorrido parcial, `2`
configuración/raíz/esquema inválidos, `3` otro indexador tiene el lock y `130`
interrupción con `SIGINT`.

Si `exiftool` no está instalado, el indexado de hashes y ubicaciones funciona
igual y los metadatos quedan `pending`; se completan en la siguiente pasada
cuando esté disponible. Lo mismo vale para el lugar: una foto con coordenadas
GPS ya leídas se geocodifica aunque no haya extractor, y las que no las tengan
esperan a la extracción.

### Ejecución desatendida

Cron:

```cron
0 3 * * * cd /ruta/al/repo && .venv/bin/foto-index --library "$HOME/Fotos" --quiet >> "$HOME/.local/state/foto-index.log" 2>&1
```

`systemd` de usuario, `~/.config/systemd/user/foto-index.service`:

```ini
[Unit]
Description=Indexa la fototeca con foto-index
After=local-fs.target

[Service]
Type=oneshot
ExecStart=%h/wrk/foto/.venv/bin/foto-index --library %h/Fotos --quiet
```

`~/.config/systemd/user/foto-index.timer`:

```ini
[Unit]
Description=Indexa la fototeca cada noche

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now foto-index.timer
```

### Desarrollo

```sh
uv run pytest
uv run ruff check .
```

## Requisitos

- Python 3.12 o superior
- PySide6
- Linux con Wayland (probado en Omarchy/Hyprland) o macOS
- `exiftool` opcional: sin él se indexan hashes y ubicaciones, pero los
  metadatos quedan pendientes

## Licencia

MIT. Ver `LICENSE` (pendiente de añadir).
