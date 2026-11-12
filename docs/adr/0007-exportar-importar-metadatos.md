# ADR 0007 — Exportación e importación JSON de metadatos de usuario: tags y lugares

- **Estado:** aceptada
- **Fecha:** 2026-11-12
- **Decide:** Jose
- **Ámbito:** la copia de seguridad y migración de los metadatos de usuario que
  no son reconstruibles desde los originales: dos ficheros JSON (`tags.json` y
  `places.json`), las herramientas CLI `foto-export` / `foto-import` (sin Qt),
  la semántica de merge aditivo y la ampliación del `CHECK` de
  `photo_tags.source` con el valor `'import'` (migración a esquema v5). Quedan
  fuera los metadatos EXIF incrustados (que siguen en los originales), las
  fotos, los sidecars XMP (ni exportar —futurible del ADR 0006— ni importar
  —prohibido—), la UI (nada de comandos `:` ni diálogos), la sincronización a
  la baja (`--sync`/`--replace`, descartada) y la copia de seguridad automática
  de la Fase 3. El pipeline de fases del indexador no cambia.

## Contexto

El ADR 0006 dejó el catálogo como **fuente de verdad de las etiquetas y los
favoritos**, con una consecuencia negativa explícita: borrar
`catalog.sqlite` las pierde, y la copia de seguridad (Fase 3 del roadmap)
pasa a ser imprescindible. Hoy `.foto/` es la única copia de las etiquetas y
manipularla a mano es irreversible.

Al mismo tiempo, los lugares del ADR 0005 **son** reconstruibles desde disco,
pero solo a base de peticiones a Nominatim con throttle de 1 petición/segundo
(~3 600 fotos nuevas por hora) y con `error` pendiente de reanudación:
restaurar una biblioteca grande repetiría la geocodificación completa, que
además es dato de terceros (direcciones) con su propia sensibilidad.

El proyectista decide (2026-11-12) la forma de la entrega: **dos ficheros JSON
separados** (tags y lugares), importación **merge aditivo** que nunca borra,
superficie **CLI nueva** al estilo de `foto-index`, un **ADR 0007 nuevo** que
enmiende el ADR 0006 ampliando el `CHECK` de `source`, y los **hashes
ausentes** informados y conservados. Las restricciones se mantienen:
originales intactos, escritura solo en `.foto/` (más el fichero de exportación
que el usuario pida), `core/`, `storage/` y la nueva `transfer/` sin Qt, sin
dependencias nuevas de runtime (`json` y stdlib bastan), `PRAGMA
user_version` con migraciones idempotentes, WAL y escritor único con
`index.lock` para quien escribe en `photos`.

## Decisión

### 1. Motivo: lo no reconstruible se respalda; lo costoso se conserva

- Las **etiquetas** (`tags` + `photo_tags`, incluido el favorito `fav`) no son
  reconstruibles: son metadatos de usuario y exigen copia de seguridad
  (consecuencia negativa del ADR 0006). Este formato es su vía de respaldo y
  su vía de migración entre bibliotecas.
- Los **lugares** (`place_*` de Nominatim) son reconstruibles pero costosos
  (Nominatim 1/s, `error` pendiente de reanudación); exportarlos restaura una
  biblioteca sin repetir la geocodificación y la mueve entre equipos.
- Los **metadatos EXIF incrustados** (cámara, ISO, GPS…) **no se exportan**:
  siguen en los originales y los originales son su fuente de verdad. Exportar
  fotos tampoco: el JSON no es una copia del catálogo, es el respaldo de lo
  que el contenido no puede regenerar (más los lugares, por decisión
  explícita).

### 2. Dos ficheros separados, nunca uno combinado

Se generan **dos ficheros por biblioteca y por tipo**, con nombre fijo:

- `tags.json` — etiquetas y asignaciones: **imprescindible**. Es el verdadero
  respaldo de los datos de usuario.
- `places.json` — lugares resueltos (`place_status='ok'`): **prescindible**
  (dato reconstruible). Al ser dato de terceros (direcciones, personas del
  `address`), toca la privacidad de quien no es dueño de la biblioteca:
  puede no exportarse, no importarse o borrarse sin afectar a las etiquetas.

Nunca un fichero combinado: mezclar lo imprescindible con lo descartable
obligaría a importar direcciones para recuperar favoritos.

