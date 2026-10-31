# ADR 0002 — Indexado de la fototeca

- **Estado:** aceptada
- **Fecha:** 2026-10-30
- **Decide:** Jose
- **Ámbito:** identidad, catálogo e indexado incremental de los originales.
  Quedan fuera las miniaturas, la lectura/escritura de XMP, los tags, las caras
  y la IA. La integración con el visor queda regulada por `ADR 0003`.

## Contexto

El MVP necesita saber qué fotos hay en una biblioteca, cuáles son la misma foto
aunque estén en rutas distintas y qué metadatos intrínsecos tienen. El
visualizador actual recorre la carpeta y genera miniaturas con Qt, pero no tiene
un índice estable ni incremental. Este ADR fija el proceso que llena
`<fototeca>/.foto/catalog.sqlite` sin acoplarse a Qt.

Restricciones ya fijadas en `AGENT.md` y en el prompt de esta entrega:

- Los originales **no se modifican, mueven ni borran**. El indexador solo escribe
  dentro de `<fototeca>/.foto/`.
- `core/` y `storage/` no importan Qt. El indexador debe funcionar desde bash,
  cron o un `systemd` oneshot, sin pantalla ni interacción.
- Sin dependencias nuevas de runtime: `hashlib`, `sqlite3`, `subprocess`,
  `json`, `logging`, `pathlib`, `dataclasses` y `fcntl` bastan.
- El catálogo es un **índice reconstruible**. Si se borra, otra pasada lo
  regenera desde disco.
- La configuración y la raíz de la biblioteca siguen el ADR 0001.
- Linux (Omarchy) y macOS son objetivos de primera clase.

## Decisión

### 1. Una foto es su contenido

La clave primaria de `photos` es el hash SHA-256 de los bytes del original, con
el formato:

```
sha256:<64 dígitos hexadecimales en minúsculas>
```

Se calcula con `hashlib.sha256` en streaming, por bloques de 1 MiB. El hash no
depende del nombre, la ruta, la fecha del sistema de ficheros ni los metadatos.
Dos ficheros con los mismos bytes son **una foto con dos ubicaciones**; dos
ficheros visualmente parecidos pero con bytes distintos son fotos distintas.

El prefijo `sha256:` permite cambiar de algoritmo en el futuro sin ambigüedad.
Ese cambio no se implementa ahora.

Para evitar insertar un hash de contenido inestable, cada fichero se firma antes
y después de leerlo. Si la firma cambia durante la lectura, se reintenta una vez;
si vuelve a cambiar, se registra un error y se deja para la siguiente pasada.

### 2. Tabla de fotos y tabla de ubicaciones

El modelo separa la identidad del contenido de sus localizaciones:

- `photos`: una fila por hash. Contiene los metadatos intrínsecos normalizados y
  el estado de extracción.
- `locations`: una fila por ruta relativa. Contiene la firma de `stat`, el estado
  `present`/`missing` y las fechas de seguimiento.
- `scan_runs`: historial de pasadas, con contadores y estado.

Las rutas se guardan **relativas a la raíz de la biblioteca**, en formato POSIX,
sin `./` ni `..`. Mover la fototeca o el disco no invalida el catálogo. La raíz
es la carpeta que contiene `.foto/`; `.foto/` se ignora siempre durante el
recorrido y no se indexan symlinks de ficheros ni de directorios.

### 3. Esquema SQLite

