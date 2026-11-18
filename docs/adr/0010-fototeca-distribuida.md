# ADR 0010 — Fototeca distribuida en varias ubicaciones

- **Estado:** propuesta
- **Fecha:** 2026-11-17
- **Decide:** Jose
- **Ámbito:** el modelo de ubicaciones de la fototeca: declaración en
  configuración (config v2), resolución, sincronización config ↔ catálogo,
  esquema v6 del catálogo, indexado y prune multi-ubicación, disponibilidad y
  tolerancia en la UI, y retirada del descriptor `library.toml`. Amplía y
  recorta [ADR 0001](0001-configuracion.md), revisa [ADR 0002](0002-indexado.md)
  y [ADR 0003](0003-consumo-del-indice-desde-el-visor.md) y revisa parcialmente
  [ADR 0008](0008-resolucion-de-ubicaciones.md); [ADR 0006](0006-etiquetas.md)
  y [ADR 0009](0009-indexado-apple-photos.md) quedan intactos en sus reglas
  y se apoyan en este modelo (el esquema de Apple pasa a v7 por orden de
  implementación; ver Decisión 4). Quedan fuera la política de emplazamiento
  y el traslado
  entre ubicaciones (feature futura), el soporte de volúmenes remotos y el
  vídeo. Base: prompt
  `prompts/fototeca-distribuida-multi-ubicacion.md` (§ 0, § 3 y § 7).

## Contexto

La fototeca es hoy **una única raíz**: `--library` → `FOTO_LIBRARY` →
`[library].path` → diálogo (ADR 0001), con toda la metainformación en
`.foto/` (ADR 0008). El catálogo (esquema v5) ya admite **varias ubicaciones
por foto** — deduplicación por hash SHA-256, tabla per-foto `locations` con
`rel_path` y estado `present`/`missing` — pero todas las rutas son relativas
a una sola raíz: no hay forma de distinguir de qué disco viene cada
`rel_path`. El escaneo (`core/scanner.py`) y el indexador
(`core/indexing.py`) asumen esa raíz única, y la UI construye las rutas
absolutas con ella (`load_from_catalog(catalog, library_path)`).

El objetivo: repartir la fototeca entre **ubicaciones físicas con nombre**
(SSD rápido + USB de archivo), con las favoritas y las últimas en el disco
rápido como horizonte, tolerando que una ubicación no esté disponible en un
momento dado (USB desmontado) sin bloquear apertura, indexado ni UI.

Tres reglas del modelo, **tomadas como decisiones ya cerradas** que este
ADR registra y hace cumplir:

1. **Declaración.** Las ubicaciones se declaran **solo en el fichero de
   configuración** — `[[library.locations]]` en config v2; `[library].path`
   en v1 equivale a una única ubicación `default`. Desaparecen `--library`
   y `FOTO_LIBRARY`; no existe ninguna otra vía (CLI ni entorno) de
   declarar ubicaciones.
2. **Sincronización.** `foto-index` es el único componente que **lee esa
   declaración para indexar** (ver el carve-out de la Decisión 7 para los
   resolutores de metainformación y la UI). Al arrancar compara la config
   con el catálogo: registra las ubicaciones nuevas y **marca como
   eliminadas** — a la ubicación y a sus fotos — las que se hayan quitado
   de la config. No borra nada: la limpieza de filas es trabajo de
   `--prune`.
3. **Verdad.** Para la UI (y para export/import) la fuente de verdad es el
   catálogo: ubicaciones, fotos, rutas, etiquetas y contadores salen de la
   BD. La config solo interviene en la cadena de metainformación y en el
   diálogo de primera ejecución.

Más una garantía nueva y una base preparada:

- **Tolerancia:** una ubicación ausente (USB desmontado) no bloquea la
  apertura, el indexado ni la UI; se avisa y las fotos de esa ubicación se
  degradan (no se borran, no se pierden metadatos). Una ubicación ausente
  **nunca** se da de baja: la baja solo la provoca quitarla de la config.
- **Emplazamiento (base preparada, traslado diferido):** las ubicaciones
  declaran su `tier` (`fast`/`archive`) como base de una futura política de
  emplazamiento. **Esta feature no mueve ficheros**: toda herramienta de
  traslado entre ubicaciones queda fuera del ámbito y se estudiará en una
  feature futura.

