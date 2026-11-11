# Prompt — Etiquetas (tags): ADR 0006 y entrega en dos pasos

> Este prompt integra el **informe de estudio** de las etiquetas y las
> **decisiones del proyectista** (ambos del 2026-11-10), y los sustituye: es
> autocontenido. La misión ya no es estudiar: es redactar el ADR 0006 e
> implementar la entrega.

## 0. Misión

Implementa en el repositorio `foto` el sistema de **etiquetas (tags)**: un
mecanismo único con tipos (`kind`) y procedencia por asignación, que sustituye
el favorito en memoria por la etiqueta de sistema `fav`. La entrega incluye
el **ADR 0006**, que se redacta al comienzo del paso 1, antes de tocar código,
y se muestra al proyectista antes de implementar.

Se ejecuta en **dos pasos diferenciados**, en este orden, cada uno terminado y
verificable por separado:

1. **Paso 1 — Catálogo e indexador** (`src/foto/storage/`, `src/foto/core/`,
   `src/foto/indexer/`): esquema v4 (`tags` + `photo_tags`) con migración
   idempotente, creación de la etiqueta `fav`, módulo de etiquetas en
   `storage/` sin Qt y los efectos automáticos en la reconciliación. Al
   terminar: `foto-index` migra catálogos v1–v3 a v4 y crea `fav`, probado y
   verificado **sin tocar la UI**.
2. **Paso 2 — Visor** (`src/foto/ui/`): `f` persiste el favorito como
   asignación de `fav`, el panel de metadatos muestra las etiquetas, el
   documento de búsqueda las incluye (con sus ancestros) y `:tag <nombre>`
   filtra la cuadrícula. **No se toca nada del indexador en este paso.**

Resumen del comportamiento pedido:

- Una **única tabla de etiquetas** cubre personas, lugares, eventos, favoritos
  y etiquetas libres: mismo objeto, distinto `kind`.
- Cada asignación foto–etiqueta guarda **procedencia**: origen
  (`manual`/`ml`), estado (`confirmed`/`suggested`/`rejected`), confianza y
  región opcional. El ML es futurible: las columnas nacen ahora, su uso no.
- El favorito actual (solo memoria de la UI) **desaparece por completo**:
  `f` conmuta la asignación de `fav` en el catálogo y sobrevive a reiniciar.
- La geolocalización del ADR 0005 es un **sistema aparte**: `place_*` no
  genera ni recibe etiquetas.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, stack, convenciones y flujo. Fíjate
   en la regla 2 (XMP como fuente de verdad): esta entrega la rectifica
   (§ 2.1).
2. `README.md` — alcance, principios (revisa «Metadatos abiertos»,
   «Componentes reemplazables» y el roadmap: hay que cambiarlos), arquitectura.
3. `docs/adr/0001...0005` — en su totalidad; fíjate en el formato de
   redacción (Estado, Fecha, Decide, Ámbito, Contexto, Decisión numerada,
   Consecuencias, Alternativas descartadas, Decisiones pendientes), en las
   notas de revisión del ADR 0002 y en el patrón de dos pasos del 0005. El
   ADR 0002 (§ 3 y § 10) anticipa las tablas de tags con `photo_hash` como
   clave foránea; el ADR 0003 recibe una enmienda en esta entrega (§ 3.10).
4. `docs/diseno-ui.md` — reglas de interfaz, teclas y comandos `:`.
5. Código real: `src/foto/storage/catalog.py` (esquema, `user_version`,
   migraciones, `open_read_only`), `src/foto/core/indexing.py` (pipeline de
   fases, rehash y `--prune`), `src/foto/core/library.py` y
   `src/foto/ui/models.py` (`favorite`), `src/foto/ui/window.py`
   (`_toggle_favorite`, `_apply_filter`), `src/foto/ui/presentation.py`
   (documento de búsqueda), `src/foto/ui/commands.py`, `src/foto/ui/grid.py`
   (`FAVORITE_ROLE`), `src/foto/ui/app.py` (carga desde el catálogo).

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible. No hagas `git commit`, `git push` ni cambios de
configuración de git sin permiso explícito.

---

## 2. Cambio de rumbo ya decidido (no se discute)

### 2.1. XMP deja de ser la fuente de verdad

- El **catálogo SQLite es la fuente de verdad de las etiquetas y los
  favoritos**. La exportación a XMP queda en el roadmap como futurible; la
  importación no existe y no se contempla.
- Esto rectifica el principio «Metadatos abiertos» de `README.md`, la regla
  2 de `AGENT.md` y el feedback de favoritos de `docs/diseno-ui.md` (§ 12).
- Consecuencia asumida: **el catálogo deja de ser 100 % reconstruible**.
  Borrar `catalog.sqlite` pierde las etiquetas; el ADR lo recoge como
  consecuencia negativa y lo encadena con la copia de seguridad (fase 3 del
  roadmap). En lo que es función del contenido (fotos, ubicaciones,
  metadatos, lugares) sigue siendo reconstruible como hasta ahora.

