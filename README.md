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
> cachea las miniaturas en `<fototeca>/.foto/thumbs/`. Los favoritos (`f`) y el
> filtro por etiqueta (`:tag <nombre>`) se escriben y leen del catálogo
> ([ADR 0006](docs/adr/0006-etiquetas.md)). Falta: poda de la caché de
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
  ([ADR 0006](docs/adr/0006-etiquetas.md)); el respaldo y la migración entre
  bibliotecas se hacen con `foto-export`/`foto-import` (JSON,
  [ADR 0007](docs/adr/0007-exportar-importar-metadatos.md)); la exportación a
  XMP queda como futurible y la importación XMP sigue sin existir.
- **Multiplataforma real.** Desarrollo y uso diario en Omarchy (Arch Linux +
  Hyprland/Wayland), con soporte de primera clase en macOS.
- **Sin telemetría.** No sale del equipo nada que no sea explícito: las copias
  de seguridad que yo configure y las consultas de geocodificación inversa
  (Nominatim), limitadas a las fotos con GPS y desactivables.
- **Componentes reemplazables.** El catálogo es reconstruible en lo que deriva
  del contenido (fotos, ubicaciones, metadatos, lugares); las etiquetas y los
  favoritos no se regeneran desde disco, así que se respaldan con `foto-export`
  (y la copia de la Fase 3 los cubrirá).

## Alcance del MVP

La primera versión se centra en **ver y entender** las fotos, no en editarlas.

- Abrir una carpeta o biblioteca y mostrar una rejilla de miniaturas.
- Vista individual con zoom y navegación entre fotos.
- Lectura de metadatos: fecha de captura, cámara, objetivo, ISO, apertura,
  velocidad, distancia focal y geolocalización.
- Favoritos, guardados en el catálogo como la etiqueta de sistema `fav`
  ([ADR 0006](docs/adr/0006-etiquetas.md)).
- Catálogo SQLite para búsquedas y metadatos, y caché de miniaturas en disco,
  regenerables desde los originales; las etiquetas y los favoritos no se
  regeneran y exigen copia de seguridad.

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
    - [x] Arranque progresivo: ventana visible de inmediato con estado de
      carga, catálogo en hilo worker y rejilla popular por lotes
      ([ADR 0011](docs/adr/0011-arranque-progresivo.md))
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
  - [x] Favoritos y etiquetas en el catálogo
    ([ADR 0006](docs/adr/0006-etiquetas.md)): `f` conmuta la etiqueta `fav`
    con escritura inmediata y `:tag <nombre>` filtra la rejilla por etiqueta
  - [ ] Menú contextual de ratón con las acciones de la foto (pendiente para más adelante)
- [ ] **Fase 2 — Organización**
  - Álbumes manuales e inteligentes
  - Búsqueda y filtros por fecha, cámara, etiquetas y ubicación (el filtro
    básico por etiqueta ya existe: `:tag <nombre>`)
    - [x] Buscar por lugar y ordenar con `:sort location` en el visor
    - [x] Buscador por texto libre sobre fecha, cámara, metadatos y lugar
- [ ] **Fase 3 — Protección de datos**
  - Copia de seguridad a disco local
  - Copia de seguridad a S3 (opcional y configurable)
  - Verificación de integridad de la biblioteca
  - [x] Exportación e importación JSON de etiquetas y lugares
    (`foto-export`/`foto-import`,
    [ADR 0007](docs/adr/0007-exportar-importar-metadatos.md))
- [ ] **Fase 4 — Edición no destructiva**
  - Recorte, rotación, exposición, color
- [ ] **Fase 5 — Migración e IA local**
  - Importación de una biblioteca existente de Apple Photos sin perder
    metadatos (primera fase: favoritos y caras; los originales de
    `originals/`, sin tocar el paquete)
    - Importar más metadatos de Apple Photos (álbumes, títulos,
      descripciones, fecha de importación…) una vez la primera fase
      importe favoritos y caras
    - Visualización de caras (personas) en la UI, a partir de las
      etiquetas `person` importadas al catálogo
  - Caras, escenas y OCR ejecutados en local
