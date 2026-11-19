"""Benchmark de arranque de la UI con un catálogo sintético de 25 000 fotos.

Instrumentalización del prompt ``prompts/rendimiento-arranque-ui-25k.md``
(T1.1): construye una biblioteca de prueba con catálogo SQLite real y mide
el pipeline síncrono de arranque. No es un test de pytest: es un script de
medición reproducible.

Condiciones medidas (cada una con repetición, se reporta el mínimo):

(a) ``load_photos`` completo: catálogo → ``load_from_catalog`` → ``Photo``.
(b) ``PhotoWindow._populate_grid`` con la lista ya cargada, sobre una
    ventana construida vacía (mide solo el populate de la rejilla).
(c) Total hasta ventana visible: la secuencia del arranque real
    (``MainWindow``: carga de fotos/tags/ubicaciones + conexión RW +
    populate + ``show()`` + ``processEvents``), sin contar la construcción
    del catálogo ni la importación de módulos (coste fijo, no del pipeline).

Condiciones del filesystem, documentadas (afectan al coste de los ``stat()``):

- **Sin originales** (por defecto): el catálogo existe pero ningún fichero
  de imagen está en el disco. Es la condición fría (USB en frío, volumen
  grande): los ``stat()`` de ``load_from_catalog`` fallan rápido y el
  tamaño cae al valor del catálogo. La disponibilidad se evalúa igual.
- **Con originales** (``--originales``): se escriben ficheros mínimos
  (bytes sin decodificar; nadie los decodifica aquí) para que los
  ``stat()`` tengan éxito. Mide el coste del caso normal.

Uso exacto::

    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --originales
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --fotos 25000 --repeticiones 3

El directorio temporal se borra al terminar (``--keep`` para conservarlo
e inspeccionar la biblioteca generada). El script fuerza la plataforma
``offscreen`` si no está definida, pero se declara en el comando para que
la medición sea explícita.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import Location
from foto.storage.tags import connect_read_write, toggle_favorite
from foto.ui.app import MainWindow, load_photos

NOW = "2026-11-19T09:00:00+00:00"
CÁMARAS = (
    ("Apple", "iPhone 15 Pro"),
    ("Canon", "Canon EOS R6"),
    ("Sony", "ILCE-7M4"),
    ("Fujifilm", "X-T5"),
    ("", ""),  # sin cámara: grupo «Sin cámara» al ordenar por cámara
)


def _hash_sintetico(index: int) -> str:
    """Hash sintético determinista de 64 hex (no se calcula sobre contenido)."""
    return "sha256:" + f"{index:064x}"


def _construir_biblioteca(raiz: Path, fotos: int, originales: bool) -> Path:
    """Biblioteca de prueba con catálogo real y (opcionalmente) ficheros mínimos.

    Sin miniaturas ni indexación real: solo filas. Metadatos variados para
    que ordenamientos, grupos, búsqueda y favoritos trabajen sobre datos
    con forma realista.
    """
    metadata = raiz / ".foto"
    metadata.mkdir(parents=True)
    declared = (Location("default", raiz, "fast"),)
    catalog = Catalog.open(metadata / CATALOG_FILE_NAME, declared_locations=declared)
    try:
        location_id = catalog.insert_named_location(
            name="default", path=raiz, tier="fast", now=NOW
        )
        base = datetime(2015, 1, 1, tzinfo=UTC)
        for index in range(fotos):
            photo_hash = _hash_sintetico(index)
            captura = base + timedelta(minutes=index * 7)
            catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
            # Metadatos normalizados directos (el indexador los extraería con
            # exiftool): fecha, cámara cíclica, GPS en un 30 %.
            make, model = CÁMARAS[index % len(CÁMARAS)]
            con_lugar = index % 10 < 3
            catalog._conn.execute(
                """
                UPDATE photos SET
                    captured_at = ?, camera_make = ?, camera_model = ?,
                    lens_model = ?, iso = ?, f_number = ?, exposure_time = ?,
                    focal_length = ?, width = ?, height = ?,
                    gps_latitude = ?, gps_longitude = ?
                WHERE hash = ?
                """,
                (
                    captura.isoformat(),
                    make,
                    model,
                    "FE 24-70mm F2.8 GM" if make else None,
                    100 + (index % 12) * 100,
                    2.8,
                    1 / 125,
                    35.0,
                    6000,
                    4000,
                    (41.0 + index % 90 / 10) if con_lugar else None,
                    (2.0 + index % 90 / 10) if con_lugar else None,
                    photo_hash,
                ),
            )
            rel_path = f"{captura.year}/{captura.month:02d}/IMG_{index:05d}.jpg"
            size = 2_000_000 + (index % 50) * 100_000
            catalog.insert_file(
                location_id=location_id,
                rel_path=rel_path,
                photo_hash=photo_hash,
                size=size,
                mtime_ns=1_700_000_000_000_000_000 + index,
                inode=index + 1,
                device=1,
                now=NOW,
            )
            if originales:
                destino = raiz / rel_path
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(b"JPEG-minimo-sintetico")
        catalog.commit()
    finally:
        catalog.close()

    # Favoritos (ADR 0006): un 5 % con la etiqueta de sistema, escritos con
    # la conexión RW del visor, como en el arranque real.
    connection = connect_read_write(metadata / CATALOG_FILE_NAME)
    try:
        for index in range(0, fotos, 20):
            toggle_favorite(connection, _hash_sintetico(index), now=NOW)
    finally:
        connection.close()
    return metadata


def _medir(repeticiones: int, funcion) -> list[float]:
    """Ejecuta ``funcion`` ``repeticiones`` veces y devuelve los tiempos."""
    tiempos = []
    for _ in range(repeticiones):
        inicio = time.perf_counter()
        funcion()
        tiempos.append(time.perf_counter() - inicio)
    return tiempos


def _formato_segundos(segundos: float) -> str:
    return f"{segundos * 1000:8.1f} ms"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fotos", type=int, default=25_000)
    parser.add_argument("--originales", action="store_true",
                        help="escribe ficheros mínimos en el disco (condición normal)")
    parser.add_argument("--repeticiones", type=int, default=3)
    parser.add_argument("--keep", action="store_true",
                        help="conserva la biblioteca temporal para inspección")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication

    raiz = Path(tempfile.mkdtemp(prefix="foto-bench-"))
    try:
        inicio = time.perf_counter()
        metadata = _construir_biblioteca(raiz, args.fotos, args.originales)
        construccion = time.perf_counter() - inicio
        print(f"biblioteca sintética: {args.fotos} fotos en {raiz}")
        print(f"  construcción del catálogo (no se mide): {construccion:.1f} s")
        print(f"  condición de filesystem: {'con originales' if args.originales else 'sin originales'}")
        print()

        # (a) load_photos completo, con repetición.
        tiempos = _medir(args.repeticiones, lambda: load_photos(raiz, metadata))
        photos = load_photos(raiz, metadata)
        print(f"(a) load_photos ({len(photos)} fotos):")
        _reportar(tiempos)

        # (b) _populate_grid sobre una ventana construida vacía.

        app = QApplication.instance() or QApplication([])
        from foto.ui.app import _placeholder_icon
        from foto.ui.window import PhotoWindow

        ventana_vacia = PhotoWindow([], placeholder_icon=_placeholder_icon(Path()))
        tiempos = _medir(
            args.repeticiones, lambda: ventana_vacia._populate_grid(photos)
        )
        print(f"(b) _populate_grid ({len(photos)} items):")
        _reportar(tiempos)

        # (c) Arranque real hasta ventana visible: MainWindow (carga +
        # populate) y show() + processEvents, segmentado.
        def _arranque() -> tuple[float, float, float]:
            t0 = time.perf_counter()
            window = MainWindow(raiz, _config(raiz), metadata)
            t1 = time.perf_counter()
            window.show()
            app.processEvents()
            t2 = time.perf_counter()
            window.close()
            return t1 - t0, t2 - t1, t2 - t0

        construir, mostrar, total = [], [], []
        for _ in range(args.repeticiones):
            c, m, t = _arranque()
            construir.append(c)
            mostrar.append(m)
            total.append(t)
        print("(c) arranque real hasta ventana visible:")
        print(f"  MainWindow (carga + populate): {_linea(construir)}")
        print(f"  show() + processEvents:        {_linea(mostrar)}")
        print(f"  TOTAL hasta visible:           {_linea(total)}")
    finally:
        if args.keep:
            print(f"\nbiblioteca conservada en: {raiz}")
        else:
            shutil.rmtree(raiz, ignore_errors=True)


def _config(raiz: Path):
    """AppConfig mínima coherente con la biblioteca sintética."""
    from foto.storage.config import AppConfig

    return AppConfig(config_path=raiz / "config.toml", library_path=raiz, version=2)


def _reportar(tiempos: list[float]) -> None:
    print(f"  mínimo {_formato_segundos(min(tiempos))} · "
          f"mediana {_formato_segundos(statistics.median(tiempos))}")


def _linea(tiempos: list[float]) -> str:
    return (f"mín {_formato_segundos(min(tiempos))} · "
            f"mediana {_formato_segundos(statistics.median(tiempos))}")


if __name__ == "__main__":
    sys.exit(main())