### 2.2. Decisiones del proyectista (estudio, 2026-11-10)

1. **Escritura del visor con conexión RW dedicada**, sin tomar `index.lock`;
   WAL + `busy_timeout` serializan con el indexador. El ADR 0003 recibe una
   enmienda (§ 3.10).
2. **El visor tiene prohibido escribir en `photos` y `locations`**: su
   conexión de escritura queda acotada a `tags` y `photo_tags`.
   Reconciliar hashes es trabajo de `foto-index`, nunca de la UI.
3. **De la sintaxis de etiquetado solo está decidida la tecla `f`**, que
   asigna y quita `fav`. No se añaden teclas ni comandos de asignación en
   esta entrega; la sintaxis general queda pendiente (§ 13).
4. **Filtro de la cuadrícula por etiquetas:** el documento de búsqueda `/`
   incluye las etiquetas **y** existe un filtro explícito `:tag <nombre>`
   (§ 8.4).
5. **Identidad estricta de las asignaciones**: están ligadas al hash de
   contenido y nunca se trasladan entre hashes (§ 3.9).

---

# Paso 1 — Catálogo e indexador

## 3. Decisiones vinculantes del modelo de datos

Estas decisiones ya están tomadas. No las cambies sin justificarlo y sin
actualizar el ADR.

### 3.1. Un mecanismo único, con `kind`

- Dos tablas: `tags` (qué existe) y `photo_tags` (qué foto lleva qué
  etiqueta, y con qué procedencia). Personas, lugares, eventos, favorito y
  etiquetas libres son el mismo objeto con distinto `kind`; así, más
  adelante, la tabla de atributos de personas cuelga de `tags` sin migrar
  nada.
- `kind ∈ ('system', 'person', 'place', 'event', 'free')` con `CHECK` y
  `DEFAULT 'free'`. Ampliar el vocabulario exigirá reconstruir la tabla en
  SQLite: coste conocido y aceptado a cambio de un catálogo inspeccionable.
- `fav` es **la única etiqueta de sistema**: se resuelve siempre por
  `kind='system'`, **jamás por `id`** (los `AUTOINCREMENT` varían entre
  bibliotecas). Ni se renombra ni se borra; el esquema lo garantiza con un
  índice único parcial (§ 3.7).
- Claves surrogadas `INTEGER PRIMARY KEY AUTOINCREMENT` en ambas tablas
  (estilo `scan_runs`/`run_phases`), y `photo_tags.photo_hash TEXT`
  referenciando `photos(hash)`: el ADR 0002 descartó ids enteros de foto.
- `created_at`/`updated_at` `NOT NULL` en ambas tablas, como `photos` y
  `locations`. `photo_tags.created_at` es la marca de «recién etiquetadas»;
  no se añade índice sobre ella hasta que exista una vista que lo use.
- La geolocalización (ADR 0005) queda **desconectada**: sin FKs, triggers
  ni generaciones automáticas entre `place_*` y `tags`. `kind='place'` es
  solo una etiqueta que el usuario pondrá a mano.

### 3.2. Nombres y normalización

- `name_normalized` se calcula al crear: **NFC + casefold + espacios en
  blanco colapsados a uno + strip**. Función propia del módulo de
  etiquetas, pura y testeada.
- **Sin plegar acentos**: `año 2024` ≡ `AÑO 2024`, pero `año 2024` ≢
  `anio 2024` (el plegado fusionaría personas distintas; `ñ` es letra).
- Unicidad **global** de `name_normalized`: no por `kind` ni por rama
  («Lisboa» persona y «Lisboa» lugar serían la misma etiqueta).
- `name` no puede estar vacío ni ser solo espacios; sin saltos de línea.
- Los alias quedan cubiertos por `merged_into`; no hay tabla de alias.
- Renombrar (cuando exista la operación) recalcula `name_normalized`; una
  colisión es un error que sugiere fusionar. Se redacta en el ADR; no se
  implementa (§ 13).

### 3.3. Jerarquía (`parent_id`)

- **Padre único**, opcional (viajes > 2024 > Lisboa). Multi-padre
  descartado.
- Ciclos prohibidos: al re-padrear (cuando exista la operación) se comprueba
  la cadena de ancestros; barato porque la taxonomía es personal. Se redacta
  en el ADR; no se implementa (§ 13).
- `ON DELETE RESTRICT`: no se borra un nodo con hijos ni un destino de
  fusión; primero se re-padrean o borran los hijos. Borrar una etiqueta con
  asignaciones arrastra las asignaciones (`CASCADE` en `photo_tags.tag_id`)
  y exigirá confirmación explícita cuando exista la operación: es la
  primera acción destructiva del proyecto.
