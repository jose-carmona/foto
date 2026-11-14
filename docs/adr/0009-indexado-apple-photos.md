# ADR 0009 — Indexado de la fototeca de Apple Photos

- **Estado:** propuesta
- **Fecha:** 2026-11-15
- **Decide:** Jose
- **Ámbito:** cómo lee `foto` una `Photos Library.photoslibrary` de Apple:
  política de escaneo del paquete (solo `originals/`), detección de la
  fuente, regla de metainformación obligatoriamente externa, tratamiento de
  vídeo y de ausencias, y la fase nueva `apple-import` del pipeline (importación
  mínima de **favoritos y personas** desde `database/Photos.sqlite`, con
  migración del esquema a v6). Amplía el [ADR 0008](0008-resolucion-de-ubicaciones.md)
  (que dejó esto «pendiente») y se apoya en el [ADR 0002](0002-indexado.md)
  (identidad, incremental, reconciliación), el [ADR 0005](0005-fases-y-geocodificacion.md)
  (pipeline de fases) y el [ADR 0006](0006-etiquetas.md) (etiquetas, procedencia
  y regiones). Quedan fuera el soporte de vídeo (Fase 6 del roadmap, aquí solo
  se decide su exclusión y se prepara el mecanismo), la importación del resto
  de metadatos de Apple (álbumes, títulos, descripciones, fecha de importación:
  anotada en el roadmap, Fase 5), la visualización de caras en la UI, el ML
  local y la descarga de originales desde iCloud.

## Contexto

La fototeca de Apple Photos es un **paquete** de macOS protegido por TCC: sin
*Full Disk Access* los `os.scandir` fallan con `Operation not permitted` (el
[ADR 0008](0008-resolucion-de-ubicaciones.md) ya permite abrir la raíz como
paquete y alojar la metainformación fuera con `--metadata`). El [ADR 0008]
(0008-resolucion-de-ubicaciones.md) dejó explícitamente pendiente decidir cómo
se indexa el paquete, porque indexarlo entero es incorrecto: casi todo su
contenido es derivado y colisiona con el modelo del [ADR 0002](0002-indexado.md).

Las colisiones concretas de indexar el paquete entero:

1. **Duplicados.** Cada asset aparece varias veces (original en `originals/`,
   editado renderizado en `resources/renders/`, proxies y miniaturas en
   `resources/`): hashes distintos para la misma foto rompen el invariante
   «un hash = una foto» y el contador `duplicate_photos`.
2. **No-imágenes.** `database/` (SQLite y binarios), `resources/` (plist,
   plists de renders, bases de búsqueda) y `scopes/` entrarían al catálogo,
   porque el scanner filtra solo por extensión de imagen.
3. **Reconciliación de ausencias.** `LibraryScanner.complete` pasa a `False`
   con cualquier error de listado; con TCC sin permiso o con originales
   «optimizados» en la nube, el indexador se comportaría como si la biblioteca
   fuera incompleta de forma permanente.
4. **Vídeo.** Una fototeca real de Apple es híbrida foto+vídeo; `IMAGE_EXTENSIONS`
   no incluye `.mov`/`.mp4` y el roadmap (Fase 6) recoge la evolución a
   fotovideoteca. Aquí solo se decide qué pasa **durante** el indexado de Apple.

La única fuente de favoritos, personas y demás metadatos de usuario de Apple
es `database/Photos.sqlite`, una base propietaria (modelo Core Data; referencia
pública de facto: `osxphotos`). El esquema v5 del catálogo no tiene sitio para
el vínculo con sus assets, pero sí para lo demás: `photo_tags` (ADR 0006)
ya admite `source='import'`, `status`, `confidence` y regiones de cara.

Restricciones que se mantienen: nunca tocar originales ni escribir dentro del
paquete, `core/`/`storage/`/`indexer/` sin Qt, errores explícitos, `pathlib`,
sin dependencias nuevas de runtime, migraciones idempotentes con
`PRAGMA user_version` y el pipeline de fases del ADR 0005.

## Decisión

