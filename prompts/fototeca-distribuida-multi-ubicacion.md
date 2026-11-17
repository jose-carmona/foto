# Prompt — Fototeca distribuida en varias ubicaciones (propuesta para el ADR 0010)

> Feature **fuera del roadmap actual**: la fototeca puede repartirse entre
> varias ubicaciones físicas (SSD rápido + disco USB externo). Las favoritas
> y las últimas fotos viven en el disco rápido; el resto, en el disco
> externo. El sistema debe **avisar y tolerar** que una ubicación no esté
> disponible en un momento dado. No existe aún una decisión de arquitectura:
> la **primera tarea es redactar el ADR 0010** a partir de la propuesta de
> este informe; nada de lo que sigue es vinculante hasta que ese ADR se
> acepte.
>
> **Seguimiento.** Este prompt es el registro de tareas. Al terminar cada
> tarea: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si el trabajo genera una pendiente nueva, añádela al final del paso
> correspondiente con el mismo formato. Una tarea solo está cerrada con sus
> tests en verde y `uv run pytest` + `uv run ruff check .` completos.

## 0. Misión

Convertir la fototeca de «una única raíz» en «un conjunto con nombre de
ubicaciones», sin romper el caso de raíz única. El modelo de funcionamiento
descansa en tres reglas, **tomadas como decisiones ya cerradas** (no son
materia del ADR; el ADR las registra y las hace cumplir):

1. **Declaración**: las ubicaciones se declaran **solo en el fichero de
   configuración** — `[[library.locations]]` en config v2; `[library].path`
   en v1 equivale a una única ubicación `default`. Desaparecen `--library`
   y `--location`; no existe ninguna otra vía (CLI ni entorno) de declarar
   ubicaciones.
2. **Sincronización**: `foto-index` es el único componente que lee esa
   declaración. Al arrancar compara la config con el catálogo: registra las
   ubicaciones nuevas y **marca como eliminadas** — a la ubicación y a sus
   fotos — las que se hayan quitado de la config. No borra nada: la limpieza
   de filas es trabajo de `--prune`.
3. **Verdad**: para la UI (y para export/import) la fuente de verdad es el
   catálogo: ubicaciones, fotos, rutas, etiquetas y contadores salen de la
   BD. La config solo interviene en la cadena de metainformación y en el
   diálogo de primera ejecución.

Sobre ese modelo, una garantía nueva y una base preparada:

1. **Tolerancia**: una ubicación ausente (USB desmontado) no bloquea la
   apertura, el indexado ni la UI; se avisa de forma explícita y las fotos
   de esa ubicación se degradan (no se borran, no se pierden metadatos).
   Una ubicación ausente **nunca** se da de baja: la baja solo la provoca
   quitarla de la config.
2. **Emplazamiento (base preparada, traslado diferido)**: las ubicaciones
   declaran su `tier` (`fast`/`archive`) como base de una futura política
   de emplazamiento (favoritas y últimas en el disco rápido). **Esta
   feature no mueve ficheros**: toda herramienta de traslado entre
   ubicaciones queda fuera del ámbito (§ 6) y se estudiará en una feature
   futura (§ 7.2).

El objetivo de fondo va más allá de «dos discos»: el diseño debe producir
**ubicaciones genéricas**. Tres escenarios futuros condicionan el modelo
(§ 3.9): vídeos en una ubicación separada cuando el proyecto pase de
fototeca a fotovideoteca, una fototeca de Apple Photos como una ubicación
más, y volúmenes remotos tipo S3. El criterio es que ninguno exija
rediseñar nada: **añadir una ubicación a la configuración y que funcione**.

La entrega se plantea en **cinco pasos** secuenciados, cada uno terminado y
verificable por separado. El paso 1 solo documenta y fija el estado actual.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, convenciones, sección
   «Configuración». Sin `git commit`/`push` sin permiso explícito.
2. `docs/adr/0001-configuracion.md` y `docs/adr/0008-resolucion-de-
   ubicaciones.md` — las cadenas de resolución vigentes; esta feature las
   **amplía** (más de una ubicación) y **recorta** (la fototeca pasa a
   declararse solo en la config: fuera `--library` y `FOTO_LIBRARY`). El
   ADR 0010 debe registrar ambas cosas como revisión de esos ADR.
3. `docs/adr/0002-indexado.md`, `0003-consumo-del-indice-desde-el-visor.md`
   y `0004-cache-de-miniaturas.md` — dónde viven hoy catálogo, índice y
   miniaturas; la metainformación ya puede vivir fuera de la fototeca.
4. `docs/adr/0006-etiquetas.md` — las etiquetas (incluida `fav`) viven solo
   en el catálogo: **funcionan sin las fotos a mano**. Clave para la
   tolerancia.
5. Código real:
   - `src/foto/storage/config.py` — `load_config`, `save_config`
     conservador, `AppConfig`, validación de `version` (solo v1 hoy).
   - `src/foto/storage/resolver.py` — único sitio de precedencia
     (`--library` → `FOTO_LIBRARY` → `[library].path`).
   - `src/foto/storage/library.py` — `ensure_library`, `load_library`,
     `library.toml` con `root`.
   - `src/foto/storage/catalog.py` — esquema v5, tabla de ubicaciones por
     foto (`rel_path`, estado `present`), `list_present_locations()`.
   - `src/foto/core/scanner.py` y `src/foto/core/indexing.py` — el escaneo
     y el `Indexer` asumen **una raíz**.
   - `src/foto/core/library.py` — `load_from_catalog`: agrupa por hash y elige
     ubicación canónica (menor `rel_path`); `CatalogPhoto`.
   - `src/foto/ui/app.py`, `src/foto/ui/window.py`,
     `src/foto/ui/presentation.py` — apertura, carga y presentación.
   - `src/foto/indexer/cli.py`, `src/foto/transfer/cli_export.py`,
     `src/foto/transfer/cli_import.py` — patrón de CLI y epílogos; export e
     import usan la raíz **solo** para derivar la metainformación.
6. Tests de referencia: `tests/test_library_resolution.py`,
   `tests/test_config_persistence.py`, `tests/test_catalog.py`,
   `tests/test_cli.py`, `tests/test_ui_load_photos.py`.

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible.

---

## 2. Estado actual relevante (lo que condiciona el diseño)

- **El catálogo ya admite varias ubicaciones por foto** (deduplicación por
  hash SHA-256; ubicación canónica = menor `rel_path`), pero todas las
  rutas son relativas a **una sola raíz**: no hay forma de distinguir de
  qué disco viene cada `rel_path`.
- **La tabla per-foto ya se llama `locations`** (`rel_path` +
  `photo_hash` + estado `present`/`missing`): el nombre está ocupado y la
  tabla nueva de ubicaciones con nombre (§ 3.4) exige un renombrado o un
  nombre distinto.
- **El escaneo y el indexador asumen una raíz única**
  (`core/scanner.py`, `core/indexing.py`).
- **La metainformación es única y ya externalizable** (ADR 0008):
  `library.toml`, `catalog.sqlite`, `thumbs/`, `index.lock`, resuelta por
  la cadena `--metadata` → `FOTO_METADATA` → `[metadata].path` →
  `<fototeca>/.foto`. Este es el punto de apoyo: la metainformación debe
  vivir en una ubicación **siempre disponible** (el SSD) y las fotos
  repartirse.
- **La UI ya consume el catálogo** (ADR 0003), pero construye las rutas
  absolutas con la raíz única resuelta
  (`load_from_catalog(catalog, library_path)`): es el punto a cambiar para
  que la BD sea su única fuente de verdad.
