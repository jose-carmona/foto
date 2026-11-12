# Prompt — Exportación e importación JSON de metadatos de usuario (ADR 0007)

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> exportar/importar metadatos. Integra las **decisiones del proyectista**
> (2026-11-12): exportar tags y lugares en **dos ficheros JSON separados**,
> importación **merge aditivo**, superficie **CLI nueva**, **ADR 0007 nuevo**
> que amplíe el `CHECK` de `source`, y hashes ausentes **informados y
> conservados**. La misión ya no es estudiar: es redactar el ADR 0007 e
> implementar la entrega.

## 0. Misión

Implementa en el repositorio `foto` la **exportación e importación** de los
metadatos que no son reconstruibles desde los originales: las **etiquetas**
(`tags` + `photo_tags`, incluido el favorito `fav`) y, como fichero separado,
los **lugares resueltos** (`place_*` de Nominatim). La entrega incluye el
**ADR 0007**, que se redacta al comienzo del paso 1, antes de tocar código, y
se muestra al proyectista antes de implementar.

Se ejecuta en **tres pasos diferenciados**, en este orden, cada uno terminado
y verificable por separado:

1. **Paso 1 — ADR 0007 y migración v5** (`docs/adr/`,
   `src/foto/storage/`): el ADR fija el formato de los ficheros, la semántica
   de importación y el esquema; la migración reconstruye `photo_tags` para
   ampliar el `CHECK` de `source` con el valor `'import'`. **No se toca la
   UI** y no existe aún herramienta nueva.
2. **Paso 2 — `foto-export`** (`src/foto/transfer/`): CLI sin Qt que vuelca
   tags y lugares a dos ficheros JSON. Solo lectura del catálogo.
3. **Paso 3 — `foto-import`** (`src/foto/transfer/`): CLI sin Qt que aplica
   los ficheros sobre un catálogo con merge aditivo idempotente. Único paso
   que escribe.

Resumen del comportamiento pedido:

- Los **metadatos incrustados en la foto** (EXIF: cámara, ISO, GPS…) **no se
  exportan**: siguen en los originales y son su fuente de verdad.
- Las **etiquetas y los favoritos** son metadatos de usuario que viven solo
  en el catálogo ([ADR 0006](docs/adr/0006-etiquetas.md)): borrar
  `catalog.sqlite` los pierde. Esta herramienta es su vía de copia de
  seguridad y su vía de migración entre bibliotecas (identidad por hash de
  contenido, así que el fichero es portable).
- Los **lugares** (`place_*`) son reconstruibles desde disco, pero solo a
  base de peticiones a Nominatim (1/s, con `error` pendiente de reanudación).
  Exportarlos permite restaurar una biblioteca sin repetir la geocodificación
  y moverla entre equipos. Por ser dato de terceros, viaja en un **fichero
  separado**, que se puede no exportar, no importar o borrar sin afectar a
  las etiquetas.
- **Exportar nunca escribe** en el catálogo; **importar nunca borra** nada.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, stack, convenciones y flujo. Fíjate
   en la regla 2 (el catálogo es la fuente de verdad de etiquetas y
   favoritos, «la importación no existe»): esta entrega la enmienda
   parcialmente (§ 13).
2. `README.md` — alcance, principios («Metadatos de usuario en el catálogo»,
   «Componentes reemplazables», «Local-first»), roadmap (Fase 3) y
   arquitectura: hay que cambiarlos.
3. `docs/adr/0001...0006` — en su totalidad; fíjate en el formato de
   redacción (Estado, Fecha, Decide, Ámbito, Contexto, Decisión numerada,
   Consecuencias, Alternativas descartadas, Decisiones pendientes), en las
   notas de revisión del ADR 0002, en el patrón de fases del 0005 y en las
   enmiendas con origen del 0003. El ADR 0006 define el esquema `tags` /
   `photo_tags` y declara explícitamente que «no existe el valor
   `xmp_import`»; el ADR 0007 lo enmienda con un valor distinto (`import`)
   y por una razón distinta.
4. `docs/adr/0005-fases-y-geocodificacion.md` — columnas `place_*`,
   estados (`pending`/`none`/`ok`/`error`), caché y throttle.
