# Prompt de construcción — Indexador de la fototeca

## 0. Misión

Implementa en el repositorio `foto` el **proceso de indexación de la fototeca**:
un ejecutable independiente del visualizador que recorre la biblioteca, calcula
la identidad de cada imagen, extrae sus metadatos incrustados y mantiene un
catálogo SQLite incremental en `<fototeca>/.foto/catalog.sqlite`.

El indexador debe poder lanzarse:

- a mano desde bash;
- desde cron;
- como servicio de una sola pasada (por ejemplo, `systemd` oneshot + timer).

No es un daemon y no debe depender de la UI ni de Qt.

La entrega incluye un **ADR nuevo** que documente las decisiones, no solo el
código.

---

## 1. Lectura obligatoria antes de tocar código

Lee completos y respeta:

1. `AGENT.md` — reglas no negociables, stack, convenciones y flujo de trabajo.
2. `README.md` — alcance, arquitectura y roadmap.
3. `docs/adr/0001-configuracion.md` — configuración, rutas y `.foto/`.
4. `docs/diseno-ui.md` — solo para no romper la UI.
5. `src/foto/core/library.py`, `src/foto/storage/config.py` y
   `src/foto/ui/app.py` — estado real del código.

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible. No hagas `git commit`, `git push` ni cambios de
configuración de git sin permiso explícito.

---

## 2. Decisiones vinculantes

Estas decisiones ya están tomadas. No las cambies sin justificarlo y sin
actualizar el ADR.

### 2.1. Identidad de una foto = hash del contenido

- La clave primaria de una foto es el **hash criptográfico de sus bytes**.
- Formato: `sha256:<64 hex en minúsculas>`.
- Se calcula con `hashlib.sha256` en streaming, sin cargar el fichero entero en
  memoria. No añadas dependencias.
- El hash identifica **contenido**, no ruta, nombre, fecha ni metadatos del
  sistema de ficheros.
- Dos ficheros con los mismos bytes, aunque estén en carpetas distintas, son
  **una sola foto** con **varias ubicaciones**.
- Una foto puede tener N ubicaciones. La tabla de fotos tiene una fila por
  hash; la tabla de ubicaciones tiene una fila por ruta.
- Esto no es deduplicación perceptual: dos versiones visualmente parecidas pero
  con bytes distintos son fotos distintas. La deduplicación perceptual queda
  fuera de alcance.
- Si en el futuro se cambia de algoritmo, el prefijo permite migrar sin
  ambigüedad. No implementes ese cambio ahora.

### 2.2. Catálogo SQLite reconstruible

- Catálogo: `<fototeca>/.foto/catalog.sqlite`.
- El catálogo es un **índice reconstruible**. Si se borra, una nueva ejecución
  lo regenera desde disco.
- Los originales no se modifican, mueven ni borran nunca. Solo se escribe en
  `.foto/`.
- SQLite en modo WAL, con `foreign_keys=ON` y `busy_timeout`.
- Versión de esquema explícita con `PRAGMA user_version`, migraciones y error
  claro si el catálogo es de una versión más nueva.

### 2.3. Rutas relativas y portabilidad

- Guarda las ubicaciones como **ruta relativa a la raíz de la biblioteca**, en
  formato POSIX (`/`), sin `./` ni `..`.
- No guardes rutas absolutas en `locations`. Así mover la fototeca de disco o
  de ruta no invalida el catálogo.
- La raíz de la biblioteca es la carpeta que contiene `.foto/`.
- No sigas enlaces simbólicos de directorios. No indexes enlaces simbólicos de
  ficheros. Evita ciclos y ubicaciones engañosas.
- Ignora siempre el directorio `.foto/` y sus descendientes.
- Reutiliza `IMAGE_EXTENSIONS` de `core/library.py`; no dupliques la lista de
  extensiones.

### 2.4. Independencia del visualizador

- El indexador no importa Qt ni `foto.ui`.
- No cambies la UI en esta entrega. El visualizador actual sigue funcionando
  como hasta ahora.