El diseño debe producir **ubicaciones genéricas**: vídeos en una ubicación
separada, una fototeca de Apple Photos como ubicación más y volúmenes
remotos tipo S3 deben resolverse en el futuro **añadiendo una ubicación a
la configuración, sin rediseñar nada** (Decisión 9).

## Decisión

### 1. Configuración: `version = 2` y `[[library.locations]]`

```toml
version = 2

[[library.locations]]
name = "ssd"                     # identificador estable, único
path = "~/Fotos/Principal"       # expansión como hoy (~ y relativa al config)
tier = "fast"                    # fast | archive

[[library.locations]]
name = "usb"
path = "/Volumes/FotoVault"
tier = "archive"

[metadata]
# Único para TODAS las ubicaciones: un solo catálogo, una sola caché de
# miniaturas. Resuelto por la cadena del ADR 0008 (sin cambios).
```

Reglas cerradas:

- **Compatibilidad v1:** un fichero con `version = 1` y `[library].path`
  sigue funcionando tal cual: equivale a una única ubicación de nombre
  `default` y `tier = "fast"` (valor de compatibilidad cerrado: la
  fototeca preexistente es la principal y gana los desempates canónicos
  si luego se declara una ubicación `archive`). Cero migración para el
  dueño.
- `[[library.locations]]` solo es válido en `version = 2`; en un v1 es
  `ConfigError` con mensaje que sugiera subir la versión.
- **Una v2 sin `[[library.locations]]` ni `[library].path` es válida** y
  significa «cero ubicaciones declaradas»: `foto-index` termina con error
  y mensaje que apunte a la lista; la UI **no** abre el diálogo de primera
  ejecución (no puede persistir en v2): muestra un error explícito que
  pida declarar las ubicaciones a mano en `config.toml`. El diálogo queda
  reservado a la primera ejecución sin config y a los ficheros v1 sin
  `[library].path`, y siempre crea o completa ficheros v1.
- **Una v2 con `[library].path` suelto es `ConfigError`** (con o sin
  lista): en v2 la única sintaxis de ubicaciones es la lista, editada a
  mano. Una sola forma de decirlo por versión, sin ambigüedad.
- Nombres: no vacíos, únicos entre sí; `fast`/`archive` son los únicos
  `tier`; las rutas expandidas también deben ser únicas (dos nombres sobre
  el mismo directorio ensucian el diff de sincronización).
- `path` es una **ruta local estricta**: error explícito si el valor
  contiene `://`, con mensaje que indique que los volúmenes remotos no
  están soportados todavía (Decisión 9). En memoria siempre `pathlib.Path`
  absoluta, con las reglas de expansión de siempre.
- `save_config` conserva su carácter conservador y **acepta
  `version = 2`**: solo escribe `[library].path`, y **rehúsa hacerlo en
  cualquier fichero v2** (con o sin lista: crearía la sintaxis prohibida).
  El diálogo de primera ejecución persiste `[library].path` en ficheros
  v1, como hoy. Las ubicaciones v2 se editan a mano.
- La regla Apple de metadatos externos se aplica **por ubicación**
  (extensión del ADR 0009): el enunciado completo vive en la Decisión 2,
  junto a la cadena que valida.

### 2. Resolución: la config es la única fuente de ubicaciones

| Fuente | Hoy | Con este ADR |
| --- | --- | --- |
| `--library PATH` | una ruta | **desaparece** de los cuatro binarios (argparse lo rechaza; el epílogo enseña la vía nueva) |
| `FOTO_LIBRARY` | una ruta | **desaparece**; si está definida, **error explícito** que explique que las ubicaciones se declaran en `config.toml` (nunca ignorarla en silencio) |
| `--location`, `--offline`, `FOTO_LOCATIONS` | — | no existen |
| `--metadata` / `FOTO_METADATA` / `[metadata].path` | ADR 0008 | **sin cambios**: un solo directorio de metadatos para todas las ubicaciones |
| `--config` / `FOTO_CONFIG` | ADR 0001 | sin cambios |

En `storage/resolver.py`:

- Nueva `resolve_locations()` → lista de `Location(name, path, tier)` **solo
  desde la config** (v1 = única `default`, `tier = "fast"`). Sin parámetro
  de biblioteca ni entorno inyectable de rutas.