> La verificación empírica de T1.1 (Apéndice A) se hizo contra una biblioteca
> real del formato moderno (`LibrarySchemaVersion` 5001, migrada de iPhoto) y
> ratifica las decisiones siguientes; lo no verificable contra esa biblioteca
> queda marcado «por verificar» y se acota en «Decisiones pendientes y riesgos».

### 1. Solo se indexa `originals/`, y dentro, solo la base sin sufijo

- La política de escaneo de un paquete Apple incluye **únicamente**
  `originals/` (raíces relativas del recorrido). Todo lo demás —`resources/`,
  `database/`, `sidecars/`, `scopes/`, `thumbnails/`, `external/`, `internal/`,
  `private/`, `PlugIns/`, `Caches/`, `Masters.legacy/`, caches de Apple TV y
  ficheros sueltos del paquete— queda fuera.
- Dentro de `originals/` se indexa el fichero `<UUID>.<ext>`: el nombre base
  **es** el identificador del asset en `Photos.sqlite` (Apéndice A). Los
  ficheros con patrón `<UUID>_<n>` —variantes editadas o derivadas con sufijo—
  se excluyen para preservar la unicidad. En la biblioteca verificada ese
  patrón **no aparece** en `originals/` (las versiones editadas viven en
  `resources/renders/<h>/<UUID>_<n>_<n>.<ext>`); la exclusión se mantiene como
  defensa frente a otras versiones del modelo de Apple.
- El destino de las versiones editadas queda cerrado: **fuera del catálogo
  hasta decisión posterior** (no se crea un segundo hash por foto editada).
- La política es una pieza declarativa del núcleo (`ScanPolicy` en
  `core/scanner.py`), no un `if apple` en el bucle: raíces relativas incluidas,
  exclusiones por nombre/patrón y extensiones efectivas. El caso por defecto
  (sin política) reproduce el comportamiento actual byte a byte.
- Si la raíz detectada como paquete Apple no contiene `originals/`, es un
  error temprano explícito (paquete anómalo), no un escaneo vacío silencioso.

### 2. Metainformación obligatoriamente externa

Si la raíz es un paquete `.photoslibrary`, **el directorio de metainformación
no puede caer dentro del paquete**, sea cual sea el origen de la cadena
(override CLI, `FOTO_METADATA`, `[metadata].path` o el nivel 4 por defecto).

- El helper de detección (`is_apple_photos_library`: sufijo `.photoslibrary`,
  comparado sin distinción de mayúsculas) vive en un sitio único de `core/`
  y lo consumen tanto la política de escaneo como `resolve_metadata_dir`.
- El enforcement ocurre en `resolve_metadata_dir` (`storage/resolver.py`,
  único sitio de la precedencia) **después** de resolver cualquier origen,
  comparando el directorio resultante contra la raíz con rutas ya expandidas
  y resueltas (contención). Incluye el nivel 4 `<fototeca>/.foto` y también
  un override explícito que apunte dentro.
- El fallo es `LibraryError` con acción sugerida: pasar `--metadata`, exportar
  `FOTO_METADATA` o fijar `[metadata].path` **fuera** del paquete. No hay
  fallback automático a una ruta externa inventada: si no se da ninguna de
  las tres fuentes, el error lo exige explícitamente.
- La UI usa el mismo resolutór y hereda la regla sin código propio.
- Las bibliotecas normales conservan el nivel 4 `<fototeca>/.foto` sin cambios.
- `.foto` sigue ignorándose por nombre en el recorrido (inocuo: con la regla
  anterior nunca puede existir dentro del paquete).

### 3. Errores de permiso (TCC) con causa explícita

- Si el primer `scandir` de la raíz falla por permiso y la raíz es un paquete
  Apple, el mensaje sugiere conceder *Full Disk Access* al proceso
  (`foto-index`/`foto`), en la incidencia y en el resumen; nunca un aviso
  anónimo enterrado en el log.
- La pasada se registra `partial` con causa explícita (TCC) y **no marca
  ausencias** (semántica de recorrido incompleto del ADR 0002); el resto de
  fases puede continuar sobre el catálogo ya existente.

### 4. Ausencias por iCloud: estado normal, no error

- Con «Optimizar almacenamiento de Mac» los originales pueden no estar en
  disco: esos ficheros simplemente no están en `originals/` y se comportan
  como hoy — `missing` bajo `--prune` tras su ventana, `present` si vuelven.
