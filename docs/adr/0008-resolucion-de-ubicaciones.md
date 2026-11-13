# ADR 0008 — Resolución de ubicaciones: configuración, fototeca y metainformación

- **Estado:** aceptada
- **Fecha:** 2026-11-13
- **Decide:** Jose
- **Ámbito:** las tres cadenas de resolución de rutas de la aplicación: dónde
  vive el fichero de configuración, dónde está la fototeca y dónde se guarda la
  metainformación (`.foto/`). Amplía el [ADR 0001](0001-configuracion.md) con
  una cuarta fuente para la metainformación (`--metadata`, `FOTO_METADATA` y
  `[metadata].path`), sin cambiar el formato de `library.toml` más allá de un
  campo opcional. Quedan fuera el contenido del catálogo y sus migraciones
  (ADR 0002), la exportación/importación (ADR 0007) y cualquier detalle de
  indexado específico de la fototeca de Apple Photos (solo se deja apuntado).

## Contexto

La aplicación resuelve hoy dos ubicaciones con precedencia bien definida y
probada:

1. **Configuración global:** `--config <ruta>` → `FOTO_CONFIG` →
   `platformdirs.user_config_dir("foto")/config.toml`
   (`storage/config.py`, `resolve_config_path`).
2. **Fototeca:** `--library <ruta>` → `FOTO_LIBRARY` → `[library].path` →
   diálogo de primera ejecución (`storage/resolver.py`,
   `resolve_library_path`, ADR 0001).

La tercera ubicación es rígida: la metainformación siempre se deriva de la
raíz de la fototeca (`storage/library.py`, `foto_dir(root) = root / ".foto"`),
y ahí viven `library.toml`, `catalog.sqlite`, `thumbs/`, `index.lock` y el
destino por defecto de `exports/`. No existe `--metadata` ni
`FOTO_METADATA`.

Esa rigidez choca con el objetivo declarado de funcionar sobre la propia
fototeca de Apple Photos. `Photos Library.photoslibrary` es un **paquete**
protegido por TCC: el sistema solo permite leer su contenido al proceso con
*Full Disk Access*, y Apple lo reserva para Photos — modificarlo desde fuera
es contrario a la regla 1 («nunca modificar los originales») tanto en letra
como en espíritu. Si la raíz de la fototeca es el paquete de Apple, la
metainformación debe poder vivir **fuera** de él.

Hay, además, una razón de flexibilidad general: bibliotecas en medios de solo
lectura (discos externos, compartidos SMB), copias de trabajo donde no se
quiere doble `.foto/`, o tests que necesitan un directorio de metadatos
independiente del árbol de fotos.

## Decisión

Toda ubicación se resuelve con el mismo patrón: **override CLI → variable de
entorno → `config.toml` → valor por defecto**, y la precedencia de cada cadena
vive en un único sitio de `storage/`.

### Cadena 1 — Configuración de la aplicación (sin cambios)

| Orden | Fuente | Valor por defecto |
| --- | --- | --- |
| 1 | `--config <ruta>` | — |
| 2 | `FOTO_CONFIG` | — |
| 3 | — | `platformdirs.user_config_dir("foto")/config.toml` |

Depende de la plataforma (XDG en Linux, `Application Support` en macOS), tal
como decidió el ADR 0001. Sin cambios.

### Cadena 2 — Fototeca (sin cambios)

| Orden | Fuente | Valor por defecto |
| --- | --- | --- |
| 1 | `--library <ruta>` | — |
| 2 | `FOTO_LIBRARY` | — |
| 3 | `[library].path` de `config.toml` | — |
| 4 | — | diálogo de primera ejecución, que escribe `[library].path` |

Implementada en `resolve_library_path` (ADR 0001). Sin cambios.

### Cadena 3 — Metainformación (nueva)

| Orden | Fuente | Valor por defecto |
| --- | --- | --- |
| 1 | `--metadata <ruta>` | — |
| 2 | `FOTO_METADATA` | — |
| 3 | `[metadata].path` de `config.toml` | — |
| 4 | — | `<fototeca>/.foto` (derivada de la raíz, como hoy) |