- `resolve_library_path` **se retira** (sus niveles CLI/env ya no existen);
  los cuatro llamadores migran. Los tests dejan de inyectar la fototeca por
  entorno y usan configs de fixture con `--config`.
- La cadena de metainformación (ADR 0008) queda intacta en niveles y gana
  una regla de default: con **una sola** ubicación declarada, el default es
  `<ubicación>/.foto` (compatibilidad total, incluida la regla Apple del
  ADR 0009 aplicada a esa ubicación); con **varias** y sin override es
  **`ConfigError`** que pida concretar la metainformación (`--metadata`,
  `FOTO_METADATA` o `[metadata].path`). Ambigüedad prohibida.
- **Metainformación en ubicación de archivo es error duro:** si la
  metainformación resuelta (por cualquier nivel) cae dentro de una
  ubicación `tier = "archive"`, `LibraryError` con mensaje claro que pida
  reubicarla. La garantía «el catálogo siempre disponible» no se negocia;
  sin esta validación, un `[metadata].path` dentro del USB anularía la
  tolerancia (§ Disponibilidad). Coherente con la regla dura del paquete
  Apple del ADR 0009.
- **Regla Apple por ubicación (extensión del ADR 0009):** la validación de
  metadatos externos pasa de comparar contra la raíz única a comparar
  contra **cada ubicación declarada**: si la metainformación resuelta —por
  cualquier nivel de la cadena— cae dentro de una ubicación cuya raíz es
  un paquete `.photoslibrary`, error duro (`LibraryError`). Cubre el
  default de una sola ubicación y el caso multi-ubicación; sin esta
  extensión, un `[metadata].path` dentro de un paquete Apple con varias
  ubicaciones no lo detectaría nadie.
- `foto-export` no necesita guardia multi-ubicación (solo leía la raíz para
  derivar la metainformación). `foto-import` sí tiene una regla nueva: ver
  Decisión 8.

### 3. Sincronización config ↔ catálogo al arrancar `foto-index`

`foto-index` es el único lector de la declaración para indexar. Al arrancar
— tras resolver la metainformación, abrir (y migrar si toca) el catálogo y
dentro del lock del indexador — compara las ubicaciones declaradas en la
config con la tabla `locations` del catálogo. La identidad de una ubicación
es su `name` (estable; `path` y `tier` pueden cambiar):

- **Alta** — nombre declarado que no está en el catálogo: se registra con
  estado `active` y se escanea en esa misma ejecución (si está disponible).
- **Cambio** — nombre declarado que ya está `active` con otro `path` o
  `tier`: se actualiza, con aviso en el resumen. Las filas de fotos siguen
  siendo válidas (`rel_path` + `location_id`); el escaneo reconciliará lo
  que haya en la ruta nueva.
- **Baja** — nombre del catálogo `active` que ya no está en la config: la
  ubicación pasa a `deleted` (con `deleted_at`) y **sus filas de fotos se
  marcan `deleted`**. No se borra nada: ni filas ni ficheros. La limpieza
  de filas es de `--prune` (Decisión 5). Quitar una ubicación de la config
  **jamás borra ficheros** (regla 1 del AGENT.md).
- **Reactivación** — nombre declarado que está `deleted`: vuelve a `active`
  con aviso. No restaura filas por sí sola: las restaura el escaneo de esa
  ubicación (ficheros vistos → `present`). Si `--prune` ya las había
  limpiado, el escaneo las repuebla: el catálogo es derivable.
- **Renombrar** es baja + alta del mismo diff, sin caso especial; el
  escaneo repuebla por hash (deduplicación natural).

Propiedades: idempotente (dos ejecuciones seguidas con la misma config no
cambian nada), determinista y reportada — `--dry-run` calcula el diff sin
escribir; `--json` gana un bloque `locations` con `added`, `removed`,
`reactivated`, `path_changed`, `scanned` y `unavailable`.

Casos límite con tests obligatorios:

- Catálogo v5 (sin ubicaciones) + config que declara **una** ubicación: la
  migración v6 asigna todas las filas heredadas a esa ubicación **con su
  nombre real** (no se inventa `default`; ver Decisión 4).
- Catálogo v5 + config que declara **varias**: error explícito pidiendo
  ejecutar primero con la ubicación original declarada sola, porque las
  filas heredadas no saben a qué raíz pertenecen.
