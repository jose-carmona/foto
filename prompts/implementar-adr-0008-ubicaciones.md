# Prompt — Implementación del ADR 0008: resolución de ubicaciones (configuración, fototeca y metainformación)

> Este prompt es autocontenido y sustituye a cualquier discusión previa sobre
> dónde vive la configuración, la fototeca y la metainformación. La decisión
> está redactada y aceptada en
> [`docs/adr/0008-resolucion-de-ubicaciones.md`](../docs/adr/0008-resolucion-de-ubicaciones.md);
> la misión ya no es decidir: es **implementar en dos pasos** y dejar constancia
> del avance en este mismo fichero.
>
> **Seguimiento.** Este prompt es el registro de tareas. Al terminar cada
> tarea: márcala `[x]` con la fecha y, si hubo desviaciones del enunciado,
> añade debajo una línea `> Nota (fecha): …`. No reordenes ni borres tareas;
> si el trabajo genera una pendiente nueva, añádela al final del paso
> correspondiente con el mismo formato. Una tarea solo está cerrada con sus
> tests en verde y `uv run pytest` + `uv run ruff check .` completos.

## 0. Misión

Implementar el ADR 0008, que fija las tres cadenas de resolución de rutas con
el mismo patrón (**override CLI → variable de entorno → `config.toml` →
valor por defecto**) y hace posible operar sobre la fototeca de Apple Photos
sin escribir dentro de su paquete.

Se ejecuta en **dos pasos diferenciados**, cada uno terminado y verificable
por separado:

1. **Paso 1 — Estado actual: auditoría y normalización.** Todos los
   elementos existentes usan el esquema actual (sin `--metadata`, sin
   `FOTO_METADATA`, sin `[metadata]`): auditar cada punto donde se resuelve
   una ruta, normalizar las inconsistencias internas y fijar el
   comportamiento actual con tests. **Cero cambios de comportamiento.** La
   UI no admite parámetros ni variables de entorno propias en este paso.
2. **Paso 2 — Nueva propuesta.** Todos los elementos usan la cadena completa
   del ADR 0008: `--metadata` / `FOTO_METADATA` / `[metadata].path` en los
   tres CLIs **y en la UI** (`foto` pasará a admitir `--config`, `--library`
   y `--metadata`), campo `root` en `library.toml` con validación de
   coherencia.

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables, convenciones, sección «Configuración»
   y flujo de trabajo. Sin `git commit`/`push` sin permiso explícito.
2. `docs/adr/0001-configuracion.md` — las cadenas de configuración y
   fototeca que el paso 1 consolida; el ADR 0008 las amplía, no las cambia.
3. `docs/adr/0008-resolucion-de-ubicaciones.md` — la decisión completa que
   este prompt implementa: las tres cadenas, la semántica del override de
   metadatos, el campo `root` y la preparación para Apple Photos.
4. `docs/adr/0002-indexado.md` y `docs/adr/0004-cache-de-miniaturas.md` —
   porque el cambio de resolución toca dónde viven `catalog.sqlite` y
   `thumbs/`.
5. Código real:
   - `src/foto/storage/config.py` — `resolve_config_path`, `load_config`,
     `save_config` conservador, validación de secciones.
   - `src/foto/storage/resolver.py` — `resolve_library_path` (único sitio de
     la precedencia, ADR 0001).
   - `src/foto/storage/library.py` — `foto_dir`, `library_file`,
     `thumbs_dir`, `ensure_library`, bootstrap atómico con lock de creación.
   - `src/foto/indexer/cli.py`, `src/foto/transfer/cli_export.py`,
     `src/foto/transfer/cli_import.py` — el patrón de CLI: `--library`,
     `--config`, epílogo de `--help` con la cadena, mensajes de error.
   - `src/foto/ui/app.py` — `main()` (sin argparse hoy), `load_photos`,
     `load_tags`, `MainWindow` (rutas `.foto` construidas a mano), diálogo de
     primera ejecución que escribe `[library].path`.
   - `pyproject.toml` — `[project.scripts]`: `foto`, `foto-index`,
     `foto-export`, `foto-import`.
