"""Benchmark de arranque de la UI con un catálogo sintético de 25 000 fotos.

Construye una biblioteca de prueba con catálogo SQLite real y mide el pipeline
de arranque. No es un test de pytest: es un script de medición reproducible.

Condiciones medidas (cada una con repetición, se reporta el mínimo):

(a) ``load_photos`` completo: catálogo → ``load_from_catalog`` → ``Photo``.
(b) ``PhotoWindow._populate_grid`` con la lista ya cargada, sobre una
    ventana construida vacía (mide solo el populate síncrono de la rejilla;
    el umbral del populate por lotes se sube para forzar el camino
    monolítico y que la cifra sea comparable con la del paso 1).
(c) Arranque progresivo (T2.2–T2.5, ADR 0011) con la secuencia real del
    ``MainWindow``: tiempo hasta el **primer píxel** (ventana visible con
    el estado de carga, sin datos), hasta el **primer lote navegable**
    (primeros ítems en la rejilla) y hasta el **populate completo**. La
    carga corre en el hilo worker y el bucle de eventos se bombea como en
    la app real. No se cuentan la construcción del catálogo ni la
    importación de módulos (coste fijo, no del pipeline).
(d) ``--interaccion`` (fase 2 del plan de revisión de UI): sobre una
    ``MainWindow`` ya poblada completamente, mide cada evento de
    interacción como *tiempo desde antes de la llamada hasta después de
    ``app.processEvents()``* —no solo la función interna—:

    - ``j``/``k``: 100 ``QTest.keyClick`` sobre la rejilla, alternando
      ambas teclas y partiendo de una foto intermedia (evento →
      selección con repintado y scroll incluidos).
    - filtro: 50 actualizaciones de ``search_changed`` con consultas de
      1–3 letras sobre nombres sintéticos (``0``, ``00``, ``000``, ``1``,
      ``12``, ``IMG``…), terminando en ``""`` (evento → visibilidad,
      cabeceras, caché de visibles y recuentos).

    Cada modo se repite ``--repeticiones`` rondas y las muestras se
    agregan para reportar **p50, p95 y máximo**, con el veredicto
    OK/FALLA del umbral **p95 < 50 ms**. Si la geometría de la rejilla no
    es fiable en ``offscreen`` (``_visual_rows()`` es ``None``), la cifra
    de ``j``/``k`` no se presenta como válida.

Condiciones del filesystem, documentadas (afectan a las rutas y a la
disponibilidad):

- **Sin originales** (por defecto): el catálogo existe pero ningún fichero
  de imagen está en el disco. Es la condición fría (USB en frío, volumen
  grande): los ``stat()`` de disponibilidad fallan rápido.
- **Con originales** (``--originales``): se escriben ficheros mínimos
  (bytes sin decodificar; nadie los decodifica aquí) para el caso normal.

Uso exacto::

    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --originales
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --fotos 25000 --repeticiones 3
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --fotos 25000 --repeticiones 3 --interaccion
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --fotos 25000 --repeticiones 3 --favoritas-primero
    QT_QPA_PLATFORM=offscreen uv run python scripts/bench_arranque.py --fotos 25000 --repeticiones 3 --fase-b

El directorio temporal se borra al terminar (``--keep`` para conservarlo
e inspeccionar la biblioteca generada). El script fuerza la plataforma
``offscreen`` si no está definida, pero se declara en el comando para que
la medición sea explícita.
"""

from __future__ import annotations

import argparse
import math
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


def _porcentil(muestras: list[float], q: float) -> float:
    """Percentil por rango cercano (p50, p95, …) sobre muestras en segundos."""
    ordenadas = sorted(muestras)
    indice = min(len(ordenadas) - 1, max(0, math.ceil(q * len(ordenadas)) - 1))
    return ordenadas[indice]


# Umbral de la fase 2 (§ 5 del plan): p95 < 50 ms para j/k y para filtro.
UMBRAL_P95 = 0.050