- [ ] **Fase 6 — De fototeca a fotovideoteca**
  - Soporte de vídeo de extremo a extremo: tipos de archivo y metadatos en
    el indexador, esquema del catálogo y reproducción en el visor

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
      SQLite (índice)          exportación XMP (futurible)
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
  Es reconstruible desde los originales salvo los metadatos de usuario
  (etiquetas y favoritos), que no se regeneran y exigen copia de seguridad. El
  visor lee también de él el lugar resuelto (`place_*`, con el `address`
  completo que devolvió el servicio), con el que busca por
  municipio/región/país y ordena con `:sort location`.
- **Miniaturas:** derivados JPEG en `<fototeca>/.foto/thumbs/`, nombrados por
  hash de contenido, tamaño y versión. También son reconstruibles y nunca
  sustituyen al original; la caché todavía no se poda automáticamente. En
  memoria solo se conserva una ventana acotada alrededor de lo visible.
- **Metadatos:** EXIF leídos con `exiftool` (opcional) y normalizados en SQLite.
  Las etiquetas y los favoritos viven en el catálogo
  ([ADR 0006](docs/adr/0006-etiquetas.md)); la exportación a XMP es futurible.
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
│   ├── transfer/   # exportación e importación JSON de metadatos
│   │               # (foto-export / foto-import, ADR 0007)
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
  - `0006-etiquetas.md` — etiquetas y favoritos en el catálogo: esquema
    `tags`/`photo_tags`, escritura RW del visor sin `index.lock`.
  - `0007-exportar-importar-metadatos.md` — exportación e importación JSON de
    tags y lugares: dos ficheros, merge aditivo idempotente y migración v5.
  - `0008-resolucion-de-ubicaciones.md` — las tres cadenas de resolución de
    rutas (configuración, fototeca y metainformación) y la preparación para
    la fototeca de Apple Photos.
  - `0009-indexado-apple-photos.md` — política de escaneo de un paquete
    `.photoslibrary` (solo `originals/`), metainformación obligatoriamente
    externa y la fase `apple-import` de favoritos y personas.
  - `0010-fototeca-distribuida.md` — la fototeca en varias ubicaciones
    con nombre: declaración en config v2, sincronización config ↔ catálogo,
    esquema v6 del catálogo, indexado y prune multi-ubicación, tolerancia
    a ubicaciones ausentes y retirada de `library.toml`.

## UI

`foto` abre la última biblioteca configurada o pide una carpeta y consume el
catálogo ([ADR 0003](docs/adr/0003-consumo-del-indice-desde-el-visor.md)). Si no
existe `catalog.sqlite`, avisa y se cierra. La rejilla agrupa las miniaturas por
año y mes, por cámara o por lugar (municipio, país) según la ordenación
activa. Pide solo las miniaturas visibles a un pool de trabajo y las guarda en
el directorio de metainformación (`thumbs/`); el visor decodifica el original
en segundo plano. La búsqueda `/` y las ordenaciones `:sort` se aplican contra
lo cargado del índice; `g m` abre la ubicación en el navegador y `?` abre la
ayuda integrada. Con una ubicación ausente (USB desmontado) la ventana se
degrada y avisa —banner, `:locations` y fotos fantasma—; ver «Tolerancia» más
abajo ([ADR 0010](docs/adr/0010-fototeca-distribuida.md)).

```sh
uv sync
uv run foto
uv run foto --metadata ~/Fotos.foto
```

La UI acepta `--config PATH` y `--metadata PATH` (mismas cadenas que los CLIs,
[ADR 0008](docs/adr/0008-resolucion-de-ubicaciones.md)); lanzar sin argumentos
sigue funcionando desde el Finder o el lanzador. La fototeca se declara solo
en `config.toml` ([ADR 0010](docs/adr/0010-fototeca-distribuida.md)):
`--library` ya no existe. Los overrides de línea de comandos no se persisten:
el diálogo de primera ejecución (sin config o con ficheros v1) sigue
escribiendo solo `[library].path`; la UI nunca escribe `[metadata].path` ni
`[[library.locations]]` (se editan a mano en `config.toml`).