- Una ubicación ausente **nunca** pasa a `deleted` por estar desconectada:
  la baja solo la provoca la config. Ausencia = aviso y degradación
  (Decisión 6).

### 4. Catálogo: esquema v6 y retirada de `library.toml`

- **La tabla per-foto `locations` se renombra a `files`** (es lo que
  contiene: apariciones de fichero) y el nombre `locations` queda reservado
  para la tabla nueva de ubicaciones con nombre. La clave primaria de
  `files` pasa a ser compuesta: **`(location_id, rel_path)`** — hoy
  `rel_path` es PK; `seen`, `mark_missing` y `get_location` migran al par.
- Tabla nueva `locations`: `id`, `name` único, `path`, `tier`,
  `state` ∈ `active|deleted`, `created_at`, `deleted_at`,
  `last_scanned_at`.
- `files` gana `location_id` (FK) junto a `rel_path`; el estado por fila
  gana `deleted` (`present|missing|deleted`). No hace falta `deleted_at`
  por fila: la ventana de poda usa el `deleted_at` de la ubicación.
- Migración v5 → v6: renombra, crea la **ubicación declarada única con su
  nombre real** de la config (si hay varias declaradas, el caso límite de
  la Decisión 3 da error antes) y asigna todas las filas a ella. Rutas
  relativas intactas: sobreviven a traslados como hasta ahora. `default`
  solo aparece cuando la config es v1.
- **Numeración: este ADR toma `SCHEMA_VERSION = 6`.** El
  [ADR 0009](0009-indexado-apple-photos.md) (Apple Photos) había definido
  también un «esquema v6» (columna `photos.apple_uuid`); como su
  implementación llega después, pasa a **v7** con migración desde este
  v6, y así queda marcado en el propio ADR 0009 (nota de corrección,
  2026-11-18). Sus reglas no cambian: solo el número.
- Ubicación canónica por foto: **el `tier` es el criterio primario**
  (`fast` gana a `archive`) y el menor `rel_path` solo desempata dentro del
  mismo tier. Consecuencia del esquema: si una foto está en los dos discos,
  la UI lee del SSD.
- El escáner registra `(location_id, rel_path)`; el estado `present` es por
  ubicación, no global. `photos` no cambia de clave; `--prune` sigue
  limpiando fotos huérfanas (cero filas en `files`) como hoy.
- **`library.toml` desaparece.** El descriptor deja de existir — no se
  crea, no se lee, no se valida — y la tabla `locations` del catálogo pasa
  a ser la única referencia de ubicaciones; la coherencia del sistema pasa
  a ser «config ↔ catálogo», responsabilidad de la sincronización de la
  Decisión 3. Consecuencias cerradas:
  - **Bootstrap sin descriptor:** `ensure_library` desaparece; los binarios
    crean/abren directamente el catálogo en el directorio de metainformación
    resuelto, con la creación atómica del directorio y el lock de escritor
    único como salvaguarda.
  - **El `id` (uuid) se elimina:** nada lo consume fuera de los tests del
    descriptor; si una feature futura necesita identidad estable, la
    reintroducirá con su propio diseño.
  - **Guard de biblioteca preparada:** la UI pasa a apoyarse en la
    existencia del catálogo (y su esquema v6): «No hay catálogo. Ejecuta
    foto-index», sustituyendo al guard del descriptor.
  - **Descriptores existentes:** los `library.toml` ya presentes en
    `.foto/` se ignoran (huérfanos); no se borran automáticamente.
  - **Coherencia perdida, riesgo aceptado:** sin `root`, dos fototecas
    apuntando a la misma metainformación por error dejan de detectarse como
    error duro al abrir. La sincronización de la Decisión 3 hace visible la
    divergencia (altas y bajas masivas en el resumen/`--json`); se documenta
    como riesgo abierto.

### 5. Indexado y `--prune` multi-ubicación

- `foto-index` recorre **solo las ubicaciones declaradas y disponibles** y
  actualiza únicamente las filas de esas ubicaciones. Una ubicación
  declarada ausente: aviso por stderr/`--json` y salida 0 (no es error); el
  epílogo lo documenta.
- **Regla de seguridad (no negociable):** `--prune` solo borra filas del
  catálogo —**nunca ficheros**— y solo de (a) ubicaciones verificadas
  presentes en esa ejecución y (b) ubicaciones `deleted` (dadas de baja por
  la config, Decisión 3). Nunca toca filas de ubicaciones declaradas pero
  no escaneadas por estar ausentes.