- El catálogo debe quedar preparado para que el visualizador lo consuma más
  adelante, pero esa integración no forma parte de este trabajo.
- No generes miniaturas en esta entrega. La caché de miniaturas es otra etapa.
- No leas ni escribas **sidecars** XMP en esta entrega. Los metadatos
  incrustados en la propia imagen (incluido XMP embebido, si `exiftool` lo
  devuelve) sí forman parte de la extracción.
- No implementes tags, caras, embeddings, OCR ni IA. Solo deja el modelo
  preparado para añadirlos después.

### 2.5. Metadatos de esta fase

- Los metadatos salen **de la propia imagen** (EXIF y metadatos incrustados),
  leídos con `exiftool`.
- `exiftool` es una dependencia externa opcional: si no está instalado, el
  indexado de hashes y ubicaciones debe seguir funcionando y los metadatos
  quedan pendientes. No falles por su ausencia.
- Invoca `exiftool` sin shell, con lista de argumentos y en lotes pequeños
  (por ejemplo, 100 rutas) para no superar `ARG_MAX`. No concatenes comandos.
- No escribas nunca con `exiftool`; solo lectura.
- Guarda solo campos normalizados y consultables. No guardes el JSON crudo
  completo de exiftool: infla la base de datos y no es la fuente de verdad.
- Los metadatos intrínsecos (EXIF) son iguales para bytes iguales. Los
  metadatos derivados del contenido (caras, embeddings) también podrán
  calcularse una sola vez por hash. Los metadatos de usuario (favoritos,
  tags) no son función del contenido y tendrán su propia política cuando se
  implemente XMP; déjalo documentado en el ADR.

### 2.6. Sin dependencias nuevas de runtime

- Usa la stdlib: `hashlib`, `sqlite3`, `subprocess`, `json`, `logging`,
  `pathlib`, `dataclasses`, `fcntl` (POSIX).
- No añadas Pillow, BLAKE3, watchdog, ORM ni frameworks de CLI.
- `pytest` y `ruff` son dependencias de desarrollo. Si no están instaladas,
  añádelas como tales con `uv` y actualiza `uv.lock`. No las pongas en
  `[project].dependencies`.

---

## 3. Entregables

### 3.1. Código

Crea, como mínimo:

```
src/foto/
├── indexer/
│   ├── __init__.py
│   ├── __main__.py        # python -m foto.indexer
│   └── cli.py             # argumentos, logging, lock, salida
├── core/
│   ├── hashing.py         # hash de contenido
│   ├── scanner.py         # recorrido de la biblioteca
│   ├── metadata.py        # modelo normalizado y contrato del extractor
│   └── indexing.py        # orquestación del indexado
└── storage/
    ├── catalog.py         # esquema, migraciones y operaciones SQLite
    ├── library.py         # bootstrap de .foto/library.toml
    ├── exiftool.py        # adaptador de exiftool
    └── lock.py            # lock exclusivo del indexador
```

Puedes ajustar nombres o agrupar módulos si el resultado es más claro, pero
mantén la separación: `core/` y `storage/` sin Qt, y el CLI como capa fina.

Añade en `pyproject.toml`:

```toml
[project.scripts]
foto-index = "foto.indexer.cli:main"
```

### 3.2. ADR

Crea `docs/adr/0002-indexado.md` con, como mínimo:

- Estado, fecha, decisor y ámbito.
- Contexto y restricciones.
- Decisión: hash de contenido como PK, tabla de fotos y tabla de ubicaciones.
- Algoritmo y formato del hash.
- Indexación incremental por firma de stat.
- Detección de borrados por reconciliación, con tombstones y sin borrado
  destructivo por defecto.
- Esquema SQLite completo.
- Concurrencia: WAL, lock de escritor único, convivencia con el futuro lector.
- Metadatos, `exiftool` opcional y evolución futura (tags, caras, embeddings).
- Ejecución desatendida: bash, cron y systemd timer.
- Consecuencias positivas y negativas.
- Alternativas descartadas, con motivos.
- Decisiones pendientes y riesgos.