- **Herencia solo en filtrado**: asignar «Lisboa» no crea filas de
  «Viajes»; filtrar «Viajes» encuentra todo lo asignado a sus
  descendientes (§ 8.4). En el orden y los grupos no hay herencia alguna.

### 3.4. Fusión y alias (`merged_into`)

- Fusión **lógica**, no física; **un solo salto**. El destino debe ser una
  etiqueta viva: no fusionada ni ella misma (evita cadenas y ciclos de 2).
- Las filas de `photo_tags` no se mueven; la resolución es de la consulta,
  que deduplica `(foto, etiqueta resuelta, región)`.
- Deshacer una fusión es poner `merged_into` a `NULL`: el historial de qué
  etiqueta puso cada fila nunca se pierde.
- Se redacta en el ADR; no se implementa (§ 13).

### 3.5. Procedencia y ciclo de vida (`source`, `status`, `confidence`)

- Las columnas nacen **desde el día uno** aunque sin ML solo ocurra
  `manual`/`confirmed`: añadirlas después exigiría reconstruir una tabla ya
  poblada, y el encargo de esta entrega es fijar la forma de los datos para
  el ML.
- `source ∈ ('manual', 'ml')`; `status ∈ ('confirmed', 'suggested',
  'rejected')`; `confidence REAL ∈ [0,1]` opcional, solo se escribe con
  `source='ml'` (regla de código; el `CHECK` de rango basta en SQL).
  **No existe el valor `xmp_import`**: no hay importación, y el `CHECK`
  cerrado es el que hace inspeccionable el catálogo.
- Las asignaciones manuales nacen directamente `confirmed`.
- `suggested → confirmed` actualiza la misma fila; no se pisa `source`
  (queda el origen: sugerida por ML, confirmada por humano) ni `created_at`
  (cuándo se sugirió). La fila es el estado; no hay tabla de historial.
- `rejected` existe desde el principio aunque nada lo produzca aún: su papel
  es **bloquear re-sugerencias**. La selección futura del ML serán las
  parejas foto–etiqueta **sin fila** en `confirmed` ni `rejected`;
  re-activar una sugerencia es borrar la fila `rejected`, decisión explícita
  del usuario.
- Los filtros cuentan `confirmed` + `suggested`; `rejected` nunca. Fijo,
  sin configuración hasta que exista el ML.

### 3.6. `region` y caras

- Rectángulo **proporcional [0,1]** respecto a la imagen **orientada tal
  como se muestra** (EXIF aplicado): origen arriba-izquierda, `x` hacia la
  derecha, `y` hacia abajo. Cuatro columnas `REAL` (`region_x/y/w/h`),
  todo-o-nada (o las cuatro o ninguna), `w,h > 0`. El código recorta a
  [0,1] al escribir; el `CHECK` defiende. Píxeles descartados: dependen de
  la resolución del original.
- «Cara» es una **convención** (etiqueta de persona con región), no un
  constraint del esquema: el esquema no liga `region` a `kind`. La misma
  persona puede convivir en la misma foto con y sin región (grupo y
  retrato); la unicidad es por región (§ 3.7).
- El visor no pinta regiones en esta entrega; solo queda definido el dato.
  La región no se carga en el modelo de UI todavía.

### 3.7. Esquema SQL final (bump de `PRAGMA user_version` a 4)

```sql
CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
    name_normalized TEXT NOT NULL UNIQUE,  -- NFC + casefold + espacios colapsados
    kind            TEXT NOT NULL DEFAULT 'free'
                    CHECK (kind IN ('system', 'person', 'place', 'event', 'free')),
    parent_id       INTEGER REFERENCES tags(id) ON DELETE RESTRICT,
    merged_into     INTEGER REFERENCES tags(id) ON DELETE RESTRICT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS tags_parent_idx ON tags(parent_id);

-- 'fav' es la única etiqueta de sistema; el esquema lo garantiza.
CREATE UNIQUE INDEX IF NOT EXISTS tags_system_singleton_idx
    ON tags(kind) WHERE kind = 'system';

CREATE TABLE IF NOT EXISTS photo_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_hash  TEXT NOT NULL REFERENCES photos(hash) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'manual'
                CHECK (source IN ('manual', 'ml')),
    status      TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (status IN ('confirmed', 'suggested', 'rejected')),
    confidence  REAL CHECK (confidence IS NULL
                            OR (confidence >= 0.0 AND confidence <= 1.0)),
    region_x    REAL,                       -- [0,1]; NULL en las cuatro = foto completa
    region_y    REAL,
    region_w    REAL,
    region_h    REAL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((region_x IS NULL AND region_y IS NULL
            AND region_w IS NULL AND region_h IS NULL)
        OR (region_x >= 0.0 AND region_x <= 1.0
            AND region_y >= 0.0 AND region_y <= 1.0
            AND region_w > 0.0 AND region_w <= 1.0
            AND region_h > 0.0 AND region_h <= 1.0))
);

-- Una asignación por (foto, etiqueta, cara). El COALESCE existe porque en
-- SQLite los NULL no colisionan en los índices únicos: sin él, la
-- asignación de foto completa (región NULL) se podría duplicar.
CREATE UNIQUE INDEX IF NOT EXISTS photo_tags_assign_unique_idx
    ON photo_tags (photo_hash, tag_id,
                   COALESCE(region_x, -1.0), COALESCE(region_y, -1.0),
                   COALESCE(region_w, -1.0), COALESCE(region_h, -1.0));

CREATE INDEX IF NOT EXISTS photo_tags_photo_idx ON photo_tags(photo_hash);
CREATE INDEX IF NOT EXISTS photo_tags_tag_idx   ON photo_tags(tag_id);
```