- **Favoritas** = etiqueta de sistema `fav` en el catálogo; **no existe el
  concepto de «últimas»** (solo ordenación por fecha en la UI).
- **Config**: `version` solo admite 1; `[library].path` es una sola ruta;
  `save_config` solo toca `[library].path`.
- **Env/CLI**: `FOTO_LIBRARY` y `--library` son una sola ruta en los cuatro
  binarios; no existe `--location` ni `--offline` (eran propuesta de la
  versión anterior de este prompt y se han retirado).

## 3. Propuesta de diseño (base para el ADR 0010)

### 3.1. Configuración: `version = 2` y `[[library.locations]]`

```toml
version = 2

[library]
# v1: compatibilidad. Si existe [[library.locations]], una [library].path
# suelta es error de configuración (ambigüedad prohibida).

[[library.locations]]
name = "ssd"                     # identificador estable, único
path = "~/Fotos/Principal"       # expansión como hoy (~ y relativa al config)
tier = "fast"                    # fast | archive

[[library.locations]]
name = "usb"
path = "/Volumes/FotoVault"
tier = "archive"

[metadata]
# Único para TODAS las ubicaciones: un solo catálogo, un solo library.toml,
# una sola caché de miniaturas. Carpeta resuelta por prioridad:
# --metadata → FOTO_METADATA → [metadata].path (→ default, ver § 3.2).
# Recomendación documentada: en la ubicación rápida; nunca en el USB de
# archivo. Sin cambios de formato (ADR 0008).

# Sin sección [placement]: la política de emplazamiento (favoritas y
# recientes en el disco rápido) se difiere a una feature futura (§ 7.2).
# El campo tier de cada ubicación queda como base de esa política.
```

Reglas:

- Un fichero v1 con `[library].path` sigue funcionando: se convierte en una
  única ubicación de nombre `default`. Cero migración para el dueño.
- `[[library.locations]]` solo es válido en `version = 2`; en un v1 es
  `ConfigError` con mensaje que sugiere subir la versión.
- Nombres: no vacíos, únicos, `fast`/`archive` son los únicos `tier`. Las
  rutas expandidas también deben ser únicas (dos nombres sobre el mismo
  directorio no aportan nada y ensucian el diff de sincronización).
- **Este fichero es la única fuente de ubicaciones** (§ 3.2): no hay lista
  en `--library`, ni `NOMBRE=ruta` en `FOTO_LIBRARY`, ni `--location`.
- `save_config` conserva su carácter conservador: solo escribe
  `[library].path`; las ubicaciones se editan a mano (igual que
  `[metadata]`). Además **rehúsa** escribir `[library].path` en un fichero
  v2 que ya tiene `[[library.locations]]` (crearía la ambigüedad prohibida).

### 3.2. Resolución: la config es la única fuente de ubicaciones

| Fuente | Hoy | Propuesto |
| --- | --- | --- |
| `--library PATH` | una ruta | **desaparece** de los cuatro binarios (argparse lo rechaza; el epílogo enseña la vía nueva) |
| `FOTO_LIBRARY` | una ruta | **desaparece**; si está definida, error explícito que explique que las ubicaciones se declaran en `config.toml` (nunca ignorarla en silencio) |
| `--location`, `--offline`, `FOTO_LOCATIONS` | — | no existen: las ubicaciones no se declaran ni se simulan por CLI ni por entorno |
| `--metadata PATH` | una ruta (ADR 0008) | **sin cambios**: un solo directorio de metadatos para todas las ubicaciones |
| `FOTO_METADATA` | una ruta (ADR 0008) | **sin cambios** |
| `FOTO_CONFIG`, `--config` | sin cambios | sin cambios |

La cadena de la fototeca (ADR 0001) colapsa a dos niveles:

- `[[library.locations]]` / `[library].path` de `config.toml` — única
  fuente, leída por `foto-index` para saber **qué fototecas indexar**.
- Nada configurado → diálogo de primera ejecución (solo UI, persiste
  `[library].path` como hoy); `foto-index` sin ubicaciones declaradas
  termina con error y mensaje que apunte a `[[library.locations]]`.

En `storage/resolver.py`:

- Nueva `resolve_locations()` → lista de `Location(name, path, tier)` solo
  desde la config (v1 = única `default`). Sin parámetro de biblioteca ni
  entorno inyectable de rutas.
- `resolve_library_path` **se retira** (sus niveles CLI/env ya no existen):
  los cuatro llamadores migran. Los tests dejan de inyectar la fototeca
  por entorno y usan configs de fixture con `--config`.
- La cadena de metainformación (ADR 0008) queda intacta en niveles
  (`--metadata` → `FOTO_METADATA` → `[metadata].path`) y gana solo la
  regla del default: con **una sola** ubicación declarada, `<ubicación>/.foto`
  (compat total, incluida la regla de Apple del ADR 0009 aplicada a esa
  ubicación); con **varias** y sin override → `ConfigError` explícito que
  pida concretar la metainformación (ambigüedad prohibida).
- `foto-export` y `foto-import` solo necesitaban la raíz para derivar la
  metainformación: con la regla del default les basta y no necesitan
  ninguna guardia multi-ubicación.

### 3.3. Sincronización config ↔ catálogo al arrancar `foto-index`

`foto-index` es el único lector de la declaración. Al arrancar — tras
resolver la metainformación, abrir (y migrar si toca) el catálogo y
**dentro del lock del indexador** — compara las ubicaciones declaradas en
la config con la tabla `locations` del catálogo. La identidad de una
ubicación es su `name` (estable; `path` y `tier` pueden cambiar):

- **Alta** — nombre declarado que no está en el catálogo: se registra con
  estado `active` y se escanea en esa misma ejecución (si está
  disponible).
- **Cambio** — nombre declarado que ya está `active` con otro `path` o
  `tier`: se actualiza, con aviso en el resumen. Las filas de fotos
  siguen siendo válidas: son rutas relativas + `location_id`; el escaneo
  reconciliará lo que haya (o no) en la ruta nueva.
- **Baja** — nombre del catálogo `active` que ya no está en la config: la
  ubicación pasa a `deleted` (con `deleted_at`) y **sus filas de fotos se
  marcan `deleted`**. No se borra nada: ni filas ni ficheros. La ejecución
  lo reporta con contadores (`--json`); la limpieza de filas es de
  `--prune` (§ 3.5). Nunca se borran ficheros por quitar una ubicación de
  la config (regla 1 del AGENT.md).
- **Reactivación** — nombre declarado que está `deleted`: vuelve a
  `active` con aviso. No restaura filas por sí sola: las restaura el
  escaneo de esa ubicación (ficheros vistos → `present`). Si `--prune`
  ya las había limpiado, el escaneo las repuebla: el catálogo es
  derivable.
- **Renombrar** es baja + alta: dos operaciones del mismo diff, sin caso
  especial; el escaneo repuebla por hash (deduplicación natural).

Propiedades: idempotente (dos ejecuciones seguidas con la misma config no
cambian nada), determinista y reportada — `--dry-run` calcula el diff sin
escribir; `--json` gana un bloque `locations` con `added`, `removed`,
`reactivated`, `path_changed`, `scanned` y `unavailable`.

Casos límite con tests obligatorios:

- Catálogo v5 (sin ubicaciones) + config que declara **una** ubicación: la
  migración v6 asigna todas las filas heredadas a esa ubicación.