def _linea_interaccion(muestras: list[float], *, fiable: bool = True) -> str:
    """p50/p95/máx y veredicto OK/FALLA del umbral de la fase 2."""
    p50 = _porcentil(muestras, 0.5)
    p95 = _porcentil(muestras, 0.95)
    maximo = max(muestras)
    veredicto = "OK" if p95 < UMBRAL_P95 else "FALLA"
    if not fiable:
        veredicto = "n/a (geometría no fiable)"
    return (
        f"p50 {_formato_segundos(p50)} · p95 {_formato_segundos(p95)} · "
        f"máx {_formato_segundos(maximo)} · umbral p95<{UMBRAL_P95 * 1000:.0f} ms: "
        f"{veredicto}"
    )


def _interaccion(
    app, raiz: Path, metadata: Path, esperado: int, repeticiones: int
) -> tuple[bool, list[float], list[float]]:
    """Modo (d): j/k y filtro sobre una ``MainWindow`` poblada del todo.

    Devuelve ``(geometria_fiable, muestras_jk, muestras_filtro)``; cada
    muestra es el tiempo evento → ``processEvents`` (ver docstring del (d)).
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window = MainWindow(raiz, _config(raiz), metadata)
    window.show()
    app.processEvents()
    while window._populate_active or len(window._grid_items) < esperado:
        app.processEvents()
        time.sleep(0.001)

    # Si la geometría no es fiable en offscreen, las cifras de j/k no se
    # presentan como válidas (se miden igual, con el veredicto n/a).
    geometria_fiable = window.grid._visual_rows() is not None
    if not geometria_fiable:
        print(
            "aviso: la geometría de la rejilla no es fiable en offscreen "
            "(_visual_rows() es None); la cifra de j/k no se presenta como válida"
        )

    filtros = ["0", "00", "000", "1", "12", "IMG"]
    jk: list[float] = []
    filtro: list[float] = []
    try:
        for _ in range(repeticiones):
            # Partir siempre de una foto intermedia.
            claves = window._visible_keys_cache
            window._select_key(claves[len(claves) // 2])
            app.processEvents()
            for indice in range(100):
                tecla = Qt.Key_J if indice % 2 == 0 else Qt.Key_K
                inicio = time.perf_counter()
                QTest.keyClick(window.grid, tecla)
                app.processEvents()
                jk.append(time.perf_counter() - inicio)
            for indice in range(50):
                texto = filtros[indice % len(filtros)]
                inicio = time.perf_counter()
                window.status.search_changed.emit(texto)
                app.processEvents()
                filtro.append(time.perf_counter() - inicio)
            # Cerrar la ronda con el filtro limpio.
            inicio = time.perf_counter()
            window.status.search_changed.emit("")
            app.processEvents()
            filtro.append(time.perf_counter() - inicio)
    finally:
        window.close()
    return geometria_fiable, jk, filtro


def _arranque_dos_fases(
    app,
    raiz: Path,
    metadata: Path,
    esperado_total: int,
    contadores: dict | None = None,
):
    """Mide primer píxel, fase 1 y populate completo sin esperas fijas.

    El fin de la fase 1 se detecta en el ``on_done`` del populate (envuelto
    aquí) con dos guardas: que el bench haya contado ya una emisión de
    ``loaded`` y que no se haya registrado todavía la fase 1. Así no hay
    carrera con la fase 2 ni hace falta dormir un tiempo concreto.

    Con ``contadores`` (modo ``--fase-b``) se instrumentan las funciones que
    la Fase B saca del hilo de UI; los parches se instalan antes del primer
    ``MainWindow`` y se restauran siempre en ``finally``.
    """
    restaurar = _instalar_contadores(contadores) if contadores is not None else None
    try:
        return _arranque_dos_fases_instrumentado(
            app, raiz, metadata, esperado_total
        )
    finally:
        if restaurar is not None:
            restaurar()


def _arranque_dos_fases_instrumentado(app, raiz, metadata, esperado_total):
    """Cuerpo de la medición, con los parches ya instalados."""
    t0 = time.perf_counter()
    window = MainWindow(raiz, _config(raiz), metadata)
    cargas: list[int] = []
    fase1: dict[str, float | int | None] = {
        "tiempo": None,
        "items": None,
        "fotos": None,
    }

    def contar_loaded(*_args) -> None:
        cargas.append(1)

    window._loader.loaded.connect(contar_loaded)
    terminar = window._finish_populate

    def finish_wrapped() -> None:
        terminar()
        if cargas and fase1["tiempo"] is None:
            fase1["tiempo"] = time.perf_counter()
            fase1["items"] = len(window._grid_items)
            fase1["fotos"] = len(window.photos)

    window._finish_populate = finish_wrapped
    window.show()
    app.processEvents()
    t1 = time.perf_counter()
    while fase1["tiempo"] is None:
        app.processEvents()
    while window._populate_active or len(window._grid_items) < esperado_total:
        app.processEvents()
    t3 = time.perf_counter()
    window.close()
    app.processEvents()
    return t1 - t0, fase1["tiempo"] - t0, t3 - t0, fase1["items"], fase1["fotos"]


def _contadores_fase_b() -> dict:
    """Contadores del modo ``--fase-b`` (una medición por repetición)."""
    return {
        "ui_index": 0,
        "ui_sort": 0,
        "worker_prep": 0,
        "filter_calls": 0,
        "filter_seconds": 0.0,
    }


def _instalar_contadores(contadores: dict):
    """Envuelve solo durante el arranque las funciones que Fase B mide.

    Devuelve una función que restaura todo, siempre llamada en ``finally``:
    el bench no deja parches colgados entre repeticiones ni modos.
    """
    from foto.ui import app as app_module
    from foto.ui import presentation
    from foto.ui import window as window_module
    from foto.ui.window import PhotoWindow

    restauraciones: list = []

    def parchear(modulo, nombre: str, fabrica) -> None:
        original = getattr(modulo, nombre)
        setattr(modulo, nombre, fabrica(original))
        restauraciones.append(lambda: setattr(modulo, nombre, original))

    def contar_ui(nombre: str, clave: str):
        def fabrica(original):
            def envuelto(*args, **kwargs):
                contadores[clave] += 1
                return original(*args, **kwargs)

            return envuelto

        parchear(window_module, nombre, fabrica)

    contar_ui("build_search_index", "ui_index")
    contar_ui("sorted_photos", "ui_sort")

    def fabrica_prep(original):
        def envuelto(*args, **kwargs):
            contadores["worker_prep"] += 1
            return original(*args, **kwargs)

        return envuelto

    parchear(app_module, "prepare_photos", fabrica_prep)
    parchear(presentation, "prepare_photos", fabrica_prep)

    original_filter = PhotoWindow._apply_filter

    def filtro_medido(self, texto):
        inicio = time.perf_counter()
        try:
            return original_filter(self, texto)
        finally:
            contadores["filter_calls"] += 1
            contadores["filter_seconds"] += time.perf_counter() - inicio

    PhotoWindow._apply_filter = filtro_medido
    restauraciones.append(
        lambda: setattr(PhotoWindow, "_apply_filter", original_filter)
    )

    def restaurar() -> None:
        for deshacer in reversed(restauraciones):
            deshacer()

    return restaurar


def _fase_b(app, raiz: Path, metadata: Path, args) -> None:
    """Modo Fase B: el de ``--favoritas-primero`` más los contadores nuevos."""
    esperado_fav = args.fotos // 20
    pixeles: list[float] = []
    primeras: list[float] = []
    completos: list[float] = []
    items: list[int] = []
    ui_index: list[int] = []
    ui_sort: list[int] = []
    worker_prep: list[int] = []
    filter_calls: list[int] = []
    filter_ms: list[float] = []
    for _ in range(args.repeticiones):
        contadores = _contadores_fase_b()
        pixel, fase1, completo, n_items, _fotos = _arranque_dos_fases(
            app, raiz, metadata, args.fotos, contadores
        )
        pixeles.append(pixel)
        primeras.append(fase1)
        completos.append(completo)
        items.append(int(n_items))
        ui_index.append(contadores["ui_index"])
        ui_sort.append(contadores["ui_sort"])
        worker_prep.append(contadores["worker_prep"])
        filter_calls.append(contadores["filter_calls"])
        filter_ms.append(contadores["filter_seconds"] * 1000)

    print("(c) arranque en dos fases (fase B):")
    print(f"  primer píxel (ventana + estado de carga): {_linea(pixeles)}")
    print(f"  fase 1 (favoritas navegables):             {_linea(primeras)}")
    print(f"  populate completo ({args.fotos} fotos):       {_linea(completos)}")
    print(f"  ítems creados en la fase 1: {items[0]} (esperado: {esperado_fav})")
    print(
        f"  build_search_index/sorted_photos en la UI: "
        f"{sum(ui_index)} / {sum(ui_sort)} (esperado: 0 / 0)"
    )
    print(
        f"  prepare_photos en el worker: {min(worker_prep)}…{max(worker_prep)} "
        f"por arranque (esperado: ≥ 1)"
    )
    medio_ms = statistics.median(filter_ms)
    print(
        f"  _apply_filter en el arranque: {sum(filter_calls)} llamadas, "
        f"mediana {medio_ms:.1f} ms (esperado: < 20 ms)"
    )
    mediana = statistics.median(primeras)
    ok = (
        mediana < 1.0
        and not any(ui_index)
        and not any(ui_sort)
        and all(n >= 1 for n in worker_prep)
        and medio_ms < 20.0
    )
    print(
        f"  veredicto fase B: {'OK' if ok else 'FALLA'} "
        f"(fase 1 mediana {mediana * 1000:.1f} ms)"
    )


def _favoritas_primero(app, raiz: Path, metadata: Path, args) -> None:
    """Modo A.3/A.5: solo la sección (c), con fase 1 separada y veredicto."""
    esperado_fav = args.fotos // 20  # ``_construir_biblioteca`` marca 1 de cada 20
    pixeles: list[float] = []
    primeras: list[float] = []
    completos: list[float] = []
    items: list[int] = []
    fotos: list[int] = []
    for _ in range(args.repeticiones):
        pixel, fase1, completo, n_items, n_fotos = _arranque_dos_fases(
            app, raiz, metadata, args.fotos
        )
        pixeles.append(pixel)
        primeras.append(fase1)
        completos.append(completo)
        items.append(int(n_items))
        fotos.append(int(n_fotos))
    print("(c) arranque en dos fases (favoritas primero):")
    print(f"  primer píxel (ventana + estado de carga): {_linea(pixeles)}")
    print(f"  fase 1 (favoritas navegables):             {_linea(primeras)}")
    print(f"  populate completo ({args.fotos} fotos):       {_linea(completos)}")
    print(
        f"  ítems creados en la fase 1: {items[0]} (esperado: {esperado_fav}) · "
        f"fotos en memoria: {fotos[0]}"
    )
    mediana = statistics.median(primeras)
    veredicto = "OK" if mediana < 1.0 else "FALLA"
    print(
        f"  veredicto fase 1 < 1000 ms: {veredicto} "
        f"(mediana {mediana * 1000:.1f} ms)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fotos", type=int, default=25_000)
    parser.add_argument("--originales", action="store_true",
                        help="escribe ficheros mínimos en el disco (condición normal)")
    parser.add_argument("--repeticiones", type=int, default=3)
    parser.add_argument("--keep", action="store_true",
                        help="conserva la biblioteca temporal para inspección")
    parser.add_argument("--interaccion", action="store_true",
                        help="mide j/k y el filtro incremental (fase 2); sin el "
                             "flag el comportamiento (a)/(b)/(c) no cambia")
    parser.add_argument("--favoritas-primero", action="store_true",
                        help="arranque en dos fases (A.3): mide la fase 1 por "
                             "separado y cuenta los ítems creados; omite (a), "
                             "(b) y (d)")
    parser.add_argument("--fase-b", action="store_true",
                        help="como --favoritas-primero más los contadores de la "
                             "Fase B: índice/orden en la UI, prepare_photos en "
                             "el worker y tiempo de _apply_filter")
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

        # A.3/A.5 y Fase B: modo de arranque en dos fases. Se omiten (a), (b) y (d).
        if args.favoritas_primero or args.fase_b:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication([])
            if args.fase_b:
                _fase_b(app, raiz, metadata, args)
            else:
                _favoritas_primero(app, raiz, metadata, args)
            return

        # (a) load_photos completo, con repetición.
        tiempos = _medir(args.repeticiones, lambda: load_photos(raiz, metadata))
        photos = load_photos(raiz, metadata)
        print(f"(a) load_photos ({len(photos)} fotos):")
        _reportar(tiempos)

        # (b) _populate_grid síncrono sobre una ventana construida vacía.
        # El umbral del populate por lotes (T2.4) se sube para forzar el
        # camino monolítico y que la cifra siga siendo la del paso 1.
        app = QApplication.instance() or QApplication([])

        # (d) interacción (fase 2): j/k y filtro, con percentiles y umbral.
        if args.interaccion:
            fiable, jk, filtro = _interaccion(
                app, raiz, metadata, args.fotos, args.repeticiones
            )
            print(f"(d) interacción ({args.fotos} fotos, {args.repeticiones} rondas):")
            print(f"  j/k    ({len(jk)} pulsaciones): {_linea_interaccion(jk, fiable=fiable)}")
            print(f"  filtro ({len(filtro)} eventos):    {_linea_interaccion(filtro)}")
            return

        from foto.ui import window as window_module
        from foto.ui.app import _placeholder_icon
        from foto.ui.window import PhotoWindow

        limite_original = window_module.POPULATE_SYNC_LIMIT
        window_module.POPULATE_SYNC_LIMIT = 10**9
        try:
            ventana_vacia = PhotoWindow([], placeholder_icon=_placeholder_icon(Path()))
            tiempos = _medir(
                args.repeticiones, lambda: ventana_vacia._populate_grid(photos)
            )
        finally:
            window_module.POPULATE_SYNC_LIMIT = limite_original
        print(f"(b) _populate_grid ({len(photos)} items):")
        _reportar(tiempos)

        # (c) Arranque progresivo (T2.7): primer píxel (ventana visible con
        # estado de carga), primer lote navegable y populate completo.
        esperado = len(photos)

        def _arranque() -> tuple[float, float, float]:
            t0 = time.perf_counter()
            window = MainWindow(raiz, _config(raiz), metadata)
            window.show()
            app.processEvents()
            t1 = time.perf_counter()
            while not window._grid_items:
                app.processEvents()
                time.sleep(0.001)
            t2 = time.perf_counter()
            while window._populate_active or len(window._grid_items) < esperado:
                app.processEvents()
                time.sleep(0.001)
            t3 = time.perf_counter()
            window.close()
            app.processEvents()
            return t1 - t0, t2 - t0, t3 - t0

        primer_pixel, primer_lote, completo = [], [], []
        for _ in range(args.repeticiones):
            a, b, c = _arranque()
            primer_pixel.append(a)
            primer_lote.append(b)
            completo.append(c)
        print("(c) arranque progresivo hasta ventana visible:")
        print(f"  primer píxel (ventana + estado de carga): {_linea(primer_pixel)}")
        print(f"  primer lote navegable:                    {_linea(primer_lote)}")
        print(f"  populate completo ({esperado} fotos):      {_linea(completo)}")
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