### 3. Formato de los ficheros (v1, congelado)

UTF-8, `indent=2`, `ensure_ascii=False`, terminados en `\n`. **Orden
determinista**: dos exportaciones seguidas sin cambios producen el mismo
fichero byte a byte (apto para `diff` y para versionarlo). Escritura
**atómica**: fichero temporal en el directorio de destino + `rename`, como
las miniaturas y la configuración.

`tags.json`:

```json
{
  "format": "foto/tags",
  "version": 1,
  "generated_at": "2026-11-12T10:00:00+00:00",
  "counts": {"tags": 42, "assignments": 1200},
  "tags": [
    {
      "name": "Lisboa",
      "normalized": "lisboa",
      "kind": "place",
      "created_at": "2026-11-10T09:00:00+00:00",
      "updated_at": "2026-11-10T09:00:00+00:00"
    }
  ],
  "assignments": [
    {
      "photo": "sha256:<64 hex>",
      "tag": "lisboa",
      "source": "manual",
      "status": "confirmed",
      "confidence": null,
      "region": null,
      "created_at": "2026-11-10T09:00:00+00:00",
      "updated_at": "2026-11-10T09:00:00+00:00"
    }
  ]
}
```

`places.json`:

```json
{
  "format": "foto/places",
  "version": 1,
  "generated_at": "2026-11-12T10:00:00+00:00",
  "counts": {"photos": 800},
  "places": [
    {
      "photo": "sha256:<64 hex>",
      "country": "Portugal",
      "country_code": "PT",
      "state": "Lisboa",
      "city": "Lisboa",
      "municipality": "Lisboa",
      "display_name": "…display_name íntegro de Nominatim…",
      "address": {"road": "…", "…": "…"},
      "resolved_at": "2026-11-10T09:00:00+00:00"
    }
  ]
}
```

Reglas del formato:

- **Identidad y referencias**: la identidad de la foto es el hash de contenido
  `sha256:<64 hex>` (ADR 0002); los `id` `AUTOINCREMENT` nunca viajan ni
  aparecen. Las asignaciones referencian la etiqueta por su
  `name_normalized` (clave estable por definición: es `UNIQUE`, ADR 0006).
- **Orden**: `tags` por `normalized`; `assignments` por
  `(photo, normalized, región)`; `places` por `photo`.
- **`region`**: `null` (foto completa) o array `[x, y, w, h]` en `[0,1]` con
  `w,h > 0`, exactamente el modelo del ADR 0006 § 7 (imagen orientada tal
  como se muestra).
- **`confidence`**: `null` salvo que la fila lo tenga (filas ML, o valores
  conservados por imports previos tras v5).
- Se exportan **todas** las etiquetas: las huérfanas también (son taxonomía
  del usuario, ADR 0006 Decisión 12) y `fav` (`kind='system'`).
- `places.json` solo incluye filas `place_status='ok'`; `address` es el
  `place_address_json` parseado (si no parsea, `null` y aviso, nunca aborto);
  `resolved_at` es `place_updated_at`.
- `generated_at` lleva offset explícito, misma convención ISO 8601 que las
  fechas del catálogo.

La **versión del formato es 1 y queda congelada**: cambiarlo exigirá versionar
y migrar el lector (un fichero de formato más nuevo que el binario aborta con
mensaje claro, mismo criterio que el catálogo).

### 4. Semántica de `foto-export`

- **Solo lectura** del catálogo (`open_read_only`): nunca escribe en él, no
  toma `index.lock` y funciona con `foto-index` o la app corriendo (WAL).
- Selección `--tags` / `--places` (por defecto, ambos, a ficheros separados);
  destino `--out DIR` (por defecto `<fototeca>/.foto/exports/`, ignorado por
  git; crea el directorio si falta).
- `--dry-run`: calcula y valida todo, no escribe ficheros (informa tamaño y
  conteos previstos).
- Nombres de fichero fijos; si existe uno anterior se sobreescribe (es una
  exportación, no un log).
- Resumen (texto y JSON) con **solo conteos**: etiquetas, asignaciones,
  lugares, huérfanas, avisos y rutas de destino. **Nunca nombres de etiquetas
  ni direcciones** (privacidad, Decisión 10).