5. Código real: `src/foto/storage/catalog.py` (esquema, `user_version`,
   migraciones, `open_read_only`, `connect_read_write`, `put_place_result`,
   `update_place_error`, resumen y `get_photos_with_place_*`),
   `src/foto/indexer/cli.py` (patrón de CLI: argumentos, `--dry-run`,
   `--json`, `--quiet`/`--verbose`, lock, códigos de salida, resumen
   segmentado), `src/foto/storage/lock.py` (lock exclusivo del escritor),
   `src/foto/storage/tags.py` (normalización y conexión RW dedicada del
   visor), `src/foto/core/geocoding.py` (servicio inyectable) y
   `pyproject.toml` (`[project.scripts]`).

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible. No hagas `git commit`, `git push` ni cambios de
configuración de git sin permiso explícito.

---

## 2. Decisiones ya tomadas (no se discuten)

1. **Dos ficheros JSON separados**: uno para **tags** y otro para
   **lugares**. El de lugares es prescindible (dato reconstruible) y el de
   tags es imprescindible (dato de usuario). Nunca un fichero combinado.
2. **Un JSON por biblioteca y por tipo** (no JSON Lines, no un fichero por
   foto): cabecera con formato, versión, fecha de generación y conteos, y
   cuerpo listo para `diff` (orden determinista).
3. **Importación merge aditivo**: añade lo que falta, no toca lo existente,
   no borra nunca. Reejecutar un import es idempotente.
4. **Superficie CLI nueva** (`foto-export` / `foto-import`), sin Qt, al
   estilo de `foto-index`: apta para cron, scripts y copias de seguridad.
   Nada de UI ni comandos `:` en esta entrega.
5. **ADR 0007 nuevo** que enmiende el 0006: el `CHECK` de
   `photo_tags.source` se amplía con `'import'` (reconstrucción de tabla,
   bump de `user_version`). No se introduce `xmp_import`: la importación es
   del catálogo JSON, no de XMP, que sigue sin existir.
6. **Hashes ausentes al importar**: se informan en el resumen (conteo y,
   con `--verbose`, detalle) y se **conservan en el fichero** para futuros
   imports; no abortan y no se sincronizan a la baja (sin modo `--sync`).

---

# Paso 1 — ADR 0007 y migración v5

## 3. Decisiones vinculantes del modelo de datos

### 3.1. Ampliación de `photo_tags.source` y bump a v5

- `source ∈ ('manual', 'ml', 'import')`, con `DEFAULT 'manual'` intacto.
- Ampliar el `CHECK` cerrado exige **reconstruir la tabla** en SQLite:
  migración `user_version = 5` idempotente, en el patrón de casa
  (`executescript` con `IF NOT EXISTS` + parámetros): crear tabla nueva con
  el mismo nombre temporal, copiar filas, soltar la vieja, renombrar y
  recrear los tres índices (`photo_tags_assign_unique_idx` con sus
  `COALESCE`, `photo_tags_photo_idx`, `photo_tags_tag_idx`) **byte a byte
  iguales** a los del ADR 0006. Verifica con `.schema` que el resultado es
  idéntico al de un catálogo creado de cero.
- Sirve para catálogos v4 (y vacíos). Un catálogo más nuevo que el binario
  aborta con el mensaje actual. Tras actualizar el código, hay que ejecutar
  `foto-index` (o el import) una vez antes de abrir la app, como en v3→v4.
- El visor exige v5 con el mecanismo existente: `open_read_only` valida la
  versión mínima.

### 3.2. Reglas de procedencia con import (enmienda del ADR 0006)

- Toda fila de asignación **creada por el import** nace con
  `source='import'`, aunque el fichero traiga `manual` o `ml`: la
  procedencia de la fila es «vino de una importación a esta biblioteca».
  El fichero conserva la procedencia original para trazabilidad y
  reexportación.
- El `status` y la `confidence` del fichero **se conservan tal cual**
  (incluidas filas `rejected`, que existen para bloquear re-sugerencias).
  El ADR 0007 re-redacta la regla «confidence solo con `source='ml'`» del
  ADR 0006: pasa a regir la escritura del visor; el import conserva el
  valor del fichero si lo trae.
- **Filas existentes: intocables.** Si la asignación `(photo, tag, región)`
  ya existe, el import no la pisa ni le cambia `source`/`status`: merge
  aditivo puro.