6. Tests existentes de referencia: `tests/test_library_resolution.py`,
   `tests/test_config_persistence.py`, `tests/test_library_bootstrap.py`,
   `tests/test_cli.py`.

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la
verificación disponible.

---

## 2. Decisiones ya tomadas (no se discuten)

1. **Tres cadenas, un solo sitio de precedencia** (`storage/resolver.py`):
   - Configuración: `--config` → `FOTO_CONFIG` → `platformdirs`
     (implementada, sin cambios).
   - Fototeca: `--library` → `FOTO_LIBRARY` → `[library].path` → diálogo
     (implementada, sin cambios).
   - Metainformación: `--metadata` → `FOTO_METADATA` → `[metadata].path` →
     `<fototeca>/.foto` (nueva).
2. **El override de metadatos redirige el directorio completo**
   (`library.toml`, `catalog.sqlite`, `thumbs/`, `index.lock`, `exports/`),
   nunca piezas sueltas.
3. **No se mueve nada automáticamente.** Directorio vacío → bootstrap
   atómico existente; descriptor previo → se adopta. La migración manual de
   una `.foto/` existente es operación del dueño (documentar, no automatizar).
4. **La UI admite `--config`, `--library` y `--metadata`** (paso 2). Las
   variables de entorno también la afectan, porque comparte resolutor con
   los CLIs. Los overrides de CLI **no se persisten** en `config.toml`; solo
   el diálogo de primera ejecución persiste `[library].path`, como hoy.
   La UI **nunca escribe** `[metadata].path`.
5. **Campo opcional `root` en `library.toml`** (paso 2): se escribe al crear
   y al adoptar el descriptor, y su no coincidencia con la raíz resuelta
   produce error explícito con acción sugerida (relocalizar o regenerar).
   Descriptores sin `root` se siguen aceptando (legado).
6. **Sin dependencias nuevas;** `tomllib`, `platformdirs`, stdlib y las
   existentes bastan. `core/`, `storage/`, `indexer/` y `transfer/` siguen
   sin importar Qt.
7. **Errores explícitos** (`ConfigError`/`LibraryError`), nunca
   `except: pass`; rutas siempre `pathlib` absolutas con `expanduser`; las
   relativas de `config.toml` se resuelven contra el directorio del propio
   fichero.

---

# Paso 1 — Estado actual: auditoría y normalización

- [x] **T1.1 — Auditoría documentada de resolución de rutas.** (2026-11-13)
  Recorrer
  `src/foto/` y enumerar **cada punto** donde se resuelve una ubicación
  (lecturas de entorno, `resolve_*`, `foto_dir`/`library_file`/`thumbs_dir`,
  rutas `.foto` literales, rutas de catálogo/miniaturas/lock). Entregar el
  resultado como una tabla al final de esta sección del prompt (fichero /
  función / qué resuelve / mecanismo), que servirá de lista de comprobación
  del resto de tareas. La auditoría se muestra al proyectista antes de
  pasar a T1.2.
- [x] **T1.2 — Normalización interna de la UI (sin cambio de comportamiento).**
  (2026-11-13)
  `src/foto/ui/app.py` construye `library_path / ".foto" / "catalog.sqlite"`
  a mano en `load_photos`, `load_tags` y `MainWindow`: sustituirlo por los
  helpers de `storage/library.py` (`foto_dir()`, `library_file()`,
  `thumbs_dir()`) para que el paso 2 solo tenga que cambiar el valor
  resuelto, no tres literales dispersos. Ni `--metadata` ni nada nuevo.
  > Nota (2026-11-13): el nombre de fichero se dejó en la constante
  > `CATALOG_FILE_NAME` de `storage/catalog.py` (`foto_dir(p) /
  > CATALOG_FILE_NAME`), el mismo patrón que ya usan los tres CLIs; así el
  > paso 2 cambia un solo valor por punto de entrada.