- Códigos de salida como `foto-index`: `0` éxito, `1` error no fatal (avisos
  incluidos), `2` configuración/catálogo inválido, `3` lock (no debería
  ocurrir: no toma lock), `130` SIGINT.

### 5. Semántica de `foto-import`: merge aditivo idempotente

- **Nunca borra nada**: añade lo que falta, no toca lo existente. Reejecutar
  el mismo import sobre el mismo catálogo no cambia nada (segunda pasada con
  todos los contadores de «aplicado» a cero y solo «ya existía»).
- **Etiquetas**: `upsert` por `name_normalized`. Si no existe, se crea con el
  `kind` y las fechas del fichero. Si existe: mismo `kind` → se conserva la
  existente sin tocarla; `kind` distinto → **aviso** (no error) y se conserva
  la existente, aplicando igual las asignaciones; `kind='system'` con nombre
  distinto de `fav` → no puede existir por el índice singleton: aviso y fila
  ignorada.
- **Asignaciones**: para cada fila del fichero con foto presente en el
  catálogo, `INSERT OR IGNORE` con `source='import'` (Decisión 8) y `status`,
  `confidence`, `region` y fechas del fichero. La unicidad `(foto, etiqueta,
  región)` la garantiza el índice con `COALESCE` del esquema, no el código.
- **Filas existentes: intocables.** Si la asignación ya existe, el import no
  la pisa ni le cambia `source`/`status`/fechas: merge aditivo puro.
- **`status` y `confidence` del fichero se conservan tal cual**, incluidas
  filas `rejected` (existen para bloquear re-sugerencias; importarlas las
  mantiene bloqueadas en la biblioteca destino).
- **Validación completa antes de escribir nada** (formato y versión
  conocidos, hashes bien formados, `kind`/`status`/`region` en rango,
  referencias `tag` presentes en la sección `tags` del propio fichero): un
  fichero inválido aborta con código `2` y **cero escrituras**.
- **Una única transacción por fichero**: o aplica el fichero entero o no
  aplica nada.
- **Hashes ausentes** (fotos del fichero que no están en el catálogo): se
  cuentan y se informan en el resumen (`hashes_ausentes` en texto y JSON;
  detalle con `--verbose`, sin volcar rutas personales). No abortan, no se
  escriben en ninguna parte y **el fichero los conserva** para futuros
  imports.
- Tras un import, el catálogo queda en v5 y la app puede abrirlo
  directamente (el import no toca `photos` salvo `place_*`).

### 6. Lugares al importar: aditivos sobre lo resuelto

- Solo fotos presentes en el catálogo. Se rellenan las columnas `place_*`
  **solo** de fotos con `place_status ∈ ('pending', 'none')`: lo resuelto
  manda sobre lo importado. **Nunca se pisan `ok` ni `error`** (salvo
  `error` con `--force`: el import puede reintentar lo que Nominatim
  rechazó, pero jamás sobreescribe un `ok`).
- Se escribe el paquete completo del fichero (`country`, `country_code`,
  `state`, `city`, `municipality`, `display_name`, `address_json`
  re-serializado canónico), `place_status='ok'`, `place_error=NULL` y
  `place_updated_at` con la **hora del import** (por parámetro, estilo
  migraciones): la fila cambia ahora, aunque el lugar venga del fichero.
- Es la única herramienta fuera del indexador que escribe en `photos`
  (columnas `place_*`); por eso firma `index.lock` (Decisión 9). La caché
  `geocode_cells` no se toca: el import no habla con la red.

### 7. Hashes ausentes: informar y conservar; sin sincronización a la baja

Un import donde faltan fotos (biblioteca parcial, restauración en curso) es
un caso normal, no un error: los hashes ausentes se **informan** (conteo y
detalle con `--verbose`) y **el fichero los conserva** para futuros imports
(cuando las fotos lleguen, el siguiente import los aplicará). **Descartado**
un modo `--sync` que sincronice el catálogo a la baja con el fichero: sería
destructivo y contradice el merge aditivo (Alternativas).

### 8. Enmienda del ADR 0006: `source='import'` y migración v5

El ADR 0006 declaraba `source ∈ ('manual', 'ml')` y «no existe el valor
`xmp_import`». Esta decisión lo enmienda con un valor distinto (`import`) y
por una razón distinta: la importación es del catálogo JSON, no de XMP, que
sigue sin existir.