Después del script, la migración inserta la etiqueta de sistema con la hora
por parámetro y sube la versión:

```sql
INSERT OR IGNORE INTO tags (name, name_normalized, kind, created_at, updated_at)
VALUES ('fav', 'fav', 'system', :now, :now);
PRAGMA user_version = 4;
```

Notas:

- La unicidad de asignaciones es **`(foto, etiqueta, región)`**: el par
  foto–etiqueta **puede** repetirse cuando cada fila lleva una cara distinta
  (dos caras de la misma persona); la asignación de foto completa (región
  `NULL`) es única gracias al `COALESCE`. **Trampa real: test obligatorio.**
- Las operaciones de escritura usan `INSERT OR IGNORE`/`UPDATE OR IGNORE`:
  la unicidad la garantiza el esquema, no el código.
- Al asignar se actualiza `photo_tags.updated_at`; `photos.updated_at` no
  se toca nunca desde el visor (§ 3.10).

### 3.8. Migración

- Bump a `user_version = 4`, idempotente al abrir, en el patrón existente
  (`executescript` con `IF NOT EXISTS` + parámetros); sirve para catálogos
  v1–v3 y vacíos. La aplica solo `foto-index`, como hasta ahora.
- **No hay datos que convertir**: el favorito nunca tuvo columna ni
  persistencia; era memoria de la UI (`favorite: bool` en los modelos). No
  inventes migración de favoritos.
- Un catálogo más nuevo que el binario aborta con el mensaje actual. El
  visor (solo lectura) exige v4 con el mecanismo existente: tras actualizar
  el código hay que ejecutar `foto-index` una vez antes de abrir la app
  (mismo precedente que v3; se documenta en la entrega).
- `--dry-run` sigue funcionando: la copia en memoria se migra igual y no
  escribe en disco.

### 3.9. Indexador y reconciliación

- **Sin fase nueva**: las etiquetas son escritura de usuario, no trabajo
  derivable del pipeline. `scan` y `geocode` no conocen las etiquetas y el
  resumen y el JSON del indexador no añaden contadores de etiquetas
  (privacidad, igual que los lugares en ADR 0005).
- `--prune` al borrar fotos arrastra sus `photo_tags` por el `CASCADE`
  (automático con `foreign_keys=ON`). Las **etiquetas huérfanas se
  conservan**: son taxonomía del usuario (jerarquías, alias), no basura; su
  limpieza será manual cuando exista el explorador (§ 13).
- **Identidad estricta**: las asignaciones están ligadas al hash de
  contenido, como los metadatos. `foto` nunca modifica fotos (originales
  intactos, MVP no destructivo), así que un cambio de contenido solo llega
  desde fuera de la app (edición in place, conversión) y equivale a una
  **foto nueva sin etiquetas**. No se traslada nada entre hashes y la fase
  `scan` no se toca. El hash viejo, huérfano de ubicaciones, muere con el
  `--prune` siguiente y su `CASCADE` se lleva las asignaciones; hasta
  entonces no molesta (sin ubicaciones `present`, la foto no aparece en el
  visor). El ADR lo recoge como consecuencia: editar el original desde
  fuera pierde el etiquetado de esa foto (en esta entrega, su `fav`).
- Huecos que quedan preparados para el ML (nada de fases ni modelos):
  columnas de procedencia (§ 3.5), patrón de selección por parejas, marco de
  fases del ADR 0005 listo para una fase futura que escriba sugerencias, y
  tablas futuras de caras/embeddings **aparte** de `photo_tags`, con
  `photo_hash` y versión de modelo (ADR 0002 § 10).

### 3.10. Escritura del visor (regla que fija el paso 2)

- La ventana mantiene su conexión de solo lectura para cargar y una
  **conexión RW propia y acotada** para etiquetas: módulo nuevo en
  `storage/` (p. ej. `storage/tags.py`), sin Qt; la ventana nunca ejecuta
  SQL.