- [x] **T1.3 — Tests que fijan el estado actual.** (2026-11-13) Cubrir huecos de las
  cadenas vigentes: `resolve_config_path` (`--config` → `FOTO_CONFIG` →
  `platformdirs`) y `resolve_library_path` (los tres orígenes) si el código
  de `tests/` actual no los cubre ya; y un test de coherencia que demuestre
  que los cuatro puntos de entrada (tres CLIs + UI vía helpers) derivan las
  mismas rutas de metainformación a partir de la misma raíz. Aprovechar el
  patrón de `environment` inyectable de `tests/test_library_resolution.py`.
  > Nota (2026-11-13): `resolve_library_path` ya estaba cubierta por
  > `tests/test_library_resolution.py` (siete casos, con entorno inyectable);
  > se añadieron `tests/test_config_resolution.py` (cadena de config) y
  > `tests/test_metadata_coherence.py` (coherencia end-to-end de los cuatro
  > puntos de entrada sobre la misma `.foto/`). `resolve_config_path` lee
  > `os.environ` directamente (no inyectable hoy): los tests usan
  > `monkeypatch`, patrón del propio módulo; inyectarlo queda como mejora
  > opcional, no como requisito del paso 1.
- [x] **T1.4 — Verificación y cierre del paso 1.** (2026-11-13) `uv sync`, `uv run
  pytest`, `uv run ruff check .` en verde; smoke test manual:
  `uv run foto-index --library <biblioteca de prueba> --dry-run` y
  `uv run foto` sobre una biblioteca ya indexada. Confirmar que nada cambió
  de sitio en disco (misma `.foto/`, mismo `config.toml`). Marcar las cuatro
  tareas y resumir en una línea el estado final del paso.
  > Nota (2026-11-13): 793 tests en verde (786 previos + 7 nuevos), ruff
  > limpio. Smoke: `--dry-run` no creó nada; indexado real y `foto`
  > (offscreen, con `FOTO_CONFIG`/`FOTO_LIBRARY` sobre biblioteca indexada)
  > abrieron la misma `.foto/` sin escribir fuera de ella y sin crear
  > `config.toml`. La UI ya está afectada por `FOTO_LIBRARY` hoy porque
  > `resolve_library_path()` lee el entorno.

### Auditoría de resolución de rutas (T1.1, 2026-11-13)

| Fichero | Función / punto | Qué resuelve | Mecanismo |
| --- | --- | --- | --- |
| `storage/config.py` | `get_default_config_path` | Nivel 3 de la cadena de configuración | `platformdirs.user_config_dir("foto") / config.toml` |
| `storage/config.py` | `resolve_config_path` | Cadena 1 completa: `--config` → `FOTO_CONFIG` → por defecto | Parámetro; `os.environ` (única lectura de entorno de `storage/`); sin inyección de entorno hoy |
| `storage/config.py` | `_expand_path` | `[library].path` | `expanduser`; relativas contra el directorio del `config.toml` |
| `storage/config.py` | `_render_canonical` / `_update_library_path` / `_write_text_atomic` | Escritura de `config.toml` | Conservadora (solo `[library].path`), temp + `os.replace` |
| `storage/resolver.py` | `resolve_library_path` | Cadena 2: `--library` → `FOTO_LIBRARY` → `[library].path` | Parámetro; `os.environ` o mapping inyectable; `load_config`. Único sitio de precedencia |
| `storage/library.py` | `foto_dir` / `library_file` / `thumbs_dir` | `.foto/`, `library.toml`, `thumbs/` | Literal `FOTO_DIR_NAME = ".foto"` derivado de la raíz (nivel 4 actual) |
| `storage/library.py` | `ensure_library` / `load_library` | Bootstrap atómico del descriptor y su lectura | `foto_dir(root)` + lock de creación (`.library.toml.creating`) |
| `storage/lock.py` | `LOCK_FILE_NAME` | `index.lock` | Constante; la ruta la componen los llamadores con `foto_dir(root)` |
| `storage/catalog.py` | `CATALOG_FILE_NAME` | `catalog.sqlite` | Constante; la componen los llamadores con `foto_dir(root)` |
| `indexer/cli.py` | `main` | Catálogo y lock del indexador | `foto_dir(root) / CATALOG_FILE_NAME`, `IndexerLock(foto_dir(root) / LOCK_FILE_NAME)` |
| `transfer/cli_export.py` | `main` | Catálogo (solo lectura) y destino por defecto de `--out` | `foto_dir(root) / CATALOG_FILE_NAME`; literal `foto_dir(root) / "exports"` |
| `transfer/cli_import.py` | `main` | Catálogo y lock del importador | `foto_dir(root) / CATALOG_FILE_NAME`, `IndexerLock(foto_dir(root) / LOCK_FILE_NAME)` |
| `ui/app.py` | `main` | Biblioteca (sin flag hoy) y primera ejecución | `resolve_library_path()` (lee el entorno); el diálogo persiste `[library].path` vía `save_config` |
| `ui/app.py` | `load_photos` / `load_tags` / `MainWindow.__init__` | Catálogo lector y conexión RW de la UI | Helper `foto_dir(library_path) / CATALOG_FILE_NAME` (antes literal `.foto` + `catalog.sqlite`; normalizado en T1.2) |
| `ui/app.py` | `_validated_library` | Descriptor | Helper `library_file(library_path)` |
| `ui/app.py` | `MainWindow.__init__` | Caché de miniaturas | Helper `thumbs_dir(library_path)` |
| `core/scanner.py` | recorrido del `scan` | Ignora `.foto/` al indexar | Comparación de `entry.name` (sin cambio pedido en el ADR 0008) |
| `core/library.py` | `list_images` (obsoleto) | Ignora `.foto` al listar | `".foto" in Path(root).parts` |