- Las ausencias masivas no son un error del indexador: es estado normal
  documentado. El criterio del punto anterior (TCC ≠ ausencias) evita
  confundir un permiso denegado con un borrado masivo.

### 5. Vídeo: excluido, con el mecanismo preparado

- `IMAGE_EXTENSIONS` no incluye vídeo y el indexado de Apple tampoco: solo
  fotos (`.jpg/.jpeg/.heic/.png/.tif/.tiff` y RAW). Los `.mov`, `.mp4`, `.avi`
  y `.mts` de `originals/` (≈ 7 % de los ficheros en la biblioteca verificada)
  quedan fuera hasta la Fase 6 del roadmap.
- El mecanismo del scanner queda preparado para extensiones efectivas por
  fuente (`ScanPolicy`), que es lo que exigirá la fotovideoteca, sin
  implementar nada de vídeo aquí.

### 6. Nunca se escribe dentro del paquete

- Toda escritura de `foto` sigue en el directorio de metainformación (ADR 0008,
  ahora obligatoriamente externo para Apple).
- La lectura de `Photos.sqlite` es **solo lectura estricta** con URI SQLite
  `mode=ro&immutable=1`: no crea `-wal` ni `-shm` (la BD de Apple vive en modo
  WAL y sus sidecars son del paquete), no toma locks y no toca el fichero.
  `immutable=1` puede leer datos ligeramente anteriores al último checkpoint de
  Photos: aceptable, porque la fase es repetible y recalcula todo cada pasada.

### 7. Fase nueva `apple-import` del pipeline

- `INDEXING_PHASES` pasa a `("scan", "geocode", "apple-import")`. La fase
  enriquece fotos ya presentes en el catálogo: no recorre el paquete, lee
  `Photos.sqlite` y escribe en el catálogo.
- El `if/elif` de `run()` se refactoriza a despacho por nombre (dict), para
  no encadenar un tercer caso cableado.
- Respetable con `--skip apple-import`; registrada en `run_phases` con estado
  `ok|partial|skipped|error`, motivo y contadores propios en `IndexingSummary.to_dict`:
  favoritos importados, personas importadas, no resueltos y errores.
- Si la raíz no es un paquete Apple, la fase se registra `skipped` sin leer
  nada. Si lo es y falta `Photos.sqlite` (o es de una versión sin las tablas
  esperadas), la fase acaba en `error` explícito; no aborta la pasada.
- Sin red: la fase es 100 % local.

### 8. Extractor read-only de Apple

- Módulo en `storage/` (la BD es estado del paquete, como el catálogo): abre
  la URI `file:...?mode=ro&immutable=1` y devuelve un modelo propio
  (`AppleAsset`, `ApplePerson`) — identificador del asset, favorito, personas
  con su región facial. Nada más se mapea.
- Los campos se toman de lo verificado en T1.1: `ZASSET.ZUUID`/`ZFAVORITE`,
  `ZPERSON` y `ZDETECTEDFACE` (regiones: centro `ZCENTERX/ZCENTERY` y lado
  `ZSIZE`, normalizados [0,1] sobre la imagen orientada). Las fechas de Apple
  (epoch Core Data) se convierten a ISO-8601 como el resto del núcleo —solo
  si algún día se importa alguna—.
- `ZADDITIONALASSETATTRIBUTES.ZFACEREGIONS` no se usa (Apéndice A: columna
  vestigial en el modelo 5001); las regiones salen de `ZDETECTEDFACE`.
- Álbumes (`ZGENERICALBUM`), títulos (`ZTITLE`), descripciones
  (`ZACCESSIBILITYDESCRIPTION`) y el resto del modelo **no se leen ni se
  importan**: la fixture de prueba los incluye precisamente para demostrar
  que la fase los ignora.

### 9. Esquema v6: una columna de vínculo, sin tablas nuevas

- `SCHEMA_VERSION = 6` con migración idempotente al estilo `_migrate_to_vN`:
  única columna nueva `photos.apple_uuid TEXT` (NULL para las fotos que no
  vienen de Apple; índice único parcial `WHERE apple_uuid IS NOT NULL`).
  Es la clave de unión entre `photos(hash)` y `ZASSET.ZUUID`.