- Esa conexión no toma `index.lock`; `busy_timeout` (5 s) + WAL serializan
  con los lotes del indexador. Transacciones mínimas: una sentencia por
  operación con commit inmediato, estilo `record_phase`, para no retrasar
  checkpoints ni bloquear lotes del indexador más de lo inevitable.
- Prohibido escribir en `photos`/`locations` desde la UI (§ 2.2). Los
  errores de escritura se muestran, nunca en silencio.
- El ADR 0003 recibe una **enmienda** con origen «ADR 0006»: el modo
  lectura se mantiene para todo lo existente; la escritura de usuario queda
  limitada a `tags`/`photo_tags` vía la conexión dedicada.

## 4. ADR 0006 (redactar antes del código)

Escribe `docs/adr/0006-etiquetas.md` con el formato de casa (Estado, Fecha,
Decide: Jose, Ámbito, Contexto, Decisión numerada, Consecuencias
positivas/negativas, Alternativas descartadas en tabla, Decisiones
pendientes). Título propuesto: **«ADR 0006 — Etiquetas en el catálogo:
tipado, procedencia y favorito»**. Muéstralo al proyectista antes de
implementar.

Índice de decisiones (renumera si hace falta, cúbrelas todas):

1. El catálogo, fuente de verdad de etiquetas y favoritos (rectifica la
   regla 2 de `AGENT.md`, el README y diseno-ui).
2. Un único mecanismo con `kind`; la geolocalización (ADR 0005) queda
   aparte.
3. Esquema `tags` + `photo_tags` (§ 3.7): claves, `CHECK`s, índices (con la
   unicidad `COALESCE` para regiones `NULL`).
4. `fav`, única etiqueta de sistema; resolución por `kind`, nunca por id.
5. `name_normalized`: NFC, casefold, espacios; unicidad global; sin plegar
   acentos.
6. Jerarquía de padre único; herencia solo en filtrado; `RESTRICT` con
   hijos, `CASCADE` con asignaciones y confirmación explícita de borrado.
7. Fusión lógica con `merged_into`: un salto, resolución en consulta con
   dedup, deshacer sin perder historial.
8. Procedencia desde el día uno; transiciones `suggested → confirmed`;
   `rejected` como bloqueo de re-sugerencias; qué cuentan los filtros.
9. Regiones proporcionales [0,1] sobre la imagen orientada; la cara como
   convención; sin detección ni pintado aquí.
10. Sustitución total del favorito: `f` conmuta `fav`, sin coexistencia;
    inventario de referencias migradas (modelos, `presentation`, `grid`,
    `window`, ayuda, tests).
11. Migración v4 idempotente; sin datos que convertir; el visor exige v4.
12. Escritura del visor: conexión RW dedicada acotada, sin `index.lock`;
    enmienda del ADR 0003.
13. Consultas y filtros: carga en memoria al arrancar, documento de
    búsqueda con etiquetas y ancestros, `:tag` con subárbol; coste.
14. Indexador: sin fase nueva; identidad estricta de las asignaciones;
    `CASCADE` con `--prune`; huérfanas se conservan; huecos para el ML.
15. Visor: panel de etiquetas, `f`, `:tag`; lo que queda pendiente.
16. Privacidad: nombres de personas y regiones fuera de logs y resúmenes
    (criterio ADR 0005).
17. Preparado para la exportación XMP (sin diseñarla):
    `free/person/place/event` → `dc:subject`/`lr:hierarchicalSubject`
    (jerarquía), personas con región → `mwg-rs Regions`; sin importación.

Consecuencias negativas que el ADR debe recoger: la pérdida de
reconstruibilidad total (§ 2.1); el coste de ampliar los `CHECK`
(reconstrucción de tabla); el visor exige ejecutar `foto-index` tras
actualizar; los errores de escritura posibles con el indexador corriendo;
que el favorito, antes memoria volátil, pasa a depender del catálogo; y que
un cambio de contenido externo pierde las etiquetas de esa foto (identidad
estricta).

Alternativas descartadas (mínimo, en tabla): tablas separadas por tipo de
etiqueta; XMP como fuente de verdad; tabla de alias aparte de `merged_into`;
jerarquía multi-padre; fusión física reasignando filas; `id` entero de foto;
columna `favorite` en `photos`; valor `xmp_import`; una fase del indexador
para etiquetas; regiones en píxeles; índice único sin `COALESCE`; coexistencia
temporal del favorito viejo y la etiqueta `fav`.

## 5. Diseño esperado del paso 1 (orientativo, no literal)

- `storage/catalog.py` — `SCHEMA_VERSION = 4` y `_migrate_to_v4`
  idempotente con el esquema de § 3.7 y el `INSERT OR IGNORE` de `fav`.