```sql
PRAGMA user_version = 1;

CREATE TABLE photos (
    hash                TEXT PRIMARY KEY,   -- 'sha256:<64 hex>'
    file_type           TEXT,
    extension           TEXT NOT NULL,
    width               INTEGER,
    height              INTEGER,
    captured_at         TEXT,
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
    rel_path        TEXT PRIMARY KEY,
    photo_hash      TEXT NOT NULL REFERENCES photos(hash) ON DELETE RESTRICT,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    inode           INTEGER,
    device          INTEGER,
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

Notas de implementación:

- `photos.hash` es una PK `TEXT` de ~71 bytes; SQLite la maneja sin problema y
  evita una indirección con un `id` entero.
- `locations.photo_hash` es la relación N:1 con `ON DELETE RESTRICT`. Una foto
  sin ubicaciones solo desaparece con `--prune`.
- `metadata_status='pending'` se reintenta en pasadas posteriores si hay
  extractor. `error` solo se reintenta con `--full`, para no repetir fallos
  permanentes en cada ejecución.
- `missing_since` se escribe solo la primera vez que se detecta la ausencia. Si
  el fichero vuelve, se limpia y la ubicación vuelve a `present`.
- `last_indexed_at` no se actualiza en las ubicaciones sin cambios; el `scan_runs`
  ya registra cuándo se hizo la pasada.
- No se guarda el JSON crudo de `exiftool`.
- Las tablas de tags, caras y embeddings se añadirán después con `photo_hash`
  como clave foránea. No existen todavía.

### 4. Indexado incremental por firma de `stat`

Cada imagen se identifica baratamente con:

```
(size, mtime_ns, st_ino, st_dev)
```

En cada pasada:

1. **Ruta nueva**: se hashea; si el hash ya existe se inserta solo la ubicación,
   y si no existe se inserta la foto con metadatos `pending`.
2. **Ruta conocida con firma idéntica y `present`**: no se abre el fichero, no
   se hashea y no se extraen metadatos. Cuenta como `unchanged`.
3. **Ruta conocida con firma idéntica y `missing`**: la foto reaparece; se marca
   `present`, se limpia `missing_since` y se actualiza `last_indexed_at` sin
   hashear.
4. **Ruta conocida con firma distinta**: se recalcula el hash. Si no cambió
   (por ejemplo, un `touch`), solo se actualiza la firma. Si cambió, la ubicación
   se mueve al nuevo hash y, si ese hash no existía, se extraen sus metadatos. La
   foto antigua puede quedar sin ubicaciones hasta un `--prune`.
5. **Error de lectura o de hash**: se cuenta como error, se añade la ruta a
   `seen` si el `stat` funcionó y se continúa. No se marca `missing`.

En una biblioteca estable, una pasada incremental hace un `stat` por fichero y no
lee ni un byte de imagen. El hash solo se calcula para altas y cambios.

Las rutas vistas se acumulan en una tabla temporal `seen(rel_path TEXT PRIMARY
KEY)` y se insertan por lotes (250 por defecto), con `temp_store=MEMORY`. Así no
se mantiene la lista completa en memoria ni se deja una transacción abierta
durante todo el recorrido.

### 5. Borrados lógicos y reconciliación

Al terminar un recorrido **completo**, una sola sentencia marca las ausencias:

```sql
UPDATE locations
SET status = 'missing',
    missing_since = :now
WHERE status = 'present'
  AND rel_path NOT IN (SELECT rel_path FROM seen);