- Sin tablas nuevas de álbumes ni de personas: favoritos y personas viven como
  etiquetas del ADR 0006. La tabla `tags` ya tiene `kind='person'` y
  `photo_tags` ya admite `source='import'`, `status`, `confidence` y regiones.
- El vínculo se establece en la fase `apple-import` resolviendo el rel_path
  `originals/<h>/<UUID>.<ext>` → UUID → `apple_uuid`; solo se tocan ubicaciones
  `status='present'`. Lo no resoluble se cuenta (`no resueltos`), no se adivina.

### 10. Importación: favoritos

- `ZFAVORITE = 1` → asignación **completa** (región NULL) de la etiqueta de
  sistema `fav` con `source='import'`, `status='confirmed'`, `confidence=NULL`.
  `ZFAVORITE = 0` → ausencia de asignación importada.
- El dueño manda: una asignación `source='manual'` (favorito puesto con `f`)
  **nunca se sobrescribe ni se revoca** desde la importación.

### 11. Importación: personas y regiones

- Cada cara **nombrada** (`ZDETECTEDFACE` enlazada a una `ZPERSON` con
  `ZFULLNAME` no vacío) de un asset resuelto crea una asignación de etiqueta
  `kind='person'` con el nombre de la persona (normalización del ADR 0006),
  `source='import'`, `status='suggested'` (Apple no pide confirmación a `foto`),
  `confidence=NULL` (Apple no aporta una confianza comparable) y región
  rectangular proporcional [0,1] derivada del centro y el lado:
  `x = cx − s/2`, `y = cy − s/2`, `w = h = s`, recortada a [0,1].
  Dos caras de la misma persona en la misma foto son dos filas (la unicidad
  del ADR 0006 es por región).
- Caras de personas sin nombre no se importan (se cuentan); el resto del
  modelo de caras (`ZFACECROP`, prints, agrupaciones) queda fuera.

### 12. Idempotencia y revocación import-only

- Cada pasada recalcula el estado desde `Photos.sqlite` y hace **upsert**:
  dos pasadas seguidas producen el mismo estado del catálogo.
- Lo que Apple deja de tener (favorito retirado, cara desasignada o persona
  renombrada) se revoca **solo** en filas `source='import'`; una etiqueta
  confirmada o sugerida por otra vía, o puesta a mano, no se toca.
- Las asignaciones están ligadas al hash (identidad estricta del ADR 0006):
  nunca se trasladan entre hashes.

### 13. Configuración opt-in

- Sección nueva `[apple_photos]` en `config.toml` con la clave `enabled`
  (booleano, `false` por defecto). Con `false`, la fase se registra `skipped`
  con motivo aunque la raíz sea un paquete Apple; con `true` importa.
- Validación al estilo `_validate_geocoding_section` (errores explícitos,
  tipos y valores); el canonical de `save_config` no la escribe nunca.
- Sin flag nuevo: la detección de la fuente es por sufijo y la activación,
  por configuración.

### 14. Privacidad

Los nombres de personas importadas y las rutas del paquete no aparecen en
logs ni en resúmenes (texto o JSON), mismo criterio que el ADR 0006 y el
ADR 0005; los contadores de la fase son numéricos.

## Consecuencias

**Positivas**

- `foto-index` puede indexar una fototeca de Apple sin corromper el catálogo:
  exactamente una entrada por asset fotográfico original, sin duplicados de
  renders ni basura de `database/` o `resources/`.
- Favoritos y personas de Apple llegan al catálogo, se pueden buscar
  (`/`, `:tag <persona>` — las personas son etiquetas del ADR 0006) y quedan
  marcadas con `source='import'`, distinguiendo lo importado de lo manual.
- La regla de metainformación externa es un error temprano y explícito, no un
  `.foto/` silencioso dentro del paquete.
- El pipeline gana su tercera fase con el marco existente (estados, contadores,
  `--skip`, `run_phases`), y el despacho por nombre deja el camino libre a las
  fases futuras (poda de miniaturas, vídeo).