- Las filas `deleted` usan la **misma ventana de gracia `--prune-after`**
  que las `missing`, para que una edición desafortunada de la config sea
  reversible sin rescan.

### 6. Disponibilidad y tolerancia

Estados por ubicación, evaluados con una **función pura e inyectable
compartida en `storage/`** (sin Qt) que usan `foto-index` antes de escanear
y la UI al cargar; no en cada acceso a fichero:

- `available` — la raíz existe y es directorio.
- `unavailable` — no existe (USB desmontado): aviso y degradación.

Solo hay dos estados: **no existe el marcador de volumen** (`.foto-volume.toml`
descartado; tampoco existe el estado `mismatch`). Mantener la regla 1 del
AGENT.md (ninguna escritura en la fototeca fuera de `.foto/`) intacta.
Consecuencia aceptada y documentada: si se monta **otro** volumen en el
mismo punto de montaje, el escaneo se comporta con normalidad — muchas
filas pasan a `missing` y muchas apariciones nuevas dan de alta — y,
transcurrida la ventana `--prune-after`, `--prune` limpia las filas
ausentes y **borra en cascada las fotos huérfanas con sus etiquetas**
(datos no derivables, ADR 0006); solo una copia previa de `foto-export`
permite recuperarlas. Riesgo aceptado, sin salvaguardia adicional: la
ventana `--prune-after` es la única mitigación.

Comportamiento tolerante:

- **Catálogo/lock:** si la metainformación no está disponible, error duro
  (es la fuente de verdad única de todas las ubicaciones; sin ella no hay
  sesión). Cualquier otra ubicación ausente no bloquea.
- **UI:** banner de estado («Ubicación «usb» no disponible:
  /Volumes/FotoVault. 1 234 fotos no visibles») y las fotos de esa
  ubicación como *placeholders* fantasma con fecha y etiquetas del
  catálogo; miniatura desde caché si existe. Favoritar/etiquetar sigue
  funcionando (todo es metadatos, ADR 0006). Las acciones que exigen el
  fichero (ampliar, ver a tamaño real) se desactivan con mensaje claro.
  El refresco de disponibilidad es **por acción manual** (reabrir o
  recargar); sin watcher en `/Volumes`.
- **CLIs export/import:** operan sobre el catálogo; las ubicaciones
  ausentes se informan, no detienen.

### 7. UI: el catálogo es la fuente de verdad

- La UI **no resuelve ubicaciones ni las lista**: abre el catálogo por la
  cadena de metainformación y de la BD salen las ubicaciones (nombre,
  tier, ruta, estado, nº de fotos), las fotos, las **rutas absolutas**
  (join `files.location_id` → `locations.path`) y las etiquetas.
  `load_from_catalog` pierde el parámetro de raíz única.
- **Carve-out de la regla 2 del modelo** (quién lee la declaración): solo
  `foto-index` la lee para indexar; los resolutores de metainformación la
  consultan para el default (contar ubicaciones: una → default derivado;
  varias → error pidiendo override); la UI solo pregunta si hay declaración
  o no (diálogo de primera ejecución) y jamás la lista ni la resuelve.
  Sin este carve-out ni el diálogo ni el default serían implementables.
- La config solo interviene en la UI en la cadena de metainformación y en
  el diálogo de primera ejecución, que solo actúa sin config o con
  ficheros v1 (persiste `[library].path`, una única ubicación `default`,
  como hoy); con una config v2 sin ubicaciones no hay diálogo: error
  explícito que pida editar la lista a mano (Decisión 1). La UI nunca
  escribe `[[library.locations]]`.
- Fotos de ubicaciones `deleted`: no se muestran (pendientes de `--prune`);
  re-declarar la ubicación y escanear las recupera. Una ubicación
  reactivada cuyo escaneo aún no ha corrido se muestra vacía (sus filas
  siguen `deleted` hasta que el escaneo las repuebla).
- Panel/indicador de ubicaciones con estado (nombre, tier, ruta, estado,
  nº de fotos), accesible desde el banner y `:help`.
- Los *placeholders* fantasma solo existen en la vista: el catálogo no
  cambia (nada de marcar «ausente» permanentemente por una desmontada).

### 8. `foto-import`: exige catálogo existente