- `storage/tags.py` (o equivalente) — el módulo de etiquetas, sin Qt, con
  type hints, dataclasses congelados y errores propios (nunca
  `except: pass`):
  - `normalize_tag_name(name) -> str` (§ 3.2);
  - lectura por la conexión de solo lectura existente: asignaciones por
    hash (nombre, `kind`, `status`, `source`; la región no se carga aún),
    la lista completa de etiquetas (con `parent_id` y `merged_into`) y el
    subárbol de una etiqueta;
  - escritura por la conexión RW dedicada (§ 3.10): asignar/quitar con la
    semántica `suggested → confirmed` de § 3.5 (un `INSERT OR IGNORE` que
    pisa la fila `suggested` si existe) y el toggle de `fav` (resuelto por
    `kind='system'`);
  - nada de crear/renombrar/re-padrear/fusionar/borrar etiquetas: no
    existen en esta entrega (§ 13).
- `core/indexing.py` — **sin cambios**: ni el pipeline ni la reconciliación
  conocen las etiquetas (los `CASCADE` actúan solos con el esquema).

## 6. Tests del paso 1

Sin Qt ni pantalla, como el resto de `storage/`. Cubre, al menos:

- Normalización: NFC/casefold/espacios; `AÑO 2024` colisiona con `año 2024`
  y no con `anio 2024`; vacíos y solo espacios rechazados.
- Migración: v1/v2/v3 → v4 idempotente (aplicar dos veces); catálogo más
  nuevo aborta; `fav` existe tras el bump y es único; el índice parcial
  impide una segunda `system`; abrir en solo lectura un v3 produce el
  error que pide ejecutar `foto-index`; `--dry-run` funciona sobre la copia
  en memoria migrada.
- Asignaciones: el toggle de `fav` crea y quita la fila con
  `source='manual'` y `status='confirmed'`; la unicidad con `COALESCE`
  impide duplicar la asignación de foto completa; asignaciones de la misma
  persona con regiones distintas coexisten (sembradas por SQL en el test,
  aún sin UI); confirmar una `suggested` actualiza la fila sin pisar
  `source` ni `created_at` (sembrada por SQL).
- Reconciliación: `--prune` de una foto arrastra sus asignaciones; las
  etiquetas huérfanas sobreviven; identidad estricta: al cambiar el
  contenido de una ruta, las asignaciones del hash viejo no se trasladan y
  desaparecen con el `--prune` que lo borra.
- Todo bug corregido lleva su test de regresión.

## 7. Verificación del paso 1

1. `uv sync` — entorno consistente y sin dependencias nuevas.
2. `uv run pytest` y `uv run ruff check .` — toda la suite en verde, sin
   pantalla.
3. `uv run foto-index --library <biblioteca de prueba>` sobre un catálogo
   v3 (real o generado como en la suite): migra a v4 y crea `fav`; una
   segunda pasada es idempotente; el catálogo sigue inspeccionable con
   `sqlite3` (`.schema tags`, una única fila con `kind='system'`).
4. **Criterio de fin del paso 1:** ADR 0006 redactado y aceptado por el
   proyectista; `foto-index` migra y crea `fav`; suite en verde y **la UI
   sin tocar** (sigue funcionando con el modelo actual: el campo
   `favorite` de la UI aún existe y no persiste). No continúes al paso 2
   sin cerrar esto.

---

# Paso 2 — Visor

## 8. Decisiones vinculantes del visor

### 8.1. `f` persiste el favorito

- `f` conmuta la asignación completa (región `NULL`) de `fav` en los
  **cuatro contextos** que hoy despachan la acción `favorite`: rejilla,
  fila del visor, panel y fallback. El inventario de `keybindings.py`, la
  acción y las tablas de teclas **no cambian**; ninguna tecla nueva.
- El modelo de UI deriva `favorite` de sus etiquetas: `Photo` y
  `CatalogPhoto` ganan una tupla inmutable de etiquetas (nombre, `kind`,
  `status`, `source`) y `favorite` pasa a propiedad calculada sobre esa
  tupla: una sola fuente de verdad en el modelo, sin campo duplicado. La
  insignia de la cuadrícula, el nombre accesible («, favorito») y los
  mensajes de la barra («favorito»/«sin favorito») se conservan.
- Feedback optimista: se aplica en memoria, se escribe por la conexión RW
  y, si falla (catálogo ocupado, disco, permisos), se revierte y se
  muestra un **error persistente** (se cierra con `Esc`), nunca silencio.
  Debe funcionar con el indexador corriendo (WAL): pruébalo con un
  escritor lento simulado.
- Por hash: una foto duplicada es favorita en todas sus copias; una sola
  fila de asignación.
- `f` queda operativo también sin catálogo: la ventana recibe un
  colaborador de etiquetas **opcional e inyectado** (`MainWindow` lo crea
  sobre el catálogo; el prototipo pasa `None` y `f` conmuta solo en
  memoria, como hoy). Actualiza helpers y demo: los tests que fabrican
  fotos (`test_presentation`) y `prototype.py` pasan etiquetas donde hoy
  ponen `favorite=True`.