- Catálogo v5 + config que declara **varias**: error explícito pidiendo
  ejecutar primero con la ubicación original declarada sola, porque las
  filas heredadas no saben a qué raíz pertenecen.
- Una ubicación ausente **nunca** pasa a `deleted` por estar desconectada:
  la baja solo la provoca la config. Ausencia = aviso y degradación
  (§ 3.6).

### 3.4. Catálogo: esquema v6

- **Colisión de nombres, resolver primero**: hoy `locations` es la tabla
  per-foto (`rel_path`, `photo_hash`, `size`, `mtime_ns`, …). Propuesta:
  renombrarla a **`files`** (es lo que contiene: apariciones de fichero) y
  reservar `locations` para la tabla nueva de ubicaciones con nombre.
  Alternativa: tabla nueva `volumes` y no tocar `locations`. Decide el
  ADR; el resto de esta sección asume la propuesta.
- Tabla nueva `locations` (`id`, `name` único, `path`, `tier`,
  `state` ∈ `active|deleted`, `created_at`, `deleted_at`,
  `last_scanned_at`).
- `files` gana `location_id` (FK) junto a `rel_path`; el estado por fila
  gana `deleted` (`present|missing|deleted`). No hace falta `deleted_at`
  por fila: la ventana de poda usa el `deleted_at` de la ubicación.
- Migración v5 → v6: renombra, crea la ubicación `default` (la única
  declarada en la config; si hay varias, caso límite de § 3.3) y asigna
  todas las filas a ella. Rutas relativas intactas: sobreviven a
  traslados como hasta ahora.
- Ubicación canónica por foto: menor `rel_path` **desempatado por tier**
  (`fast` gana a `archive`): si una foto está en los dos discos, la UI lee
  del SSD.
- El escáner registra `(location_id, rel_path)`; el estado `present` es por
  ubicación, no global.
- `photos` no cambia de clave; `--prune` sigue limpiando fotos huérfanas
  (cero filas en `files`) como hoy.

### 3.5. Indexado y `--prune` multi-ubicación

- `foto-index` recorre **solo las ubicaciones declaradas y disponibles** y
  actualiza únicamente las filas de esas ubicaciones. Una ubicación
  declarada ausente: aviso por stderr/`--json` y salida 0 (no es error); el
  epílogo lo documenta.
- **Regla de seguridad (no negociable)**: `--prune` solo borra filas del
  catálogo —**nunca ficheros**— y solo de (a) ubicaciones verificadas
  presentes en esa ejecución (como hoy) y (b) ubicaciones `deleted`
  (dadas de baja por la config, § 3.3). Nunca toca filas de ubicaciones
  declaradas pero no escaneadas por estar ausentes.
- Propuesta (cierra el ADR): las filas `deleted` usan la misma ventana de
  gracia `--prune-after` que las `missing`, para que una edición
  desafortunada de la config sea reversible sin rescan.
- `library.toml` sigue único (en la metainformación). Con varias raíces,
  `root` pierde su sentido único: propuesta de sustituirlo por la tabla
  `locations` del catálogo (la coherencia del descriptor pasa a ser
  «descriptor ↔ catálogo»); mantenerlo solo en el caso de ubicación única
  es la alternativa. Decide el ADR.

### 3.6. Disponibilidad y tolerancia

Estados por ubicación, evaluados con una función pura e inyectable
compartida en `storage/` (sin Qt) que usan `foto-index` antes de escanear
y la UI al cargar; no en cada acceso a fichero:

- `available` — la raíz existe y es directorio.
- `unavailable` — no existe (USB desmontado): aviso y degradación.

Sin marcador de volumen (decisión de 2026-11-17, § 7.2): no existe el
estado `mismatch` ni el fichero `.foto-volume.toml`. La disponibilidad es
solo «existe y es directorio», lo que mantiene intacta la regla 1 del
AGENT.md (ninguna escritura fuera de `.foto/`). Consecuencia aceptada y
documentada: si se monta **otro** volumen en el mismo punto de montaje,
el escaneo se comporta con normalidad — muchas filas pasan a `missing`
y muchas apariciones nuevas dan de alta — y, una vez transcurrida la
ventana `--prune-after`, `--prune` limpia las filas ausentes y **borra
en cascada las fotos huérfanas con sus etiquetas** (datos no derivables,
ADR 0006); solo una copia previa de `foto-export` permite recuperarlas.
Riesgo aceptado: sin salvaguardia adicional; la ventana `--prune-after`
es la única mitigación.

Comportamiento tolerante:

- **Catálogo/lock**: si la metainformación no está disponible, error duro
  (es la fuente de verdad única de todas las ubicaciones; sin ella no hay
  sesión). Cualquier otra ubicación ausente no bloquea.
- **UI**: banner de estado («Ubicación «usb» no disponible:
  /Volumes/FotoVault. 1 234 fotos no visibles») y las fotos de esa
  ubicación como *placeholders* fantasma con fecha y etiquetas del
  catálogo; miniatura desde caché si existe. Favoritar/etiquetar sigue
  funcionando (todo es metadatos). Acciones que exigen el fichero
  (ampliar, ver a tamaño real) se desactivan con mensaje claro.
- **CLIs export/import**: operan sobre el catálogo; las ubicaciones
  ausentes se informan, no detienen.

### 3.7. UI: el catálogo es la fuente de verdad

- La UI **no lee** `[[library.locations]]` ni resuelve ubicaciones: abre
  el catálogo por la cadena de metainformación (§ 3.2) y de la BD salen
  las ubicaciones (nombre, tier, ruta, estado, nº de fotos), las fotos, las
  **rutas absolutas** (join `files.location_id` → `locations.path`) y las
  etiquetas. `load_from_catalog` pierde el parámetro de raíz única.
- La config solo interviene en la UI en la cadena de metainformación y en
  el diálogo de primera ejecución (que persiste `[library].path`, una
  única ubicación `default`, como hoy). La UI nunca escribe
  `[[library.locations]]`.
- Fotos de ubicaciones `deleted`: no se muestran (están pendientes de
  `--prune`); re-declarar la ubicación y escanear las recupera.
- Panel/indicador de ubicaciones con estado (nombre, tier, ruta, estado,
  nº de fotos); accesible desde el banner y `:help`.
- Los *placeholders* fantasma solo se muestran en la vista; el catálogo no
  cambia (nada de marcar «ausente» permanentemente por una desmontada).

### 3.8. Política de emplazamiento (diferida)

> Nota (2026-11-17): la política de emplazamiento, el traslado seguro
> entre ubicaciones y `foto-rebalance` quedan **fuera del ámbito** de
> esta feature y se estudiarán en una feature futura (§ 7.2). El
> resumen de la propuesta original queda recogido en las tareas T5.1–T5.5
> conservadas con nota. Lo único que sobrevive aquí es el campo `tier`
> (`fast`/`archive`) de `[[library.locations]]`: se mantiene en v2 como
> base de esa política futura. El modelo no debe acoplarse a nada que
> bloquee el traslado (§ 3.9.3).

### 3.9. Horizonte: qué debe soportar el diseño sin rediseño

El modelo de ubicaciones debe ser genérico. Tres escenarios futuros, y lo
que cada uno exige a **esta** feature (no implementarlos: solo no acoplar
nada que los bloquee):

