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
ubicaciones», sin romper el caso de raíz única, y con dos garantías nuevas:

1. **Tolerancia**: una ubicación ausente (USB desmontado) no bloquea la
   apertura, el indexado ni la UI; se avisa de forma explícita y las fotos
   de esa ubicación se degradan (no se borran, no se pierden metadatos).
2. **Emplazamiento**: existe una política declarativa por la que favoritas
   y últimas fotos terminan en la ubicación rápida (`tier = "fast"`) y el
   resto en la de archivo (`tier = "archive"`), con traslado seguro
   (copiar → verificar hash → registrar → eliminar origen).

El objetivo de fondo va más allá de «dos discos»: el diseño debe producir
**ubicaciones genéricas**. Tres escenarios futuros condicionan el modelo
(§ 3.8): vídeos en una ubicación separada cuando el proyecto pase de
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
   **amplía** (más de una ubicación), no las sustituye.
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
     `src/foto/transfer/cli_import.py` — patrón de CLI y epílogos.
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
- **El escaneo y el indexador asumen una raíz única**
  (`core/scanner.py`, `core/indexing.py`).
- **La metainformación es única y ya externalizable** (ADR 0008):
  `library.toml`, `catalog.sqlite`, `thumbs/`, `index.lock`, resuelta por
  la cadena `--metadata` → `FOTO_METADATA` → `[metadata].path` →
  `<fototeca>/.foto`. Este es el punto de apoyo: la metainformación debe
  vivir en una ubicación **siempre disponible** (el SSD) y las fotos
  repartirse.
- **Favoritas** = etiqueta de sistema `fav` en el catálogo; **no existe el
  concepto de «últimas»** (solo ordenación por fecha en la UI).
- **Config**: `version` solo admite 1; `[library].path` es una sola ruta;
  `save_config` solo toca `[library].path`.
- **Env/CLI**: `FOTO_LIBRARY` y `--library` son una sola ruta en los cuatro
  binarios.

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