Alternativas que el ADR debe valorar explícitamente:

| Alternativa | Por qué se descarta o se pospone |
| --- | --- |
| Ruta como clave primaria | Rompe la deduplicación y los renombrados/movimientos. |
| Hash perceptual como identidad | No identifica bytes iguales; confunde fotos parecidas. |
| Reindexar y rehashear todo en cada ejecución | Correcto pero lento; se reserva para `--full`. |
| `mtime` o tamaño como identidad | No detectan copias iguales ni cambios con la misma firma. |
| `inotify`/FSEvents como mecanismo principal | Solo funciona con el proceso vivo; falla con discos desmontados y no es portable. Se pospone como complemento. |
| Caché de `mtime` de directorios como modo por defecto | No detecta modificaciones in-place; se pospone como modo rápido opcional. |
| ID entero + hash único | Añade indirección sin beneficio en SQLite; el hash ya es estable y único. |
| `ImageUniqueID` de EXIF | No existe en todas las fotos, se duplica y no es fiable. |

### 3.3. Documentación

- Actualiza `README.md`:
  - estado real del indexado;
  - comando `foto-index`;
  - arquitectura y catálogo;
  - roadmap: marca solo lo que realmente se haya completado (el indexado, no
    las miniaturas ni la integración con el visor);
  - enlace al ADR 0002;
  - ejemplos de bash, cron y systemd timer.
- Actualiza `AGENT.md`:
  - estado actual;
  - estructura;
  - comandos;
  - stack (catálogo SQLite ya implementado);
  - tests.
- Añade `.foto/` a `.gitignore` para no versionar catálogos creados por error
  dentro del repositorio. No añadas fotos ni bases de datos de prueba.
- No inventes badges, CI ni comandos que no existan.

---

## 4. Modelo de datos

Usa este esquema como base. Puedes añadir columnas si están justificadas, pero
no elimines las claves ni cambies la semántica.

```sql
PRAGMA user_version = 1;

CREATE TABLE photos (
    hash                TEXT PRIMARY KEY,   -- 'sha256:<64 hex>'
    file_type           TEXT,               -- JPEG, HEIC, RAF...
    extension           TEXT NOT NULL,      -- '.jpg'
    width               INTEGER,
    height              INTEGER,
    captured_at         TEXT,               -- ISO 8601 local, si existe
    camera_make         TEXT,
    camera_model        TEXT,
    lens_model          TEXT,
    iso                 INTEGER,
    f_number            REAL,
    exposure_time       REAL,
    focal_length        REAL,
    gps_latitude        REAL,
    gps_longitude       REAL,
    orientation         INTEGER,
    metadata_status     TEXT NOT NULL DEFAULT 'pending'
                        CHECK (metadata_status IN ('pending', 'ok', 'error')),
    metadata_error      TEXT,
    metadata_updated_at TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE locations (
    rel_path        TEXT PRIMARY KEY,       -- POSIX, relativa a la raíz
    photo_hash      TEXT NOT NULL REFERENCES photos(hash) ON DELETE RESTRICT,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    inode           INTEGER,                -- NULL si no está disponible
    device          INTEGER,                -- NULL si no está disponible
    status          TEXT NOT NULL DEFAULT 'present'
                    CHECK (status IN ('present', 'missing')),
    first_seen_at   TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL,
    missing_since   TEXT
);

CREATE INDEX locations_photo_hash_idx ON locations(photo_hash);
CREATE INDEX locations_status_idx ON locations(status);

CREATE TABLE scan_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    mode           TEXT NOT NULL,           -- 'incremental' | 'full'
    status         TEXT NOT NULL,           -- 'running' | 'ok' | 'partial'
                                            -- | 'interrupted' | 'error'
    scanned        INTEGER NOT NULL DEFAULT 0,
    unchanged      INTEGER NOT NULL DEFAULT 0,
    added          INTEGER NOT NULL DEFAULT 0,
    modified       INTEGER NOT NULL DEFAULT 0,
    missing        INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0,
    hashed_bytes   INTEGER NOT NULL DEFAULT 0
);
```