1. **Vídeo — de fototeca a fotovideoteca.** Cuando el proyecto incorpore
   vídeo, un disco de vídeos será una ubicación más
   (`[[library.locations]]` con su `name`/`tier`). El catálogo indexa por
   hash y `rel_path` con `location_id`: es agnóstico del tipo de
   contenido. Requisito: que las ubicaciones no asuman «fotos» — nada de
   extensiones, filtros de imagen ni conceptos de foto acoplados a la
   configuración o al esquema de ubicaciones. La clasificación por tipo
   de medio es asunto del escaneo y del catálogo (otra feature), no de
   este modelo.
2. **Una fototeca de Apple Photos como ubicación.** Una ubicación cuya
   raíz es un paquete `.photoslibrary`. El ADR 0009 ya define su política
   de escaneo (solo `originals/`); el cambio necesario es que la política
   se aplique **por ubicación** (el escáner ya decide mirando la raíz con
   `is_apple_photos_library`) y que la validación de paquete de los CLIs
   pase de global a por ubicación. La metainformación externa que exige
   el paquete ya está garantizada por el ADR 0008. Propuesta: detección
   automática por sufijo, sin campo `kind` en la configuración.
3. **Volúmenes remotos (S3 u otro).** El modelo no debe asumir disco
   POSIX local. Tres requisitos concretos para esta feature:
   - La comprobación de disponibilidad (§ 3.6) se encapsula tras una
     abstracción por ubicación: en el futuro, «disponible» no será solo
     «el directorio existe», sino «el backend responde».
   - El traslado seguro (diferido a una feature futura, § 7.2) ya se
     concibe agnóstico del backend (copiar →
     verificar hash → registrar → borrar); no añadir operaciones que solo
     tengan sentido en disco local (p. ej. renombrados atómicos en
     origen).
   - La caché de miniaturas en la metainformación local gana valor con un
     volumen remoto offline: mantenerla como pieza independiente del
     volumen donde vivan las fotos.

   El campo `path` queda como ruta local en v2; el soporte de esquemas
   (`s3://…`) se reserva para el futuro (ver § 5).

Criterio de aceptación transversal: **cada escenario se resuelve añadiendo
(o declarando) una ubicación en la config, no tocando el modelo**. El ADR
0010 debe registrar este horizonte explícitamente.

---

# Paso 1 — ADR 0010 y auditoría (solo documentación y tests)

- [ ] **T1.1 — Redactar `docs/adr/0010-fototeca-distribuida.md`.** Partir
  de la § 3 de este informe, registrando las tres reglas del modelo (§ 0)
  como decisiones cerradas; resolver ahí las decisiones abiertas de la
  § 5 (nombres de tablas v6, política ante `FOTO_LIBRARY` definida,
  ventana de prune para `deleted`, `root` vs tabla `locations`,
  `foto-rebalance` vs subcomando, semántica del default de metainformación
  con varias ubicaciones, detección de Apple Photos, `path` local vs URI,
  marcador de volumen). Registrar el vínculo con los ADR 0001, 0002, 0006
  y 0008 (ampliación **y recorte** de sus cadenas), y el horizonte de
  ubicaciones genéricas de la § 3.9 (vídeo, Apple Photos como ubicación,
  volúmenes remotos).
  > Nota (2026-11-17): la lista de decisiones se recorta con la segunda
  > ronda (§ 7.2): no hay marcador de volumen (descartado) ni
  > `foto-rebalance` vs subcomando (paso 5 diferido). Restan las demás.
- [ ] **T1.2 — Auditoría de supuestos de raíz única y de «contenido =
  fotos».** Recorrer `src/foto/` y enumerar cada punto que asuma una sola
  raíz (scanner, indexer, catálogo, UI, CLIs, tests), cada punto que acople
  las ubicaciones al contenido fotográfico (extensiones, filtros,
  mensajes) — este segundo grupo es lo que bloquearía el horizonte de la
  § 3.9 — y cada lectura de la declaración de fototeca fuera de la config
  (`--library`, `FOTO_LIBRARY`). Tabla al final de esta sección (fichero /
  punto / qué asume), lista de comprobación de los pasos siguientes.
  Mostrar al proyectista antes de T2.1.
- [ ] **T1.3 — Tests que fijan el estado actual.** Cubrir que la cadena
  v1 (una raíz, con `--library`/`FOTO_LIBRARY` aún vivos) se comporta
  byte a byte como hoy: resolución, bootstrap, escaneo, prune, UI. Sin
  cambios de comportamiento.
- [ ] **T1.4 — Verificación y cierre del paso 1.** `uv sync`, `uv run pytest`,
  `uv run ruff check .` en verde; resumen de una línea del estado del paso.
  Se ejecuta en último lugar, tras las tareas de la addenda (§ 7).
- [ ] **T1.5 — Enmendar la regla 1 del AGENT.md para traslado y marcador
  (D1).** Registrar en el ADR 0010 la excepción explícita: el traslado
  verificado entre ubicaciones declaradas (copiar → verificar SHA-256 →
  registrar → eliminar origen) y la escritura del marcador
  `.foto-volume.toml` en la raíz de una ubicación quedan fuera de «nunca
  mover ni borrar originales» y de «escrituras solo en `.foto/`», con sus
  condiciones (verificación previa obligatoria, nunca dentro de un paquete
  Apple, nunca al quitar una ubicación de la config, jamás sin `--execute`
  explícito). La enmienda del propio AGENT.md se aplica al aceptar el ADR;
  hasta entonces el paso 5 no es ejecutable y este informe debe decirlo.
  > Nota (2026-11-17): retirada (§ 7.2). Con el marcador de volumen
  > descartado y el traslado diferido a una feature futura, la regla 1
  > del AGENT.md no necesita enmienda: queda intacta y resuelve D1.
- [ ] **T1.6 — Fijar el nombre de la ubicación de la migración v6 (D2).**
  Decidir y corregir § 3.4 y el enunciado de T3.1: la migración crea y
  asigna la **ubicación declarada única** con su nombre real, y `default`
  solo aparece cuando la config es v1. Test obligatorio del caso límite
  nuevo: catálogo v5 + config v2 con una única ubicación de nombre propio
  → las filas heredadas pasan a esa ubicación, sin baja intermedia.
- [ ] **T1.7 — Fijar el orden de la ubicación canónica (D3).** Decidir y
  corregir § 3.4 y T3.6: el tier es el criterio primario (`fast` gana a
  `archive`) y el `rel_path` solo desempata dentro del mismo tier, de modo
  que «si una foto está en los dos discos, la UI lee del SSD» sea
  consecuencia del esquema. Test: foto con `rel_path` menor en la
  ubicación `archive` y mayor en la `fast` → gana la `fast`.
- [ ] **T1.8 — Delimitar quién lee la declaración de ubicaciones (D4).**
  Reformular la regla 2 de § 0 y los textos de § 3.2 y § 3.7 con el
  carve-out explícito: `foto-index` la lee para indexar; los resolutores
  de metainformación la consultan para el default (una ubicación) y para
  contar (varias); la UI solo obtiene si hay declaración o no (diálogo de
  primera ejecución) y jamás la lista. Sin ese arreglo, ni el diálogo ni
  el default son implementables como está escrito.
- [ ] **T1.9 — Resolver el diseño del marcador de volumen (D5).** Cerrar
  en el ADR dónde vive el uuid esperado (columna en `locations` o en la
  metainformación) y la semántica de «falta»: un marcador ausente en una
  ubicación nueva o recién migrada **no** es `mismatch`; lo escribe el
  primer indexado. Corregir § 3.6 y el enunciado de T3.5: sin uuid
  almacenado, la «validación de identidad» no tiene contra qué comparar.
  > Nota (2026-11-17): retirada (§ 7.2). El marcador de volumen se
  > descarta por completo: no hay uuid que almacenar ni estado
  > `mismatch`; solo `available`/`unavailable`. Resuelve D5.