`PYTHONPATH=src .venv/bin/python -m foto.ui.prototype` abre la demo con fotos
falsas para revisar la interfaz sin tocar discos.

## Ubicaciones: configuración, fototeca y metainformación

La fototeca se declara **solo en `config.toml`**
([ADR 0010](docs/adr/0010-fototeca-distribuida.md)): `[[library.locations]]`
en `version = 2`, o `[library].path` en `version = 1` (una única ubicación
`default`); `--library` y `FOTO_LIBRARY` ya no existen (definir esta variable
es un error explícito). Para el resto de ubicaciones se mantiene el patrón:
**override CLI → variable de entorno → `config.toml` → valor por defecto**
([ADR 0008](docs/adr/0008-resolucion-de-ubicaciones.md)). Los cuatro
binarios (`foto`, `foto-index`, `foto-export`, `foto-import`) comparten los
mismos resolutores en `storage/resolver.py`.

| Ubicación | Cadena | Valor por defecto |
| --- | --- | --- |
| Configuración | `--config` → `FOTO_CONFIG` | `platformdirs.user_config_dir("foto")/config.toml` |
| Fototeca | `[[library.locations]]` / `[library].path` de `config.toml` (única fuente, ADR 0010; sin CLI ni entorno) | diálogo de primera ejecución (solo UI, ficheros v1) |
| Metainformación | `--metadata` → `FOTO_METADATA` → `[metadata].path` | `<única ubicación declarada>/.foto`; con varias, obligatorio concretarla |

- El override de metadatos redirige el **directorio completo**:
  `catalog.sqlite`, `thumbs/`, `index.lock` y `exports/` viven juntos ahí
  o no viven. Nunca piezas sueltas.
- No se mueve nada automáticamente: apuntar `--metadata` a un directorio
  vacío lo inicializa `foto-index` (crea ahí el catálogo) y apuntar a uno
  con un `library.toml` previo lo ignora: el descriptor ya no existe
  (ADR 0010) y los ficheros huérfanos no se borran ni se leen.
- La referencia de ubicaciones es la tabla `locations` del catálogo
  (esquema v6): `foto-index` la sincroniza con la config al arrancar —
  registra las ubicaciones nuevas y marca `deleted` las quitadas de la
  config, sin borrar nada (la limpieza de filas es de `--prune`); una
  ubicación ausente (USB desmontado) avisa y se degrada, jamás se da de
  baja por estar desconectada.
- `[metadata]` es opt-in: un `config.toml` nuevo creado por la app no la
  incluye y `save_config` nunca la escribe ni la reordena. Se edita a mano:

  ```toml
  version = 1

  [library]
  path = "~/Pictures/Photos Library.photoslibrary"

  [metadata]
  # Solo hace falta si la metainformación no va dentro de la fototeca.
  # Admite "~" y rutas relativas al directorio de este fichero, como [library].
  path = "~/Fotos/ApplePhotos.foto"
  ```

- Con `version = 2`, las ubicaciones se declaran en la lista (editada a
  mano; `save_config` acepta v2 pero rehúsa escribir `[library].path`):

  ```toml
  version = 2

  [[library.locations]]
  name = "ssd"                    # identificador estable, único
  path = "~/Fotos/Principal"      # "~" y rutas relativas al config
  tier = "fast"                   # fast | archive

  [[library.locations]]
  name = "usb"
  path = "/Volumes/FotoVault"
  tier = "archive"

  [metadata]
  # Único para TODAS las ubicaciones; con varias es obligatorio.
  ```

- Si la fototeca es un paquete de Apple Photos (`.photoslibrary`), la
  metainformación no puede caer dentro del paquete por ninguna vía de la
  cadena ([ADR 0009](docs/adr/0009-indexado-apple-photos.md)): el nivel por
  defecto `<fototeca>/.foto` y cualquier override que apunte dentro fallan
  con un error explícito que pide una ruta externa. La regla se aplica por
  ubicación: con varias declaradas, ninguna raíz puede ser un paquete cuyo
  interior reciba la metainformación (ADR 0010).
