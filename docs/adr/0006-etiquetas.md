# ADR 0006 — Etiquetas en el catálogo: tipado, procedencia y favorito

- **Estado:** aceptada
- **Fecha:** 2026-11-10 (revisada el 2026-11-11)
- **Decide:** Jose
- **Ámbito:** el sistema de etiquetas (tags) del catálogo SQLite —esquema v4
  (`tags` + `photo_tags`), migración, escritura del visor y su reflejo en la
  UI— y la sustitución del favorito en memoria por la etiqueta de sistema
  `fav`. Quedan fuera la sintaxis general de etiquetado (solo `f` → `fav`
  está decidida), la creación/renombrado/borrado de etiquetas desde la UI, el
  explorador de etiquetas, el ML y las caras, la exportación XMP y cualquier
  conexión entre la geolocalización del ADR 0005 y las etiquetas. El pipeline
  de fases del indexador no cambia.

## Contexto

El visor tiene un favorito que solo vive en memoria (`favorite: bool` en el
modelo de UI): se pierde al cerrar la aplicación, no llega al catálogo y no
sirve para organizar la biblioteca. Al mismo tiempo la Fase 2 (organización)
necesita etiquetas de personas, lugares, eventos y etiquetas libres, y la
Fase 5 contempla detección de caras en local, lo que exige que cada
asignación foto–etiqueta pueda llevar **procedencia** (origen, estado,
confianza) y, para las caras, una **región** de la imagen.

El estudio de etiquetas del 2026-11-10 fija las decisiones de esta entrega.
Dos de ellas cambian principios asentados:

- La regla 2 de `AGENT.md` («XMP es la fuente de verdad») y el principio
  «Metadatos abiertos» de `README.md` quedan **rectificados** para los
  metadatos de usuario: el catálogo SQLite es la fuente de verdad de las
  etiquetas y los favoritos. La exportación a XMP queda en el roadmap como
  futurible; la importación no existe y no se contempla.
- En consecuencia, **el catálogo deja de ser 100 % reconstruible**: en lo que
  es función del contenido (fotos, ubicaciones, metadatos, lugares) sigue
  siéndolo, pero las etiquetas no se regeneran desde disco. Esto se encadena
  con la copia de seguridad (Fase 3 del roadmap).

Restricciones que se mantienen: originales intactos, escritura solo en
`.foto/`, `core/`, `storage/` e `indexer/` sin Qt, sin dependencias nuevas de
runtime, `PRAGMA user_version` como versión de esquema con migraciones
idempotentes, WAL y un único escritor con `index.lock` para el indexador.

El ADR 0002 (§ 3 y § 10) ya anticipaba tablas de tags con `photo_hash` como
clave foránea; llega el momento de crearlas, y con ellas de enmendar el ADR
0003, que reservaba el modo solo lectura para todo el visor.

## Decisión

> **Revisión (2026-11-11).** La taxonomía pasa a ser **plana**: no se da
> soporte a etiquetas anidadas (padre/hijo) ni a la fusión de etiquetas. Se
> retiran del esquema las columnas `parent_id` y `merged_into` (y su índice),
> la herencia en el filtrado y el subárbol de `:tag`; un nombre de etiqueta
> es una lista plana inspeccionable. El resto de la decisión no cambia.

### 1. El catálogo, fuente de verdad de etiquetas y favoritos

El catálogo SQLite es la **fuente de verdad de las etiquetas y los
favoritos**. Esto rectifica:

- la regla 2 de `AGENT.md`, que pasa a decir que XMP es la fuente de verdad
  de los metadatos que no son de usuario y que los datos de usuario
  (etiquetas, favoritos) viven en el catálogo;
- el principio «Metadatos abiertos» y el estado/roadmap de `README.md`;
- el feedback de favoritos de `docs/diseno-ui.md` (§ 12): escritura
  inmediata en el catálogo, no en XMP.

Consecuencia asumida: borrar `catalog.sqlite` pierde las etiquetas; la copia
de seguridad (Fase 3) pasa a cubrirlas. La regla 1 de `AGENT.md` no cambia:
los originales siguen intactos y todo lo escrito vive en `.foto/`.

### 2. Un único mecanismo, con `kind`; la geolocalización queda aparte

Personas, lugares, eventos, favorito y etiquetas libres son **el mismo
objeto con distinto `kind`**: dos tablas, `tags` (qué existe) y
`photo_tags` (qué foto lleva qué etiqueta, y con qué procedencia). Así, la
futura tabla de atributos de personas colgará de `tags` sin migrar nada.