Nueva función `resolve_metadata_dir(root, ...)` en `storage/resolver.py`, con
la misma firma y semántica de `environment` inyectable que
`resolve_library_path`. En `config.toml`:

```toml
version = 1

[library]
path = "~/Pictures/Photos Library.photoslibrary"

[metadata]
# Solo hace falta si la metainformación no va dentro de la fototeca.
# Admite "~" y rutas relativas al directorio de este fichero, como [library].
# path = "~/Fotos/ApplePhotos.foto"
```

### Semántica del override

- Redirige el **directorio completo** de metainformación, no piezas sueltas:
  `library.toml`, `catalog.sqlite`, `thumbs/`, `index.lock` y `exports/`
  viven juntos ahí o no viven. Redirigir solo el catálogo crearía dos
  mitades de estado con vidas independientes.
- **No se mueve nada automáticamente.** Apuntar a un directorio vacío lo
  inicializa con el bootstrap atómico existente (`ensure_library`); apuntar
  a un directorio con `library.toml` previo **adopta** ese descriptor. La
  migración manual de una `.foto/` existente queda como operación del dueño
  (copiar el directorio y cambiar la configuración), documentada pero no
  automatizada.
- El valor por defecto (nivel 4) es el único derivado de la fototeca; los
  niveles 1–3 son rutas absolutas del dueño. Una vez fijadas, la relación
  «fototeca → metainformación» es explícita, no implícita.
- Todos los puntos de entrada (`foto-index`, `foto-export`, `foto-import` y
  la UI) resuelven la metainformación por la misma cadena. La UI expone los
  mismos overrides que los CLIs (`--metadata`, más `--config` y `--library`)
  y sus overrides de CLI no se persisten: nunca escribe `[metadata].path`
  (ver nota de corrección al final).

### Coherencia: el descriptor sabe de dónde viene

Con la metainformación fuera de la fototeca se pierde la garantía física de
que «este `.foto/` pertenece a esta fototeca» (la daba el directorio padre).
Para recuperar la coherencia lógica:

- `library.toml` acepta un campo **opcional** `root` con la ruta de la
  fototeca a la que pertenece (pista, no certeza: misma filosofía que
  `[library].path` en el ADR 0001). El formato no cambia de versión: es un
  campo añadido, ignorado por lectores antiguos y validado como texto.
- Al abrir una biblioteca, si el `root` del descriptor existe y no coincide
  con la raíz resuelta, se produce un **error explícito** con la acción
  sugerida (relocalizar o regenerar el descriptor), nunca un fallo en
  silencio ni la mezcla de catálogos. Sin `root` (descriptores antiguos), se
  sigue como hoy.
- Esto detecta el caso peligroso de dos fototecas apuntando a la misma
  metainformación por error de configuración.

### Preparación para Apple Photos

- La raíz es `~/Pictures/Photos Library.photoslibrary`; macOS la abre como
  directorio si el proceso tiene *Full Disk Access*. Con la metainformación
  en otro sitio, `foto` **no escribe nada dentro del paquete de Apple**:
  cumple la regla 1 y no interfiere con Photos.
- `~` y las rutas con espacios funcionan por las reglas ya existentes
  (`expanduser`, rutas relativas respecto al directorio del `config.toml`).
- Queda apuntado para un ADR futuro: indexar el paquete entero incluiría los
  derivados de Photos (`resources/`, `database/`…); el caso Apple
  probablemente exija restringir el escaneo a `originals/`. La flexibilidad
  de rutas de este ADR no depende de esa decisión.

## Reglas de implementación

- **La precedencia vive en un solo sitio:** `storage/resolver.py`, al estilo
  de `resolve_library_path`, con `environment` inyectable para tests.
  Ningún módulo lee `FOTO_METADATA` por su cuenta; los CLIs tampoco leen el
  `config.toml` por su cuenta.
- **Sin Qt:** la resolución y el bootstrap viven en `storage/`; la UI solo
  muestra el resultado y pide cambios.
- **`pathlib` siempre**, `expanduser`, rutas relativas de `[metadata].path`
  resueltas contra el directorio del fichero de configuración, y en memoria
  solo rutas absolutas.
