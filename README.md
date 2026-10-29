# foto

Una fototeca local-first para sustituir a Apple Photos, escrita en Python + Qt.

`foto` nace como herramienta personal: quiero gestionar mi archivo fotográfico sin
depender de iCloud, del ecosistema Apple ni de una base de datos opaca. Los
originales no se tocan, los metadatos viven en ficheros estándar y todo funciona
sin conexión.

> **Estado:** prototipo funcional de la UI. La aplicación abre una carpeta,
> muestra la rejilla, el visor con la rejilla en una fila y el panel de
> metadatos. Faltan el catálogo SQLite, la lectura/escritura de XMP y el indexado
> incremental.

## Objetivo

Sustituir a Apple Photos en mi flujo diario manteniendo lo que sí funciona bien:
una biblioteca visual rápida, metadatos legibles y copias de seguridad
automáticas. La diferencia es que aquí la biblioteca es mía y puedo abrirla,
inspeccionarla y migrarla con herramientas estándar.

## Principios

- **Local-first.** La biblioteca vive en disco. La red es opcional.
- **Originales intactos.** `foto` nunca modifica ni borra los ficheros
  originales; los cambios se guardan como metadatos.
- **Metadatos abiertos.** XMP como fuente de verdad, compatible con Lightroom,
  digiKam y exiftool.
- **Multiplataforma real.** Desarrollo y uso diario en Omarchy (Arch Linux +
  Hyprland/Wayland), con soporte de primera clase en macOS.
- **Sin telemetría.** Nada sale del equipo salvo las copias de seguridad que yo
  configure explícitamente.
- **Componentes reemplazables.** Catálogo reconstruible desde disco: si la base
  de datos desaparece, se puede regenerar.

## Alcance del MVP

La primera versión se centra en **ver y entender** las fotos, no en editarlas.

- Abrir una carpeta o biblioteca y mostrar una rejilla de miniaturas.
- Vista individual con zoom y navegación entre fotos.
- Lectura de metadatos: fecha de captura, cámara, objetivo, ISO, apertura,
  velocidad, distancia focal y geolocalización.
- Favoritos y valoraciones, escritos en el XMP correspondiente.
- Catálogo SQLite para búsquedas y miniaturas, regenerable desde los XMP.

Fuera del MVP, por ahora: edición, álbumes, reconocimiento de caras y cualquier
forma de publicación o compartición.

## Roadmap

- [ ] **Fase 1 — Visualización y metadatos** (MVP)
  - Indexado de carpetas y generación de miniaturas
  - Rejilla, visor con la rejilla en una fila y panel de metadatos (prototipo;
    faltan metadatos reales)
  - Favoritos persistidos en XMP
- [ ] **Fase 2 — Organización**
  - Álbumes manuales e inteligentes
  - Búsqueda y filtros por fecha, cámara, etiquetas y ubicación
- [ ] **Fase 3 — Protección de datos**
  - Copia de seguridad a disco local
  - Copia de seguridad a S3 (opcional y configurable)
  - Verificación de integridad de la biblioteca
- [ ] **Fase 4 — Edición no destructiva**
  - Recorte, rotación, exposición, color
- [ ] **Fase 5 — Migración e IA local**
  - Importación de una biblioteca existente de Apple Photos sin perder metadatos
  - Caras, escenas y OCR ejecutados en local

## Arquitectura

```
┌─────────────────────────────┐
│  UI (Qt / PySide6)          │  rejilla, visor, panel de metadatos
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│  Núcleo (Python)            │  importación, indexado, miniaturas
└───────┬──────────────┬──────┘
        │              │
   SQLite          XMP sidecars
   (índice)        (fuente de verdad)
        │
   originales en disco
        │
   copias: disco local + S3
```

- **UI:** Qt mediante PySide6 (LGPL, compatible con una licencia MIT). Teclado
  primero, atajos de vim y lenguaje visual sobrio; reglas en
  `docs/diseno-ui.md`.
- **Catálogo:** SQLite como índice rápido de búsqueda y miniaturas.
- **Metadatos:** sidecars XMP junto a cada original. SQLite se puede reconstruir
  a partir de ellos.
- **Copias de seguridad:** destino local y, opcionalmente, S3.
- **Configuración:** TOML en el directorio de configuración del usuario; lo
  específico de cada biblioteca vive en `<fototeca>/.foto/`. Ver
  `docs/adr/0001-configuracion.md`.

Estructura actual:

```
foto/
├── docs/
│   ├── adr/        # decisiones de arquitectura
│   └── diseno-ui.md
├── src/foto/
│   ├── core/       # listado de imágenes
│   ├── storage/    # configuración TOML
│   └── ui/         # aplicación real y prototipo de UI
├── tests/
└── README.md
```

## Documentación

- `docs/diseno-ui.md` — reglas de interfaz: teclado primero, atajos de vim,
  composición visual y accesibilidad.
- `docs/adr/` — decisiones de arquitectura, empezando por la configuración
  (`0001-configuracion.md`).

## Prototipo

El repositorio ya contiene una UI ejecutable con datos reales de una carpeta,
pero sin catálogo ni metadatos:

```sh
uv sync
uv run foto
PYTHONPATH=src .venv/bin/python -m foto.ui.prototype
```

`foto` abre la última biblioteca configurada o pide una carpeta. El prototipo
usa fotos falsas para revisar la interfaz sin tocar discos.

## Requisitos

- Python 3.12 o superior
- PySide6
- Linux con Wayland (probado en Omarchy/Hyprland) o macOS
- `exiftool` para la fase de metadatos (aún no integrado)

Las instrucciones completas de instalación y uso se ampliarán cuando el MVP
indexe metadatos reales.

## Licencia

MIT. Ver `LICENSE` (pendiente de añadir).