**Criterio de fin del paso 1:** el comportamiento observable es byte a byte
el de antes; todo pasa por los helpers/resolutores existentes; la auditoría
de T1.1 está registrada en este fichero. **No continuar al paso 2 sin
cerrar esto.**

> Estado final del paso 1 (2026-11-13): T1.1–T1.4 cerradas; 793 tests y
> ruff en verde, cero cambios de comportamiento, auditoría registrada
> arriba.

---

# Paso 2 — Nueva propuesta (ADR 0008)

## 3. Decisiones vinculantes del diseño

### 3.1. `[metadata]` en `config.toml`

- Sección nueva, opcional, con una sola clave: `path` (texto). Validación al
  estilo de las secciones existentes (`ConfigError` si no es texto; vacío o
  con solo espacios es inválido).
- Expansión igual que `[library].path`: `~` y relativas al directorio del
  fichero de configuración; en memoria, ruta absoluta.
- `AppConfig` gana el campo (`metadata_path: Path | None = None`, valor por
  defecto `None` = «derivar de la fototeca»).
- `save_config` sigue siendo conservador: **nunca escribe ni reordena**
  `[metadata]`; la reescritura de `[library].path` existente debe conservar
  la sección y sus comentarios intactos (los tests de
  `test_config_persistence.py` lo deben demostrar).
- Un fichero nuevo creado por la app (`_render_canonical`) **no** incluye
  `[metadata]`: es opt-in.

### 3.2. Resolutor de metadatos (`storage/resolver.py`)

- Constante `ENVIRONMENT_METADATA = "FOTO_METADATA"`.
- `resolve_metadata_dir(root, metadata=None, *, config_path=None,
  environment=None) -> MetadataResolution`, con `MetadataResolution`
  análoga a `LibraryResolution` (ruta + origen: `option` / `environment` /
  `config` / `default`).
- Precedencia exacta: `--metadata` → `FOTO_METADATA` → `[metadata].path` →
  `root / ".foto"`. Un override por CLI no llega a leer `config.toml`, como
  en `resolve_library_path`.
- Validación en el resolutor: si la ruta resuelta (de cualquier origen)
  existe y **no es un directorio**, error explícito del núcleo con la ruta
  y el origen. Si no existe, no se crea aquí: lo crea el bootstrap.
- `resolve_library_path` y `resolve_config_path` no cambian de semántica;
  actualizar referencias documentales (docstrings apuntan a ADR 0008).

### 3.3. Descriptor con `root` y bootstrap con directorio explícito
(`storage/library.py`)

- `LibraryConfig` gana `root: Path | None`. `load_library` lo valida como
  texto expandido a ruta absoluta si existe; su no coincidencia con la raíz
  resuelta es `LibraryError` con acción sugerida (relocalizar o regenerar
  el descriptor), nunca mezcla silenciosa de catálogos.