[placement]                      # política de emplazamiento (paso 5)
recent_days = 90                 # «últimas»: por fecha de captura
fast_tier = "fast"               # destino de favoritas y recientes
```

Reglas:

- Un fichero v1 con `[library].path` sigue funcionando: se convierte en una
  única ubicación de nombre `default`. Cero migración para el dueño.
- `[[library.locations]]` solo es válido en `version = 2`; en un v1 es
  `ConfigError` con mensaje que sugiere subir la versión.
- Nombres: no vacíos, únicos, `fast`/`archive` son los únicos `tier`.
- `save_config` conserva su carácter conservador: solo escribe
  `[library].path`; las ubicaciones se editan a mano (igual que `[metadata]`).

### 3.2. Resolución: cómo cambian env y CLI

| Fuente | Hoy | Propuesto |
| --- | --- | --- |
| `FOTO_LIBRARY` | una ruta | `NOMBRE=ruta[:NOMBRE=ruta…]` (separador `os.pathsep`); una ruta sin nombre = ubicación única `default` (compat) |
| `--library PATH` | una ruta | igual que `FOTO_LIBRARY` (compat); marcado como forma simple |
| `--location NOMBRE=PATH` | — | nuevo, **repetible**: fija o añade una ubicación por nombre; gana por encima de env y config |
| `--offline NOMBRE` | — | nuevo, repetible: trata esa ubicación como no disponible (simulación/pruebas) |
| `--metadata PATH` | una ruta (ADR 0008) | **sin cambios**: un solo directorio de metadatos para todas las ubicaciones |
| `FOTO_METADATA` | una ruta (ADR 0008) | **sin cambios** |
| `FOTO_CONFIG`, `--config` | sin cambios | sin cambios |

Precedencia por ubicación (mismo patrón del ADR 0001/0008, en
`storage/resolver.py`): `--location` → `FOTO_LIBRARY`/`FOTO_LOCATIONS`
(env) → `[[library.locations]]`/`[library].path` (config) → default.

- `FOTO_LOCATIONS` (nueva) reemplaza el conjunto completo; `FOTO_LIBRARY`
  queda como atajo de una sola ubicación y convive con ella (compat).
- Un override CLI **no lee** config, como hoy.
- `resolve_library_path` pasa a devolver una `LibraryResolution` con
  **varias ubicaciones** (lista de `Location(name, path, tier, origin)`);
  los llamadores que esperan una raíz única reciben la ubicación `default`
  o la única (compat); la nueva API es `resolve_locations()`.

**Cadena de metainformación (patrón vigente, ADR 0008, sin cambios de
precedencia):** `--metadata` → `FOTO_METADATA` → `[metadata].path`.
La metainformación sigue siendo **un único directorio, metadatos de todas
las ubicaciones**: no existe «un catálogo por disco», ni ubicaciones que
tengan metainformación propia. Consecuencia nueva del modelo distribuido:

- Hoy el nivel 4 deriva el default de la raíz (`<fototeca>/.foto`), pero
  con varias ubicaciones ya no hay «la fototeca» única de la que derivar.
- Propuesta de regla: con **una sola** ubicación (v1, cadena clásica) el
  default sigue siendo `<fototeca>/.foto` (compat total). Con **varias**
  ubicaciones y sin `--metadata`/`FOTO_METADATA`/`[metadata].path` →
  `ConfigError` explícito que pide concretar la metainformación
  (ambigüedad prohibida, mismo criterio que `[library].path` + lista).
  Decisión en el ADR (§ 5).

### 3.3. Catálogo: esquema v6

- Tabla nueva `locations` (`id`, `name`, `path`, `tier`); la tabla de
  ubicaciones por foto gana `location_id` (FK) junto a `rel_path`.
- Migración v5 → v6: todas las filas existentes pasan a la ubicación
  `default` (la raíz resuelta). Rutas relativas intactas: sobreviven a
  traslados como hasta ahora.
- Ubicación canónica por foto: menor `rel_path` **desempatado por tier**
  (`fast` gana a `archive`): si una foto está en los dos discos, la UI lee
  del SSD.
- El escáner registra `(location_id, rel_path)`; el estado `present` es por
  ubicación, no global.

### 3.4. Indexado y `--prune` multi-ubicación

- `foto-index` recorre **solo las ubicaciones disponibles** y actualiza
  únicamente las filas de esas ubicaciones. Una ubicación ausente: aviso
  por stderr/`--json` y salida 0 (no es error); el epílogo lo documenta.
- **Regla de seguridad (no negociable)**: `--prune` nunca elimina
  ubicaciones de fotos pertenecientes a volúmenes no escaneados en esa
  ejecución. Prune solo sobre ubicaciones verificadas presentes.
- `library.toml` sigue único (en la metainformación); la coherencia de
  `root` (ADR 0008) se adapta: `root` pasa a ser la lista de raíces o se
  elimina en favor de la tabla `locations` del catálogo (decisión del ADR).

### 3.5. Disponibilidad y tolerancia

Estados por ubicación, evaluados al resolver (no en cada acceso):

- `available` — la raíz existe y es directorio.
- `unavailable` — no existe (USB desmontado): aviso y degradación.
- `mismatch` — existe pero **no es el volumen esperado**: cada raíz lleva
  un marcador `.foto-volume.toml` (`name` + `uuid`, escrito en el primer
  indexado de esa ubicación); si falta o no coincide, estado degradado con
  aviso (evita leer de un disco equivocado montado en el mismo punto).

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

### 3.6. Política de emplazamiento (favoritas y últimas en el SSD)

- «Últimas» = fecha de captura (EXIF `DateTimeOriginal`, *fallback* mtime)
  dentro de `recent_days` (o `recent_count`; una u otra, no ambas).
- Reglas: `favorita → tier fast`; `reciente → tier fast`; el resto
  permanece en su ubicación (nunca se mueve solo por política a *archive*:
  el traslado a archive es decisión explícita del dueño).
- **Traslado seguro** (los originales no se tocan sin verificación):
  1. copiar a la ubicación destino con nombre temporal,
  2. verificar SHA-256 idéntico,
  3. registrar la nueva ubicación como `present` en el catálogo,
  4. eliminar el fichero de origen solo tras la verificación,
  5. marcar la ubicación de origen como eliminada (o dejarlo a `--prune`).
- Herramienta: `foto-rebalance` (nuevo binario, `--dry-run` por defecto,
  `--execute` para aplicar), que calcula y aplica el plan. Alternativa a
  valorar en el ADR: subcomando de `foto-index`.
- Ejecución bajo el mismo lock que el indexador.

### 3.7. UI

- Panel/indicador de ubicaciones con estado (nombre, tier, ruta, estado,
  nº de fotos); accesible desde el banner y `:help`.
- Los *placeholders* fantasma solo se muestran en la vista; el catálogo no
  cambia (nada de marcar «ausente» permanentemente por una desmontada).

### 3.8. Horizonte: qué debe soportar el diseño sin rediseño

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
   - La comprobación de disponibilidad (§ 3.5) se encapsula tras una
     abstracción por ubicación: en el futuro, «disponible» no será solo
     «el directorio existe», sino «el backend responde».
   - El traslado seguro (§ 3.6) ya es agnóstico del backend (copiar →
     verificar hash → registrar → borrar); no añadir operaciones que solo
     tengan sentido en disco local (p. ej. renombrados atómicos en
     origen).
   - La caché de miniaturas en la metainformación local gana valor con un
     volumen remoto offline: mantenerla como pieza independiente del
     volumen donde vivan las fotos.

   El campo `path` queda como ruta local en v2; el soporte de esquemas
   (`s3://…`) se reserva para el futuro (ver § 5).