- **`source ∈ ('manual', 'ml', 'import')`**, con `DEFAULT 'manual'` intacto.
- Toda fila de asignación **creada por el import** nace con
  `source='import'`, aunque el fichero traiga `manual` o `ml`: la
  procedencia de la fila es «vino de una importación a esta biblioteca». El
  fichero conserva la procedencia original para trazabilidad y
  reexportación.
- El valor **`xmp_import` sigue sin existir** y ninguna herramienta importa
  XMP (su exportación sigue siendo futurible y su importación, prohibida).
- Ampliar el `CHECK` cerrado exige **reconstruir la tabla** en SQLite
  (coste conocido y aceptado del ADR 0006): migración a
  `user_version = 5`, idempotente, en el patrón de casa (`executescript` con
  `IF NOT EXISTS` + parámetros) — crear tabla nueva temporal, copiar filas,
  soltar la vieja, renombrar y recrear los tres índices de `photo_tags`
  (`photo_tags_assign_unique_idx` con sus `COALESCE`, `photo_tags_photo_idx`
  y `photo_tags_tag_idx`) **byte a byte iguales** a los del ADR 0006. El
  resultado debe ser idéntico al esquema de un catálogo creado de cero.
- **Re-redacción de la regla de `confidence`** del ADR 0006 («solo se escribe
  con `source='ml'`»): pasa a regir la **escritura del visor**; el import
  conserva el valor del fichero si lo trae (el `CHECK` de rango `[0,1]`
  sigue siendo la defensa en SQL).
- Sirve para catálogos v4 y vacíos. Un catálogo más nuevo que el binario
  aborta con el mensaje actual. Tras actualizar el código hay que ejecutar
  `foto-index` (o el import) una vez antes de abrir la app, como en v3→v4.
  El visor exige v5 con el mecanismo existente (`open_read_only` valida la
  versión mínima).

### 9. Concurrencia: lectores sin lock, import como escritor único

- **`foto-export` no bloquea y no toma lock**: lee con la conexión de solo
  lectura (WAL) y convive con `foto-index` y la app.
- **`foto-import` es escritor único y toma `index.lock`** como `foto-index`
  (`storage/lock.py`): si otro indexador lo tiene, sale con código `3`. Lo
  justifica escribir en `photos` (columnas `place_*`) además de en
  `tags`/`photo_tags`: **escribir `photos` es trabajo de herramientas de
  indexado, nunca de la UI** (criterio del ADR 0006 Decisión 10, llevado a
  las herramientas de línea de comandos). `--dry-run` no toma el lock ni
  escribe: valida contra una copia en memoria del catálogo (mismo patrón que
  `foto-index`).

### 10. Privacidad

Los ficheros contienen **datos personales**: nombres de personas y etiquetas,
direcciones de terceros (`address` y `display_name` de Nominatim). Por eso:

- Los logs y los resúmenes **no los vuelcan** (mismo criterio que las rutas
  completas y los lugares en los ADR 0005/0006): solo conteos, y el detalle
  de hashes ausentes con `--verbose` no incluye rutas personales.
- El fichero de exportación va **donde el usuario diga** (`--out`); el
  destino por defecto es `<fototeca>/.foto/exports/`, dentro de `.foto/`
  (ignorado por git, regla 1 de `AGENT.md`). El usuario decide con qué
  respaldo protegerlo: es el dueño de sus datos, pero la herramienta no los
  publica.

## Consecuencias

**Positivas**

- Las etiquetas y los favoritos dejan de depender de la única copia en
  `.foto/`: hay una vía de respaldo y de migración entre bibliotecas
  (identidad por hash, así que el fichero es portable) antes de la Fase 3.
- Restaurar una biblioteca no repite la geocodificación; los lugares viajan
  entre equipos sin tocar Nominatim.
- El formato es diffable y versionable: orden determinista y escritura
  atómica.
- El merge aditivo hace el import seguro por construcción: reejecutarlo no
  cambia nada y no puede borrar ni pisar trabajo del usuario.
- Catálogo sigue inspeccionable: el `CHECK` cerrado de `source` gana un valor
  documentado en vez de un `TEXT` libre; la procedencia «vino de una
  importación» viaja en el esquema.
- Los ficheros separados permiten respaldar lo imprescindible sin arrastrar
  direcciones de terceros.

**Negativas**