Con `ensure_library` retirado, `foto-import` **no crea nada**: si no hay
catálogo en el directorio de metainformación resuelto, termina con error
(código 2) pidiendo ejecutar `foto-index` primero. Con catálogo, el merge
aditivo idempotente del ADR 0007 no cambia. `foto-export` no tiene guardia
nueva: solo lee el catálogo por la cadena de metainformación.

### 9. Horizonte: ubicaciones genéricas (sin rediseño futuro)

Lo que **esta** feature exige — no implementa — para que cada escenario
futuro se resuelva añadiendo (o declarando) una ubicación en la config:

1. **Vídeo (fotovideoteca):** las ubicaciones no asumen «fotos» — nada de
   extensiones, filtros de imagen ni conceptos de foto acoplados a la
   configuración o al esquema de ubicaciones. La clasificación por tipo de
   medio es asunto del escaneo y del catálogo (otra feature).
2. **Apple Photos como ubicación:** la política de escaneo del ADR 0009
   (solo `originals/`) pasa a aplicarse **por ubicación**; la validación de
   paquete de los CLIs pasa de global a por ubicación. Detección
   **automática por sufijo** (`is_apple_photos_library`), sin campo `kind`
   en la configuración. La metainformación externa del paquete queda
   garantizada por las reglas de la Decisión 2.
3. **Volúmenes remotos (S3 u otro):** la comprobación de disponibilidad se
   encapsula tras una abstracción por ubicación (en el futuro, «disponible»
   será «el backend responde»); no se añaden operaciones que solo tengan
   sentido en disco local; la caché de miniaturas se mantiene como pieza
   independiente del volumen donde vivan las fotos. El campo `path` queda
   como ruta local en v2; el soporte de esquemas (`s3://…`) se reserva para
   el futuro (error explícito si aparece hoy).

### 10. Fuera del ámbito de esta decisión

- Sincronización automática en segundo plano entre discos.
- Trasladar ficheros entre ubicaciones (política de emplazamiento,
  traslado seguro, `foto-rebalance`): diferido a una feature futura; esta
  feature solo prepara la base (`tier`).
- Soporte real de volúmenes remotos y de vídeo.
- Borrar ficheros al quitar una ubicación de la config: nunca.
- Flags de simulación de disponibilidad; los tests usan configs de fixture
  con rutas inexistentes.
- Dependencias nuevas de runtime.

## Reglas de implementación

- La precedencia y las reglas de default viven en **un solo sitio**:
  `storage/resolver.py`. Ningún módulo lee `FOTO_LIBRARY` (ya inexistente
  como fuente: su presencia es error explícito), `FOTO_METADATA` ni el
  `config.toml` por su cuenta.
- Sin Qt en la resolución, la sincronización, la evaluación de
  disponibilidad ni el catálogo; la UI solo muestra resultados.
- `pathlib` siempre; en memoria solo rutas absolutas; nada de concatenar
  strings para construir rutas.
- Escrituras atómicas y lock de escritor único como hasta ahora; la
  sincronización corre **dentro del lock** del indexador, antes de
  escanear.
- Errores explícitos (`ConfigError`, `LibraryError`), nunca silencio:
  `FOTO_LIBRARY` definida, v2 con `[library].path`, `[[library.locations]]`
  en v1, metainformación ambigua o en ubicación `archive`/paquete Apple,
  catálogo v5 con config multi-ubicación, config v2 sin ubicaciones al
  abrir la UI (sin diálogo: la lista se edita a mano).
- `--help` de los cuatro binarios documenta la cadena nueva y los epílogos
  enseñan los ejemplos sin `--library`.
- Tests: migración v5→v6 (nombre real y caso multi), sincronización
  (alta/cambio/baja/reactivación/renombrado, idempotencia, `--dry-run`,
  `--json`), prune adversarial, disponibilidad, desempate canónico por
  tier, UI con rutas desde la BD, y el estado actual v1 fijado antes de
  tocar código (paso 1 del prompt).

## Consecuencias

**Positivas**

- Fototeca repartida entre discos con avisos explícitos y sin pérdida de
  metadatos cuando el USB no está.
- Una sola fuente de ubicaciones (la config) y una sola verdad (el
  catálogo); se elimina el descriptor y su segundo fichero de verdad.