- El mecanismo de política de escaneo declarativa sirve a la vez para la
  Fase 6 (vídeo) y para bibliotecas con layouts raros.

**Negativas**

- El modelo Core Data de Apple es privado y cambia entre versiones de macOS:
  el extractor depende de campos concretos (`ZUUID`, `ZFAVORITE`,
  `ZDETECTEDFACE`) y puede romperse con actualizaciones de Photos; la
  verificación por presencia de tablas/columnas da error explícito, y el
  soporte de más versiones queda en pendientes.
- Los favs de vídeos Apple no llegan (el vídeo no se indexa); se cuentan como
  no resueltos.
- Las caras entran como `suggested` sin confirmación posible en la UI (no hay
  sintaxis de confirmación todavía, ADR 0006); quedan contadas junto a
  `confirmed` en los filtros, como el resto de sugeridas.
- Una columna más en `photos` y una sección más de configuración que mantener.
- El enforcement del resolutór añade un caso a documentar en los cuatro
  puntos de entrada y en `--help`.
- Con la fase opt-in desactivada, una fototeca Apple funciona con favoritos
  y personas ausentes hasta que el dueño active `[apple_photos] enabled = true`.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Indexar el paquete entero con filtros por extensión | Los derivados de `resources/` y `database/` entrarían al catálogo y romperían «un hash = una foto»; la exclusión por extensión no basta. |
| Indexar también `resources/renders/` (versiones editadas) | Segundo hash por foto editada: rompe la unicidad y duplica el catálogo; decisión futura aparte. |
| Flag `--apple` para activar la política | Redundante: el sufijo `.photoslibrary` ya identifica el paquete; un flag invita a apuntar a un paquete sin tenerlo o a no usar la política debiendo. |
| Fallback automático de metainformación a una ruta externa inventada | Crea metainformación donde el dueño no la pidió; el error explícito con las tres vías (`--metadata`, `FOTO_METADATA`, `[metadata].path`) es suficiente y verificable. |
| Importar todo el modelo de Apple (álbumes, títulos, descripciones, fecha de importación) | No hay destino en el esquema v5/v6; importar sin lugar donde vivir es basura en el catálogo. Queda anotado en el roadmap (Fase 5). |
| Tabla nueva de favoritos/álbumes en el esquema v6 | Duplicaría el mecanismo único de etiquetas del ADR 0006, que ya tiene `kind`, `source`, `status`, `confidence` y regiones. |
| Usar `osxphotos` como dependencia | Dependencia nueva de runtime solo para leer dos tablas; `sqlite3` de la stdlib basta y mantiene el núcleo sin dependencias. |
| Copiar `Photos.sqlite` a un temporal antes de leer | Copia de cientos de MB en cada pasada y una escritura más que gestionar; `mode=ro&immutable=1` garantiza lo mismo sin copia. |
| `mode=ro` a secas | Sobre una BD en WAL sin `-shm`/`-wal` accesibles o a medio checkpoint puede exigir crear sidecars (escritura en el paquete) o fallar; `immutable=1` no toca nada. |
| Leer regiones de `ZADDITIONALASSETATTRIBUTES.ZFACEREGIONS` | Columna vestigial en el modelo verificado (3 filas con blob opaco no-plist); la fuente real es `ZDETECTEDFACE`. |
| Importar caras sin nombre | Una etiqueta necesita nombre (ADR 0006); sin `ZFULLNAME` no hay etiqueta que crear. Se cuentan y quedan para el ML local (Fase 5). |
| Fase Apple que recorra el paquete por su cuenta | Rompería el escritor único y duplicaría el recorrido; enriquecer el catálogo ya lleno por `scan` respeta el pipeline. |
| Filtrar por `ZKIND = 0` en el extractor | Innecesario: los vídeos no están en el catálogo y quedan naturalmente como no resueltos; el extractor no interpreta el modelo más de lo verificado. |

## Decisiones pendientes y riesgos

- **Patrón de variantes en `originals/`.** La biblioteca verificada no tiene
  ningún `<UUID>_<n>` en `originals/` (las editadas viven en
  `resources/renders/`); la exclusión defensiva se mantiene, pero conviene
  confirmar el comportamiento en bibliotecas no migradas de iPhoto. *Por
  verificar.*