Criterio de aceptación transversal: **cada escenario se resuelve añadiendo
(o declarando) una ubicación, no tocando el modelo**. El ADR 0010 debe
registrar este horizonte explícitamente.

---

# Paso 1 — ADR 0010 y auditoría (solo documentación y tests)

- [ ] **T1.1 — Redactar `docs/adr/0010-fototeca-distribuida.md`.** Partir
  de la § 3 de este informe; resolver ahí las decisiones abiertas de la
  § 5 (esquema v6, `root` vs tabla `locations`, `foto-rebalance` vs
  subcomando, semántica exacta de `FOTO_LIBRARY` heredada, detección de
  Apple Photos, `path` local vs URI). Registrar el vínculo con los ADR
  0001, 0002, 0006 y 0008, y el horizonte de ubicaciones genéricas de la
  § 3.8 (vídeo, Apple Photos como ubicación, volúmenes remotos).
- [ ] **T1.2 — Auditoría de supuestos de raíz única y de «contenido =
  fotos».** Recorrer `src/foto/` y enumerar cada punto que asuma una sola
  raíz (scanner, indexer, catálogo, UI, CLIs, tests) y cada punto que
  acople las ubicaciones al contenido fotográfico (extensiones, filtros,
  mensajes) — este segundo grupo es lo que bloquearía el horizonte de la
  § 3.8. Tabla al final de esta sección (fichero / punto / qué asume),
  lista de comprobación de los pasos siguientes. Mostrar al proyectista
  antes de T2.1.
- [ ] **T1.3 — Tests que fijan el estado actual.** Cubrir que la cadena
  v1 (una raíz) se comporta byte a byte como hoy: resolución, bootstrap,
  escaneo, prune, UI. Sin cambios de comportamiento.
- [ ] **T1.4 — Verificación y cierre del paso 1.** `uv sync`, `uv run pytest`,
  `uv run ruff check .` en verde; resumen de una línea del estado del paso.

# Paso 2 — Config v2 y resolución multi-ubicación (§ 3.1, § 3.2)

