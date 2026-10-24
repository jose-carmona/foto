# ADR 0001 — Almacenamiento de la configuración

- **Estado:** aceptada
- **Fecha:** 2026-10-23
- **Decide:** Jose
- **Ámbito:** configuración de la aplicación. No cubre metadatos de fotos ni el
  catálogo, que se rigen por `README.md`.

## Contexto

`foto` es una aplicación de escritorio local-first, mono-usuario, que debe
persistir como mínimo la **ubicación de la fototeca**. Más adelante tendrá que
guardar preferencias de UI, destinos de copia de seguridad y credenciales de S3.

Restricciones ya fijadas en `AGENT.md`:

- `platformdirs` para localizar configuración, caché y datos.
- `core/` y `storage/` no importan Qt.
- Los secretos no viven en el repositorio ni en ficheros de configuración
  versionados.
- Sin estado global mutable: la configuración se pasa explícitamente.
- Linux (Omarchy) y macOS son objetivos de primera clase.

El detonante es un único valor, pero la decisión se toma pensando en que el
fichero crezca sin rehacerse.

## Decisión

La configuración se divide en tres niveles con ciclos de vida distintos.

### 1. Configuración global del usuario → TOML

Un único fichero `config.toml` en el directorio de configuración que devuelve
`platformdirs.user_config_dir("foto")`:

| Sistema | Ruta |
| --- | --- |
| Linux (XDG) | `~/.config/foto/config.toml` |
| macOS | `~/Library/Application Support/foto/config.toml` |

Contenido inicial:

```toml
version = 1

[library]
# Ruta a la fototeca. Admite "~" y rutas relativas al directorio de este fichero.
path = "/home/jose/Fotos"
```

Criterios:

- **TOML** porque es legible, admite comentarios y `tomllib` está en la stdlib
  desde Python 3.11, así que leer no añade dependencias.
- **Un solo fichero**, plano y versionable mentalmente. Nada de árboles de
  ficheros ni includes.
- El fichero es **del usuario**: debe poder abrirlo y editarlo a mano sin romper
  la aplicación, y la aplicación debe poder reescribirlo (diálogo «abrir
  fototeca») sin perder el resto del contenido.

### 2. Configuración de la biblioteca → dentro de la biblioteca

Todo lo que describe *esa* fototeca concreta vive con los datos, en un
directorio derivado:

```
<fototeca>/
├── 2024/
├── 2025/
└── .foto/
    ├── library.toml     # descriptor: id estable, versión de esquema
    ├── catalog.sqlite   # índice reconstruible
    └── thumbs/          # caché de miniaturas
```

`library.toml`:

```toml
version = 1
id = "3f9c1b62-8a44-4f0e-9d1a-2c7b5e0f4a11"   # uuid4, generado al crear la biblioteca
```

Motivos:

- El catálogo SQLite es **por biblioteca y reconstruible**, así que no puede
  vivir en un SQLite global: se perdería al mover la biblioteca.
- Mover la carpeta o el disco entero no rompe nada; la biblioteca se describe a
  sí misma.
- El `id` permite reconocer una biblioteca aunque cambie de ruta (discos
  externos, renombrados).
- Es un directorio derivado nuevo, permitido por la regla 1 de `AGENT.md`.
  Nunca se escribe fuera de `.foto/` (salvo XMP junto al original).

> Nota: en la conversación previa se barajó `foto/library.toml` en la raíz de la
> fototeca. Se descarta en favor de `.foto/` para agrupar descriptor, catálogo y
> caché en un único sitio y no ensuciar la carpeta de fotos.

### 3. Secretos → llavero del sistema

Credenciales de S3 y similares **nunca** entran en `config.toml`:

- Uso normal: llavero del sistema (fase 3, cuando exista la copia a S3). No se
  añade la dependencia antes de necesitarla.
- CI y tests: variables de entorno.
- `config.toml` guarda como mucho el nombre del perfil o del destino, no el
  secreto.

## Precedencia

Para localizar la biblioteca, de mayor a menor prioridad:

1. `--library <ruta>` (CLI)
2. `FOTO_LIBRARY` (override de desarrollo y CI)
3. `[library].path` de `config.toml`
4. Si no hay ninguna: primera ejecución, diálogo para elegir carpeta; la
   elección se escribe en `config.toml`.