Notas:

- `hash` es la PK de `photos`. SQLite maneja bien una PK TEXT de ~71 bytes.
- `locations.rel_path` es la PK de la ubicación. No uses rutas absolutas.
- `locations.photo_hash` es la relación N:1 con la foto.
- `metadata_status='pending'` se reintenta en ejecuciones posteriores cuando
  hay extractor disponible. `error` solo se reintenta con `--full`, para no
  repetir fallos permanentes en cada pasada.
- `missing_since` solo se escribe la primera vez que se detecta la ausencia.
  Si el fichero reaparece, se limpia y la ubicación vuelve a `present`.
- No actualices `last_indexed_at` de las ubicaciones sin cambios: evita
  escrituras masivas. El `scan_runs` ya registra cuándo se escaneó.
- No guardes el JSON crudo de exiftool.
- No crees tablas de tags, caras o embeddings todavía. El ADR debe explicar
  que se añadirán después con `photo_hash` como clave foránea.

---

## 5. Flujo de indexado

### 5.1. Arranque

1. Resuelve la biblioteca con la precedencia de `docs/adr/0001-configuracion.md`:
   `--library` → `FOTO_LIBRARY` → `[library].path` de `config.toml` → error.
   Para el fichero de configuración: `--config` → `FOTO_CONFIG` →
   `platformdirs`.
2. Verifica que la raíz existe y es un directorio. Si no, error explícito y
   salida distinta de cero. No crees la carpeta.
3. Asegura `<raíz>/.foto/` y `<raíz>/.foto/library.toml`. Si faltan, créalos de
   forma atómica con `version = 1` y un `id` UUID4. Si existen, valídalos y no
   los sobrescribas. Con `--dry-run`, no crees nada: si falta, informa de que
   sería una biblioteca nueva.
4. Abre o crea `catalog.sqlite`, aplica migraciones y activa WAL,
   `foreign_keys=ON` y `busy_timeout`. Con `--dry-run`, si el catálogo no
   existe, usa una base SQLite en memoria con el mismo esquema para poder
   simular el resultado sin escribir en disco.
5. Adquiere el lock exclusivo `<raíz>/.foto/index.lock`. Si ya hay otro
   indexador, sal con código 3 y un mensaje claro.
6. Marca como `interrupted` cualquier `scan_runs` que haya quedado en `running`
   por un cierre abrupto anterior.
7. Crea un registro `scan_runs` con estado `running`.

### 5.2. Recorrido

Recorre la raíz con `os.scandir` recursivo:

- ignora `.foto/`;
- no sigas symlinks de directorios;
- no indexes symlinks de ficheros;
- filtra por `IMAGE_EXTENSIONS`;
- para cada imagen, obtén `st_size`, `st_mtime_ns`, `st_ino` y `st_dev`.

Por cada fichero, aplica esta lógica:

1. **Ruta nueva** (no existe en `locations`):
   - calcula el hash;
   - si el hash ya existe en `photos`, inserta solo la ubicación;
   - si el hash no existe, extrae metadatos e inserta foto y ubicación;
   - añade la ruta a `seen`.
2. **Ruta existente con firma idéntica y `present`**:
   - no abras el fichero, no hashees, no extraigas metadatos;
   - añade la ruta a `seen`;
   - cuenta como `unchanged`.
3. **Ruta existente con firma idéntica y `missing`**:
   - la foto ha reaparecido: marca `present`, limpia `missing_since`,
     actualiza `last_indexed_at` y añade a `seen`;
   - no hashees si la firma no cambió.
4. **Ruta existente con firma distinta**:
   - recalcula el hash;
   - si el hash es el mismo (por ejemplo, un `touch`), actualiza solo la firma
     y `last_indexed_at`;
   - si el hash cambia, mueve la ubicación al nuevo hash; si el nuevo hash no
     existe, extrae metadatos; la foto antigua puede quedar sin ubicaciones.
5. **Fichero ilegible o error al hashear**:
   - no marques la ruta como missing;
   - añádela igualmente a `seen` si el stat funcionó;
   - registra el error, cuenta `errors` y continúa. Se reintentará en la
     siguiente pasada.