### 8.2. Panel de metadatos

- Sección «Etiquetas» al final de la ficha; una línea por asignación
  (`confirmed` + `suggested`), con el nombre y, solo si aplica: el `kind`
  cuando no es `free`, el estado cuando no es `confirmed` (p. ej.
  «sugerida») y el origen (`machine learning`) solo si `source='ml'`.
- `fav` aparece como una etiqueta más, sin casos especiales: la insignia ya
  comunica el favorito.
- Confirmar/rechazar sugeridas con teclado queda **pendiente** (§ 13):
  hoy no hay sugerencias.

### 8.3. Documento de búsqueda

- `photo_search_text` añade los nombres de las etiquetas resueltas (con
  `merged_into` aplicado: el alias muerto no aparece) **y sus ancestros**,
  contando `confirmed` + `suggested`. Regla pura en `presentation.py`,
  sobre la tabla de etiquetas cargada al arrancar.
- `/fav` encuentra los favoritos (hoy «favorito» no busca nada: ese hueco
  se cierra aquí).

### 8.4. `:tag <nombre>`: filtro explícito

- Normaliza el nombre (§ 3.2), resuelve la etiqueta y expande el
  **subárbol**: filtrar «Viajes» encuentra lo asignado a sus
  descendientes. Cuenta `confirmed` + `suggested`; `rejected` nunca
  (§ 3.5).
- Repetir `:tag <nombre>` sobre la etiqueta activa, o `:tag` sin argumento,
  desactiva el filtro. Un nombre que no existe produce un mensaje en la
  barra. Mientras el filtro está activo, la barra lo indica de forma
  persistente (estilo `/texto`, p. ej. «etiqueta: nombre») y se compone con
  la búsqueda `/` en AND.
- Añádelo al vocabulario (`commands.py`), al dispatcher, a la ayuda
  integrada y a `docs/diseno-ui.md`. El vocabulario futuro de asignación
  no debe colisionar con `:tag`, que queda como filtro (§ 13).

## 9. Diseño esperado del paso 2 (orientativo, no literal)

- `core/library.py` y `ui/app.py` — la carga trae las asignaciones junto a
  los datos actuales (una consulta más por la conexión de solo lectura; una
  entrada por hash) y las etiquetas del modelo.
- `ui/models.py` — tupla de etiquetas y `favorite` derivado;
  `ui/presentation.py` — documento de búsqueda con etiquetas y ancestros, y
  las reglas puras del filtro por etiqueta (subárbol sobre la tabla de
  etiquetas, sin SQL por foto); `ui/panels.py` — sección de etiquetas;
  `ui/window.py` — `f` contra el colaborador de etiquetas (reemplaza
  `replace(photo, favorite=...)` por la reconstrucción de la tupla), el
  estado del filtro `:tag` y su composición con la búsqueda;
  `ui/commands.py` — `:tag`.
- Coste: decenas de miles de fotos y miles de asignaciones caben en la
  carga (una consulta, mapa por hash); el filtrado es en memoria, como
  hoy. Sin Qt en el SQL ni en las reglas; la ventana solo consume
  funciones de `storage/` y de `presentation.py`.

## 10. Tests del paso 2

Offscreen (`QT_QPA_PLATFORM=offscreen`) donde haya Qt; sin Qt para
`presentation`. Cubre, al menos:

- `f` en los cuatro contextos (el inventario actual lo cubre) y la
  asignación sobrevive a una recarga desde el catálogo (persistencia real,
  no memoria).
- Derivación de `favorite` y del nombre accesible; el prototipo sin
  catálogo sigue funcionando (`f` en memoria).
- Escritura fallida (catálogo de solo lectura por permisos o escritor
  ocupado simulado) → reversión y error visible; el `f` no se pierde con
  una escritura del indexador lenta en paralelo.
- Documento de búsqueda: nombres, ancestros y alias resueltos; `/fav`
  filtra.
- `:tag`: filtra por nombre normalizado; incluye descendientes y
  `suggested` y excluye `rejected` (filas sembradas por SQL); se desactiva
  repitiendo o sin argumento; nombre inexistente → mensaje; composición
  AND con `/`.
- Panel: etiquetas con estado/origen solo cuando aplican; `fav` como una
  más.
- Documentación: ayuda, comandos e inventario de teclas al día (los tests
  que vigilan `:sort` hoy).
- Todo bug corregido lleva su test de regresión.

## 11. Verificación del paso 2

1. `uv run pytest` y `uv run ruff check .` — suite en verde.
2. `uv run foto` con la biblioteca de prueba: `f` persiste (reinicia la
   app y compruébalo), `/fav` filtra, `:tag fav` filtra y se desactiva, el
   panel muestra las etiquetas, y una pasada de `foto-index` con la app
   abierta no rompe el etiquetado.