- La metainformación tampoco puede caer dentro de una ubicación
  `tier = "archive"` (error duro, ADR 0010): el catálogo debe vivir en un
  disco siempre disponible.

### Tolerancia: qué se ve cuando el USB no está

Al abrir `foto` con una ubicación declarada ausente (USB desmontado), la
ventana no se bloquea: se degrada y avisa (ADR 0010, Decisión 6).

- **Banner sobre la rejilla** con el formato del ADR: «Ubicación «usb» no
  disponible: /Volumes/FotoVault. 1 234 fotos no visibles». Un clic en el
  banner, o el comando `:locations`, abre el panel con el estado de cada
  ubicación (nombre, tier, ruta, estado y nº de fotos). La ayuda integrada
  (`?`) lo documenta.
- **Las fotos de esa ubicación se muestran como *placeholders*** con fecha,
  cámara, etiquetas y favorito del catálogo; la miniatura sale de la caché
  (`thumbs/`) si existe. Solo existen en la vista: el catálogo no cambia.
- **Favoritos y etiquetas siguen funcionando** (todo son metadatos del
  catálogo, ADR 0006); filtrar con `:tag` y buscar también.
- **Las acciones que exigen el fichero se desactivan con mensaje claro**:
  abrir la foto en el visor muestra «el fichero no está disponible ·
  ubicación «usb»».
- **El refresco de disponibilidad es manual** (Decisión 6, sin watcher en
  `/Volumes`): monta el volumen y reabre `foto`.
- **La metainformación es el único error duro**: sin catálogo no hay sesión
  (la UI pide ejecutar `foto-index`); cualquier otra ubicación ausente no
  bloquea nunca.

Quitar una ubicación de la config y ejecutar `foto-index` la da de baja
(estado `deleted`) junto a sus filas: no se borra nada —ni filas ni
ficheros—; `--prune` limpia solo filas del catálogo transcurrida la ventana
`--prune-after`, y re-declararla y escanear la restaura. Una ubicación
reactivada cuyo escaneo aún no ha corrido se muestra vacía (sus filas siguen
`deleted` hasta que el escaneo las repuebla).

### Fototeca de Apple Photos

`Photos Library.photoslibrary` es un **paquete** protegido por TCC: para leer
su contenido el proceso necesita *Full Disk Access* (requisito del entorno,
no del código). Con la metainformación en un directorio externo, `foto` no
escribe nada dentro del paquete de Apple: los originales quedan intactos y
sin interferencias con Photos. El comportamiento está decidido en el
[ADR 0009](docs/adr/0009-indexado-apple-photos.md) y es automático:

- **Solo se escanea `originals/`**: al detectar el sufijo `.photoslibrary`
  el indexador aplica la política de escaneo del ADR 0009 (sin flags): una
  entrada por asset fotográfico original, sin variantes editadas ni
  derivados de `resources/` o `database/`. El vídeo queda fuera hasta la
  Fase 6 del roadmap.
- **La metainformación debe vivir fuera del paquete**: sin `--metadata`,
  `FOTO_METADATA` o `[metadata].path` externo el arranque falla con un
  error explícito (también si el override apunta dentro del paquete); no
  hay fallback automático.
- **Sin *Full Disk Access*** el recorrido acaba `partial` con la causa
  explícita y la acción sugerida de conceder *Full Disk Access*; no se
  marcan ausencias para no confundir un permiso denegado con fotos borradas.
- **Ausencias por iCloud** («Optimizar almacenamiento de Mac»): son estado
  normal, no errores; los originales ausentes se comportan como cualquier
  ubicación ausente del catálogo.
- La importación de favoritos y personas desde `Photos.sqlite` (fase
  `apple-import` del indexador, opt-in con `[apple_photos] enabled = true`)
  está decidida en el ADR 0009 y pendiente de implementar (paso 3 de su
  prompt).

Para usarla hoy:

Con la fototeca declarada en `config.toml`
(`[library].path = "~/Pictures/Photos Library.photoslibrary"` o su
`[[library.locations]]` equivalente):