- El bootstrap pasa a recibir el **directorio de metadatos resuelto**
  (paramétrico, `None` = derivar de la raíz, para compatibilidad con los
  tests actuales):
  - creación: `library.toml` nace con `version`, `id` **y `root`**;
  - adopción (descriptor ya presente): si no tiene `root`, se añade con
    escritura conservadora (preservar el resto byte a byte salvo la línea
    añadida); si tiene `root` y coincide, nada que hacer; si no coincide,
    `LibraryError`.
- Los helpers pasan a operar sobre el directorio de metadatos resuelto
  (`library_file(dir)`, `thumbs_dir(dir)`, `foto_dir(root)` queda como
  derivación por defecto del nivel 4). Actualizar **todas** las llamadas de
  `indexer/`, `transfer/` y `ui/` (el trabajo de normalización de T1.2
  reduce esto a un punto por componente).
- El scanner (`core/scanner.py`) ignora `.foto/` por nombre; sin cambio:
  con la metainformación fuera simplemente no habrá nada que ignorar.

### 3.4. Superficie CLI (`foto-index`, `foto-export`, `foto-import`)

- Flag nuevo `--metadata PATH` (metavar `PATH`, help en español: «directorio
  de metainformación; por defecto: <fototeca>/.foto»).
- Los tres CLIs resuelven la metainformación **solo** con
  `resolve_metadata_dir` (nada de leer `FOTO_METADATA` por su cuenta).
- `--help` (epílogo) documenta la cadena completa de las tres ubicaciones,
  como ya hace con la fototeca.
- Mensajes de error de «no hay biblioteca» y de «no hay metainformación»
  explícitos y con la acción sugerida; códigos de salida como hoy.
- `foto-export` (solo lectura) no crea nada: si el directorio de
  metainformación no existe, error explícito; `foto-index` y `foto-import`
  usan el bootstrap paramétrico y el lock en el directorio resuelto.

### 3.5. UI (`foto`)

- `foto` pasa a parsear `sys.argv[1:]` con `argparse` (antes de construir
  nada de Qt): `--config PATH`, `--library PATH`, `--metadata PATH`, con
  help y epílogo iguales a los CLIs. El entry point de `pyproject.toml` no
  cambia (`foto.ui.app:main`); lanzar sin argumentos sigue funcionando
  (Finder/lanzador de macOS).
- La resolución es la misma cadena que los CLIs (comparten resolutores):
  `--library` → `FOTO_LIBRARY` → `[library].path` → diálogo de primera
  ejecución (que persiste `[library].path`, como hoy) y `--metadata` →
  `FOTO_METADATA` → `[metadata].path` → `<fototeca>/.foto`.
- Errores de resolución/configuración en diálogo crítico, como hoy.

### 3.6. Documentación (parte de la entrega)

- `AGENT.md`, sección «Configuración»: añadir la cadena de metainformación,
  el flag de la UI y el campo `root`; actualizar la referencia al ADR.
- `README.md`: uso de `--metadata` y nota sobre fototeca de Apple Photos
  (metainformación fuera del paquete; requisito de *Full Disk Access*;
  escaneo de `originals/` aún pendiente de su propio ADR).
- `docs/adr/0008`: solo si la implementación obliga a una corrección menor
  (no rediseñar); registrar cualquier desviación como nota del ADR.
- Los `--help` de los cuatro binarios documentan las tres cadenas.

## 4. Desglose de tareas del paso 2

- [x] **T2.1 — `[metadata]` en `storage/config.py`** (§ 3.1): validación,
  `AppConfig.metadata_path`, conservación en `save_config`, canonical sin
  la sección. Tests en `tests/test_config_persistence.py`: carga de la
  sección (absoluta, relativa al fichero de config, con `~`), error si no
  es texto, conservación byte a byte de `[metadata]` al reescribir
  `[library].path`, y fichero nuevo sin la sección. (2026-11-13)
- [x] **T2.2 — Resolutor de metadatos en `storage/resolver.py`** (§ 3.2):
  `MetadataResolution`, `resolve_metadata_dir`, validación de «existe y no
  es directorio». Tests (patrón de `test_library_resolution.py`): los
  cuatro orígenes con `environment` inyectable, precedencia completa,
  override que corta la lectura de config, y error de ruta-fichero.
  (2026-11-13)