- [ ] **T1.10 — Raíz de referencia de `foto-import` (D6).** Decidir qué
  pasa con `ensure_library(root, metadata_dir)` en multi-ubicación (hoy
  `foto-import` la llama con la raíz única, `transfer/cli_import.py`) y
  corregir § 3.2: «no necesitan ninguna guardia multi-ubicación» solo es
  cierto para `foto-export`. Implementación en T2.6.
- [ ] **T1.11 — Corregir el paso 5 del traslado (D7).** § 3.8 y T5.3 deben
  referirse a la **fila del fichero de origen** (marcarla `deleted` o
  dejarla a `missing` + `--prune`), nunca a «la ubicación de origen»:
  aplicada literalmente, mover una foto daría de baja la ubicación
  entera.
  > Nota (2026-11-17): retirada (§ 7.2): el traslado queda diferido a
  > una feature futura, que heredará esta corrección (fila del fichero
  > de origen, nunca «la ubicación de origen»). Resuelve D7 en ese
  > diseño futuro; nada que ejecutar en esta feature.
- [ ] **T1.12 — Excluir las ubicaciones Apple del rebalance (D8).**
  Registrar en el ADR que una ubicación cuya raíz es un paquete
  `.photoslibrary` nunca es origen ni destino del traslado (§ 3.8 con
  § 3.9.2; el ADR 0009 prohíbe escribir en el paquete), y añadir el caso
  a los tests adversariales de T5.3 y T5.4.
  > Nota (2026-11-17): retirada (§ 7.2): sin rebalance en esta feature no
  > hay interacción que tratar; el ADR 0009 (no escribir en el paquete
  > Apple) queda intacto y resuelve D8 de raíz. Si la feature futura de
  > traslado ve la luz, heredará esta exclusión.
- [ ] **T1.13 — Semántica completa de `[placement]` (D10).** Cerrar en el
  ADR qué pasa sin ninguna ubicación `fast` (¿política inactiva con aviso,
  error?), con varias `fast` (¿destino?), qué tier tiene la ubicación
  `default` que nace de una config v1, y eliminar `fast_tier` o justificar
  su existencia. Corregir § 3.1 y § 3.8.
  > Nota (2026-11-17): retirada (§ 7.2): `[placement]` desaparece de la
  > config (ya corregido en § 3.1) y la política se difiere; el campo
  > `tier` se mantiene como base. Resuelve D10.
- [ ] **T1.14 — Guardia de metainformación en ubicación de archivo (D11).**
  Decidir la validación (aviso o error) cuando la metainformación
  resuelta cae dentro de una ubicación `archive`: hoy es solo
  «recomendación documentada» (§ 3.1) y la garantía 3 de § 4 depende de
  ella (§ 3.6 la hace error duro). Implementación junto a la cadena de
  T2.2, con tests.
- [ ] **T1.15 — Regla Apple de metadatos externos en multi-ubicación
  (D12).** Extender la regla dura del ADR 0009 —hoy
  `_enforce_apple_metadata_external` compara solo contra la raíz única—
  a «contra cada ubicación declarada que sea paquete Apple»; § 3.2 solo la
  menciona para el default de ubicación única. Implementación en la cadena
  de T2.2, con tests.
- [ ] **T1.16 — Semántica de config v2 incompleta (D13).** Cerrar si una
  v2 sin `[[library.locations]]` ni `[library].path` es válida y si una
  v2 con `[library].path` suelta genera la ubicación `default`; corregir
  «`save_config` intacto» (§ 3.1, T2.1): aceptar `version = 2` y rehusar
  `[library].path` con lista es conservador, no intacto.
- [ ] **T1.17 — Completar las decisiones del ADR (D9).** Añadir a la lista
  de T1.1 el refresco de disponibilidad de la UI (watcher en `/Volumes`
  frente a acción manual; propuesta de § 5, ausente de T1.1) y revisar
  que § 5 y T1.1 quedan sincronizados con las decisiones nuevas de esta
  addenda (T1.5–T1.16).
- [ ] **T1.18 — Correcciones de redacción del informe (D15, D18).** En
  § 4.1, «sigue funcionando sin cambios» debe precisar que no hay
  migración para el dueño, pero que `--library`/`FOTO_LIBRARY` desaparecen
  con error explícito (rompen scripts existentes) y que el catálogo migra
  a v6. En T1.3, «byte a byte» → «sin cambios de comportamiento».

# Paso 2 — Config v2 y resolución solo-config (§ 3.1, § 3.2)

- [ ] **T2.1 — `storage/config.py`: `version = 2` y `[[library.locations]]`.**
  Validación (nombres únicos, tiers, rutas expandidas únicas, error si
  `[library].path` convive con la lista, error de lista en v1), expansión
  de rutas como hoy, `AppConfig` con la lista de ubicaciones;
  `save_config` intacto (conservador, y rehúsa `[library].path` en v2 con
  lista; los tests de persistencia deben demostrar ambas cosas).
- [ ] **T2.2 — `storage/resolver.py`: `resolve_locations()` y la config
  como única fuente.** Retirar `resolve_library_path` y migrar los cuatro
  llamadores; `FOTO_LIBRARY` definida → error explícito (según lo decidido
  en el ADR); cadena de metainformación intacta (`--metadata` →
  `FOTO_METADATA` → `[metadata].path`) con la regla del default (una
  ubicación → `<ubicación>/.foto`, con la regla Apple aplicada a esa
  ubicación; varias sin override → error explícito). Tests al patrón de
  `test_library_resolution.py` pero con configs de fixture y `--config`:
  v1, v2 con lista, vacío, y los dos casos del default de metainformación.
- [ ] **T2.3 — Superficie CLI de los cuatro binarios.** Quitar `--library`
  (argparse lo rechaza solo); epílogos con las cadenas nuevas y ejemplos
  sin `--library`; mensajes de error que citen `[[library.locations]]`.
  Guardia transitoria documentada: `foto-index` y la UI con más de una
  ubicación declarada fallan con «multi-ubicación llega en el paso 3/4»
  (se retira ahí); `foto-export`/`foto-import` no necesitan guardia.
  Tests de CLI actualizados.
- [ ] **T2.4 — UI: parseo y resolución sin `--library`** (patrón de
  `_build_ui_parser`; quedan `--config` y `--metadata`); el diálogo de
  primera ejecución persiste como hoy (ubicación única `default`); la UI
  nunca escribe `[[library.locations]]`.
- [ ] **T2.5 — Documentación** (AGENT.md § Configuración y comandos,
  README, `--help`): la fototeca se declara en la config, punto.
- [ ] **T2.6 — `foto-import` en multi-ubicación (D6).** Aplicar lo
  decidido en T1.10 sobre `ensure_library`/`foto-import` (raíz de
  referencia o rediseño del arranque según el ADR), con tests de import
  con varias ubicaciones declaradas y metadatos explícitos.

# Paso 3 — Catálogo v6, sincronización y indexado multi-ubicación (§ 3.3, § 3.4, § 3.5)

- [ ] **T3.1 — Migración v5 → v6** (renombre `locations` → `files` o lo
  decidido en el ADR, tabla `locations` nueva con estado, filas existentes
  → `default`, error explícito v5 + config multi). Tests de migración
  sobre catálogos v5 reales de `tests/fixtures` o generados.