```sh
uv run foto-index --metadata ~/Fotos/ApplePhotos.foto
uv run foto --metadata ~/Fotos/ApplePhotos.foto
```

### Migración manual de una `.foto/` existente

La migración es operación del dueño, no automatizada: copia el directorio
completo a la nueva ubicación y usa
`--metadata`/`FOTO_METADATA`/`[metadata].path` para apuntar allí. Las rutas
del catálogo son relativas a cada ubicación y sobreviven al traslado; un
`library.toml` que venga en la copia es un huérfano que se ignora. La app
nunca mezcla catálogos en silencio: la sincronización config ↔ catálogo
hace visibles las altas y bajas masivas en el resumen (`--json`).

## Indexador

`foto-index` es un proceso de una sola pasada, independiente de Qt, que recorre
la biblioteca y mantiene el catálogo SQLite en el directorio de
metainformación. Identifica cada foto por el SHA-256 de sus bytes: dos copias
idénticas son una foto con dos ubicaciones. No modifica, mueve ni borra
originales; solo escribe en el directorio de metainformación.

Con la fototeca declarada en `config.toml` (`[library].path` o
`[[library.locations]]`, [ADR 0010](docs/adr/0010-fototeca-distribuida.md)):

```sh
uv sync
uv run foto-index
uv run foto-index --json
uv run foto-index --dry-run --verbose
uv run foto-index --prune --prune-after 30
uv run foto-index --full
uv run foto-index --skip geocode
```

También se puede lanzar como módulo: `python -m foto.indexer`.

Opciones principales:

| Opción | Comportamiento |
| --- | --- |
| `--metadata PATH` | Directorio de metainformación; por defecto `<única ubicación>/.foto`. |
| `--config PATH` | Fichero de configuración global. |
| `--full` | Rehashea y reextrae todo; reintenta también los lugares en `error`. |
| `--skip FASE` | Excluye una fase del pipeline (`scan`, `geocode`); repetible. |
| `--prune` | Borra filas del catálogo —nunca ficheros—: filas `missing` antiguas de ubicaciones escaneadas, filas `deleted` de ubicaciones dadas de baja y fotos huérfanas. |
| `--prune-after DAYS` | Antigüedad mínima para `--prune`; 30 por defecto. |
| `--dry-run` | No escribe nada ni toca la red; simula sobre una copia en memoria (incluido el diff de sincronización). |
| `--json` | Resumen JSON en stdout, segmentado por fases, con el bloque `locations` (altas, bajas, reactivadas, cambios, escaneadas, no disponibles); los logs van a stderr. |
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

La biblioteca se declara solo en `config.toml`
([ADR 0010](docs/adr/0010-fototeca-distribuida.md)): `[[library.locations]]`
en `version = 2`, `[library].path` en `version = 1`. `--library` ya no existe
y `FOTO_LIBRARY` definida es un error explícito. El fichero de configuración,
con `--config` → `FOTO_CONFIG` → `platformdirs`.

Con varias ubicaciones declaradas, `foto-index` sincroniza la declaración
con el catálogo al arrancar (altas, cambios, bajas con filas marcadas
`deleted` y reactivaciones; idempotente y reportado en el bloque
`locations` de `--json`) y después recorre solo las ubicaciones disponibles:
una ausente (USB desmontado) avisa por stderr y no es error (salida 0); sus
filas no se tocan y jamás se da de baja por estar desconectada.

Códigos de salida: `0` éxito (una ubicación ausente no es error), `1` error
no fatal o recorrido parcial, `2` configuración o esquema inválidos
(incluida metainformación no disponible), `3` otro indexador tiene el lock
y `130` interrupción con `SIGINT`.

Si `exiftool` no está instalado, el indexado de hashes y ubicaciones funciona
igual y los metadatos quedan `pending`; se completan en la siguiente pasada
cuando esté disponible. Lo mismo vale para el lugar: una foto con coordenadas
GPS ya leídas se geocodifica aunque no haya extractor, y las que no las tengan
esperan a la extracción.