Para no mantener millones de rutas en memoria, inserta las rutas vistas en una
tabla temporal `seen(rel_path TEXT PRIMARY KEY)` e insértalas por lotes. Usa
`PRAGMA temp_store=MEMORY` si está disponible.

Al terminar el recorrido y si `exiftool` está disponible, recoge las fotos con
`metadata_status='pending'` que tengan alguna ubicación vista y reintenta su
extracción en lote. Así una biblioteca indexada sin `exiftool` completa sus
metadatos en la siguiente pasada, sin rehashear nada.

### 5.3. Borrados

Al terminar el recorrido:

- si el recorrido fue **completo**, ejecuta una sola actualización:

```sql
UPDATE locations
SET status = 'missing',
    missing_since = :now
WHERE status = 'present'
  AND rel_path NOT IN (SELECT rel_path FROM seen);
```

- si la raíz no existe, no se pudo abrir o hubo un error al listar algún
  directorio, **no marques ninguna ausencia**. Registra el run como `partial`
  y explica el motivo. Es preferible dejar ubicaciones obsoletas a marcar como
  borradas fotos que solo están en un disco desmontado o en una carpeta sin
  permisos.
- los borrados son lógicos (`missing`), no físicos. Por defecto no se borra
  ninguna fila.
- `--prune` elimina ubicaciones en estado `missing` con `missing_since` más
  antiguo que `--prune-after` días (por defecto 30) y, después, fotos sin
  ninguna ubicación. `--prune-after 0` permite limpiar de inmediato. Esto solo
  borra filas del catálogo, nunca ficheros originales.

### 5.4. Cierre

- Actualiza el `scan_runs` con contadores, estado y `finished_at`.
- Libera el lock.
- Escribe un resumen legible en stderr/stdout según el modo.
- Si hubo errores no fatales, termina con código 1 y estado `partial`; el
  catálogo queda consistente y la siguiente pasada reintenta.

---

## 6. Detección rápida de altas y bajas

Este es el mecanismo que debe quedar implementado y documentado en el ADR:

### Camino rápido por firma de stat

Cada ejecución hace una **reconciliación** de la raíz. Para cada imagen compara
la firma `(size, mtime_ns, inode, device)` con la guardada en `locations`.

- Si coincide y está `present`: no se abre el fichero. Coste: un `stat`.
- Si no coincide o no existe: se lee y se hashea.
- El hash solo se calcula para ficheros nuevos o cuya firma cambió. En una
  biblioteca estable, una pasada completa no lee ni un byte de imagen.
- El coste de una pasada estable es O(número de ficheros) en `stat`, no
  O(bytes).

### Borrados por diferencia de conjuntos

- Las rutas vistas se acumulan en `seen`.
- Al final, las ubicaciones `present` que no están en `seen` se marcan
  `missing` en una sola sentencia.
- Es O(n) y no requiere comparar ficheros ni recorrer el catálogo foto a foto.
- La ausencia de la raíz o un recorrido parcial bloquean la reconciliación
  para no confundir "disco desmontado" con "foto borrada".

### Modos

- **Incremental (por defecto):** firma de stat + hash solo si hace falta +
  reconciliación de ausencias.
- **`--full`:** ignora la firma, rehashea y reextrae metadatos de todo. Sirve
  para verificar integridad, reconstruir el catálogo o detectar cambios que
  conservaron tamaño y `mtime`.

### Optimizaciones futuras (no implementar ahora)

- Caché de `mtime` de directorios para saltar subárboles sin cambios en un
  modo `--quick`. Solo detecta altas y bajas, no modificaciones in-place; debe
  combinarse con pasadas incrementales completas periódicas.
- `inotify`/FSEvents como complemento para reaccionar mientras el proceso está
  vivo. No sustituye a la reconciliación y no debe ser el mecanismo principal.
- Paralelizar el hash de ficheros nuevos. No lo hagas en la primera versión:
  mide antes y mantén la corrección.