- El valor `xmp_import` **sigue sin existir** y ninguna herramienta importa
  XMP (seguirá siendo futurible la exportación y prohibida su importación).

### 3.3. Identidad y unicidad (sin cambios)

- La identidad sigue siendo el **hash de contenido** `sha256:<64 hex>`; los
  `id` AUTOINCREMENT nunca viajan entre bibliotecas ni aparecen en el
  fichero: las asignaciones referencian la etiqueta por su
  **`name_normalized`** (clave estable por definición: es UNIQUE).
- La unicidad de asignaciones sigue siendo `(foto, etiqueta, región)`; el
  import respeta el índice `COALESCE` y usa `INSERT OR IGNORE`: la
  unicidad la garantiza el esquema, no el código.

## 4. ADR 0007 (redactar antes del código)

Escribe `docs/adr/0007-exportar-importar-metadatos.md` con el formato de
casa. Título propuesto: **«ADR 0007 — Exportación e importación JSON de
metadatos de usuario: tags y lugares»**. Muéstralo al proyectista antes de
implementar.

Índice de decisiones (renumera si hace falta, cúbrelas todas):

1. Motivo: las etiquetas no son reconstruibles y exigen copia de seguridad
   (Fase 3 del roadmap, consecuencia negativa del ADR 0006); los lugares
   son reconstruibles pero costosos (Nominatim 1/s). Los metadatos EXIF
   incrustados no se exportan: los originales siguen siendo su fuente de
   verdad.
2. Dos ficheros separados (`tags` / `places`) y por qué: el de lugares es
   descartable y toca a la privacidad de terceros (direcciones); el de tags
   es el verdadero respaldo.
3. Formato exacto de ambos ficheros (§ 5), versionado del formato y
   ordenación determinista para `diff`.
4. Semántica de `foto-export`: solo lectura, atomía de escritura,
   selección `--tags`/`--places`.
5. Semántica de `foto-import`: merge aditivo idempotente, nunca borra,
   creación de filas con `source='import'`, conservación de
   `status`/`confidence`, filas existentes intocables.
6. Lugares al importar: qué estados se rellenan (`pending`/`none`, nunca
   `ok`/`error` sin `--force`), qué columnas se escriben y quién firma
   `place_updated_at`.
7. Hashes ausentes: informar y conservar; sin modo `--sync` (descartado).
8. Enmienda del ADR 0006: `source='import'`, migración v5 con
   reconstrucción de `photo_tags`, re-redacción de la regla de
   `confidence`.
9. Concurrencia: export no bloquea (lectura WAL); import es escritor único
   y **toma `index.lock`** como `foto-index` (escribe en `photos.place_*`
   y en `tags`/`photo_tags`: escribir `photos` es trabajo de herramientas
   de indexado, nunca de la UI).
10. Privacidad: los ficheros contienen datos personales (nombres de
    personas, direcciones); los logs y resúmenes no los vuelcan (criterio
    ADR 0005/0006); el fichero va donde el usuario diga y `.foto/exports/`
    por defecto está ignorado por git.

Consecuencias negativas que el ADR debe recoger: el coste de la
reconstrucción de `photo_tags` en v5 (y que el visor exige ejecutar
`foto-index` tras actualizar); que el fichero JSON no es una copia
completa del catálogo (no sustituye a la copia de la Fase 3: no contiene
fotos ni historial de `runs`); que un import de lugares rellena `photos`
(por eso firma el lock); que dos bibliotecas que importan el mismo fichero
divergen en `source`/fechas; y que el formato v1 queda congelado (cambiarlo
exigirá versionar y migrar el lector).

Alternativas descartadas (mínimo, en tabla): un único JSON combinado;
JSON Lines; export/import en la UI; importar como `source='manual'` (pierde
trazabilidad); sync bidireccional o modo `--replace` (borra, contradice el
merge aditivo); exportar todo el catálogo (fotos y metadatos EXIF incluidos:
ya viven en los originales); XMP como formato de intercambio; importar
lugares pisando estados `ok`/`error`; trasladar asignaciones entre hashes.

---

# Paso 2 — `foto-export`

## 5. Formato de los ficheros (vinculante)