Para localizar el propio fichero de configuración (pensado para tests):

1. `--config <ruta>`
2. `FOTO_CONFIG`
3. `platformdirs.user_config_dir("foto")`

La precedencia debe estar implementada en un solo sitio y documentada en
`--help`. Ningún módulo lee variables de entorno por su cuenta.

## Reglas de implementación

- **Lectura** con `tomllib`. **Escritura**: serializador propio o `tomli-w`,
  a decidir al implementar; si es propio, debe conservar el orden de claves
  conocidas y no reformatear el fichero a lo loco.
- **Escritura atómica**: escribir en un temporal del mismo directorio y
  `os.replace`. Un corte de luz no puede dejar la app sin arrancar.
- **`version` desde el primer día**, con una función de migración que hoy solo
  valide. Sin esto, el primer cambio de formato es una rotura.
- **Validación al cargar**: fichero corrupto, ruta inexistente o `version`
  desconocida producen un error explícito del núcleo, no un `except: pass`.
- **Rutas**: expandir `~`; las relativas se resuelven respecto al directorio del
  fichero de configuración. En memoria siempre `pathlib.Path` absoluta.
- **La ruta guardada es una pista, no una certeza.** Un disco externo monta en
  `/run/media/<usuario>/FOTOS` en Linux y en `/Volumes/FOTOS` en macOS. Si al
  arrancar no existe, la app ofrece **relocalizar**; nunca falla en silencio ni
  crea la carpeta por su cuenta.
- **Modelos**: `AppConfig` y `LibraryConfig` como `dataclass(frozen=True)`,
  construidos una vez al arrancar y pasados explícitamente. Nada de singletons.
- **Sin Qt**: la carga y la escritura viven en `storage/` (o `core/`), nunca en
  la UI. La UI solo muestra el resultado y pide cambios.
- **Sin `.env` como fuente de verdad.** Un `.env` solo puede aportar overrides
  para desarrollo; nunca es la configuración de la aplicación.

## Consecuencias

**Positivas**

- Configuración inspeccionable y editable a mano; diffs limpios.
- Cero dependencias para leer.
- La biblioteca es portable y autodescriptiva.
- Secretos separados de la configuración, como exige `AGENT.md`.
- Testeable sin tocar la configuración real del usuario (`--config`/`FOTO_CONFIG`).

**Negativas**

- Hay que escribir un serializador TOML mínimo o añadir `tomli-w`.
- Dos ficheros que mantener (global y de biblioteca) en vez de uno.
- `.foto/` añade un directorio oculto dentro de la carpeta de fotos. Mitigado
  porque es derivado y reconstruible.

**Riesgos abiertos**

- Rutas de discos externos: mitigado por la relocalización; si aparece a menudo,
  añadir un UUID de volumen al descriptor en lugar de (o además de) la ruta.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| `.env` como fuente de verdad | Depende del cwd (impredecible al lanzar desde el menú), es invisible para el usuario, no está pensado para reescribirse desde la app, no expresa tipos ni jerarquía e invita a meter secretos en claro. |
| `QSettings` | Usa registro/plist/INI nativos distintos por plataforma, no es inspeccionable ni portable, y obligaría a que `storage/` importe Qt (regla 5). |
| SQLite global de configuración | Sobredimensionado para pocos valores; además la config debe leerse antes de abrir ningún catálogo. |
| JSON | Aceptable y sin dependencias, pero pierde comentarios, que importan en un fichero que el dueño edita a mano. |
| YAML | Dependencia nueva para el mismo resultado; TOML ya está en la stdlib. |
| Solo `--library` por CLI | No persiste; inservible en uso diario desde el lanzador gráfico. |
| Config dentro de la carpeta de fotos únicamente | No resuelve el arranque en frío: para abrir la biblioteca hay que saber dónde está. Por eso el nivel 1 sigue siendo necesario. |

## Pendiente

- Decidir `tomli-w` frente a serializador propio en la primera implementación.
- Añadir `keyring` cuando llegue la copia a S3, no antes.
- Valorar UUID de volumen si la relocalización se vuelve frecuente.
- Decidir si `[backup]` vive en `config.toml` o en un `foto/backup.toml` aparte
  cuando exista la fase 3.