- [x] **T2.3 — `root` y bootstrap paramétrico en `storage/library.py`**
  (§ 3.3): `LibraryConfig.root`, validación de coincidencia, escritura al
  crear y al adoptar (conservadora), helpers sobre el directorio resuelto.
  Tests en `tests/test_library_bootstrap.py`: creación con `root`,
  adopción legacy (se añade `root` preservando el resto), coincidencia,
  no coincidencia (`LibraryError`), y bootstrap sobre directorio externo
  vacío (crea `.foto`-equivalente donde se le mande). Actualizar las
  llamadas internas que usen los helpers cambiados. (2026-11-13)
  > Nota (2026-11-13): `library_file`/`thumbs_dir`/`load_library` pasan a
  > recibir el directorio de metadatos resuelto; `load_library(dir, root)`
  > valida la coherencia del campo. `LibraryBootstrap.foto_dir` pasa a
  > llamarse `metadata_dir`. La adopción inserta `root` tras la línea `id`.
  > El test `test_move_library_preserves_relative_paths_and_photos` se
  > adapta al ADR 0008: al relocalizar la fototeca, el dueño actualiza el
  > `root` del descriptor; las rutas relativas siguen sobreviviendo al
  > traslado.
- [x] **T2.4 — `foto-index` con `--metadata`** (§ 3.4): flag, resolución por
  el resolutor, epílogo de `--help` con las tres cadenas, bootstrap y lock
  en el directorio resuelto. Tests en `tests/test_cli.py`: `--metadata`
  crea/adopta en el sitio indicado, precedencia `--metadata` >
  `FOTO_METADATA` > config > default, y el mensaje de error sugiere las
  tres fuentes. (2026-11-13)
  > Nota (2026-11-13): los mensajes de error de metainformación inválida
  > incluyen la ruta y el origen de la cadena (option/environment/config);
  > no existe el estado «no hay metainformación» porque el nivel 4 siempre
  > deriva de la fototeca. El epílogo documenta las tres cadenas y el
  > ejemplo con `--metadata`.
- [x] **T2.5 — `foto-export` y `foto-import` con `--metadata`** (§ 3.4):
  igual que T2.4, con la salvedad de solo-lectura de export (error si no
  existe el directorio de metainformación, sin crearlo) y lock del import
  en el directorio resuelto. Tests en `tests/test_transfer_export.py` y
  `tests/test_transfer_import.py`. (2026-11-13)
- [x] **T2.6 — `foto` con `--config --library --metadata`** (§ 3.5):
  argparse en `main()`, mismos resolutores, diálogo de primera ejecución
  intacto (persiste solo `[library].path`), la UI no escribe
  `[metadata].path`. Tests con `QT_QPA_PLATFORM=offscreen` donde proceda
  (parseo de argumentos y resolución pueden cubrirse sin Qt; los
  existentes de `tests/test_ui_load_photos.py` marcan el patrón).
  (2026-11-13)
  > Nota (2026-11-13): el parseo se factoriza en `_build_ui_parser`,
  > `_resolve_before_dialog` y `_resolve_metadata_dir` (sin Qt), cubiertos
  > por `tests/test_ui_arguments.py`; la resolución de metadatos ocurre
  > tras el diálogo, cuando la raíz ya es definitiva. El entry point de
  > `pyproject.toml` no cambia y lanzar sin argumentos sigue funcionando.
- [x] **T2.7 — Documentación** (§ 3.6): `AGENT.md`, `README.md`,
  epílogos de los cuatro binarios y, si procede, corrección menor del ADR
  0008 con nota de origen. (2026-11-13)
  > Nota (2026-11-13): corrección menor registrada en el ADR 0008 (§ «Nota
  > de corrección»): la UI pasa a admitir los tres flags y nunca escribe
  > `[metadata].path`, en línea con la decisión ya tomada del prompt (§ 2.4),
  > que corrige el texto original del ADR; el «Pendiente» del ADR queda
  > actualizado con lo decidido y lo documentado.