Dos ficheros, UTF-8, `indent=2`, `ensure_ascii=False`, terminados en `\n`,
orden determinista (para que dos exportaciones seguidas sin cambios
produzcan el mismo fichero byte a byte, apto para `diff` y para
versionarlo). Escritura **atómica**: fichero temporal en el directorio de
destino + `rename`, como las miniaturas y la configuración.

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

- `tags` ordenadas por `normalized`; `assignments` por
  `(photo, normalized, región)`; `places` por `photo`. `tag` referencia por
  `name_normalized`; nunca `id` de etiqueta ni de foto.
- `region`: `null` (asignación de foto completa) o array `[x, y, w, h]` en
  `[0,1]` con `w,h > 0` (exactamente el modelo del ADR 0006 § 7).
- `confidence`: `null` salvo que la fila lo tenga (filas ML, o conservado
  por imports previos tras v5).
- Se exportan **todas** las etiquetas, incluidas las huérfanas (sin
  asignaciones: son taxonomía del usuario) e incluida `fav`
  (`kind='system'`).
- `places.json` solo incluye filas con `place_status='ok'`; `address` es el
  `place_address_json` parseado (si no parsea, se exporta como `null` y se
  avisa, nunca se aborta); `resolved_at` es `place_updated_at`.
- La cabecera lleva `generated_at` con offset explícito (misma convención
  que `created_at`/`updated_at` del catálogo).

## 6. CLI `foto-export`

```sh
uv run foto-export --library ~/Fotos
uv run foto-export --library ~/Fotos --tags --out ~/backups
uv run foto-export --library ~/Fotos --places --out ~/backups
uv run foto-export --library ~/Fotos --dry-run
```

| Opción | Comportamiento |
| --- | --- |
| `--library PATH` | Raíz de la biblioteca; misma precedencia que `foto-index` (ADR 0001). |
| `--tags` / `--places` | Selección; por defecto se exportan ambos, a ficheros separados. |
| `--out DIR` | Directorio de destino; por defecto `<fototeca>/.foto/exports/`. Crea el directorio si falta. |
| `--dry-run` | Calcula y valida todo, no escribe ficheros. |
| `--json` | Resumen JSON en stdout; los logs a stderr (mismo patrón que `foto-index`). |
| `--quiet` / `--verbose` | Menos o más detalle. |

- **Solo lectura** del catálogo (`open_read_only`); no toma `index.lock` y
  funciona con `foto-index` o la app corriendo (WAL).
- Nombres de fichero fijos: `tags.json` y `places.json`. Si existe uno
  anterior se sobreescribe (es una exportación, no un log); con `--dry-run`
  se informa del tamaño y conteo previsto sin escribir.
- Resumen (texto y JSON): etiquetas exportadas, asignaciones, lugares,
  etiquetas huérfanas, avisos (`place_address_json` no parseado) y rutas de
  destino. **Los resúmenes no contienen nombres de etiquetas ni direcciones**
  (privacidad, § 4.10): solo conteos.
- Códigos de salida como `foto-index`: `0` éxito, `1` error no fatal (avisos
  incluidos), `2` configuración/catálogo inválido, `3` otro proceso con el
  lock (no debería ocurrir: no toma lock), `130` SIGINT.

## 7. Diseño esperado del paso 2 (orientativo, no literal)

- Módulo nuevo `src/foto/transfer/` (sin Qt, sin dependencias nuevas de
  runtime; `json` y stdlib bastan), con la misma disciplina de `storage/`:
  dataclasses congelados, type hints, errores propios en `core`-style,
  nunca `except: pass`:
  - `transfer/model.py` — dataclasses de los dos formatos + funciones puras
    `(catálogo) -> fichero` y de validación (hash bien formado, `kind`
    válido, región en rango, versión de formato conocida al leer).
  - `transfer/exporter.py` — lectura del catálogo y generación de los
    ficheros; atomía; nada de SQL fuera de aquí.
  - `transfer/cli_export.py` — la CLI (estilo `indexer/cli.py`), expuesta
    en `pyproject.toml` como `foto-export = "foto.transfer.cli_export:main"`.
- `storage/`: solo la migración del paso 1; el export usa las conexiones de
  solo lectura existentes sin tocar el módulo de tags del visor.