- La migración v5 **reconstruye `photo_tags`** (coste proporcional a sus
  filas) y el visor exige ejecutar `foto-index` una vez tras actualizar el
  código, como en v3→v4 y v4.
- **El JSON no es una copia completa del catálogo**: no contiene fotos,
  miniaturas ni historial de `runs`; no sustituye a la copia de la Fase 3, la
  complementa.
- Un import de lugares **escribe en `photos`** y por eso firma `index.lock`:
  una segunda herramienta compite por el escritor único.
- Dos bibliotecas que importan el mismo fichero **divergen** en `source`
  (todas las filas nuevas quedan `import`) y en fechas (`place_updated_at` es
  la hora de cada import); el fichero conserva la verdad original para
  reexportar, pero el catálogo no.
- El **formato v1 queda congelado**: cualquier cambio de esquema del fichero
  exigirá versión y migración del lector, y los ficheros viejos deberán
  seguir leyéndose.
- Las filas `rejected` importadas **bloquean re-sugerencias** en la
  biblioteca destino por diseño: una biblioteca que reciba un fichero con
  rechazos hereda esas decisiones.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Un único JSON combinado (tags + lugares) | Mezcla lo imprescindible con lo descartable: importar favoritos obligaría a importar direcciones de terceros. Los ciclos de vida y la sensibilidad son distintos. |
| JSON Lines | Sobra por fichero y pierde la cabecera con conteos y versión; un JSON por biblioteca es inspeccionable y diffable. |
| Export/import desde la UI (comandos `:`, diálogos) | La UI no debe bloquearse con escrituras de ficheros ni pedir rutas; la CLI al estilo de `foto-index` sirve para cron, scripts y copias de seguridad. Pendiente de diseño para la UI. |
| Importar como `source='manual'` | Pierde la trazabilidad: «vino de una importación» desaparecería y una fila importada sería indistinguible de una escrita a mano. |
| Sincronización a la baja (`--sync`, `--replace`) | Borra o degrada filas existentes: contradice el merge aditivo y es destructiva. Si algún día existe, será un ADR aparte. |
| Exportar todo el catálogo (fotos, EXIF, historial) | Las fotos y sus metadatos incrustados ya viven en los originales (fuente de verdad); el historial de `runs` es derivado y no aporta al respaldo de usuario. |
| XMP como formato de intercambio | La exportación XMP sigue siendo futurible (ADR 0006) y su importación está prohibida; JSON de stdlib basta, sin dependencias ni shell. |
| Importar lugares pisando estados `ok`/`error` sin `--force` | Lo resuelto manda: pisar un `ok` con datos de un fichero viejo degradaría el catálogo; el import no habla con la red ni re-deriva nada. |
| Trasladar asignaciones entre hashes | Contradice la identidad estricta del ADR 0006: el contenido nuevo es una foto nueva sin etiquetas. El fichero respeta los hashes originales y los ausentes se informan. |
| Un fichero por foto o metadatos por ruta | Rompería la deduplicación por contenido; el hash ya es la clave estable entre bibliotecas. |

## Decisiones pendientes y riesgos

- **Export/import desde la UI** (comandos `:` con diálogo de fichero):
  pendiente de diseño; la CLI es la superficie de esta entrega.
- **Sincronización a la baja** (`--sync`, `--replace`): descartada por
  destructiva; si algún día existe, será un ADR aparte.
- **Exportación XMP** (futurible del ADR 0006, jamás importación) y su
  relación con este formato JSON: decisión futura.
- **Copia de seguridad automática** (disco local/S3, Fase 3): puede usar
  `foto-export` como pieza; no se diseña aquí.
- **Exportar más metadatos** (regiones de caras, futura tabla de atributos de
  personas, embeddings): el formato v1 no los incluye; versionarlo exigirá
  migrar lectores.
- **Riesgo de divergencia de `kind`**: si el fichero trae una etiqueta con
  `kind` distinto al de la biblioteca destino, el import avisa y conserva la
  existente; una biblioteca puede desviar su taxonomía de la del fichero, y
  la reexportación de la destino llevará su `kind`, no el original.
- **Ficheros grandes en memoria**: el import valida el fichero completo
  antes de escribir; para bibliotecas de cientos de miles de asignaciones el
  coste es aceptable pero habrá que medirlo antes de crecer el formato.