- `kind ∈ ('system', 'person', 'place', 'event', 'free')` con `CHECK` y
  `DEFAULT 'free'`. Ampliar el vocabulario exigirá reconstruir la tabla en
  SQLite: coste conocido y aceptado a cambio de un catálogo inspeccionable.
- La geolocalización del ADR 0005 queda **desconectada**: sin claves
  foráneas, triggers ni generaciones automáticas entre las tablas `place_*`
  y `tags`. `kind='place'` es solo una etiqueta que el usuario pondrá a
  mano; ninguna etiqueta se genera desde el lugar.

### 3. Esquema `tags` + `photo_tags` (bump de `PRAGMA user_version` a 4)

Claves surrogadas `INTEGER PRIMARY KEY AUTOINCREMENT` en ambas tablas (estilo
`scan_runs`/`run_phases`) y `photo_tags.photo_hash TEXT` referenciando
`photos(hash)`: el ADR 0002 descartó ids enteros de foto.
`created_at`/`updated_at` `NOT NULL` en ambas, como en `photos` y
`locations`.

```sql
CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
    name_normalized TEXT NOT NULL UNIQUE,  -- NFC + casefold + espacios colapsados
    kind            TEXT NOT NULL DEFAULT 'free'
                    CHECK (kind IN ('system', 'person', 'place', 'event', 'free')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

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

Notas:

- La unicidad de asignaciones es **`(foto, etiqueta, región)`**: el par
  foto–etiqueta **puede** repetirse cuando cada fila lleva una cara distinta
  (dos caras de la misma persona en la misma foto); la asignación de foto
  completa (región `NULL`) es única gracias al `COALESCE`.
- `photo_tags.created_at` es la marca de «recién etiquetadas»; no se añade
  índice sobre ella hasta que exista una vista que lo use.
- Las operaciones de escritura usan `INSERT OR IGNORE`/`UPDATE OR IGNORE`:
  la unicidad la garantiza el esquema, no el código.
- Al asignar se actualiza `photo_tags.updated_at`; `photos.updated_at` no se
  toca nunca desde el visor (ver Decisión 10).

### 4. `fav`, única etiqueta de sistema

- `fav` es **la única etiqueta de sistema**: se resuelve siempre por
  `kind='system'`, **jamás por `id`** (los `AUTOINCREMENT` varían entre
  bibliotecas).
- No se renombra ni se borra; el índice único parcial
  `tags_system_singleton_idx` garantiza en el esquema que solo exista una
  fila `system`.
- La migración la inserta (`INSERT OR IGNORE`) con la hora por parámetro.

### 5. Nombres y normalización (`name_normalized`)

- `name_normalized` se calcula al crear: **NFC + casefold + espacios en
  blanco colapsados a uno + strip**. Función pura del módulo de etiquetas,
  testeada.
- **Sin plegar acentos**: `año 2024` ≡ `AÑO 2024`, pero `año 2024` ≢
  `anio 2024`. El plegado fusionaría personas distintas; `ñ` es letra.
- Unicidad **global** de `name_normalized`: ni por `kind` («Lisboa» persona
  y «Lisboa» lugar serían la misma etiqueta).
- `name` no puede estar vacío ni ser solo espacios; sin saltos de línea.
- Renombrar (cuando exista la operación) recalcula `name_normalized`; una
  colisión es un error, no una fusión.

### 6. Procedencia desde el día uno (`source`, `status`, `confidence`)

- Las columnas nacen desde el primer día aunque, sin ML, solo ocurra
  `manual`/`confirmed`: añadirlas después exigiría reconstruir una tabla ya
  poblada.
- `source ∈ ('manual', 'ml')`; `status ∈ ('confirmed', 'suggested',
  'rejected')`; `confidence REAL ∈ [0,1]` opcional, que solo se escribe con
  `source='ml'` (regla de código; el `CHECK` de rango basta en SQL).
  **No existe el valor `xmp_import`**: no hay importación, y el `CHECK`
  cerrado es el que hace inspeccionable el catálogo.
- Las asignaciones manuales nacen directamente `confirmed`.
- Transición `suggested → confirmed`: actualiza la misma fila **sin pisar**
  `source` (queda el origen: sugerida por ML, confirmada por humano) ni
  `created_at` (cuándo se sugirió). La fila es el estado; no hay tabla de
  historial.
- `rejected` existe desde el principio aunque nada lo produzca aún: su papel
  es **bloquear re-sugerencias**. La futura selección del ML serán las
  parejas foto–etiqueta **sin fila** en `confirmed` ni `rejected`;
  re-activar una sugerencia será borrar la fila `rejected`, decisión
  explícita del usuario.
- Los filtros cuentan `confirmed` + `suggested`; `rejected` nunca. Fijo,
  sin configuración hasta que exista el ML.

### 7. Regiones y caras

- Región rectangular **proporcional [0,1]** respecto a la imagen
  **orientada tal como se muestra** (EXIF aplicado): origen arriba-izquierda,
  `x` hacia la derecha, `y` hacia abajo. Cuatro columnas `REAL`
  (`region_x/y/w/h`), todo-o-nada (o las cuatro o ninguna), `w,h > 0`. El
  código recorta a `[0,1]` al escribir; el `CHECK` defiende. Píxeles
  descartados: dependen de la resolución del original.
- «Cara» es una **convención** (etiqueta de persona con región), no un
  constraint del esquema: el esquema no liga `region` a `kind`. La misma
  persona puede convivir en la misma foto con y sin región (grupo y
  retrato); la unicidad es por región (Decisión 3).
- El visor no pinta regiones en esta entrega y la región no se carga en el
  modelo de UI: solo queda definido el dato.

### 8. Sustitución total del favorito

El favorito en memoria **desaparece por completo**, sin coexistencia con la
etiqueta `fav`. `f` conmuta la asignación completa (región `NULL`) de `fav`
en los cuatro contextos que hoy despachan la acción `favorite` (rejilla, fila
del visor, panel y fallback). El inventario de `keybindings.py`, la acción y
las tablas de teclas **no cambian**: ninguna tecla nueva.

- El modelo de UI deriva `favorite` de sus etiquetas: `Photo` y
  `CatalogPhoto` ganan una tupla inmutable de etiquetas (nombre, `kind`,
  `status`, `source`) y `favorite` pasa a propiedad calculada sobre esa
  tupla: una sola fuente de verdad, sin campo duplicado. La insignia de la
  cuadrícula (`FAVORITE_ROLE`), el nombre accesible («, favorito») y los
  mensajes de la barra («favorito»/«sin favorito») se conservan.
- Referencias migradas: `ui/models.py` (`favorite`), `core/library.py`
  (`favorite: bool`), `ui/app.py` (carga), `ui/presentation.py` (nombre
  accesible), `ui/grid.py` (`FAVORITE_ROLE`), `ui/window.py`
  (`_toggle_favorite`), `ui/prototype.py` y los tests que fabrican fotos con
  `favorite=True`; `src/foto/resources/ayuda.md` y `docs/diseno-ui.md`
  documentan que `f` persiste.
- Feedback optimista: se aplica en memoria, se escribe por la conexión RW y,
  si falla (catálogo ocupado, disco, permisos), se revierte y se muestra un
  **error persistente** (se cierra con `Esc`), nunca silencio.
- Por hash: una foto duplicada es favorita en todas sus copias; una sola
  fila de asignación.
- `f` queda operativo también sin catálogo: la ventana recibe un colaborador
  de etiquetas **opcional e inyectado** (`MainWindow` lo crea sobre el
  catálogo; el prototipo pasa `None` y `f` conmuta solo en memoria, como
  hoy).

### 9. Migración v4 idempotente

- Bump a `user_version = 4` en el patrón existente (`executescript` con
  `IF NOT EXISTS` + parámetros): sirve para catálogos v1–v3 y vacíos, y es
  idempotente al abrir. La aplica solo `foto-index`, como hasta ahora.
- **No hay datos que convertir**: el favorito nunca tuvo columna ni
  persistencia; era memoria de la UI. No hay migración de favoritos que
  inventar.
- Tras crear las tablas, la migración inserta la etiqueta de sistema:

  ```sql
  INSERT OR IGNORE INTO tags (name, name_normalized, kind, created_at, updated_at)
  VALUES ('fav', 'fav', 'system', :now, :now);
  PRAGMA user_version = 4;
  ```

- Un catálogo más nuevo que el binario aborta con el mensaje actual. El
  visor (solo lectura) exige v4 con el mecanismo existente: tras actualizar
  el código hay que ejecutar `foto-index` una vez antes de abrir la app
  (mismo precedente que v3).
- `--dry-run` sigue funcionando: la copia en memoria se migra igual y no
  escribe en disco.

### 10. Escritura del visor: conexión RW dedicada, acotada

- La ventana mantiene su conexión de solo lectura para cargar y una
  **conexión RW propia y acotada** para etiquetas: módulo nuevo en
  `storage/` (`storage/tags.py`), sin Qt; la ventana nunca ejecuta SQL.
- Esa conexión **no toma `index.lock`**; `busy_timeout` (5 s) + WAL
  serializan con los lotes del indexador. Transacciones mínimas: una
  sentencia por operación con commit inmediato (estilo `record_phase`),
  para no retrasar checkpoints ni bloquear lotes del indexador más de lo
  inevitable.
- El visor tiene **prohibido escribir en `photos` y `locations`**: su
  conexión de escritura queda acotada a `tags` y `photo_tags`. Reconciliar
  hashes es trabajo de `foto-index`, nunca de la UI. Los errores de
  escritura se muestran, nunca en silencio.
- **Enmienda del ADR 0003** con origen «ADR 0006»: el modo lectura se
  mantiene para todo lo existente; la escritura de usuario queda limitada a
  `tags`/`photo_tags` vía la conexión dedicada.

### 11. Consultas y filtros

- **Carga en memoria al arrancar**: las asignaciones por hash viajan con los
  datos actuales (una consulta más por la conexión de solo lectura, una
  entrada por hash), junto con la lista completa de etiquetas. Decenas de
  miles de fotos y miles de asignaciones caben en la carga; el filtrado es
  en memoria, como hoy.
- **Documento de búsqueda `/`**: `photo_search_text` añade los nombres de
  las etiquetas, contando `confirmed` + `suggested`. Regla pura en
  `presentation.py`, sobre la tabla de etiquetas cargada. `/fav` encuentra
  los favoritos (hoy «favorito» no busca nada).
- **Filtro explícito `:tag <nombre>`**: normaliza el nombre (Decisión 5),
  resuelve la etiqueta y filtra por ella. Cuenta `confirmed` + `suggested`;
  `rejected` nunca (Decisión 6).
  - Repetir `:tag <nombre>` sobre la etiqueta activa, o `:tag` sin
    argumento, desactiva el filtro; un nombre que no existe produce un
    mensaje en la barra.
  - Mientras está activo, la barra lo indica de forma persistente (estilo
    `/texto`, p. ej. «etiqueta: nombre») y se compone con la búsqueda `/`
    en AND.
  - Entra en el vocabulario (`commands.py`), el dispatcher, la ayuda
    integrada y `docs/diseno-ui.md`. El vocabulario futuro de asignación no
    debe colisionar con `:tag`, que queda como filtro.
- Coste: una consulta de carga, mapa por hash, filtrado en memoria. Sin Qt
  en el SQL ni en las reglas; la ventana solo consume funciones de
  `storage/` y de `presentation.py`.

### 12. Indexador: sin fase nueva, identidad estricta

- **Sin fase nueva**: las etiquetas son escritura de usuario, no trabajo
  derivable del pipeline. `scan` y `geocode` no conocen las etiquetas, y el
  resumen y el JSON del indexador no añaden contadores de etiquetas
  (privacidad, igual que los lugares en el ADR 0005). El pipeline de fases
  del ADR 0005 queda listo para una fase futura que escriba sugerencias del
  ML, pero no se toca aquí.
- `--prune` al borrar fotos arrastra sus `photo_tags` por el `CASCADE`
  (automático con `foreign_keys=ON`). Las **etiquetas huérfanas se
  conservan**: son taxonomía del usuario, no basura; su limpieza será manual
  cuando exista el explorador.
- **Identidad estricta**: las asignaciones están ligadas al hash de
  contenido y **nunca se trasladan entre hashes**, como los metadatos. `foto`
  nunca modifica fotos, así que un cambio de contenido solo llega desde
  fuera de la app (edición in place, conversión) y equivale a una **foto
  nueva sin etiquetas**. El hash viejo, huérfano de ubicaciones, muere con
  el `--prune` siguiente y su `CASCADE` se lleva las asignaciones; hasta
  entonces no molesta (sin ubicaciones `present`, la foto no aparece en el
  visor).
- Huecos preparados para el ML (nada de fases ni modelos): columnas de
  procedencia, patrón de selección por parejas, marco de fases del ADR 0005
  y futuras tablas de caras/embeddings **aparte** de `photo_tags`, con
  `photo_hash` y versión de modelo (ADR 0002 § 10).

### 13. Visor: panel de etiquetas, `f` y `:tag`

- **Panel de metadatos**: sección «Etiquetas» al final de la ficha; una
  línea por asignación (`confirmed` + `suggested`) con el nombre y, solo si
  aplica: el `kind` cuando no es `free`, el estado cuando no es `confirmed`
  (p. ej. «sugerida») y el origen (`machine learning`) solo si
  `source='ml'`. `fav` aparece como una etiqueta más, sin casos especiales:
  la insignia ya comunica el favorito. Confirmar/rechazar sugeridas con
  teclado queda pendiente: hoy no hay sugerencias.
- **`f`**: conmuta la asignación de `fav` (Decisión 8) con feedback
  optimista, reversión y error persistente si la escritura falla.
- **`:tag`**: filtro por etiqueta (Decisión 11).
- Queda pendiente la sintaxis general de etiquetado (ver Pendientes): en
  esta entrega no se añaden teclas ni comandos de asignación, y no se puede
  crear ninguna etiqueta que no sea `fav` desde la UI.

### 14. Privacidad

Los nombres de personas (y las etiquetas en general) y las regiones quedan
**fuera de los logs y de los resúmenes**, mismo criterio que las rutas
completas y los lugares del ADR 0005: el indexador no publica contadores de
etiquetas en texto ni en JSON.

### 15. Preparado para la exportación XMP (sin diseñarla)

El modelo deja los datos listos para una **futura exportación** (jamás
importación): `free/person/place/event` → `dc:subject` / `mwg-rs Regions`
para las personas con región. La exportación jerárquica con
`lr:hierarchicalSubject` no tiene sentido con la taxonomía plana; queda
descartada mientras no haya jerarquía. Su diseño no es parte de este ADR.

## Consecuencias

**Positivas**

- Un solo mecanismo cubre personas, lugares, eventos, favoritos y etiquetas
  libres; la futura tabla de atributos de personas cuelga de `tags` sin
  migrar nada.
- La taxonomía plana mantiene el esquema y las reglas mínimos: sin
  `parent_id`, sin detección de ciclos, sin resolución de ancestros ni
  expansión de subárboles en la consulta ni en el filtro.
- El favorito sobrevive a reiniciar la aplicación, viaja por hash (una foto
  duplicada es favorita en todas sus copias) y entra en el documento de
  búsqueda con `/fav` y en el filtro `:tag fav`.
- La procedencia y las regiones nacen con el esquema: el ML no exigirá
  reconstruir una tabla poblada.
- Catálogo inspeccionable: `CHECK`s cerrados, nombres normalizados únicos,
  `fav` singleton garantizado por el esquema y unicidad de asignaciones por
  `(foto, etiqueta, región)` también en el esquema, no en el código.
- La UI no gana lógica de negocio: `storage/tags.py` sin Qt, reglas puras en
  `presentation.py`, la ventana nunca ejecuta SQL.
- El visor no toma `index.lock` y escribe con transacciones mínimas: WAL +
  `busy_timeout` serializan con el indexador sin bloquear sus lotes.

**Negativas**

- **El catálogo deja de ser 100 % reconstruible**: borrar `catalog.sqlite`
  pierde las etiquetas y los favoritos; la copia de seguridad (Fase 3 del
  roadmap) pasa a ser imprescindible para ellos.
- Sin jerarquía ni fusión, agrupar conceptos afines («Viajes» y sus años)
  depende de convenciones de nombre del usuario, y un etiquetado repetido o
  mal escrito genera etiquetas distintas que no se pueden unir: borrar las
  asignaciones de una de ellas y etiquetar de nuevo con la buena (el
  `CASCADE` en `photo_tags.tag_id` lo permite; si la operación existiera,
  exigiría confirmación explícita: sería la primera acción destructiva del
  proyecto).
- Ampliar los `CHECK` cerrados (`kind`, `source`, `status`) exige
  reconstruir la tabla en SQLite; coste conocido y aceptado.
- Tras actualizar el código, el visor exige un catálogo v4: hay que ejecutar
  `foto-index` una vez antes de abrir la app.
- Con el indexador corriendo, una escritura del visor puede fallar
  (catálogo ocupado, disco, permisos): la UI debe reverter y mostrar el
  error, nunca callarlo.
- El favorito, antes memoria volátil sin coste, pasa a depender del
  catálogo: perder `.foto/` pierde también los favs.
- Identidad estricta: editar el original desde fuera (edición in place,
  conversión) pierde el etiquetado de esa foto —en esta entrega, su `fav`—,
  porque el contenido nuevo es una foto nueva sin etiquetas.
- La migración añade dos tablas, cuatro índices y dos `CHECK` compuestos que
  hay que explicar, documentar y probar.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Tablas separadas por tipo de etiqueta | Duplicaría consultas, índices y reglas para cada tipo; con `kind` un solo mecanismo cubre todos y la futura tabla de atributos de personas cuelga de `tags` sin migrar. |
| XMP como fuente de verdad de etiquetas | Rectificado por decisión del proyectista: el catálogo gana inspectabilidad y esquema; XMP queda como exportación futurible y la importación no se contempla. |
| Etiquetas anidadas (padre/hijo, con `parent_id`) | Descartado en la revisión del 2026-11-11: la taxonomía es plana; la jerarquía añade ciclos, herencia en filtrado y expansión de subárboles sin caso de uso real para una biblioteca personal. Agrupar queda en manos de convenciones de nombre. |
| Fusión de etiquetas (`merged_into`) | Descartado en la revisión del 2026-11-11: no se da soporte a fusionar; la limpieza de etiquetas mal escritas será borrar sus asignaciones y reetiquetar. |
| `id` entero de foto en `photo_tags` | El ADR 0002 descartó ids enteros de foto: la identidad es el hash de contenido; los ids de foto no estabilizan entre bibliotecas. |
| Columna `favorite` en `photos` | Acoplaría el favorito al esquema de fotos y lo haría escribir `photos` desde la UI; como etiqueta `fav` reutiliza el mecanismo único y la conexión acotada. |
| Valor `xmp_import` en `source` | No hay importación XMP; el `CHECK` cerrado es el que hace inspeccionable el catálogo. |
| Una fase del indexador para etiquetas | Las etiquetas son escritura de usuario, no trabajo derivable del contenido; una fase las haría depender de pasadas y del lock. |
| Regiones en píxeles | Dependen de la resolución del original; las proporcionales `[0,1]` sobre la imagen orientada son estables e independientes del tamaño. |
| Índice único sin `COALESCE` | En SQLite los `NULL` no colisionan en los índices únicos: sin `COALESCE` se podrían duplicar las asignaciones de foto completa. |
| Coexistencia temporal del favorito viejo y la etiqueta `fav` | Dos fuentes de verdad para el mismo concepto; el favorito en memoria nunca persistió, así que no hay datos que convertir ni usuarios que migrar. |

## Decisiones pendientes y riesgos

- **Sintaxis general de etiquetado.** Solo `f` → `fav` está decidido. Queda
  por decidir una tecla con autocompletado y/o comandos equivalentes; el
  vocabulario futuro de asignación no debe colisionar con `:tag` (que ya es
  el filtro).
- **Creación, renombrado y borrado de etiquetas.** Sus reglas quedan escritas
  en este ADR (Decisión 5 y el `CASCADE` del esquema) pero las funciones no
  existen; no se puede crear ninguna etiqueta que no sea `fav` desde la UI.
  El borrado de una etiqueta con asignaciones arrastrará estas por el
  `CASCADE` y exigirá confirmación explícita: será la primera acción
  destructiva del proyecto.
- **Explorador de etiquetas** (vista navegable): decisión pendiente; se
  menciona como necesario, no se diseña. Con la taxonomía plana, basta una
  lista plana.
- **ML**: detección de caras, pintado de regiones, confirmar/rechazar
  sugerencias con teclado, fase propia del indexador y tabla de atributos de
  personas.
- **Exportación XMP** (jamás importación), `:sort` por etiqueta, limpieza de
  etiquetas huérfanas, índice sobre `photo_tags.created_at` cuando exista
  una vista «recién etiquetadas», y autocompletado del argumento de `:tag`.
- **Consultas guardadas y colecciones inteligentes**: no entran en este ADR.
- **Reversibilidad de la simplificación.** Si la taxonomía plana se quedara
  corta, reintroducir `parent_id` exigirá reconstruir `tags` y repensar el
  filtrado; se asume el riesgo por la simplicidad ganada.
- **Riesgo de pérdida**: hasta que exista la copia de seguridad (Fase 3),
  `.foto/` es la única copia de las etiquetas; manipularlo a mano es
  irreversible.