- La serialización JSON es una función pura testeada sin ficheros
  (dataclass → dict → texto), y el orden determinista se prueba comparando
  dos exportaciones.

## 8. Tests del paso 2

Sin Qt ni pantalla, como el resto de `core/`/`storage/`. Cubre, al menos:

- Formato: cabecera (formato, versión, conteos); orden determinista (dos
  exportaciones idénticas byte a byte); `region` `null` y `[x,y,w,h]`;
  asignaciones con las tres `source` y los tres `status`; `confidence`
  `null` y numérico; etiquetas huérfanas y `fav` incluidas.
- `places.json` solo con `place_status='ok'`; `place_address_json` no
  parseable → `address: null` + aviso, sin abortar.
- Atomía: el fichero destino no queda truncado si la serialización falla
  (simula un error a mitad); `--dry-run` no escribe.
- Resumen: conteos correctos; sin nombres ni direcciones en el resumen
  (test que lo comprueba).
- Todo bug corregido lleva su test de regresión.

## 9. Verificación del paso 2

1. `uv sync`, `uv run pytest` y `uv run ruff check .` — todo en verde.
2. `uv run foto-export --library <biblioteca de prueba> --dry-run` y luego
   real: revisa los dos ficheros con `jq` (o `python -m json.tool`);
   re-exporta y comprueba con `diff` que no cambia; valida que `fav` y las
   huérfanas están; exporta con `foto-index` corriendo en paralelo sin que
   nada se bloquee.
3. **Criterio de fin del paso 2:** ADR 0007 aceptado, migración v5 en verde
   y `foto-export` genera los dos ficheros válidos y deterministas. No
   continúes al paso 3 sin cerrar esto.

---

# Paso 3 — `foto-import`

## 10. Semántica de importación (vinculante)

### 10.1. Merge aditivo idempotente

- **Etiquetas**: `upsert` por `name_normalized`. Si no existe, se crea con
  el `kind` y las fechas del fichero. Si existe:
  - mismo `kind` → se conserva la existente sin tocarla;
  - `kind` distinto → **aviso** (no error) y se conserva la existente; las
    asignaciones del fichero se aplican igual sobre la etiqueta existente;
  - `kind='system'` con nombre distinto de `fav` → no puede existir por el
    índice singleton: aviso y fila ignorada.
- **Asignaciones**: para cada fila del fichero con foto presente en el
  catálogo, `INSERT OR IGNORE` con `source='import'` (§ 3.2), `status`,
  `confidence`, `region` y fechas del fichero. Si la asignación
  `(photo, tag, región)` ya existe, **no se toca** (ni `source`, ni
  `status`, ni fechas).
- **Lugares**: solo fotos presentes en el catálogo. Se rellenan las
  columnas `place_*` **solo** de fotos con `place_status ∈ ('pending',
  'none')`; nunca se pisan `ok` ni `error` (merge aditivo: lo resuelto
  manda sobre lo importado). Se escribe el paquete completo del fichero
  (`country`…`display_name`, `address_json` re-serializado canónico),
  `place_status='ok'`, `place_error=NULL` y `place_updated_at` con la hora
  del import (por parámetro, estilo migraciones): la fila cambia ahora.
  Con `--force` se aceptan además `error` (nunca `ok`): el import puede
  reintentar lo que Nominatim rechazó, pero no sobreescribe lo ya
  resuelto.
- **Hashes ausentes**: se cuentan y se informan en el resumen
  (`hashes_ausentes` en texto y JSON; detalle con `--verbose`, sin volcar
  rutas personales). No abortan y no se guardan en ninguna parte: el
  fichero los conserva para futuros imports.
- Reejecutar el mismo import sobre el mismo catálogo **no cambia nada**
  (idempotencia): segunda pasada con todos los contadores de «aplicado» a
  cero y solo «ya existía».

### 10.2. Validación y errores

- El fichero se valida **completo antes de escribir nada** (§ 7:
  `transfer/model.py`): formato y versión conocidos, hashes bien formados,
  `kind`/`status`/`region` en rango, referencias `tag` que existan en la
  sección `tags` del propio fichero. Un fichero inválido aborta con
  código `2` y **cero escrituras**.
- Todo el import se aplica en **una única transacción** por fichero: o
  aplica el fichero entero o no aplica nada (con el lock tomado, no hay
  riesgo de interferir con nadie).