- **Bootstrap atómico e idempotente** con el mecanismo existente (lock de
  creación, publicación sin sobrescribir): la nueva ubicación no introduce
  un segundo mecanismo de creación.
- **Errores explícitos** (`LibraryError`/`ConfigError`): descriptor
  incompatible, `root` que no coincide, ruta que existe y no es un
  directorio. Nunca `except: pass`.
- **El `--help` de los tres CLIs documenta la cadena completa**, como ya
  hacen con la fototeca.
- **Sin dependencias nuevas.**

## Consecuencias

**Positivas**

- `foto` funciona sobre una fototeca de Apple Photos sin escribir en el
  paquete: metainformación externa de nacimiento, no un parche.
- Bibliotecas en medios de solo lectura pasan a ser operables.
- Simetría de diseño: las tres ubicaciones se comportan igual, lo que hace
  el sistema predecible y documentable en una sola tabla.
- El override por CLI permite dos fototecas con la misma configuración
  global (p. ej. un disco externo conectado puntualmente) sin editar TOML.

**Negativas**

- Cuarta fuente que mantener por comando (`--metadata`) y una sección más en
  `config.toml`.
- Con la metainformación fuera, se pierde la garantía física del
  emplazamiento: mitigado con el campo `root` y el error explícito.
- Riesgo de configuraciones incoherentes (metainformación compartida entre
  fototecas): mitigado por la validación en arranque, no por silencio.

**Riesgos abiertos**

- Que el usuario mueva la fototeca y olvide actualizar `[metadata].path`:
  mismo tratamiento que la relocalización del ADR 0001 (ofrecer corregir,
  nunca fallar en silencio).
- TCC: sin *Full Disk Access* el paquete de Apple ni siquiera se puede leer;
  es un requisito del entorno, no del código, pero hay que documentarlo.

## Alternativas descartadas

| Alternativa | Motivo |
| --- | --- |
| Redirigir solo `catalog.sqlite` (y dejar `library.toml` y `thumbs/` dentro) | Divide el estado en dos sitios con vidas distintas; rompe el invariante «todo lo escrito por `foto` vive en `.foto/`». |
| Enlazado simbólico `.foto → ruta externa` | Depende de crear un symlink dentro del paquete de Apple (escritura que se quiere evitar) y de sistemas que no los soportan; invisible para el dueño que copia el directorio. |
| Guardar la ruta de metainformación en `library.toml` | El descriptor vive dentro del directorio redirigido: sería la ubicación describiéndose a sí misma, circular; no resuelve el arranque (hay que saber dónde está antes de leerlo). |
| Solo `FOTO_METADATA` sin flag ni sección de config | Rompe la simetría con la cadena de la fototeca y no sirve para uso diario desde el lanzador gráfico. |
| Directorio de metadatos global único (p. ej. en `platformdirs.user_data_dir`) | Una sola metainformación para todas las fototecas impide tener dos bibliotecas independientes y deshace el modelo por-biblioteca del ADR 0001. |

## Pendiente

- ADR de indexado sobre la fototeca de Apple Photos (escaneo restringido a
  `originals/`, manejo de paquetes, notas de TCC/*Full Disk Access*).
- Decidido durante la implementación (paso 2 del prompt): el diálogo de
  primera ejecución solo pregunta por la fototeca; `[metadata].path` es la
  única vía persistente para la metainformación.
- Documentada durante la implementación (paso 2 del prompt): la migración
  manual de una `.foto/` existente a una ubicación externa está en
  `README.md` (§ «Migración manual de una `.foto/` existente»).

### Nota de corrección (2026-11-13)

La implementación del paso 2 (prompt
`prompts/implementar-adr-0008-ubicaciones.md`) cambió el comportamiento de
la UI respecto al texto original de este ADR: `foto` pasa a admitir
`--config`, `--library` y `--metadata` (mismas cadenas que los CLIs) y la
UI **nunca escribe** `[metadata].path`; el diálogo de primera ejecución
sigue persistiendo solo `[library].path`. La frase original «En la UI no
hay flag: lee `[metadata].path` y, si se redirige desde el diálogo de
fototeca, lo escribe» queda sustituida por la del apartado «Semántica del
override». No hay otros cambios de diseño.
