# Prompt — Indexado de una fototeca de Apple Photos (ADR 0009 + indexado de `originals/` + importación de `Photos.sqlite`)

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> cómo debe leer `foto` una fototeca de Apple Photos. Se ejecuta en **tres
> pasos diferenciados**, cada uno terminado y verificable por separado:
>
> 1. **Paso 1 — Redacción del ADR 0009**: verificar la estructura del paquete
>    en una biblioteca real, cerrar las decisiones de diseño y redactar el
>    ADR. Sin código.
> 2. **Paso 2 — Indexado filesystem**: el indexer restringe el escaneo al
>    contenido real del paquete (`originals/`) y excluye los derivados. Con
>    esto, `foto-index` produce un catálogo correcto y sin duplicados.
> 3. **Paso 3 — Importación de metadatos de Apple**: fase nueva del pipeline
>    que lee `database/Photos.sqlite` en solo lectura e importa
>    únicamente **favoritos y personas (caras)** al catálogo (migración
>    del esquema a v6). Ningún otro metadato de Apple (álbumes, títulos,
>    descripciones, …) se importa por ahora; queda anotado en el roadmap
>    para una fase posterior, igual que la visualización de caras en la
>    UI.
>
> **Seguimiento.** Este prompt es el registro de tareas. Al terminar cada
> tarea: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si el trabajo genera una pendiente nueva, añádela al final del paso
> correspondiente con el mismo formato. Una tarea solo está cerrada con sus
> tests en verde y `uv run pytest` + `uv run ruff check .` completos.

## 0. Misión

Hacer que el indexador lea una `Photos Library.photoslibrary` de Apple sin
corromper el catálogo ni tocar el paquete, y sin perder los metadatos que
solo Apple conserva. El alcance de la importación es deliberadamente
**mínimo**: favoritos y personas (caras). Apple tiene muchos más metadatos
que la versión actual de `foto` no sabe representar (álbumes, títulos,
descripciones, fecha de importación, …); importarlos sin destino en el
esquema no aporta nada. El pipeline ya está desacoplado por protocolos
(`Hasher`, `MetadataExtractor`, `Geocoder`); la fuente de datos es el único
componente no inyectable, y el esquema v5 no tiene sitio para el vínculo
con los assets de Apple.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables: nunca tocar originales, todo lo
   escrito por `foto` vive en el directorio de metainformación, sin Qt en
   `core/`/`storage/`/`indexer/`. Sin `git commit`/`push` sin permiso
   explícito.
2. `docs/adr/0008-resolucion-de-ubicaciones.md` — § «Preparación para Apple
   Photos» y § «Pendiente»: la raíz ya se abre como paquete con
   *Full Disk Access* y la metainformación vive fuera (`--metadata`).
   Este prompt materializa justo lo que ese ADR dejó pendiente.
3. `docs/adr/0002-indexado.md` y `docs/adr/0005-fases-y-geocodificacion.md`
   — el modelo de `locations` (firma, `status`, reconciliación) y el
   pipeline de fases que la fase nueva de Apple debe respetar.
4. `docs/adr/0006-etiquetas.md` — taxonomía plana con `kind`
   (`system|person|place|event|free`) y `photo_tags` con
   `source ('manual'|'ml'|'import')`, `status ('confirmed'|'suggested'|
   'rejected')`, `confidence` y regiones. La importación de favoritos y
   personas encaja aquí sin tabla nueva de etiquetas.
5. Código real (puntos de integración identificados):
   - `src/foto/core/scanner.py` — `LibraryScanner` (único recorrido; solo
     ignora `.foto` por nombre, línea 96; `complete=False` al primer error
     de listado bloquea la reconciliación).
   - `src/foto/core/indexing.py` — `INDEXING_PHASES` (línea 39),
     `Indexer.__init__` (191-217, protocolos inyectables), el
     `if/elif` de `run()` (258-261) cableado a `scan`/`geocode`, y
     `_scan` (369-370) que instancia `LibraryScanner(self.root)` sin
     inyección.
   - `src/foto/core/library.py` — `IMAGE_EXTENSIONS` (línea 13), sin vídeo.
   - `src/foto/core/metadata.py` / `src/foto/storage/exiftool.py` —
     `PhotoMetadata` y el mapeo exiftool (ya soporta HEIC).
   - `src/foto/storage/catalog.py` — `SCHEMA_VERSION = 5`, patrón de
     migraciones (`_migrate_to_vN`, `_ensure_columns`), `photo_tags`,
     `run_phases`, `list_present_locations`.
   - `src/foto/storage/config.py` — `AppConfig` y patrón de validación de
     secciones (`_validate_geocoding_section`).
   - `src/foto/indexer/cli.py` — `main()` (200-342): construcción de
     dependencias y `IndexingOptions`.