- Un fichero de formato más nuevo que el binario aborta con mensaje claro
  (mismo criterio que el catálogo).

## 11. CLI `foto-import`

```sh
uv run foto-import --library ~/Fotos --tags ~/backups/tags.json
uv run foto-import --library ~/Fotos --places ~/backups/places.json
uv run foto-import --library ~/Fotos --tags a.json --places b.json
uv run foto-import --library ~/Fotos --tags a.json --dry-run --verbose
```

| Opción | Comportamiento |
| --- | --- |
| `--library PATH` | Raíz de la biblioteca; misma precedencia que `foto-index`. |
| `--tags FILE` / `--places FILE` | Ficheros a importar; al menos uno. |
| `--force` | Permite rellenar lugares de fotos con `place_status='error'` (nunca `ok`). |
| `--dry-run` | Valida y calcula lo que cambiaría (estados destino incluidos) sin escribir. |
| `--json` / `--quiet` / `--verbose` | Mismo patrón que `foto-export` y `foto-index`. |

- **Escritor único**: toma `index.lock` como `foto-index`
  (`storage/lock.py`); si otro indexador lo tiene, sale con código `3` y
  mensaje. Es la única herramienta fuera del indexador que escribe en
  `photos` (columnas `place_*`): el ADR 0007 lo justifica (§ 4.9).
- `--dry-run` no toma el lock ni escribe: valida contra una copia en
  memoria del catálogo (mismo patrón que `--dry-run` de `foto-index`).
- Resumen (texto y JSON), por fichero y total: etiquetas creadas /
  ya existentes (con avisos de `kind`), asignaciones aplicadas /
  ya existentes / sin foto, lugares rellenados / ya resueltos omitidos /
  rellenados con `--force`, avisos. Sin nombres de etiquetas ni direcciones
  en el resumen.
- Códigos de salida: `0` éxito, `1` error no fatal (avisos), `2`
  configuración/fichero/catálogo inválido, `3` lock ocupado, `130` SIGINT.
- Tras un import, el catálogo queda en v5 y **la app puede abrirlo
  directamente** (el import no invalida nada: no toca `photos` salvo
  `place_*`).

## 12. Diseño y tests del paso 3

Diseño (orientativo, no literal):

- `transfer/importer.py` — validación, transacción única, upsert de
  etiquetas, `INSERT OR IGNORE` de asignaciones, actualización condicionada
  de `place_*`; `transfer/cli_import.py` — la CLI, expuesta como
  `foto-import = "foto.transfer.cli_import:main"`.
- Reutiliza `storage/lock.py` y las conexiones existentes; no duplica SQL
  del módulo de tags del visor si una función sirve para ambos (súbela a
  `storage/tags.py`).

Tests (sin Qt ni pantalla). Cubre, al menos:

- Idempotencia real: importar dos veces el mismo fichero deja el catálogo
  idéntico (comparar con `iterdump` o conteos + hash de contenido lógico).
- Aditivo puro: una asignación existente con `source='manual'` no cambia a
  `'import'` ni cambia fechas; una etiqueta existente conserva `kind`.
- `source='import'` en las filas creadas por import; `status`/`confidence`
  del fichero conservados; filas `rejected` importadas y respetadas.
- Aviso con `kind` distinto; `system` distinto de `fav` ignorado.
- Unicidad: importar dos asignaciones de foto completa para la misma
  etiqueta no duplica (el `COALESCE` del esquema manda).
- Lugares: `pending`/`none` rellenados; `ok` intocado con y sin `--force`;
  `error` solo con `--force`; `place_updated_at` = hora del import.
- Hashes ausentes: contados, reportados, sin abortar y sin escritura
  parcial (transacción).
- Validación: fichero inválido → código `2` y cero escrituras; versión de
  formato desconocida → error claro.
- Lock: un `foto-index` con el lock simulado → código `3`; `--dry-run`
  funciona sin lock.
- Migración v5 (del paso 1, si no se cerró allí): v4 → v5 idempotente;
  esquema reconstruido idéntico al de cero; catálogo más nuevo aborta.
- Todo bug corregido lleva su test de regresión.

Verificación del paso 3:

1. `uv sync`, `uv run pytest` y `uv run ruff check .` — todo en verde.
2. Ciclo completo en una biblioteca de prueba: etiqueta algo a mano (o por
   SQL), `foto-export`, borra `catalog.sqlite`, re-indexa, `foto-import`,
   y comprueba que etiquetas, asignaciones, favoritos y lugares vuelven
   (excepto los de fotos que ya estaban `ok`). Repite el import: idempotente.
3. Ciclo entre bibliotecas: exporta de A e importa en B (misma foto,
   distinta ruta): la asignación viaja por hash.
4. **Criterio de fin del paso 3:** ciclo export → destruir → import
   restaura las etiquetas; suite en verde; cero cambios pendientes.

---

## 13. Documentación (parte de la entrega)

| Fichero | Sección | Cambio |
| --- | --- | --- |
| `docs/adr/0007-exportar-importar-metadatos.md` | — | Nuevo, formato de casa, índice de § 4 (paso 1) |
| `docs/adr/0006-etiquetas.md` | Nota de revisión | `photo_tags.source` se amplió con `'import'` por ADR 0007 (migración v5); el resto de la decisión no cambia |
| `docs/adr/0002-indexado.md` | Nota de revisión | `place_*` son rellenables por `foto-import` (escritor único con lock) según ADR 0007 |
| `AGENT.md` | Reglas no negociables · regla 2 | «La importación no existe» → la importación JSON del catálogo existe (`foto-import`, merge aditivo); la importación XMP sigue sin existir y la exportación XMP sigue siendo futurible |
| `AGENT.md` | Estado actual / «Indexador y catálogo» | Esquema v5; herramientas `foto-export`/`foto-import` en `src/foto/transfer/`; el import toma `index.lock` |
| `AGENT.md` | Comandos disponibles | Añade los dos comandos con un ejemplo mínimo |
| `README.md` | Principios · «Metadatos de usuario en el catálogo» | La exportación/importación JSON existe; XMP sigue como futurible |
| `README.md` | Principios · «Componentes reemplazables» | Las etiquetas y los favoritos «exigen copia de seguridad (Fase 3)» → se respaldan con `foto-export` (y la copia de la Fase 3 los cubrirá) |
| `README.md` | Roadmap · Fase 3 | Casilla marcada: exportación/importación JSON de tags y lugares |
| `README.md` | Indexador · opciones y estructura | Sección «Exportar e importar» con las dos CLIs; añade `transfer/` al árbol |
| `README.md` | Documentación | Añade el ADR 0007 a la lista |

## 14. Pendientes que el ADR deja por escrito y no se implementan

- **Export/import desde la UI** (comandos `:` con diálogo de fichero):
  pendiente de diseño; la CLI es la superficie de esta entrega.
- **Sincronización a la baja** (`--sync`, `--replace`): descartada; si
  algún día existe, será una decisión de ADR aparte por ser destructiva.
- **Exportación XMP** (futurible del ADR 0006, jamás importación) y su
  relación con este formato JSON: decisión futura.
- **Copia de seguridad automática** (disco local/S3, Fase 3): puede usar
  `foto-export` como pieza, no se diseña aquí.
- **Exportar más metadatos** (regiones de caras, tablas futuras de
  atributos de personas, embeddings): el formato v1 no los incluye;
  versionarlo exigirá migrar lectores.
- Autocompletado del argumento de `:tag`, explorador de etiquetas,
  creación/renombrado/borrado desde la UI: sin cambios (ADR 0006).

## 15. Fuera del objetivo

- Exportar o importar **metadatos EXIF incrustados** (cámara, ISO, GPS…):
  viven en los originales y su fuente de verdad sigue siendo la foto.
- Exportar fotos, miniaturas, metadatos de imagen o cualquier cosa
  reconstruible desde el contenido, salvo los lugares (decisión explícita
  de esta entrega).
- XMP: ni exportar (sigue futurible) ni importar (prohibido).
- UI, comandos `:`, teclas nuevas; la ayuda integrada no cambia.
- Álbumes, consultas guardadas, ML, caras, jerarquía de etiquetas.
- Cambiar el pipeline de fases del indexador ni su esquema de contadores
  (los resúmenes del indexador no conocen esta herramienta).
- `git commit`, `git push` o cambios de configuración de git sin permiso
  explícito.