- **Orientación de las coordenadas de cara.** Las regiones de
  `ZDETECTEDFACE` están normalizadas [0,1], pero hay que verificar contra
  fotos con EXIF de rotación si Apple las da sobre la imagen orientada tal
  como la muestra `foto` (convención del ADR 0006). *Por verificar en el
  paso 3 con la fixture y la biblioteca real.*
- **Placeholders de iCloud en disco** (miniatura sin original): su forma
  exacta dentro de `originals/` no aparece en la biblioteca verificada
  (31 965 ficheros ≈ 31 966 assets). *Por verificar.*
- **Versiones del modelo de Apple.** La validación del extractor se hace por
  presencia de tablas/columnas esperadas; el rango de `LibrarySchemaVersion`
  soportado se fijará en el paso 3 con los datos reales (5001 verificado).
- **Esquema v6 y visor.** Como en v3 y v4, tras actualizar el código hay que
  ejecutar `foto-index` una vez antes de abrir la app (el visor exige el
  esquema actual).
- **Confirmación de caras en la UI:** sin sintaxis de asignación/confirmación
  (ADR 0006), las sugerencias importadas no se pueden confirmar ni rechazar
  con teclado; queda para la fase de ML/etiquetado.
- **Nombre canónico de la etiqueta de persona** si Apple cambiara nombres
  con espacios/acentos raros: la normalización del ADR 0006 cubre el caso;
  renombrados de personas en Apple son alta + revocación import-only.

---

## Apéndice A — Verificación empírica (T1.1, 2026-11-15)

Biblioteca real inspeccionada **en solo lectura** (nada se escribió en el
paquete): copia de `Photos.sqlite` (695 MB) a un directorio temporal abierta
con URI `mode=ro&immutable=1`; el árbol se listó con `ls`. La biblioteca es
del formato moderno: `DataModelVersion.plist` → `LibrarySchemaVersion` **5001**,
`MetaSchemaVersion` 3; migrada desde iPhoto (conserva `Masters.legacy/`,
`photos.db`, `Library6.iPhoto`).

### A.1. Árbol de primer nivel del paquete

```
Apple TV Photo Cache/   ← derivado (excluir)
Caches/                 ← derivado (excluir)
database/               ← Photos.sqlite y metadatos internos (excluir salvo lectura)
external/               ← recursos de servicios Apple (excluir)
internal/               ← recursos internos (excluir)
Masters.legacy/         ← masters de iPhoto pre-migración (excluir)
originals/              ← ÚNICO CONTENIDO INDEXADO
Plugins/                ← (excluir)
private/                ← recursos de servicios Apple (excluir)
resources/              ← derivados: renders, proxies, miniaturas (excluir)
scopes/                 ← bibliotecas compartidas cloudsharing/momentshared/syndication (excluir)
iPhotoAux.db, iPhotoLock.data, Library.data, Library.iPhoto, Library6.iPhoto,
Projects.db, ProjectDBVersion.plist, repairOnLaunch   ← restos iPhoto (excluir)
```

Segundo nivel destacado: `database/` → `Photos.sqlite` (+ `-wal`/`-shm`
propios de Photos), `metaSchema.db`, `photos.db` (iPhoto), `protection/`,
`search/`, `DataModelVersion.plist`; `resources/` → `caches`, `cpl`,
`derivatives`, `journals`, `media.legacy`, `projects`, `renders`,
`smartsharing`; `originals/` → 16 subdirectorios `0`–`9`, `A`–`F`.

### A.2. `originals/`

- Layout: `originals/<h>/<UUID>.<ext>`, con `<h>` = primer carácter
  (hexadecimal, mayúscula) del UUID y `<UUID>` en mayúsculas con guiones.
  16 subdirectorios; 31 965 ficheros.
- **El nombre del fichero es el identificador del asset**: en las 31 966 filas
  de `ZASSET`, `ZFILENAME = "<ZUUID>.<ext>"` y `ZDIRECTORY` = primer carácter
  de `ZUUID`. No hay otras vías de unión.