- [ ] **T2.1 — `storage/config.py`: `version = 2` y `[[library.locations]]`.**
  Validación (nombres únicos, tiers, error si `[library].path` convive con
  la lista, error de lista en v1), expansión de rutas como hoy,
  `AppConfig` con la lista de ubicaciones; `save_config` intacto
  (conservador; los tests de persistencia lo deben demostrar).
- [ ] **T2.2 — `storage/resolver.py`: `resolve_locations()` y el nuevo
  conjunto de precedencia.** `--location` repetible, `--offline`,
  `FOTO_LOCATIONS`, compat de `FOTO_LIBRARY`/`--library`/`[library].path`
  (ubicación única `default`). `resolve_library_path` queda como envoltorio
  de compat. Cadena de metainformación intacta (`--metadata` →
  `FOTO_METADATA` → `[metadata].path`) con la regla del default decidida
  en el ADR (una ubicación → `<fototeca>/.foto`; varias sin override →
  error explícito). Tests al patrón de `test_library_resolution.py`
  (entorno inyectable): cada origen, precedencia, override que corta
  config, y los dos casos del default de metainformación.
- [ ] **T2.3 — Superficie CLI de los cuatro binarios.** `--location`,
  `--offline`, epílogos actualizados con la cadena nueva y el ejemplo
  `NOMBRE=ruta`. Mensajes de error con la ubicación y su origen.
- [ ] **T2.4 — UI: parseo y resolución sin raíz única** (patrón de
  `_build_ui_parser`); el diálogo de primera ejecución persiste como hoy
  (ubicación única `default`); la UI nunca escribe `[[library.locations]]`.
- [ ] **T2.5 — Documentación** (AGENT.md § Configuración, README, `--help`).

# Paso 3 — Catálogo v6, escaneo e indexado multi-ubicación (§ 3.3, § 3.4)

- [ ] **T3.1 — Migración v5 → v6** (tabla `locations`, `location_id`,
  filas existentes → `default`). Tests de migración sobre catálogos v5
  reales de `tests/fixtures` o generados.
- [ ] **T3.2 — Scanner e `Indexer` por ubicación**: recorrido de cada raíz
  disponible, filas `(location_id, rel_path)`, aviso y continuación si una
  ubicación falta (exit 0; `--json` lo reporta).
- [ ] **T3.3 — Reglas de prune multi-ubicación**: nunca podar ubicaciones
  de volúmenes no escaneados. Tests adversariales (USB «desmontado»
  simulado durante el prune).
- [ ] **T3.4 — Marcador de volumen `.foto-volume.toml`** (escritura en el
  primer indexado, validación de identidad, estado `mismatch`).
- [ ] **T3.5 — Canonical por tier** en `load_from_catalog` (fast > archive).
  Tests del desempate.
- [ ] **T3.6 — `library.toml`: coherencia multi-raíz** según lo decidido en
  el ADR (adaptar `root` o sustituirlo).
- [ ] **T3.7 — Smoke end-to-end multi-ubicación**: dos raíces de prueba,
  indexado parcial con una «desmontada», prune seguro, export/import.

# Paso 4 — Disponibilidad y tolerancia en la UI (§ 3.5)

- [ ] **T4.1 — Evaluación de estados** (`available`/`unavailable`/
  `mismatch`) en el resolutor, inyectable para tests.
- [ ] **T4.2 — Banner de estado y panel de ubicaciones** en `ui/`
  (presentación pura en `presentation.py`, sin Qt en la lógica).
- [ ] **T4.3 — Placeholders fantasma** con fecha y etiquetas del catálogo;
  acciones que exigen fichero desactivadas con mensaje; miniatura desde
  caché si existe.
- [ ] **T4.4 — Favoritas y etiquetas offline**: tests de que `fav`/tags
  funcionan con la ubicación ausente.
- [ ] **T4.5 — Error duro solo si falta la metainformación**; tests de la
  distinción.
- [ ] **T4.6 — Documentación de usuario** (README: qué se ve cuando el USB
  no está; ayuda integrada `?`/`:help`).

# Paso 5 — Política de emplazamiento y traslado seguro (§ 3.6)