6. Tests existentes de referencia: `tests/test_scanner.py`,
   `tests/test_indexing.py`, `tests/test_cli.py`,
   `tests/test_catalog_migrations.py` (o equivalente del patrón vN) y los
   de etiquetas (`tests/test_tags*.py`).

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible.

---

## 2. Contexto investigado (base de la redacción del ADR)

### 2.1. Estructura del paquete `.photoslibrary`

Es un paquete de macOS protegido por TCC: sin *Full Disk Access* los
`os.scandir` fallan con `Operation not permitted`. El contenido típico
(**a verificar en T1.1 contra una biblioteca real**, es documentación no
contratada):

- `originals/<h>/<UUID>.<ext>` — los ficheros originales, repartidos en
  subdirectorios por un carácter hexadecimal del UUID. **Único contenido
  que merece indexar**: es lo que el usuario posee; todo lo demás es
  derivado.
- Versiones editadas del mismo asset, junto a la original en `originals/`
  (patrón de nombre con sufijo `<UUID>_<n>.<ext>`) y/o renderizadas en
  `resources/`. Indexarlas crearía dos hashes para la misma foto.
- `resources/` — renderizados, proxies, miniaturas y demás derivados
  (incluye vídeo/HEIC proxies): indexarlos duplicaría todo el catálogo.
- `database/Photos.sqlite` — la base de datos propietaria de Apple
  (modelo Core Data `ZASSET`, `ZALBUM`, `ZADDITIONALASSETATTRIBUTES`,
  `ZPERSON`, …): la única fuente de favoritos, álbumes, títulos,
  descripciones, personas y fecha de importación. Referencia pública de
  facto: el proyecto `osxphotos`.
- `database/search/`, `sidecars/`, `scopes/`, `external/`, `PlugIns/`,
  `thumbnails/` — derivados y metadatos internos de Photos: excluir.
- Con «Optimizar almacenamiento de Mac» activo, los originales pueden **no
  estar en disco** (solo miniaturas/placeholders): el estado «fichero
  ausente» es normal, no un error.

### 2.2. Colisiones con el modelo actual si se indexa el paquete entero

1. **Duplicados**: cada asset aparece varias veces (original, proxy,
   render) → hashes distintos → el invariante «un hash = una foto» y el
   contador `duplicate_photos` se rompen.
2. **No-imágenes**: SQLite, plist y binarios de `resources/` entraban el
   catálogo (el scanner filtra solo por extensión).
3. **Reconciliación de ausencias**: `LibraryScanner.complete` se pone a
   `False` con cualquier error de listado; con TCC sin permiso o con
   originales en la nube, el indexador se comportaría como si la
   biblioteca fuera incompleta de forma permanente.
4. **Vídeo**: `IMAGE_EXTENSIONS` no incluye `.mov`/`.mp4`; una fototeca
   real de Apple es híbrida foto+vídeo. El roadmap ya recoge la evolución
   a fotovideoteca (README, Fase 6); este ADR solo debe decidir qué pasa
   con el vídeo **durante** el indexado de Apple (no implementarlo).

### 2.3. Encaje ya preparado

- `--library` sobre el paquete y `--metadata` externo ya funcionan (ADR
  0008); `library.toml` lleva `root` validado. Nada que cambiar ahí.
- **Caveat que este prompt convierte en requisito (§ 3.8)**: hoy el
  nivel 4 de `resolve_metadata_dir` deriva `<fototeca>/.foto`, de modo
  que sin `--metadata` explícito la metainformación acabaría **dentro**
  del paquete de Apple. Con la detección de la fuente eso pasa a ser
  un error en vez de un silencio (ver § 5 y T2.4).
- Los originales de Apple son inmutables: el modelo incremental de
  `locations` (firma `size/mtime_ns/inode/device`, `status`) encaja sin
  cambios; las rutas relativas `originals/<h>/<UUID>.<ext>` son estables.