- Los casos v1 siguen funcionando sin migración para el dueño.
- Base preparada para vídeo, Apple Photos y S3 sin rediseño.

**Negativas**

- `--library` y `FOTO_LIBRARY` desaparecen: rompe scripts y cron existentes
  (error explícito con la vía nueva, pero rotura al fin).
- El catálogo migra a v6 (renombre de tabla y PK compuesta: trabajo de
  migración real).
- Sin `root`, se pierde la detección dura de «esta metainformación
  pertenece a esta fototeca»; mitigada (no eliminada) por la visibilidad de
  la sincronización.
- Quitar una ubicación de la config marca sus fotos `deleted`; si el dueño
  pasa del `--prune-after` sin darse cuenta, el prune limpia las filas
  (recuperables rescan; los ficheros jamás se borran).

**Riesgos abiertos**

- Volumen distinto montado en el mismo punto: riesgo aceptado y documentado
  (Decisión 6); la única mitigación es la ventana `--prune-after`.
- Config editada a mano: la validación de v2 cubre los errores sintácticos,
  pero una ruta mal escrita solo se manifiesta como ubicación ausente
  (aviso, degradación) — comportamiento correcto por diseño.
- La regla Apple por ubicación exige que la validación de paquetes de los
  CLIs se rehaga por ubicación en el paso 3; hasta entonces, multi-ubicación
  con paquetes Apple queda cubierta por la guardia transitoria del paso 2.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Tabla nueva `volumes` en lugar de renombrar `locations` → `files` | Deja el nombre `locations` mal usado para siempre; el renombre es lo que la tabla contiene (apariciones de fichero). |
| Mantener `library.toml` con `root` solo para ubicación única | Doble criterio de coherencia según el caso y un segundo fichero con verdad sobre la biblioteca; el catálogo ya la tiene toda. |
| Trasladar el `id` del descriptor al catálogo | Nada lo consume fuera de los tests; reintroducir identidad estable es trivial si una feature futura la necesita. |
| Componer el `apple_uuid` del ADR 0009 dentro de este v6 | Acopla dos features con ritmos de implementación distintos; el número es barato, la coordinación no. Renumeración por orden de ejecución: v6 aquí, v7 en Apple. |
| Marcador de volumen `.foto-volume.toml` + estado `mismatch` | Exigía escribir fuera de `.foto/` (contra la regla 1 del AGENT.md), bucle huevo-gallina en ubicaciones nuevas/migradas y un uuid sin sitio donde vivir. Descartado: solo `available`/`unavailable`. |
| Metainformación en ubicación `archive` con solo aviso | Anula la garantía «el catálogo siempre disponible» (el USB desmontado dejaría el sistema sin sesión). Error duro, como la regla Apple. |
| v2 con `[library].path` suelto equivalente a `default` | Dos sintaxis para lo mismo en v2; ambigüedad prohibida. |
| `foto-import` crea el catálogo si falta | El bootstrap del catálogo es trabajo del indexador (migración v6, sincronización inicial); crearlo desde import duplicaría el arranque y ocultaría errores de config. |
| Watcher en `/Volumes` para refrescar disponibilidad | Fuera del objetivo de esta feature; la acción manual basta. |
| `FOTO_LIBRARY` ignorada en silencio | Ocultaría una fuente de configuración que ya no existe: rompería scripts sin explicación. Error explícito. |
| `path` con esquema (`s3://…`) desde v2 | El backend remoto es una feature propia; validar `://` como error da mensajes claros hoy. |
| Declarar ubicaciones por CLI/entorno (`--location`, `NOMBRE=ruta`) | Segunda vía de verdad que el diff de sincronización no puede reconciliar con fiabilidad; la config es la única fuente. |

## Pendiente

- Feature futura: política de emplazamiento, traslado seguro y
  `foto-rebalance` (hereda las correcciones ya registradas en el prompt:
  fila del fichero de origen — nunca «la ubicación de origen» — y
  exclusión de paquetes Apple como origen/destino).
- Feature futura: backend de volúmenes remotos (S3), con la abstracción de
  disponibilidad de la Decisión 9 (encapsulación por ubicación, sobre la
  función pura inyectable de la Decisión 6).
- Feature futura: vídeo/fotovideoteca.
- Paso 2 de la entrega: guardia transitoria «multi-ubicación llega en el
  paso 3/4» en `foto-index` y la UI (se retira ahí).