- **Patrón de variantes editadas: no aparece en `originals/`** (0 ficheros con
  patrón `<UUID>_<n>`). Las 1 118 assets con `ZADJUSTMENTSSTATE != 0` guardan
  su versión editada en `resources/renders/<h>/<UUID>_<n>_<n>.<ext>`
  (verificado con muestras), p. ej. `8C19494C-…_1_201_a.jpeg`.
- Extensiones (por `ZASSET.ZFILENAME`): `.jpeg` 29 656, `.mov` 1 165,
  `.mp4` 965, `.avi` 129, `.jpg` 28, `.mts` 17, `.png` 3, `.pdf` 2, `.psd` 1.
  Aproximadamente un 7 % es vídeo y un 0,1 % son PDF/PSD (fuera de
  `IMAGE_EXTENSIONS`): ambos quedan excluidos del catálogo con las reglas
  normales del scanner.

### A.3. `Photos.sqlite`: tablas y campos usados

85 tablas. Las relevantes para la fase `apple-import`:

- **`ZASSET`** (132 columnas; 31 966 filas):
  - `ZUUID` **TEXT** (UUID mayúsculas con guiones) — clave de unión con el
    nombre del fichero en `originals/`.
  - `ZFAVORITE` 0/1 — favorito (777 assets: 747 fotos `ZKIND=0`, 30 vídeos).
  - `ZKIND` 0 = foto (29 690), 1 = vídeo (2 276).
  - `ZDIRECTORY`, `ZFILENAME`, `ZUNIFORMTYPEIDENTIFIER`, `ZORIENTATION`.
  - `ZHIDDEN` (12 ocultos), `ZTRASHEDSTATE` (0 en esta biblioteca),
    `ZCOMPLETE` (1 en todas), `ZADJUSTMENTSSTATE` (1 118 editados).
  - `ZDATECREATED`, `ZADDEDDATE` — fechas en **epoch Core Data** (segundos
    desde 2001-01-01 UTC; no se importan en esta fase).
- **`ZADDITIONALASSETATTRIBUTES`** (91 columnas; 1:1 con `ZASSET` vía
  `ZASSET` → `Z_PK`): `ZORIGINALFILENAME` (nombre de cámara previo a la
  importación), `ZTITLE`, `ZACCESSIBILITYDESCRIPTION` (descripción),
  `ZORIGINALFILESIZE`, … **`ZFACEREGIONS`: columna vestigial** (solo 3 filas
  con blobs de 32/128 bytes, no son plists); no se usa.
- **`ZPERSON`** (255 filas; 172 con `ZFULLNAME` no vacío): `ZFULLNAME`,
  `ZDISPLAYNAME`, `ZFACECOUNT`, `ZKEYFACE`, `ZPERSONUUID`.
- **`ZDETECTEDFACE`** (46 950 filas): región facial como **centro + lado de
  cuadrado normalizados [0,1]** — `ZCENTERX`, `ZCENTERY`, `ZSIZE`
  (rangos observados: x ∈ [0.012, 0.982], y ∈ [0.018, 0.986],
  size ∈ [0.011, 0.797]); enlaces `ZASSETFORFACE`, `ZPERSONFORFACE`
  (→ `ZPERSON.Z_PK`) y `ZFACECROP`; 14 469 caras con persona nombrada sobre
  10 239 assets distintos; 1 114 caras sin asset enlazado. También hay
  `ZQUALITY`, `ZMANUAL`, `ZASSETVISIBLE` (no se usan en esta fase).
- **Álbumes**: no existe tabla `ZALBUM` (mencionada en referencias antiguas);
  viven en `ZGENERICALBUM` + tablas de unión `Z_30ASSETS`, etc. — **fuera de
  alcance**, no se leen.

### A.4. Supuestos restantes «por verificar»

1. Que el patrón `<UUID>_<n>` no aparece nunca en `originals/` en otras
   versiones del modelo (exclusión defensiva en la política, ver § 1).
2. La orientación exacta de las coordenadas de cara respecto a la imagen
   orientada (EXIF) — § 11 y riesgos.
3. La forma de los placeholders de iCloud dentro de `originals/`.
4. El rango de `LibrarySchemaVersion` soportado (5001 verificado; validación
   del extractor por presencia de tablas/columnas).