- `photo_tags` (v4/v5) ya admite `source='import'`, `status='suggested'`,
  `confidence` y regiones de cara: favoritos y personas no exigen
  migrar esa tabla. El esquema v6 de este prompt solo añade la columna
  de vínculo con Apple; los álbumes quedarían para una migración
  futura, cuando se decida importarlos.

---

## 3. Decisiones ya tomadas (no se discuten)

1. **Nunca se escribe dentro del paquete.** La lectura de
   `Photos.sqlite` es estrictamente solo lectura (URI SQLite
   `mode=ro`; valorar `immutable=1` para no crear WAL/sidecars). La
   metainformación de `foto` vive donde resuelva ADR 0008 (externa).
2. **Sin dependencias nuevas de runtime.** `sqlite3`, stdlib y lo
   existente bastan; el extractor de Apple no usa Qt.
3. **El modelo incremental y el invariante «un hash = una foto» no se
   tocan.** `originals/` se indexa con las mismas reglas que cualquier
   carpeta; la fase de Apple solo **enriquece** fotos ya presentes.
4. **La importación es idempotente y repetible.** Cada pasada de la fase
   Apple recalcula el estado desde `Photos.sqlite` (upsert); el dueño
   sigue mandando sobre lo manual: lo importado nunca sobrescribe una
   decisión `manual`/`confirmed` del usuario (favoritos puestos con `f`,
   etiquetas a mano). Toda importación de etiqueta usa
   `source='import'`.
5. **La fase Apple es una fase más del pipeline**: respetable con
   `--skip`, registrada en `run_phases`, contadores propios y estados
   `ok|partial|skipped|error` como las demás.
6. **Los fallos de permiso (TCC) son un error explícito con acción
   sugerida**, nunca un aviso anónimo enterrado en el log.
7. `core/`, `storage/` e `indexer/` siguen sin importar Qt; errores
   explícitos (`IndexingError`, `LibraryError`), `pathlib`, sin
   `except: pass`.
8. **Si la fototeca es de Apple Photos, la metainformación apunta
   obligatoriamente a una ruta fuera del paquete.** Regla dura, en el
   resolutór (`storage/resolver.py`, único sitio de la precedencia):
   - Detectado el paquete (sufijo `.photoslibrary`, helper compartido
     con la política de escaneo), toda resolución cuyo directorio de
     metainformación caiga dentro de la raíz es `LibraryError` con
     acción sugerida (`--metadata`, `FOTO_METADATA` o
     `[metadata].path` fuera del paquete). Aplica al nivel 4 por
     defecto **y también** a un override explícito que apunte dentro.
   - No hay fallback automático a una ruta externa inventada: si no se
     da ninguna de las tres fuentes, el error lo exige explícitamente.
   - El enforcement es compartido: la UI usa el mismo resolutór y hereda
     la regla sin código propio.
   - El resto de bibliotecas conservan el nivel 4 `<fototeca>/.foto`
     sin cambios.

## 4. Decisiones que el ADR 0009 debe cerrar (con recomendación)

Son las tres que el estudio dejó abiertas; el ADR las ratifica o refuta
con argumentos, y el resto del prompt asume la recomendación:

1. **¿Qué se indexa de `originals/`?** Recomendación: solo la versión
   original no editada de cada asset; excluir las variantes editadas
   (`<UUID>_<n>`) para preservar el invariante de unicidad. El ADR debe
   fijar el patrón exacto (verificado en T1.1) y su tratamiento futuro
   (quedan fuera hasta decisión posterior).
2. **¿Vídeo?** Recomendación: excluirlo en este ADR (coherente con
   `IMAGE_EXTENSIONS` y con la Fase 6 del roadmap), dejando el
   mecanismo del scanner preparado para una lista de extensiones por
   fuente.
3. **¿Ausencias por iCloud?** Recomendación: los ficheros que no están
   en disco se comportan como hoy (`missing` bajo `--prune` tras su
   ventana), pero el ADR debe fijar: (a) que `complete=False` por
   permiso TCC no bloquea el resto (se reporta `partial` con causa
   explícita), y (b) que las ausencias masivas no son error del
   indexador sino estado normal documentado.
4. **Detección de la fuente.** Recomendación: autodetección por sufijo
   `.photoslibrary` en la raíz (sin flag nuevo), con logging de la
   política aplicada y sección opt-in en `config.toml` para la fase de
   importación. El ADR fija el nombre de la sección y las claves.