- [ ] **T3.2 — Sincronización config ↔ catálogo** (§ 3.3): alta, cambio,
  baja (marca ubicación **y** fotos, no borra), reactivación, renombrado,
  dentro del lock y antes de escanear; idempotencia; `--dry-run`; bloque
  `locations` en `--json`. Tests de todos los casos límite de § 3.3.
- [ ] **T3.3 — Scanner e `Indexer` por ubicación**: recorrido de cada raíz
  declarada y disponible, filas `(location_id, rel_path)`, aviso y
  continuación si una ubicación falta (exit 0; `--json` lo reporta).
  Retirar la guardia transitoria de `foto-index`.
- [ ] **T3.4 — Reglas de prune multi-ubicación** (§ 3.5): solo filas, nunca
  ficheros; ubicaciones verificadas presentes + ubicaciones `deleted`
  (ventana según el ADR); nunca filas de ubicaciones ausentes no
  escaneadas. Tests adversariales (USB «desmontado» simulado durante el
  prune; foto en dos ubicaciones, una de ellas dada de baja).
- [ ] **T3.5 — Marcador de volumen `.foto-volume.toml`** (escritura en el
  primer indexado, validación de identidad, estado `mismatch`; caso Apple
  Photos según el ADR).
  > Nota (2026-11-17): retirada (§ 7.2): el marcador se descarta; no
  > existe el estado `mismatch` (solo `available`/`unavailable`, T4.1).
- [ ] **T3.6 — Canonical por tier y rutas desde la BD** en
  `load_from_catalog`: desempate `fast` > `archive`, paths absolutos por
  fila (join con `locations.path`), sin parámetro de raíz. Tests del
  desempate. Retirar la guardia transitoria de la UI.
- [ ] **T3.7 — `library.toml`: coherencia multi-raíz** según lo decidido en
  el ADR (`root` sustituido por la tabla o limitado a ubicación única).
- [ ] **T3.8 — Smoke end-to-end multi-ubicación**: dos raíces de prueba,
  alta y baja en la config, indexado parcial con una «desmontada», prune
  seguro, export/import.
- [ ] **T3.9 — Clave compuesta `(location_id, rel_path)` (D17).** Hoy
  `locations.rel_path` es clave primaria; la tabla `files` de v6 exige
  clave compuesta, y `seen`, `mark_missing` y `get_location` pasan a
  trabajar con el par. Tests de un mismo `rel_path` en dos ubicaciones
  (deduplicación por hash intacta).
- [ ] **T3.10 — Evaluación de estados disponible en el paso 3 (D14).**
  Subir la función pura de estados de T4.1 a este paso (o documentar la
  provisional de T3.3 y sustituirla en T4.1 sin duplicar lógica):
  `foto-index` la necesita «antes de escanear» (§ 3.6).

# Paso 4 — Disponibilidad y tolerancia en la UI (§ 3.6, § 3.7)

- [ ] **T4.1 — Evaluación de estados** (`available`/`unavailable`) como
  función pura inyectable compartida en `storage/`.
  > Nota (2026-11-17): enunciado ajustado (§ 7.2): solo dos estados; no
  > existe `mismatch` ni marcador de volumen.
- [ ] **T4.2 — Banner de estado y panel de ubicaciones** en `ui/`
  (presentación pura en `presentation.py`, sin Qt en la lógica; los datos
  salen del catálogo).
- [ ] **T4.3 — Placeholders fantasma** con fecha y etiquetas del catálogo;
  acciones que exigen fichero desactivadas con mensaje; miniatura desde
  caché si existe.
- [ ] **T4.4 — Favoritas y etiquetas offline**: tests de que `fav`/tags
  funcionan con la ubicación ausente.
- [ ] **T4.5 — Error duro solo si falta la metainformación**; tests de la
  distinción.
- [ ] **T4.6 — Documentación de usuario** (README: qué se ve cuando el USB
  no está; qué pasa al quitar una ubicación de la config; ayuda integrada
  `?`/`:help`).
- [ ] **T4.7 — Ubicación reactivada con filas `deleted` (D16).** Definir
  y testear qué muestra la UI de una ubicación reactivada cuyo escaneo
  aún no ha corrido (filas `deleted`), en coherencia con § 3.3
  («reactivación») y § 3.7.

# Paso 5 — Política de emplazamiento y traslado seguro (§ 3.8)

> Nota (2026-11-17): el paso 5 completo se difiere a una feature futura
> (§ 7.2); queda fuera del ámbito de esta entrega (cuatro pasos). Las
> tareas se conservan como referencia para ese diseño futuro.

- [ ] **T5.1 — `[placement]` en config** (validación, `recent_days` por
  fecha de captura con fallback mtime, exclusión mutua con `recent_count`).
  > Nota (2026-11-17): diferida (§ 7.2); `[placement]` ya no está en la
  > config de esta feature.
- [ ] **T5.2 — Plan de rebalance** (lógica pura en `core/`: qué fotos
  deben estar en qué tier, con el estado real del catálogo; `--dry-run`
  por defecto).
  > Nota (2026-11-17): diferida (§ 7.2).
- [ ] **T5.3 — Traslado seguro**: copiar → verificar SHA-256 → registrar
  `present` → eliminar origen → estado de origen a `--prune`. Lock del
  indexador. Tests adversariales (verificación que falla = no se borra
  nunca el origen).
  > Nota (2026-11-17): diferida (§ 7.2); heredará la corrección de
  > T1.11 (fila del fichero de origen, no «la ubicación de origen») y la
  > exclusión de ubicaciones Apple de T1.12.
- [ ] **T5.4 — Binario `foto-rebalance`** (o lo que decida el ADR) con
  `--execute`, `--json`, epílogo y códigos de salida.
  > Nota (2026-11-17): diferida (§ 7.2).
- [ ] **T5.5 — Verificación y cierre**: suite completa en verde, smoke
  manual con favorita movida a SSD y prune posterior; resumen final y
  desviaciones en este fichero.
  > Nota (2026-11-17): diferida (§ 7.2). El cierre de esta feature es
  > T4.6/T4.7 y el criterio de fin de la § 4 (cuatro pasos).

---

## 4. Criterio de fin de la feature

1. Una fototeca v1 existente sigue funcionando sin cambios (una ubicación).
2. Quitar una ubicación de la config y ejecutar `foto-index`: la ubicación y
   sus fotos quedan marcadas `deleted`, no se borra nada; `--prune` limpia
   solo filas del catálogo; re-declararla antes del prune la restaura al
   escanear.
3. Dos ubicaciones configuradas: indexado parcial con el USB «desmontado»
   no pierde datos ni bloquea; la UI avisa y degrada; favoritar funciona.
4. `--prune` no destruye nada de un volumen ausente ni de uno no escaneado
   que no esté `deleted`, y nunca borra ficheros.

> Nota (2026-11-17): el criterio 5 original (foto favorita o reciente en
> el SSD tras el rebalance) se retira: el rebalance se difiere a una
> feature futura (§ 7.2) y la entrega queda en cuatro pasos.

## 5. Decisiones abiertas que el ADR 0010 debe cerrar

- ¿Nombres de tablas v6? Propuesta: renombrar la tabla per-foto
  `locations` → `files` y reservar `locations` para la tabla de ubicaciones
  con nombre; alternativa: tabla nueva `volumes` (§ 3.4).
- ¿Qué hacer si `FOTO_LIBRARY` está definida? Propuesta: error explícito
  que enseñe la declaración en config; jamás ignorarla en silencio (§ 3.2).