El ADR debe explicar por qué la reconciliación por stat es la fuente de verdad
y por qué los eventos del sistema de ficheros no bastan.

---

## 7. Metadatos

### 7.1. Extracción

- Implementa un contrato `MetadataExtractor` y una implementación
  `ExifToolExtractor` basada en `subprocess`.
- Comprueba `exiftool` con `shutil.which` una vez por ejecución.
- Invoca en lotes, sin shell, con `-json`, `-n` y modo silencioso. Procesa la
  salida JSON y mapea los campos.
- Si `exiftool` no está, crea las fotos igualmente con
  `metadata_status='pending'` y un mensaje claro. No es un error fatal.
- Si exiftool devuelve error para un fichero, guarda `metadata_status='error'`
  y el mensaje, sin volcar rutas absolutas.
- Los metadatos pendientes se reintentan en la siguiente pasada si el extractor
  está disponible.
- Un fichero con hash ya conocido no necesita nueva extracción: los metadatos
  intrínsecos son los mismos.

### 7.2. Campos mínimos

Mapea, como mínimo:

- fecha de captura normalizada a ISO 8601;
- fabricante y modelo de cámara;
- objetivo;
- ISO;
- apertura;
- tiempo de exposición;
- distancia focal;
- latitud y longitud con signo;
- orientación;
- ancho y alto;
- tipo de fichero y extensión.

Si un campo no existe, guárdalo como `NULL`; no inventes valores vacíos.

### 7.3. Evolución

El ADR debe dejar claro:

- los tags llegarán después y se asociarán a `photos.hash`, no a la ruta;
- las caras y embeddings serán tablas derivadas con `photo_hash` y versión de
  modelo, y se calcularán una sola vez por contenido;
- XMP seguirá siendo la fuente de verdad de los metadatos de usuario;
- si la misma foto existe en varias ubicaciones y los sidecars XMP divergen,
  hará falta una política de ubicación canónica/conflicto. Propón una regla
  explícita (por ejemplo, ubicación canónica determinista y réplica de
  sidecars) y déjala como decisión pendiente si no se puede cerrar ahora.

No implementes nada de esto en esta entrega.

---

## 8. CLI y ejecución desatendida

### 8.1. Interfaz

`foto-index` debe funcionar sin pantalla y sin interacción. Opciones mínimas:

| Opción | Comportamiento |
| --- | --- |
| `--library PATH` | Raíz de la biblioteca; máxima precedencia. |
| `--config PATH` | Fichero de configuración global. |
| `--full` | Rehashea y reextrae todo, ignorando firmas. |
| `--prune` | Borra ubicaciones `missing` antiguas y fotos huérfanas. |
| `--prune-after DAYS` | Antigüedad mínima para `--prune`; por defecto 30. |
| `--dry-run` | No escribe nada; informa de lo que haría. |
| `--json` | Resumen legible por máquina en stdout. |
| `--quiet` | Solo errores. |
| `--verbose` | Más detalle; puede incluir rutas relativas. |
| `--help` | Documenta precedencia, códigos de salida y ejemplos. |

Códigos de salida:

- `0`: éxito, con o sin cambios.
- `1`: error no fatal, recorrido parcial o errores de ficheros.
- `2`: configuración inválida, biblioteca inexistente o esquema incompatible.
- `3`: otro indexador tiene el lock.

El resumen debe incluir: escaneados, sin cambios, nuevas, modificadas,
ausentes, fotos totales, ubicaciones totales, fotos duplicadas, bytes hasheados,
errores y duración. No vuelques rutas absolutas en los logs salvo `--verbose`.

### 8.2. Ejemplos que deben quedar documentados

Bash:

```sh
uv run foto-index --library ~/Fotos
```

Cron:

```cron
0 3 * * * cd /ruta/al/repo && .venv/bin/foto-index --library "$HOME/Fotos" --quiet >> "$HOME/.local/state/foto-index.log" 2>&1
```

Systemd de usuario (oneshot + timer): incluye un ejemplo de `.service` y
`.timer` en la documentación. El indexador es de una pasada; no implementes un
daemon ni un bucle de espera.