### Ejecución desatendida

Cron:

```cron
0 3 * * * cd /ruta/al/repo && .venv/bin/foto-index --quiet >> "$HOME/.local/state/foto-index.log" 2>&1
```

`systemd` de usuario, `~/.config/systemd/user/foto-index.service`:

```ini
[Unit]
Description=Indexa la fototeca con foto-index
After=local-fs.target

[Service]
Type=oneshot
ExecStart=%h/wrk/foto/.venv/bin/foto-index --quiet
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

## Exportar e importar

Las etiquetas, los favoritos y los lugares resueltos no viven en los
originales: las etiquetas no son reconstruibles (son metadatos de usuario) y
los lugares exigen peticiones a Nominatim a 1/segundo
([ADR 0007](docs/adr/0007-exportar-importar-metadatos.md)). `foto-export` los
vuelca a **dos ficheros JSON separados** —`tags.json` (imprescindible) y
`places.json` (descartable, dato de terceros)— y `foto-import` los aplica con
**merge aditivo idempotente**: añade lo que falta, no toca lo existente y
nunca borra. La identidad es el hash de contenido, así que los ficheros son
portables entre bibliotecas. Exportar nunca escribe en el catálogo; los
metadatos EXIF incrustados no se exportan (su fuente de verdad sigue siendo la
foto) y XMP no se exporta ni se importa.

```sh
uv run foto-export                                         # ambos, a .foto/exports/
uv run foto-export --tags --out ~/backups
uv run foto-export --places --out ~/backups
uv run foto-export --dry-run --json

uv run foto-import --tags ~/backups/tags.json
uv run foto-import --tags a.json --places b.json
uv run foto-import --tags a.json --dry-run --verbose
```

| Opción (export) | Comportamiento |
| --- | --- |
| `--tags` / `--places` | Selección; por defecto se exportan ambos, a ficheros separados. |
| `--out DIR` | Directorio de destino; por defecto `<fototeca>/.foto/exports/`. |
| `--dry-run` | Calcula y valida todo, no escribe ficheros. |
| `--json` | Resumen JSON en stdout; los logs a stderr. |
| `--quiet` / `--verbose` | Menos o más detalle. |

| Opción (import) | Comportamiento |
| --- | --- |
| `--tags FILE` / `--places FILE` | Ficheros a importar; al menos uno. |
| `--force` | Rellena lugares de fotos con `place_status='error'` (nunca `ok`). |
| `--dry-run` | Valida y calcula lo que cambiaría sin escribir; no toma el lock. |
| `--json` | Resumen JSON en stdout; los logs a stderr. |
| `--quiet` / `--verbose` | Menos o más detalle. |

Los ficheros son UTF-8 con `indent=2` y orden determinista: dos exportaciones
seguidas sin cambios producen el mismo cuerpo (aptos para `diff` y para
versionar). La escritura es atómica. `foto-export` solo lee el catálogo (WAL:
funciona con `foto-index` o la app corriendo); `foto-import` es escritor único
y toma `index.lock` como `foto-index` porque rellena `place_*`. Con el ADR
0010, `foto-import` no crea nada: exige un catálogo ya creado por
`foto-index` (sin él, error pidiendo ejecutarlo primero). Tras un import, el
catálogo queda en v5 y la app puede abrirlo directamente.

Los resúmenes solo traen conteos: nunca nombres de etiquetas ni direcciones.
Los hashes ausentes (fotos del fichero que no están en el catálogo) se
informan y se conservan en el fichero para futuros imports.

Códigos de salida: `0` éxito, `1` error no fatal (avisos incluidos), `2`
configuración/biblioteca/catálogo/fichero inválido, `3` otro proceso tiene el
lock (solo import), `130` interrupción con `SIGINT`.

## Requisitos

- Python 3.12 o superior
- PySide6
- Linux con Wayland (probado en Omarchy/Hyprland) o macOS
- `exiftool` opcional: sin él se indexan hashes y ubicaciones, pero los
  metadatos quedan pendientes

## Licencia

MIT. Ver `LICENSE` (pendiente de añadir).