5. **Vínculo catálogo ↔ asset.** El nombre del fichero en `originals/`
   es el identificador del asset en `Photos.sqlite` (UUID; verificar
   con qué campo y formato exacto en T1.1, con `osxphotos` como
   referencia). El ADR fija la clave de unión y su columna en el
   esquema v6.

---

# Paso 1 — Redacción del ADR 0009 (sin código)

- [x] **T1.1 — Verificación empírica del paquete** (2026-11-15). Contra una
  biblioteca real disponible en el entorno (si no la hay, con la documentación y
  `osxphotos` como fuentes, dejando los supuestos marcados como
  «por verificar»): listar el árbol de primer y segundo nivel del
  paquete, confirmar el layout de `originals/`, el patrón de nombre de
  las versiones editadas, los directorios a excluir, y abrir
  `Photos.sqlite` en solo lectura para confirmar las tablas y campos
  relevantes (`ZASSET.ZUUID`/favorito, `ZALBUM`, personas, `filename`).
  Entregar los hallazgos como apéndice del ADR (estructura + tablas con
  los campos usados). **Ninguna escritura en el paquete.**
  > Nota (2026-11-15): la biblioteca local `~/Pictures/Photos
  > Library.photoslibrary` es inaccesible (TCC: «Operation not permitted» sin
  > *Full Disk Access*). Se verificó contra otra biblioteca real, legible en un
  > volumen externo, del formato moderno (LibrarySchemaVersion 5001, migrada de
  > iPhoto). El árbol y la BD se leyeron solo lectura: la BD se copió a un
  > temporal y se abrió con `mode=ro&immutable=1`; ninguna escritura en el
  > paquete. Hallazgos que desvían el enunciado: (1) el patrón de versiones
  > editadas `<UUID>_<n>` **no aparece en `originals/`** — viven en
  > `resources/renders/<h>/<UUID>_<n>_<n>.<ext>` —; (2) no existe tabla
  > `ZALBUM`: los álbumes viven en `ZGENERICALBUM`; (3) las regiones faciales
  > salen de `ZDETECTEDFACE` (centro `ZCENTERX/ZCENTERY` + lado `ZSIZE`,
  > normalizados [0,1]); la columna `ZADDITIONALASSETATTRIBUTES.ZFACEREGIONS`
  > es vestigial (3 blobs opacos, no plists). Hallazgos en el Apéndice A del
  > ADR 0009, con los supuestos «por verificar» acotados.
  > Nota (2026-11-15): línea base del repo: `uv run ruff check .` limpio;
  > `uv run pytest` deja un fallo no determinista preexistente en suite
  > completa (`test_transfer_export.py::test_export_is_deterministic_byte_to_byte`
  > pasa en solitario); no tiene relación con este trabajo.
- [x] **T1.2 — Redactar `docs/adr/0009-indexado-apple-photos.md`** (2026-11-15)
  con el formato de los ADR previos: contexto (colisiones del § 2.2),
  decisiones (las del § 3 más las cerradas del § 4), consecuencias, y
  el apéndice de T1.1. Debe fijar: política de escaneo de Apple
  (incluye/excluye, patrón de versiones editadas), tratamiento de vídeo
  y de ausencias, detección de la fuente, la regla de metainformación
  obligatoriamente externa (§ 3.8, con su error y su acción sugerida),
  la fase nueva y el esquema v6 (columna de vínculo con Apple; sin
  tablas nuevas de álbumes, fuera de alcance), con favoritos y personas
  vía ADR 0006.
- [ ] **T1.3 — Aceptación.** Mostrar el ADR al proyectista y obtener el
  visto bueno antes de continuar. Actualizar las referencias
  pendientes que apuntan a «ADR futuro» (README § Fototeca de Apple
  Photos, ADR 0008 § Pendiente) cuando el ADR exista.

**Criterio de fin del paso 1:** ADR 0009 redactado y aceptado. **No
continuar al paso 2 sin cerrar esto.**

---

# Paso 2 — Indexado filesystem (restringir el escaneo a `originals/`)

## 5. Diseño vinculante del paso 2

- **Política de escaneo declarativa en el núcleo** (no un `if apple` en
  el bucle): `core/scanner.py` gana una política (frozen dataclass, p.
  ej. `ScanPolicy`) con raíces relativas incluidas (`originals/`),
  exclusiones por nombre/ruta relativa y extensiones efectivas.
  `LibraryScanner` la aplica; el caso por defecto (`None`) reproduce el
  comportamiento byte a byte actual.