3. **Criterio de fin del paso 2:** visor con favorito persistido, panel,
   búsqueda y filtro por etiqueta; suite en verde; cero cambios pendientes
   sobre el indexador.

---

## 12. Documentación (parte de los dos pasos)

El ADR 0006 se redacta al comienzo del paso 1 (fija las decisiones antes de
implementar); el resto se cierra al terminar el paso 2:

| Fichero | Sección | Cambio | Paso |
| --- | --- | --- | --- |
| `docs/adr/0006-etiquetas.md` | — | Nuevo, formato de casa, índice de § 4 | 1 |
| `docs/adr/0002-indexado.md` | Nota de revisión | Las tablas de tags llegaron con ADR 0006 por `photo_hash`; XMP ya no es la verdad de los datos de usuario | 1 |
| `docs/adr/0003-consumo-del-indice-desde-el-visor.md` | Enmiendas | Nueva enmienda: escritura de usuario acotada a `tags`/`photo_tags` vía conexión dedicada (origen: ADR 0006) | 1 |
| `AGENT.md` | Reglas no negociables · regla 2 | Reescritura: el catálogo es la fuente de verdad de etiquetas y favoritos; los originales siguen intactos y todo lo escrito vive en `.foto/` | 1 |
| `AGENT.md` | Estado actual / «Indexador y catálogo» | Esquema v4; el visor escribe `tags`/`photo_tags` con conexión RW dedicada sin `index.lock`; `fav` por `kind='system'` | 2 |
| `README.md` | Principios · «Metadatos abiertos» | El catálogo es la fuente de verdad de los metadatos de usuario; XMP, exportación futurible | 1 |
| `README.md` | Principios · «Componentes reemplazables» | Reconstruible en lo derivable del contenido; etiquetas y favoritos no se regeneran: exigen copia de seguridad | 1 |
| `README.md` | Estado y roadmap | «Favoritos persistidos en XMP» se sustituye por favoritos persistidos como etiqueta `fav` (casilla marcada); «Filtrar por etiqueta con `:tag`» marcada en Fase 2 | 2 |
| `README.md` | Arquitectura y Documentación | «XMP sidecars (futuro)» → «exportación XMP (futurible)»; el párrafo de metadatos deja de prometer XMP como verdad; añade el ADR 0006 a la lista | 2 |
| `docs/diseno-ui.md` | Principio 6 y «Favoritos» del feedback | Escritura inmediata en el catálogo (no XMP); reversión y error persistente si falla | 2 |
| `docs/diseno-ui.md` | Búsqueda / Línea de comandos / Pendiente | Documento con etiquetas y ancestros; `:tag` en el vocabulario; pendientes: sintaxis general, explorador, confirmaciones futuras | 2 |
| `src/foto/resources/ayuda.md` | Globales y Comandos | `f` persiste el favorito; `:tag` documentado | 2 |

## 13. Pendientes que el ADR deja por escrito y no se implementan

- **Sintaxis general de etiquetado.** Solo `f` → `fav` está decidido. El
  estudio recomendó una tecla con autocompletado más comandos equivalentes;
  queda por decidir, y el vocabulario futuro de asignación no debe
  colisionar con `:tag` (que ya es el filtro).
- Creación, renombrado, re-padre, fusión y borrado de etiquetas: sus reglas
  se redactan en el ADR (§§ 3.2–3.4) pero las funciones no existen; no se
  puede crear ninguna etiqueta que no sea `fav` desde la UI.
- **Explorador de etiquetas** (vista navegable con jerarquía): decisión
  pendiente del ADR; se menciona como necesario, no se diseña.
- ML: detección de caras, pintado de regiones, confirmar/rechazar
  sugerencias con teclado, fase propia del indexador, tabla de atributos
  de personas.
- Exportación XMP (jamás importación), `:sort` por etiqueta, limpieza de
  etiquetas huérfanas, índice sobre `photo_tags.created_at` cuando exista
  una vista «recién etiquetadas», autocompletado del argumento de `:tag`.
- Consultas guardadas y colecciones inteligentes: no entran en este ADR.

## 14. Fuera del objetivo

- Implementar la sintaxis general de etiquetado, teclas nuevas o comandos
  de asignación; el explorador; el ML o las caras; la exportación o
  importación XMP; `:sort` por etiqueta; consultas guardadas.
- Conectar la geolocalización del ADR 0005 con las etiquetas o generar
  etiquetas automáticas de lugar.
- Tocar `photos` o `locations` desde la UI; tabla de atributos de
  personas; mapa, vista de mapa o geocodificación.
- Cambiar el pipeline de fases del indexador (§ 3.9: no se toca).
- `git commit`, `git push` o cambios de configuración de git sin permiso
  explícito.