- ¿Ventana de gracia `--prune-after` para las filas `deleted`? Propuesta:
  sí, la misma que las `missing` (§ 3.5).
- ¿`root` en `library.toml` se sustituye por la tabla `locations` del
  catálogo o se limita al caso de ubicación única (§ 3.5)?
- ¿Marcador de volumen obligatorio o solo informativo (§ 3.6)? Y para una
  ubicación Apple Photos, ¿dónde vive el marcador si no puede escribirse
  dentro del paquete?
  **Resuelta (2026-11-17)**: el marcador se descarta por completo y no
  existe el estado `mismatch`; solo `available`/`unavailable` (§ 7.2).
- ¿`foto-rebalance` como binario propio o subcomando de `foto-index`
  (§ 3.8)? El house style actual es un binario por responsabilidad.
  **Resuelta (2026-11-17)**: `foto-rebalance` se difiere con todo el paso
  5 a una feature futura; no hay nada que decidir en este ADR (§ 7.2).
- ¿Default de la metainformación con varias ubicaciones y sin override
  (§ 3.2)? Propuesta: `ConfigError` que pida `--metadata`/`FOTO_METADATA`/
  `[metadata].path`.
- ¿Detección de Apple Photos por sufijo automática o campo `kind` en la
  ubicación (§ 3.9.2)? Propuesta: automática, sin `kind`.
- ¿`path` con esquema (URI) desde v2 o ruta local estricta reservando el
  esquema para el futuro (§ 3.9.3)? Propuesta: ruta local estricta; error
  explícito si el valor contiene `://`, con mensaje que indique que los
  volúmenes remotos no están soportados todavía.
- ¿La UI refresca la disponibilidad al montar un volumen (watcher en
  `/Volumes`) o solo al reabrir/acción manual? Propuesta: acción manual en
  esta feature; watcher fuera del objetivo.

## 6. Fuera del objetivo

- **Sincronización automática en segundo plano** entre discos.
- **Trasladar ficheros entre ubicaciones** (política de emplazamiento,
  traslado seguro, `foto-rebalance`): fuera del ámbito de esta feature y
  diferido a una futura (§ 7.2); esta feature solo prepara la base
  (`tier` en la config).
- **Soporte de volúmenes remotos (S3 u otro)**: esta feature solo deja el
  modelo preparado (§ 3.9.3); el backend remoto es una feature propia.
- **Soporte de vídeo / fotovideoteca**: idem — solo se garantiza que las
  ubicaciones no asuman fotos (§ 3.9.1); el escaneo de vídeo es otra
  feature.
- **Mover a archive por política**; cubierto por el traslado diferido
  (§ 7.2).
- **Borrar ficheros al quitar una ubicación de la config**: nunca (regla 1
  del AGENT.md); quitar una ubicación solo la da de baja del catálogo
  (§ 3.3).
- Nube, carpetas compartidas en red, y cualquier esquema de replicación.
- Verificación de integridad programada de todo el archivo (hashes en
  segundo plano); con el traslado diferido (§ 7.2), ninguna verificación
  de hash existe en esta feature.
- **Flags de simulación de disponibilidad** (`--offline` y similares): los
  tests simulan ubicaciones ausentes con configs de fixture que apuntan a
  rutas inexistentes.
- Escribir `[[library.locations]]` o `[metadata].path` desde la UI.
- Dependencias nuevas de runtime.

## 7. Addenda (2026-11-17): revisión crítica del enunciado

> Revisión de este prompt contra el código real (`storage/catalog.py`,
> `storage/resolver.py`, `storage/config.py`, `storage/library.py`,
> `indexer/cli.py`, `transfer/cli_import.py`, `ui/app.py`), AGENT.md y los
> ADR 0001–0009, antes de empezar el paso 1. El § 2 (estado actual)
> resultó fiel al código; los hallazgos afectan al diseño de la § 3 y a la
> planificación. Cada hallazgo (D1–D18) cita entre paréntesis la tarea que
> lo resuelve. Severidad: **[alta]** = contradicción con riesgo de pérdida
> de datos o diseño no ejecutable; **[media]** = ambigüedad que el ADR debe
> cerrar; **[baja]** = redacción. Las tareas nuevas T1.5–T1.18 cierran cada
> hallazgo como decisión del ADR 0010 y corrigen el texto de § 3 afectado;
> T1.1 las incorpora al redactar el ADR y T1.4, cierre del paso, se ejecuta
> al final. T2.6, T3.9, T3.10 y T4.7 añaden trabajo a los pasos 2–4.
> Varias tareas corrigen enunciados existentes (T3.1, T3.5, T3.6, T5.3):
> prevalece lo decidido en la tarea de la addenda.
>
> Segunda ronda (2026-11-17): ver § 7.2 — el marcador de volumen se
> descarta (solo `available`/`unavailable`) y el paso 5 completo
> (emplazamiento y traslado) se difiere a una feature futura.

- **D1 [alta] — El paso 5 contradice la regla 1 del AGENT.md.** «Nunca
  modificar, mover ni borrar originales» y «escrituras: solo en
  `<fototeca>/.foto/`»: el traslado de § 3.8 mueve originales (copia →
  verificar → **eliminar origen**) y el marcador `.foto-volume.toml`
  (§ 3.6) se escribe en la raíz de la ubicación, fuera de `.foto/`. El
  prompt cita la regla 1 (§ 3.3 y § 6) sin registrar el conflicto ni
  encargar su enmienda. (T1.5)
  **RESUELTA (2026-11-17):** con el marcador descartado y el traslado
  diferido (§ 7.2), no hay excepción que enmendar: la regla 1 del
  AGENT.md queda intacta y sin conflictos.
- **D2 [alta] — La migración v5→v6 se contradice sobre el nombre de la
  ubicación que crea.** § 3.3 (casos límite) asigna las filas heredadas a
  «esa ubicación», la declarada; § 3.4 y T3.1 dicen «crea la ubicación
  `default`». Con una config v2 que declara una única ubicación con otro
  nombre, crear `default` hace que la primera sincronización la dé de baja
  y marque `deleted` todas las filas heredadas hasta el rescan. (T1.6)
- **D3 [alta] — El desempate canónico no cumple la garantía que enuncia.**
  § 3.4 y T3.6: «menor `rel_path` desempatado por tier» deja el tier como
  criterio secundario; el `rel_path` menor puede estar en el USB y «si una
  foto está en los dos discos, la UI lee del SSD» (§ 3.4) deja de
  cumplirse. Para cumplirla, el tier debe ser primario y el `rel_path` el
  desempate. (T1.7)
- **D4 [alta] — La regla 2 de § 0 («solo foto-index lee la declaración»)
  es insostenible con el resto del diseño.** El default de metainformación
  (§ 3.2: una ubicación → `<ubicación>/.foto`; varias → error) obliga a
  contar ubicaciones al resolver metadatos (UI, export, import), y el
  diálogo de primera ejecución (§ 3.2, § 3.7) necesita saber si hay
  declaración. «La UI no lee `[[library.locations]]` ni resuelve
  ubicaciones» (§ 3.7) choca con ambos. (T1.8)
- **D5 [alta] — Marcador de volumen: bucle huevo-gallina y uuid sin sitio
  donde vivir.** § 3.6 lo escribe «en el primer indexado» pero «si falta o
  no coincide, estado degradado»: una ubicación nueva o un catálogo
  migrado v5→v6 (sin marcador) quedarían degradados y jamás escaneados.
  Además, el esquema v6 (§ 3.4) no almacena uuid alguno: la «validación de
  identidad» de T3.5 no tendría contra qué comparar. (T1.9)
  **RESUELTA (2026-11-17):** el marcador y el estado `mismatch` se
  descartan por completo (§ 7.2); no hay uuid que almacenar ni bucle
  huevo-gallina: solo `available`/`unavailable`.