---

## 9. Concurrencia, integridad y errores

- Un solo escritor: lock exclusivo con `fcntl.flock` no bloqueante sobre
  `.foto/index.lock`. Mantén el descriptor abierto mientras dure el proceso.
- SQLite en WAL para que un futuro lector no se bloquee con el indexador.
- `PRAGMA busy_timeout=5000` y transacciones por lotes (por ejemplo, 200–500
  ubicaciones). No dejes una transacción abierta durante todo el recorrido.
- Antes y después de hashear, vuelve a comprobar la firma. Si el fichero cambió
  durante la lectura, reintenta una vez o descarta con error; no insertes un
  hash de contenido inestable.
- Los errores de un fichero no abortan la pasada completa.
- Los errores de configuración, esquema o raíz sí abortan con mensaje claro.
- Nada de `except: pass`. Excepciones propias en `core`.
- Si el proceso recibe SIGINT, cierra el `scan_runs` como `interrupted`, deja
  el catálogo consistente y libera el lock. La siguiente pasada continúa.
- No escribas fuera de `.foto/`.

---

## 10. Tests

Añade una suite `pytest` en `tests/` que no necesite Qt ni pantalla. Usa
`tmp_path`, bytes pequeños y fixtures; no uses fotos personales ni imágenes
grandes.

Cubre como mínimo:

### Hash e identidad

- Mismos bytes en dos rutas → una foto, dos ubicaciones, una duplicada.
- Bytes distintos → dos fotos.
- Renombrar/mover una ruta → misma foto, ubicación antigua `missing`, nueva
  `present`.
- Modificar el contenido → nuevo hash y ubicación reasignada.
- El hash no depende de nombre, ruta ni `mtime`.

### Indexación incremental

- Segunda pasada sin cambios: no se hashea y no se extraen metadatos (inyecta
  un hasher/extractor que cuente llamadas).
- `touch` sin cambiar contenido: se rehashea, el hash no cambia y no se
  reextraen metadatos.
- Fichero nuevo: se hashea y se extraen metadatos.
- Copia de un hash ya conocido: se añade ubicación sin reextraer metadatos.
- Borrado: ubicación `missing`, foto conservada.
- `--prune --prune-after 0`: elimina ubicación y foto huérfana.
- Raíz inexistente: error, sin marcar ausencias.
- Recorrido parcial (simula un error de listado de directorio): estado
  `partial`, sin marcar ausencias.
- `--full`: rehashea aunque la firma no cambie.
- `--dry-run`: no modifica la base de datos.

### Metadatos

- Extractor falso → campos normalizados correctamente.
- `exiftool` ausente → indexado correcto con `metadata_status='pending'`.
- Error de exiftool en un fichero → `error`, sin abortar el resto.
- Pendiente se reintenta en la siguiente pasada si el extractor aparece.
- Fechas, GPS con signo y campos ausentes se guardan como `NULL`/valor
  correcto, no como cadenas vacías inventadas.

### Catálogo y CLI

- Bootstrap de `.foto/library.toml` con UUID y versión, idempotente.
- `PRAGMA user_version` y migración; esquema más nuevo → error.
- WAL activo.
- Lock ocupado → código 3.
- `--json` produce JSON válido en stdout y logs en stderr.
- Códigos de salida 0/1/2/3.
- Mover la biblioteca completa de sitio conserva las rutas relativas y no
  duplica fotos.
- Borrar `catalog.sqlite` y reindexar reconstruye el catálogo.
- `import foto.indexer` no importa `PySide6`.

Si `pytest` o `ruff` no están instalados, añádelos como dependencias de
desarrollo con `uv` y actualiza `uv.lock`. Si no hay red, deja los tests
escritos y documenta en la respuesta final que no se pudieron ejecutar.

---

## 11. Criterios de aceptación

La entrega se considera terminada cuando:

- [ ] `uv run foto-index --help` funciona y documenta opciones, precedencia y
      códigos de salida.