```

Los borrados son lógicos: ninguna fila se elimina por defecto y ningún original
se toca. Si la raíz no existe, no se pudo listar un directorio o falló la
clasificación de una entrada, el recorrido es **parcial** y no se marca ninguna
ausencia. Es preferible conservar ubicaciones obsoletas a confundir un disco
desmontado o una carpeta sin permisos con fotos borradas.

`--prune` elimina ubicaciones `missing` cuyo `missing_since` sea anterior a
`--prune-after` días (30 por defecto) y, después, fotos sin ninguna ubicación.
`--prune-after 0` permite limpiar de inmediato. Solo borra filas del catálogo.

### 6. Modos de ejecución

- **Incremental (por defecto):** firma de `stat`, hash solo si hace falta,
  extracción de metadatos pendientes y reconciliación de ausencias.
- **`--full`:** ignora las firmas, rehashea todo y reextrae metadatos de todas
  las fotos. Sirve para verificar integridad o detectar cambios que conservaron
  tamaño y `mtime`. No borra ni modifica originales.
- **`--dry-run`:** usa una copia en memoria del catálogo (o una base vacía con el
  mismo esquema), no crea `.foto/`, no escribe el catálogo y no adquiere el lock.
  Informa de lo que haría.

El resumen incluye, además de los contadores de reconciliación, cuántos ficheros
se han hasheado (`hashed_files`) y cuántas fotos han recibido metadatos en la
pasada (`metadata_extracted`). Así `--full` se distingue de una pasada
incremental aunque el contenido no haya cambiado (`unchanged`).

### 7. Metadatos y `exiftool`

Los metadatos de esta fase salen **de la propia imagen** (EXIF y metadatos
incrustados, incluido XMP embebido si `exiftool` lo devuelve). Se normalizan a un
modelo `PhotoMetadata` con: fecha de captura ISO 8601, fabricante, modelo,
objetivo, ISO, apertura, tiempo de exposición, distancia focal, latitud y
longitud con signo, orientación, ancho, alto, tipo y extensión. Un campo ausente
se guarda como `NULL`, nunca como cadena vacía inventada.

`exiftool` es opcional:

- Se busca una vez por ejecución con `shutil.which`.
- Se invoca sin shell, con lista de argumentos, `-json`, `-n`, modo silencioso y
  lotes de 100 rutas para no superar `ARG_MAX`.
- Solo lectura: nunca se le pasan opciones de escritura.
- Si no está, el indexado de hashes y ubicaciones termina correctamente y las
  fotos quedan con `metadata_status='pending'`.
- Si falla un fichero, se guarda `error` con un mensaje sin rutas absolutas y el
  resto del lote continúa.
- Los `pending` se reintentan en la siguiente pasada si aparece el extractor, sin
  rehashear nada. Los `error` requieren `--full`.

Los metadatos intrínsecos son función del contenido, así que se extraen una sola
vez por hash aunque haya varias ubicaciones.

### 8. Concurrencia e integridad

- SQLite en **WAL** para que un futuro lector (el visor) no se bloquee con el
  indexador. En `--dry-run` se usa `:memory:` y no se activa WAL.
- `PRAGMA foreign_keys = ON`, `PRAGMA busy_timeout = 5000` y
  `PRAGMA temp_store = MEMORY`.
- Un único escritor: `fcntl.flock` exclusivo y no bloqueante sobre
  `.foto/index.lock`. El descriptor se mantiene abierto durante todo el proceso.
  Si el lock está ocupado, se sale con código 3.
- Transacciones por lotes, no una transacción abierta durante todo el recorrido.
- `PRAGMA user_version` es la versión del esquema. Si el catálogo es de una
  versión más nueva, se aborta con un mensaje claro. Las migraciones son
  idempotentes y se aplican al abrir.
- Al arrancar, cualquier `scan_runs` que quedó en `running` por un cierre
  abrupto se marca `interrupted`. Cada pasada crea su propio registro.
- Con `SIGINT`, el run se cierra como `interrupted`, el catálogo queda
  consistente y el lock se libera. La siguiente pasada continúa.
- Los errores de un fichero no abortan la pasada; los de configuración, esquema o
  raíz sí.

### 9. CLI y ejecución desatendida

`foto-index` es un proceso de una sola pasada, sin daemon ni bucle de espera.
Opciones: `--library`, `--config`, `--full`, `--prune`, `--prune-after`,
`--dry-run`, `--json`, `--quiet`, `--verbose` y `--help`.

Precedencia de la biblioteca: `--library` → `FOTO_LIBRARY` →
`[library].path` de `config.toml` → error. Para el fichero de configuración:
`--config` → `FOTO_CONFIG` → `platformdirs`.

Códigos de salida:

| Código | Significado |
| --- | --- |
| `0` | Éxito, con o sin cambios. |
| `1` | Error no fatal, recorrido parcial o errores de ficheros. |
| `2` | Configuración inválida, biblioteca inexistente o esquema incompatible. |
| `3` | Otro indexador mantiene el lock. |
| `130` | Interrumpido con `SIGINT`. |

El resumen incluye escaneados, sin cambios, nuevas, modificadas, ausentes, fotos,
ubicaciones, fotos duplicadas, bytes hasheados, errores, pendientes de metadatos
y duración. Los logs van a stderr; el JSON, a stdout. Las rutas de los logs son
relativas salvo en `--verbose`.

Bash:

```sh
uv run foto-index --library ~/Fotos
```

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

### 10. Evolución futura

- **Tags y metadatos de usuario:** no son función del contenido. Llegarán con
  XMP como fuente de verdad y se asociarán a `photos.hash`, no a una ruta.
- **Caras y embeddings:** serán tablas derivadas con `photo_hash` y versión de
  modelo. Se calcularán una vez por contenido y se podrán reconstruir.
- **Miniaturas:** caché derivada en `.foto/thumbs/`; no forman parte de este ADR.
- **Integración con el visor:** el visor consume el catálogo en modo lectura
  (ver `ADR 0003`). WAL permite que no se bloquee con una pasada de indexado.

## Consecuencias

**Positivas**

- Mover o renombrar fotos no duplica filas de `photos`; solo cambian ubicaciones.
- Mover la biblioteca completa no invalida el catálogo porque las rutas son
  relativas.
- Una pasada estable es barata: `stat` por fichero y cero lecturas de imagen.
- El catálogo se puede borrar y reconstruir; no es una fuente de verdad única.
- Los originales quedan intactos y el indexador no depende de Qt.
- Sin `exiftool` se puede empezar a indexar; los metadatos se completan después.
- La separación `photos`/`locations` deja el modelo listo para tags, caras y
  embeddings sin rehacer la identidad.

**Negativas**

- El hash SHA-256 de una biblioteca grande cuesta una primera pasada de lectura
  completa; es inevitable si la identidad es el contenido.
- La firma `(size, mtime_ns, inode, device)` puede no detectar un cambio que
  conserve las cuatro cosas. `--full` existe para verificar integridad.
- En sistemas de ficheros sin inodos estables o con `mtime` de baja resolución,
  la detección fina depende más del hash.
- `fcntl` es POSIX; Windows queda fuera del objetivo actual.
- Los borrados lógicos y `--prune` añaden estados que hay que explicar y probar.
- El catálogo sigue siendo un índice: si se pierde, se pierde el historial de
  `scan_runs`, no las fotos.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Ruta como clave primaria | Rompe la deduplicación y obliga a reescribir filas al renombrar o mover. |
| Hash perceptual como identidad | No identifica bytes iguales; confunde fotos parecidas y no es determinista entre versiones de algoritmo. |
| Reindexar y rehashear todo en cada ejecución | Es correcto pero lento; se reserva para `--full`. |
| `mtime` o tamaño como identidad | No detectan copias iguales ni cambios que conservan la firma. |
| `inotify`/FSEvents como mecanismo principal | Solo funciona con el proceso vivo; falla con discos desmontados y no es portable. Se pospone como complemento. |
| Caché de `mtime` de directorios como modo por defecto | No detecta modificaciones in-place; se pospone como modo rápido opcional. |
| `id` entero + `hash` único | Añade indirección sin beneficio en SQLite; el hash ya es estable y único. |
| `ImageUniqueID` de EXIF | No existe en todas las fotos, se duplica y no es fiable. |
| Guardar rutas absolutas | Rompe la portabilidad al mover la biblioteca o el disco. |
| Borrado físico inmediato de ausencias | Un disco desmontado o un error de permisos parecería un borrado masivo. |
| Guardar el JSON crudo de `exiftool` | Infla la base de datos y mezcla la fuente de verdad con el índice. |
| Un daemon que vigile cambios | El caso de uso es una pasada desatendida; un daemon añade estado, fallos y dependencia de que el proceso siga vivo. |

## Decisiones pendientes y riesgos

- **Ubicación canónica y XMP divergente.** Cuando una foto tenga varias
  ubicaciones y existan sidecars XMP, ¿cuál es la ubicación canónica para leer y
  escribir metadatos de usuario? Propuesta para la fase XMP: elegir de forma
  determinista la primera ruta `present` en orden lexicográfico POSIX, tratar las
  demás como réplicas y detectar conflictos por hash de contenido del sidecar.
  No se implementa ahora.
- **Cambio de algoritmo de hash.** El prefijo `sha256:` deja la puerta abierta,
  pero no hay plan de migración ni doble hash.
- **Bibliotecas en sistemas sin hardlinks.** El bootstrap de `library.toml` usa
  `os.link` para crear el descriptor sin sobrescribir; si el sistema de ficheros
  no lo soporta, cae a una creación exclusiva con `O_EXCL`. Queda por probar en
  exFAT/SMB reales.
- **Rendimiento del primer indexado.** No se paraleliza el hash. Se medirá antes
  de añadir concurrencia.
- **Resolución de `mtime`.** En sistemas con `mtime` de baja resolución, un
  cambio rápido de contenido podría no detectarse hasta un `--full`.
- **Discos externos.** La raíz ausente bloquea ausencias, pero no hay todavía una
  noción de volumen o relocalización automática.
- **Lectores futuros.** WAL está activo, pero la política de lectura concurrente
  del visor (snapshot, refresco, caché) se decidirá al integrarlo.