- **Inyección del scanner en `Indexer`**: `Indexer.__init__` admite un
  scanner o factoría opcional; `_scan` usa el inyectado y, a falta de
  él, construye el actual. Es el mismo patrón que `Hasher`/
  `MetadataExtractor`/`Geocoder`.
- **Detección en la CLI** (`indexer/cli.py`): si la raíz resuelta es un
  paquete `.photoslibrary`, se construye la política de Apple según el
  ADR 0009 y se registra en el log (y en el resumen, si procede) la
  política aplicada. Sin flags nuevos.
- **Metainformación obligatoriamente externa** (§ 3.8): el helper de
  detección de paquete Apple vive en un sitio único (`core/` o
  `storage/` según el ADR) y lo consumen tanto la política de escaneo
  como `resolve_metadata_dir`. El enforcement ocurre en el resolutór,
  después de resolver cualquier origen de la cadena, comparando el
  directorio resultante contra la raíz (contención de rutas ya
  expandidas y resueltas).
- **Errores de permiso con causa explícita**: si el primer `scandir` de
  la raíz falla con permiso denegado y la raíz es un paquete Apple, el
  mensaje de error debe sugerir *Full Disk Access* (§ 3.6). El resto de
  errores de directorio mantienen la semántica actual de
  `complete=False`/`partial` salvo lo que fije el ADR (§ 4.3).
- **Exclusiones efectivas**: `originals/` únicamente; `.foto` sigue
  ignorándose (inocuo, la metainformación es externa); nada de
  `resources/`, `database/`, `sidecars/`, `scopes/`, `thumbnails/`,
  `external/` ni versiones editadas según el patrón del ADR.

## 6. Desglose de tareas del paso 2

- [ ] **T2.1 — Política de escaneo en `core/scanner.py`** (§ 5):
  `ScanPolicy` (raíces incluidas relativas, exclusiones, extensiones),
  aplicación en el recorrido, comportamiento por defecto idéntico al
  actual. Tests: política por defecto sin cambio, inclusión de raíz
  relativa, exclusión de directorios y del patrón de versiones
  editadas, symlinks e ignorados como hoy.
- [ ] **T2.2 — Inyección del scanner en `core/indexing.py`** (§ 5):
  parámetro en `__init__`, uso en `_scan`, error temprano si la política
  y la raíz son incompatibles. Tests: el Indexer usa el scanner
  inyectado y el default no cambia; el flujo incremental
  (firma/`unchanged`/`missing`) se demuestra sobre `originals/`.
- [ ] **T2.3 — Detección y política de Apple en `indexer/cli.py`** (§ 5):
  autodetección del paquete, construcción de la política según ADR,
  logging explícito. Tests con una biblioteca Apple sintética en
  `tests/fixtures/` (paquete con `originals/` con versiones editadas y
  derivados para demostrar que solo lo debido entra al catálogo).
- [ ] **T2.4 — Metainformación obligatoriamente externa en
  `storage/resolver.py`** (§ 3.8, § 5): helper de detección de paquete
  Apple compartido, enforcement en `resolve_metadata_dir` para todos
  los orígenes (default incluido) con `LibraryError` y acción
  sugerida; sin fallback automático. Tests: Apple sin ninguna fuente →
  error explícito; `--metadata` dentro del paquete → error;
  `FOTO_METADATA`/`[metadata].path` dentro → error; `--metadata` fuera
  → resuelve; biblioteca normal sin cambios (nivel 4 intacto); la UI
  hereda la regla vía el resolutór compartido (test de coherencia con
  `environment` inyectable). Actualizar los mensajes de error y los
  epílogos de `--help` que documenten la cadena de metainformación.
- [ ] **T2.5 — Errores de TCC con acción sugerida** (§ 5): mensaje
  específico para permiso denegado en paquete Apple; test con un
  scandir fallido inyectado.
- [ ] **T2.6 — Documentación**: README (§ Fototeca de Apple Photos:
  comportamiento real tras el paso 2, TCC, iCloud y la regla de
  metainformación externa obligatoria con su error), `AGENT.md` si el
  flujo de trabajo lo menciona. Actualizar la nota de «pendiente» que
  quede obsoleta.