- [x] **T2.8 — Verificación y cierre del paso 2.** Suite completa en verde;
  smoke manual end-to-end: biblioteca de prueba con `--metadata` a un
  directorio externo → `foto-index` crea ahí `library.toml` (con `root`) +
  `catalog.sqlite` + `thumbs/` → `foto-export` lee → `foto-import` importa
  → `foto` abre con los tres flags y sin ellos. Confirmar que la fototeca
  no recibe escrituras (comparar mtime del árbol de fotos). Cerrar la
  entrega con un resumen de tareas y desviaciones en este fichero.
  (2026-11-13)
  > Nota (2026-11-13): 848 tests en verde (793 del paso 1 + 55 nuevos) y
  > ruff limpio. Smoke con biblioteca de prueba y metainformación externa:
  > `foto-index` creó en el directorio externo `library.toml` (con `root`),
  > `catalog.sqlite` e `index.lock`; `foto-export` leyó de ahí y
  > `foto-import` importó con el lock en el directorio resuelto; `foto`
  > (offscreen) abrió tanto con `--config --library --metadata` como sin
  > flags (`FOTO_CONFIG`/`FOTO_LIBRARY`/`FOTO_METADATA`), sin errores. La
  > fototeca no recibió ninguna escritura (mtimes idénticos antes y
  > después) y no se creó `.foto/` dentro de ella. `thumbs/` lo crea el
  > servicio de miniaturas de la UI, no el indexador (igual que antes).

**Criterio de fin del paso 2:** las tres cadenas resueltas en un único
sitio, los cuatro binarios exponen `--metadata` (y la UI además
`--config`/`--library`), `library.toml` lleva `root` con validación, y una
fototeca de Apple Photos es operable con metainformación externa sin
escribir dentro del paquete.

> Estado final del paso 2 (2026-11-13): T2.1–T2.8 cerradas. Las tres
> cadenas (configuración, fototeca, metainformación) viven en
> `storage/resolver.py` con el patrón override CLI → entorno →
> `config.toml` → por defecto; los cuatro binarios exponen `--metadata` y
> la UI además `--config`/`--library` (argparse antes de construir Qt,
> sin persistir overrides); `config.toml` admite `[metadata].path`
> (opt-in, conservada por `save_config`, nunca escrita por la app);
> `library.toml` lleva `root` (escrito al crear, añadido al adoptar
> descriptores legacy, validado con error explícito de
> relocalizar/regenerar) y el bootstrap opera sobre el directorio de
> metainformación resuelto (`None` = `<fototeca>/.foto`). 848 tests y
> ruff en verde; smoke end-to-end con metainformación externa sin
> escribir en la fototeca.
>
> Desviaciones respecto al enunciado literal:
> - `library_file`, `thumbs_dir` y `load_library` pasan a recibir el
>   directorio de metainformación resuelto (como prevé § 3.3);
>   `LibraryBootstrap.foto_dir` pasa a llamarse `metadata_dir`.
> - `ensure_library(root, metadata_dir=None, *, dry_run=False)`: la firma
>   previa era posicional en `dry_run`; ningún llamador interno la usaba
>   posicional.
> - El test `test_move_library_preserves_relative_paths_and_photos` se
>   adapta: relocalizar la fototeca exige actualizar el `root` del
>   descriptor (el ADR 0008 lo convierte en error explícito); las rutas
>   relativas siguen sobreviviendo al traslado.
> - El mensaje «no hay metainformación» de `foto-export` se da cuando el
>   directorio resuelto no existe (solo lectura, no lo crea); en
>   `foto-index`/`foto-import` no existe tal estado porque el nivel 4
>   siempre deriva de la fototeca.

---

## 5. Fuera del objetivo

- **Migración automática** de una `.foto/` existente a la nueva ubicación:
  operación manual del dueño; solo se documenta (README, T2.7).
- **Indexado específico de Apple Photos** (restringir el escaneo a
  `originals/`, detalles de paquete y TCC): ADR futuro; aquí solo se deja
  preparada la ruta externa de metainformación.
- **XMP**, sincronización a la baja, `--replace`, y cualquier forma de
  escribir `[metadata].path` desde la UI.
- **Dependencias nuevas** de runtime.
- Cambiar `resolve_config_path`/`resolve_library_path` más allá de lo
  indicado (docstrings): su semántica está probada y aceptada.