- [ ] Indexar una biblioteca vacía crea `.foto/library.toml` y
      `.foto/catalog.sqlite`.
- [ ] Dos copias idénticas en carpetas distintas producen una foto y dos
      ubicaciones.
- [ ] Una segunda ejecución sin cambios no hashea ningún fichero.
- [ ] Un fichero nuevo se detecta y se indexa.
- [ ] Un fichero borrado se marca `missing`, no se borra del catálogo.
- [ ] Una raíz inexistente o un recorrido parcial no marca ausencias.
- [ ] `--prune` solo borra filas del catálogo, nunca originales.
- [ ] Sin `exiftool`, el indexado termina correctamente con metadatos
      pendientes.
- [ ] `--full` rehashea y reextrae.
- [ ] El catálogo se puede reconstruir borrando `catalog.sqlite`.
- [ ] `pytest` pasa.
- [ ] `ruff check .` pasa.
- [ ] El indexador no importa Qt.
- [ ] No se ha añadido ninguna dependencia de runtime.
- [ ] `README.md`, `AGENT.md` y `docs/adr/0002-indexado.md` reflejan la
      realidad.
- [ ] No se ha modificado ningún original.
- [ ] No se ha hecho commit ni push.

---

## 12. Fuera de alcance

No implementes en esta entrega:

- miniaturas ni caché de miniaturas;
- integración del visor con el catálogo;
- lectura o escritura de XMP;
- tags, favoritos;
- caras, embeddings, OCR, escenas o IA;
- deduplicación perceptual;
- daemon, watcher o `inotify`/FSEvents;
- soporte de vídeo;
- múltiples raíces por biblioteca;
- interfaz gráfica o diálogos;
- copias de seguridad, S3 o sincronización;
- migración de Apple Photos.

Si algo de esto aparece como necesario para que el indexador funcione, para y
pregunta antes de ampliar el alcance.

---

## 13. Estilo y restricciones

- Código e identificadores en inglés; comentarios, docstrings, documentación y
  mensajes de CLI en español.
- Type hints en todas las funciones públicas.
- `dataclasses` para modelos; nada de dicts sueltos entre módulos.
- `pathlib` para rutas; `os.scandir`/`os.stat` para el recorrido.
- Sin estado global mutable; configuración y catálogo se pasan explícitamente.
- Excepciones propias y errores explícitos.
- Logging con el módulo `logging`, no `print` salvo la salida JSON del CLI.
- Respeta `ruff` con línea de 100.
- Cambios pequeños y revisables. Nada de refactors masivos no pedidos.
- Si una decisión de diseño no está clara y afecta al esquema o a la
  semántica, pregunta antes de asumirla.

---

## 14. Orden de trabajo sugerido

1. Lee la documentación y comprueba el estado del repo.
2. Redacta el ADR 0002 con las decisiones de este prompt.
3. Implementa `storage/library.py` y `storage/catalog.py` con migraciones.
4. Implementa `core/hashing.py` y `core/scanner.py`.
5. Implementa el contrato de metadatos y `storage/exiftool.py`.
6. Implementa `core/indexing.py` con el flujo incremental y la reconciliación.
7. Implementa `storage/lock.py` y `indexer/cli.py`; añade el script.
8. Escribe los tests y ejecútalos.
9. Actualiza README, AGENT y `.gitignore`.
10. Revisa el ADR contra lo implementado y corrige lo que haya cambiado.
11. Ejecuta `pytest` y `ruff`; resume resultados y limitaciones.

---

## 15. Resumen para no perder el norte

- **Una foto = un hash de contenido.**
- **Una ubicación = una ruta relativa.**
- **La firma de stat evita leer ficheros que no cambiaron.**
- **El hash solo se calcula para altas y cambios.**
- **Los borrados se detectan por diferencia de conjuntos y se marcan, no se
  destruyen.**
- **Raíz ausente o escaneo parcial = no se marca nada.**
- **El indexador es un proceso de una pasada, sin Qt, lanzable desde bash,
  cron o systemd.**
- **El ADR 0002 es parte de la entrega, no un extra.**