- [ ] **T5.1 — `[placement]` en config** (validación, `recent_days` por
  fecha de captura con fallback mtime, exclusión mutua con `recent_count`).
- [ ] **T5.2 — Plan de rebalance** (lógica pura en `core/`: qué fotos
  deben estar en qué tier, con el estado real del catálogo; `--dry-run`
  por defecto).
- [ ] **T5.3 — Traslado seguro**: copiar → verificar SHA-256 → registrar
  `present` → eliminar origen → estado de origen a `--prune`. Lock del
  indexador. Tests adversariales (verificación que falla = no se borra
  nunca el origen).
- [ ] **T5.4 — Binario `foto-rebalance`** (o lo que decida el ADR) con
  `--execute`, `--json`, epílogo y códigos de salida.
- [ ] **T5.5 — Verificación y cierre**: suite completa en verde, smoke
  manual con favorita movida a SSD y prune posterior; resumen final y
  desviaciones en este fichero.

---

## 4. Criterio de fin de la feature

1. Una fototeca v1 existente sigue funcionando sin cambios (una ubicación).
2. Dos ubicaciones configuradas: indexado parcial con el USB «desmontado»
   no pierde datos ni bloquea; la UI avisa y degrada; favoritar funciona.
3. `--prune` no destruye nada de un volumen ausente.
4. Una foto favorita o reciente queda en el SSD tras el rebalance, con
   verificación de hash y sin perder la copia en caso de fallo.

## 5. Decisiones abiertas que el ADR 0010 debe cerrar

- ¿`root` en `library.toml` se adapta a lista o se elimina a favor de la
  tabla `locations` del catálogo (§ 3.4)?
- ¿Marcador de volumen obligatorio o solo informativo (§ 3.5)?
- ¿`foto-rebalance` como binario propio o subcomando de `foto-index`
  (§ 3.6)? El house style actual es un binario por responsabilidad.
- ¿`FOTO_LIBRARY` se depreca en favor de `FOTO_LOCATIONS` o conviven para
  siempre (§ 3.2)? Compat propuesta: conviven, con `FOTO_LIBRARY` como
  atajo documentado.
- ¿Default de la metainformación con varias ubicaciones y sin override
  (§ 3.2)? Propuesta: con una sola ubicación, `<fototeca>/.foto` (compat);
  con varias, `ConfigError` que pide `--metadata`/`FOTO_METADATA`/
  `[metadata].path`.
- ¿Detección de Apple Photos por sufijo automática o campo `kind` en la
  ubicación (§ 3.8.2)? Propuesta: automática, sin `kind`.
- ¿`path` con esquema (URI) desde v2 o ruta local estricta reservando el
  esquema para el futuro (§ 3.8.3)? Propuesta: ruta local estricta; error
  explícito si el valor contiene `://`, con mensaje que indique que los
  volúmenes remotos no están soportados todavía.
- ¿La UI refresca la disponibilidad al montar un volumen (watcher en
  `/Volumes`) o solo al reabrir/acción manual? Propuesta: acción manual en
  esta feature; watcher fuera del objetivo.

## 6. Fuera del objetivo

- **Sincronización automática en segundo plano** entre discos; el
  emplazamiento solo se aplica con `foto-rebalance` (explícito).
- **Soporte de volúmenes remotos (S3 u otro)**: esta feature solo deja el
  modelo preparado (§ 3.8.3); el backend remoto es una feature propia.
- **Soporte de vídeo / fotovideoteca**: idem — solo se garantiza que las
  ubicaciones no asuman fotos (§ 3.8.1); el escaneo de vídeo es otra
  feature.
- **Mover a archive por política**; el traslado hacia el disco lento es
  siempre manual.
- Nube, carpetas compartidas en red, y cualquier esquema de replicación.
- Verificación de integridad programada de todo el archivo (hashes en
  segundo plano); la verificación de hash existe solo en el traslado.
- Escribir `[[library.locations]]` o `[metadata].path` desde la UI.
- Dependencias nuevas de runtime.