- [ ] **T2.7 — Verificación y cierre del paso 2.** Suite en verde;
  smoke manual end-to-end si hay biblioteca real disponible (con FDA):
  `foto-index --library <paquete>` sin `--metadata` falla con el error
  del § 3.8; con `--metadata <externo>` el catálogo queda sin
  duplicados, sin entradas de `resources/` ni `database/`, contador
  `duplicate_photos` estable; `foto --metadata <externo>` la
  visualiza. Confirmar que el paquete no recibe escrituras (mtimes del
  árbol idénticos antes y después) y que no aparece ningún `.foto/`
  dentro de él.

**Criterio de fin del paso 2:** con una biblioteca real, el catálogo
contiene exactamente una entrada por asset fotográfico original de
Apple y es imposible resolver metainformación dentro del paquete (ni
por defecto ni por override); sin biblioteca real, la suite y los
fixtures sintéticos demuestran lo mismo.

---

# Paso 3 — Importación de favoritos y caras desde `Photos.sqlite`

## 7. Diseño vinculante del paso 3

- **Alcance mínimo (favoritos y personas, nada más)**: la fase importa
  exactamente dos cosas, `ZASSET` → favorito y persona(s) con su región
  facial, según lo verificado en T1.1. Cualquier otro metadato de Apple
  (álbumes, títulos, descripciones, fecha de importación, ajustes de
  edición, …) **no se lee ni se importa**: no hay destino en el esquema
  actual y su incorporación futura está anotada en el roadmap (README,
  Fase 5). El extractor puede ignorar el resto del modelo, no hay que
  mapearlo.
- **Migración v5 → v6** (`storage/catalog.py`, patrón `_migrate_to_vN`):
  según el ADR, únicamente la columna de vínculo con Apple en `photos`
  (§ 4.5). **Sin tablas nuevas**: favoritos y personas viven como
  etiquetas del ADR 0006 — tag `fav` con `source='import'`, tags
  `person` con `status='suggested'` y `confidence`/regiones si Apple
  las aporta. La tabla `person`/`place`/`free` ya admite estos kinds.
- **Extractor read-only** (`storage/apple_photos.py` o sitio del ADR):
  abre `Photos.sqlite` con URI `mode=ro`; devuelve un modelo propio
  (`AppleAsset`, `ApplePerson`) mapeando los campos verificados en
  T1.1 (favorito, personas y sus regiones). Fechas de Apple (epoch Core
  Data, si aplica) a ISO-8601 como el resto del núcleo. Falla con error
  explícito si la BD no existe o es de una versión incompatible.
- **Fase `apple-import`** en `INDEXING_PHASES`: el `if/elif` de
  `run()` se refactoriza a despacho por nombre (dict/método) para no
  encadenar un tercer caso; respetable con `--skip apple-import`,
  registrada en `run_phases` y con contadores propios en
  `IndexingSummary.to_dict` (favoritos importados, personas
  importadas, no resueltos, errores).
- **Vínculo e idempotencia**: la fase resuelve los rel_paths del
  catálogo (`locations.status='present'`) desde el identificador del
  asset (§ 4.5); lo no resoluble se cuenta, no se adivina. Upsert
  repetible; nunca toca asignaciones `source='manual'` (§ 3.4); una
  etiqueta dejada de estar en Apple se revoca solo si sigue siendo
  `source='import'`.
- **Sin caché de red ni servicios externos**: esta fase es 100 % local.
- **Configuración opt-in** según ADR (sección `[source.apple_photos]`
  o la que fije el ADR 0009): habilitación y claves; validación al
  estilo `_validate_geocoding_section`; el canonical nuevo de
  `save_config` no la incluye.

## 8. Desglose de tareas del paso 3

- [ ] **T3.1 — Fixture de `Photos.sqlite` de prueba.** Fixture sintética
  (o copia reducida y anonimizada de una real, si el proyectista lo
  autoriza) con el subconjunto del esquema verificado en T1.1: unos
  assets con favorito, una persona con cara y región facial. Los
  álbumes y el resto del modelo pueden existir en la fixture para
  demostrar que la fase los ignora. Sirve a todas las tareas
  siguientes.
- [ ] **T3.2 — Migración v6 en `storage/catalog.py`** (§ 7): columna de
  vínculo, `SCHEMA_VERSION = 6`, migración desde v5 (y ruta completa
  desde v1 si el patrón lo exige), tests de migración y de las
  operaciones de etiquetas importadas (upsert, revocación
  import-only).