- **D6 [media] — «foto-import no necesita ninguna guardia multi-ubicación»
  (§ 3.2) es falso.** Hoy `foto-import` llama
  `ensure_library(root, metadata_dir)` (`transfer/cli_import.py`):
  necesita una raíz (que exista y sea coherente con `library.toml`). Con
  varias ubicaciones, ¿cuál pasa? Cierto para `foto-export`, falso para
  import. (T1.10, T2.6)
- **D7 [alta] — § 3.8, paso 5 del traslado: «marcar la ubicación de origen
  como eliminada».** Debe decir «la fila del fichero de origen»: tal cual
  está, mover una favorita daría de baja la ubicación entera. T5.3 repite
  la ambigüedad («estado de origen a `--prune`»). (T1.11)
  **RESUELTA (2026-11-17):** el traslado se difiere a una feature
  futura (§ 7.2), que heredará la corrección (fila del fichero de
  origen, nunca «la ubicación de origen»); en esta feature no aplica.
- **D8 [alta] — Rebalance × Apple Photos: interacción sin tratar.** § 3.9.2
  pone un paquete `.photoslibrary` en el horizonte y el ADR 0009 prohíbe
  escribir dentro; § 3.8 no excluye las ubicaciones Apple como origen
  (borraría dentro del paquete) ni como destino del traslado. (T1.12)
  **RESUELTA (2026-11-17):** el rebalance se difiere (§ 7.2); el ADR
  0009 (no escribir en el paquete Apple) queda intacto y sin
  interacción que tratar en esta feature.
- **D9 [media] — T1.1 no lista todas las decisiones abiertas de § 5.**
  Falta el refresco de disponibilidad de la UI (watcher en `/Volumes`
  frente a acción manual). (T1.17)
- **D10 [media] — `[placement].fast_tier` es una perilla vacía y deja casos
  sin definir.** Con solo dos tiers no puede tomar otro valor, y no
  responde qué pasa sin ninguna ubicación `fast`, con varias, ni qué tier
  tiene la ubicación `default` que nace de una config v1. (T1.13)
  **RESUELTA (2026-11-17):** `[placement]` desaparece de la config y la
  política se difiere (§ 7.2); el campo `tier` se mantiene como base de
  esa política futura.
- **D11 [media] — Metainformación en el disco de archivo: recomendación sin
  dientes.** § 3.1 lo deja en «recomendación documentada», pero § 3.6 hace
  de la metainformación un error duro: un `[metadata].path` dentro del USB
  destruye la garantía 3 de § 4 y nada lo valida ni avisa. (T1.14)
- **D12 [media] — La regla Apple de metadatos externos no se extiende a
  multi-ubicación.** § 3.2 solo la menciona para el default de ubicación
  única; con varias, un `[metadata].path` dentro de un paquete Apple no lo
  detecta nadie (hoy `_enforce_apple_metadata_external` compara contra la
  raíz única). (T1.15)
- **D13 [media] — Semántica de config v2 incompleta y `save_config` no
  «intacto».** Sin definir: si una v2 sin lista ni `[library].path` es
  válida y si una v2 con `[library].path` suelta genera `default`. Y
  `save_config` debe aceptar `version = 2` (hoy `_validate_version` la
  rechaza) además de rehusar el path con lista: conservador, no intacto.
  (T1.16)
- **D14 [media] — Solapamiento de los pasos 3 y 4.** § 3.6 dice que
  `foto-index` usa la función de estados «antes de escanear», pero esa
  función es T4.1 (paso 4) y el indexado multi-ubicación es T3.3
  (paso 3). (T3.10)
- **D15 [baja] — § 4.1 «sigue funcionando sin cambios» exagera.**
  `--library` y `FOTO_LIBRARY` desaparecen (error explícito, pero rompe
  cron/scripts existentes) y el catálogo migra a v6. (T1.18)
- **D16 [baja] — § 3.7 no dice qué muestra la UI de una ubicación
  reactivada cuyas filas siguen `deleted`** a la espera de escaneo. (T4.7)
- **D17 [baja] — `locations.rel_path` es `PRIMARY KEY` hoy**; v6 exige
  clave compuesta `(location_id, rel_path)` y adaptar `seen`,
  `mark_missing` y `get_location`. No se menciona. (T3.9)
- **D18 [baja] — T1.3 «byte a byte como hoy»**: son tests de
  comportamiento, no de bytes. (T1.18)

### 7.2. Decisiones cerradas (2026-11-17, segunda ronda)

Cerradas por el dueño tras la primera ronda de la addenda (§ 7.1);
el detalle aplicado al enunciado está en las secciones y tareas citadas.

**Decisión A — Sin marcador de volumen.** Se descarta por completo el
fichero `.foto-volume.toml` y con él el estado `mismatch`. La
disponibilidad de una ubicación tiene exactamente dos estados:
`available` (la raíz existe y es directorio) y `unavailable` (no existe).
Nunca hay un tercer estado.

- Por qué: el marcador exigía escribir fuera de `.foto/` (contra la
  regla 1 del AGENT.md), tenía un problema huevo-gallina en ubicaciones
  nuevas o migradas (D5) y un uuid sin sitio donde vivir (D5).
- Consecuencia aceptada y documentada (§ 3.6): si se monta **otro**
  volumen en el mismo punto de montaje, el escaneo se comporta con
  normalidad — muchas filas pasan a `missing` y muchas apariciones
  nuevas dan de alta — y, transcurrida la ventana `--prune-after`,
  `--prune` limpia las filas ausentes y **borra en cascada las fotos
  huérfanas con sus etiquetas** (datos no derivables, ADR 0006); solo
  una copia previa de `foto-export` permite recuperarlas. Riesgo
  aceptado, sin salvaguardia adicional: la ventana `--prune-after` es la
  única mitigación. La regla 1 del AGENT.md (ninguna escritura en la
  fototeca fuera de `.foto/`) queda intacta.
- Resuelve: D1, D5 y la duda del marcador de la § 5.

**Decisión B — Paso 5 diferido.** Toda herramienta para mover ficheros
entre ubicaciones queda fuera del ámbito de esta feature: la política de
emplazamiento (`[placement]`), el traslado seguro y `foto-rebalance` se
estudiarán en una feature futura. La entrega queda en **cuatro pasos**.

- Lo que sobrevive: el campo `tier` (`fast`/`archive`) de
  `[[library.locations]]` se mantiene en v2 como base de esa política
  futura; el § 0 convierte la garantía de emplazamiento en «base
  preparada, traslado diferido».
- Resuelve: D7, D8, D10, las dudas de `foto-rebalance` de la § 5 y
  retira el criterio de fin 5 (§ 4).

**Tareas afectadas** (conservadas con nota de retirada/diferida, sin
borrar):

- Con nota (ajuste de la lista de decisiones): T1.1.
- Retiradas por la decisión A: T1.5, T1.9, T3.5; T4.1 con el enunciado
  ajustado a dos estados.
- Retiradas/diferidas por la decisión B: T1.11, T1.12, T1.13 y todo el
  paso 5 (T5.1–T5.5).
- La regla 1 del AGENT.md no necesita enmienda; los ADR 0006 y 0009
  quedan intactos.