- [ ] **T3.3 — Extractor read-only `storage/apple_photos.py`** (§ 7):
  modelo propio (`AppleAsset`, `ApplePerson`), mapeo de campos/fechas,
  solo lectura estricta (demostrar que no se crean `-wal`/`-shm`),
  errores explícitos, y prueba de que álbumes/títulos/descripciones
  presentes en la fixture no se importan. Tests sobre la fixture de
  T3.1.
- [ ] **T3.4 — Fase `apple-import` en `core/indexing.py`** (§ 7):
  refactor del despacho de fases, registro en `run_phases`, contadores
  y `to_dict` (favoritos importados, personas importadas, no
  resueltos, errores); `--skip apple-import` funciona. Tests: fase
  completa sobre catálogo con fotos de la fixture, `skipped`, `error`
  si la BD falta, interrupción.
- [ ] **T3.5 — Vínculo e idempotencia** (§ 7): doble pasada de la fase
  produce el mismo estado (upsert); favorito manual del dueño no se
  sobrescribe; favorito y persona retirados en Apple se revocan si
  siguen siendo `source='import'`; assets sin correspondencia se
  cuentan como no resueltos sin error. Tests de cada regla.
- [ ] **T3.6 — Configuración y CLI** (§ 7): sección opt-in en
  `storage/config.py`, lectura en `indexer/cli.py`, construcción del
  extractor de Apple, epílogo de `--help` actualizado. Tests de
  configuración (patrón de geocoding) y de CLI.
- [ ] **T3.7 — Documentación**: README (qué importa la fase: solo
  favoritos y personas; cómo desactivarla; nota de que el resto de
  metadatos de Apple y la visualización de caras en la UI quedan en el
  roadmap), `AGENT.md`, y el ADR 0009 si la implementación obliga a una
  corrección menor (registrarla como nota del ADR, no rediseñar).
- [ ] **T3.8 — Verificación y cierre del paso 3.** Suite completa en
  verde; smoke end-to-end si hay biblioteca real: dos pasadas seguidas
  de `foto-index` con estado estable, favoritos visibles con `f` en la
  UI y `:tag <persona>` filtra (las personas entran como etiquetas del
  ADR 0006); export/import JSON sigue funcionando con lo importado.
  La visualización de caras (regiones en el canvas) **no** es parte de
  este paso: está en el roadmap. Confirmar que el paquete no recibe
  escrituras ni en la fase Apple.

**Criterio de fin del paso 3:** con una biblioteca real (o la fixture),
favoritos y personas de Apple viven en el catálogo con las reglas del
§ 7, nada más se importa, la fase es repetible, omitible y registrada,
y ninguna pasada escribe dentro del paquete.

---

## 9. Fuera del objetivo

- **Vídeo** (Fase 6 del roadmap): solo la decisión del ADR y el
  mecanismo preparado; ninguna implementación de reproducción, miniaturas
  ni metadatos de vídeo.
- **Descarga de originales desde iCloud**: `foto` nunca provoca
  descargas ni espera a Photos; las ausencias se documentan (§ 4.3).
- **Escritura de XMP** o sincronización a la baja de etiquetas hacia
  Apple; `foto` sigue siendo local-first con el catálogo como fuente de
  verdad.
- **Importar más metadatos de Apple** (álbumes, títulos, descripciones,
  fecha de importación, ajustes de edición, …): posterior a este
  prompt y anotado en el roadmap (README, Fase 5); aquí solo se importa
  fav y caras, y el extractor debe demostrar que ignora el resto.
- **Visualización de caras en la UI** (regiones faciales en el canvas,
  navegar por personas, …): fase propia posterior; este prompt deja los
  datos en el catálogo (etiquetas `person` del ADR 0006) y nada en la
  interfaz. El filtro `:tag` existente sí las alcanza de forma natural.
- **Detección de caras/escenas propia (ML)**: solo se importan las
  personas que Apple ya clasificó; el ML local es la Fase 5 del roadmap.
- **Escritura en `Photos.sqlite`**, conversión de HEIC, redención de
  versiones editadas y cualquier operación que modifique el paquete.
- **Dependencias nuevas de runtime**, Qt fuera de `ui/`, y cambios de
  semántica en `resolve_library_path`/`resolve_metadata_dir` (ADR 0008,
  estables).
- **Álbumes inteligentes en la UI** y cualquier presentación de álbumes
  en el visor: los álbumes ni siquiera se importan en este prompt.
