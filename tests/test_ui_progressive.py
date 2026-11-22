"""Arranque progresivo de la UI (paso 2 del prompt de 25k; ADR 0011).

Ciclo completo sin pantalla: ventana con estado de carga → carga del
catálogo en el worker → populate de la rejilla por lotes → búsqueda y
cierre a mitad sin errores. Incluye la regresión de la salida por
``Ctrl+q``/``:q`` (``aboutToQuit``), que no pasa por el ``closeEvent``.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QListWidgetItem

from foto.core.errors import CatalogError
from foto.core.library import LibraryLoadCancelled
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import AppConfig, Location
from foto.storage.tags import connect_read_write
from foto.ui import app as app_module
from foto.ui import window as window_module
from foto.ui.canvas import ImageLoadTask
from foto.ui.grid import GROUP_ROLE, HEADER_ROLE, PHOTO_ROLE, THUMB_H, THUMB_W
from foto.ui.models import LocationStatus, Photo, PhotoTag
from foto.ui.presentation import photo_group
from foto.ui.thumbnails import ThumbnailTask
from foto.ui.window import PhotoWindow
from tests.ui_factories import make_photo

NOW = "2026-11-19T09:00:00+00:00"


FIVE_PHOTOS = [
    make_photo(f"{letra}.jpg", f"2024-05-0{indice} 08:00")
    for indice, letra in enumerate("abcde", start=1)
]


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def progresivo(qapp, monkeypatch):
    """Ventana con el umbral del populate por lotes reducido para probarlo.

    Con bibliotecas pequeñas el populate es el síncrono de siempre; el
    umbral se baja a 2 ítems y el lote a 2 para ejercitar el camino
    progresivo de forma determinista.
    """
    monkeypatch.setattr(window_module, "POPULATE_SYNC_LIMIT", 2)
    monkeypatch.setattr(window_module, "POPULATE_BATCH_SIZE", 2)
    return qapp


def _ventana(qapp, photos: list[Photo]) -> PhotoWindow:
    window = PhotoWindow(
        photos,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    return window


def _bombear(qapp, condicion, intentos: int = 4000) -> bool:
    """Procesa eventos hasta que se cumpla la condición (sin esperas fijas)."""
    for _ in range(intentos):
        qapp.processEvents()
        if condicion():
            return True
        time.sleep(0.002)
    return condicion()


# ----------------------------------------------------------------------
# T2.4 — populate síncrono y por lotes
# ----------------------------------------------------------------------
def test_small_library_populates_synchronously(qapp):
    """Bajo el umbral, el populate es el monolítico de siempre."""
    window = _ventana(qapp, FIVE_PHOTOS)
    assert window._populate_active is False
    assert len(window._grid_items) == 5
    assert window._visible_count() == 5
    window.close()


def test_constructor_populates_the_grid_once(qapp, monkeypatch):
    """El constructor popula la rejilla una sola vez (R1).

    Regresión del doble populate: la llamada que ignoraba el resultado y
    ocurría antes de conectar las señales ya no existe; queda la segunda,
    que contempla el camino síncrono y el diferido.
    """
    llamadas: list[list[Photo]] = []
    original = PhotoWindow._populate_grid

    def espia(self, photos):
        llamadas.append(list(photos))
        return original(self, photos)

    monkeypatch.setattr(PhotoWindow, "_populate_grid", espia)

    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert len(llamadas) == 1
        assert llamadas[0] == FIVE_PHOTOS
        assert len(window._grid_items) == 5
        assert window._visible_count() == 5
        assert window.grid.currentItem() is not None
    finally:
        window.close()


def test_progressive_populate_inserts_in_batches(qapp, progresivo):
    """Por encima del umbral, la rejilla entra lote a lote y termina completa.

    Tras el primer lote la rejilla ya tiene ítems y la caché de visibles
    es coherente; al terminar, ``_grid_items`` y los recuentos cubren
    toda la biblioteca.
    """
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert window._populate_active is True
        assert len(window._grid_items) == 0

        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        assert window._populate_active is True
        assert len(window._grid_items) == 2
        assert window._visible_count() == 2
        assert len(window.grid._visible_rows) == 2

        assert _bombear(qapp, lambda: not window._populate_active)
        assert len(window._grid_items) == 5
        assert window._visible_count() == 5
        assert len(window.grid._visible_rows) == 5
        # La primera selección quedó puesta al terminar (T2.4).
        assert window.grid.currentItem() is not None
    finally:
        window.close()


def test_progressive_populate_defers_the_callers_post_work(qapp, progresivo):
    """``set_photos`` con lotes restaura la selección solo al terminar."""
    window = _ventana(qapp, [])
    try:
        window.photos = {photo.key: photo for photo in FIVE_PHOTOS}
        assert window._populate_active is False
        window._populate_grid(FIVE_PHOTOS)
        assert window._populate_active is True
        assert window.status._idle_right == "0 de 0"

        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        assert window.status._idle_right.startswith("2 de 5")

        assert _bombear(qapp, lambda: not window._populate_active)
        assert window._visible_count() == 5
        assert window.status._idle_right.startswith("5 de 5")
    finally:
        window.close()


def test_search_works_mid_populate(qapp, progresivo):
    """Búsqueda a medio populate: coherente con lo insertado, y al final total.

    Cada ítem nace ya oculto o visible según el filtro vigente, y la
    caché de visibles se mantiene lote a lote: la decisión del prompt
    (funcionar a medio populate) queda fijada.
    """
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        insertadas = set(window._grid_items)

        window.search_text = "a.jpg"
        window._apply_filter("a.jpg")

        visibles = set(window._visible_keys_cache)
        assert visibles <= insertadas
        assert all(
            "a.jpg" in window._search_index[key] for key in visibles
        )

        assert _bombear(qapp, lambda: not window._populate_active)
        assert window._visible_count() == 1
        assert set(window._visible_keys_cache) == {"a.jpg"}
    finally:
        window.close()


def test_live_search_survives_populate(qapp, progresivo):
    """La búsqueda viva escrita entre lotes no se pierde al terminar (A3)."""
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)

        # Solo el filtro vivo: ``search_text`` sigue siendo la consulta
        # confirmada (vacía).
        window._apply_filter("a.jpg")
        assert window.search_text == ""

        assert _bombear(qapp, lambda: not window._populate_active)
        assert window._filter_text == "a.jpg"
        assert window.search_text == ""
        assert window._visible_keys_cache == [FIVE_PHOTOS[0].key]
    finally:
        window.close()


def test_cancel_search_restores_committed_filter(qapp):
    """Cancelar con Esc vuelve al texto confirmado, no al vivo (A3)."""
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        window._commit_search("a.jpg")
        assert window._visible_keys_cache == [FIVE_PHOTOS[0].key]

        window._apply_filter("b.jpg")
        assert window._visible_keys_cache == [FIVE_PHOTOS[1].key]

        window._cancel_prompt("search")

        assert window._filter_text == "a.jpg"
        assert window.search_text == "a.jpg"
        assert window._visible_keys_cache == [FIVE_PHOTOS[0].key]
    finally:
        window.close()


def test_sort_mid_populate_keeps_live_filter(qapp, progresivo):
    """``:sort`` a medio populate conserva la consulta viva (A3)."""
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        window._apply_filter("a.jpg")

        window._sort_grid("camera")

        assert _bombear(qapp, lambda: not window._populate_active)
        assert window._filter_text == "a.jpg"
        assert window._visible_keys_cache == [FIVE_PHOTOS[0].key]
    finally:
        window.close()


def test_tag_filter_mid_populate_keeps_live_filter(qapp, progresivo):
    """``:tag`` a medio populate compone con la consulta viva (A3)."""
    fav = PhotoTag(name="fav", kind="system", status="confirmed", source="manual")
    photos = [
        make_photo("a.jpg", "2024-05-01 08:00", tags=(fav,)),
        make_photo("b.jpg", "2024-05-02 08:00", tags=(fav,)),
        make_photo("c.jpg", "2024-05-03 08:00"),
    ]
    window = _ventana(qapp, photos)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        window._apply_filter("a.jpg")

        window._set_tag_filter("fav")

        assert _bombear(qapp, lambda: not window._populate_active)
        assert window._filter_text == "a.jpg"
        assert window._tag_filter == "fav"
        assert window._visible_keys_cache == [photos[0].key]
    finally:
        window.close()


def test_sort_mid_populate_does_not_lose_the_cursor(qapp, progresivo):
    """``:sort`` durante un populate cancela la sesión vieja y no pierde nada."""
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        assert window._populate_active is True

        window._sort_grid("camera")
        # La sesión anterior quedó cancelada y la nueva manda.
        assert window._populate_active is True
        assert _bombear(qapp, lambda: not window._populate_active)
        assert len(window._grid_items) == 5
        assert window._visible_count() == 5
        assert window.grid.currentItem() is not None
    finally:
        window.close()


# ----------------------------------------------------------------------
# R9 — los grupos se calculan dentro del lote, no antes del primer lote
# ----------------------------------------------------------------------
def test_no_upfront_group_materialization(qapp, progresivo, monkeypatch):
    """Nada se materializa de grupos antes del primer lote (R9).

    El contador de ``photo_group`` sigue a cero cuando ``_populate_grid``
    vuelve (el primer ``QTimer`` aún no disparó) y al terminar el
    populate suma una llamada por foto.
    """
    llamadas: list[Photo] = []
    original = window_module.photo_group

    def contada(photo, key):
        llamadas.append(photo)
        return original(photo, key)

    monkeypatch.setattr(window_module, "photo_group", contada)
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert window._populate_active is True
        assert llamadas == []

        assert _bombear(qapp, lambda: not window._populate_active)
        assert len(llamadas) == len(FIVE_PHOTOS)
    finally:
        window.close()


def test_groups_and_headers_survive_mid_populate(qapp, progresivo):
    """Cabeceras, grupos y visibles coherentes a mitad de populate (R9).

    Biblioteca con varios grupos por fecha (dos meses) y por lugar
    (cuatro países más una foto sin lugar): a mitad del populate por
    fecha y al terminar el repoblado por ``:sort location`` cada foto
    insertada lleva el grupo que dicta ``photo_group``, su cabecera está
    desocultada si tiene fotos visibles y la caché de visibles coincide
    con la rejilla.
    """
    photos = [
        make_photo(
            "paris.jpg", "2024-05-01 08:00", place_country="Francia", place_city="París"
        ),
        make_photo(
            "madrid.jpg",
            "2024-05-02 08:00",
            place_country="España",
            place_city="Madrid",
        ),
        make_photo("roma.jpg", "2024-06-01 08:00", place_country="Italia", place_city="Roma"),
        make_photo(
            "sevilla.jpg",
            "2024-06-02 08:00",
            place_country="España",
            place_city="Sevilla",
        ),
        make_photo("sin-lugar.jpg", "2024-05-03 08:00"),
    ]

    def comprobar(window) -> None:
        cabeceras: list[str] = []
        cabecera_por_grupo: dict[str, QListWidgetItem] = {}
        grupos: list[tuple[str, str]] = []
        visibles: list[str] = []
        for row in range(window.grid.count()):
            item = window.grid.item(row)
            if item.data(HEADER_ROLE):
                cabeceras.append(item.text())
                cabecera_por_grupo.setdefault(item.data(GROUP_ROLE), item)
            else:
                clave = item.data(PHOTO_ROLE)
                grupo = photo_group(window.photos[clave], window._sort_key)
                assert item.data(GROUP_ROLE) == grupo[0]
                grupos.append(grupo)
                if not item.isHidden():
                    visibles.append(clave)
        # Las cabeceras parten la rejilla en bloques consecutivos: hay
        # una por grupo distinto, en el mismo orden y con la etiqueta
        # que dicta ``photo_group``.
        por_grupo: dict[str, str] = {}
        for clave_grupo, etiqueta in grupos:
            por_grupo.setdefault(clave_grupo, etiqueta)
        assert cabeceras == list(por_grupo.values())
        # Sin filtro activo, todo grupo con fotos tiene su cabecera a la vista.
        for clave_grupo in por_grupo:
            assert not cabecera_por_grupo[clave_grupo].isHidden()
        # La caché de visibles coincide con lo visible en la rejilla.
        assert window._visible_keys_cache == visibles

    window = _ventana(qapp, photos)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        assert window._populate_active is True
        comprobar(window)

        window._sort_grid("location")
        assert _bombear(qapp, lambda: not window._populate_active)
        comprobar(window)
        assert len(window._grid_items) == len(photos)
        assert window._visible_count() == len(photos)
    finally:
        window.close()


# ----------------------------------------------------------------------
# T2.5 — miniaturas durante el populate
# ----------------------------------------------------------------------
def test_visible_thumbnail_requests_ignore_uninserted_items(qapp, progresivo):
    """Las peticiones de miniatura solo tocan ítems ya insertados (T2.5).

    ``_visible_photo_keys`` recorre la rejilla real, así que una foto aún
    no insertada jamás entra en la cola; a medio populate la lista de
    claves visibles es subconjunto de las insertadas.
    """
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        insertadas = set(window._grid_items)

        claves = window._visible_photo_keys()

        assert set(claves) <= insertadas
        assert set(claves) == set(window._visible_keys_cache)
    finally:
        window.close()


# ----------------------------------------------------------------------
# T2.6 — cancelación y cierre limpio
# ----------------------------------------------------------------------
def test_close_mid_populate_leaves_no_live_timers(qapp, progresivo):
    """Cerrar a medio populate: sin timers, sin sesión y sin traceback."""
    window = _ventana(qapp, FIVE_PHOTOS)
    assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
    assert window._populate_active is True

    window.close()

    assert window._populate_active is False
    assert window._populate_state is None
    assert not window._populate_timer.isActive()
    assert not window._visible_timer.isActive()
    assert not window._g_timer.isActive()
    qapp.processEvents()  # nada más entra por el bucle: sin errores


def test_shutdown_is_idempotent(qapp):
    """El apagado puede repetirse (``closeEvent`` + ``aboutToQuit``)."""
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        window._visible_timer.start()
        window._g_timer.start()

        window.shutdown_background_work()
        window.shutdown_background_work()

        assert window._closing is True
        assert not window._visible_timer.isActive()
        assert not window._g_timer.isActive()
    finally:
        window.close()


def test_late_populate_batch_after_shutdown_is_ignored(qapp, progresivo):
    """El trabajo que llega tras el apagado no toca rejilla ni timers (A5)."""
    pedidas: list = []
    window = PhotoWindow(
        FIVE_PHOTOS,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=lambda requests: pedidas.append(requests),
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    try:
        assert _bombear(qapp, lambda: len(window._grid_items) >= 2)
        window.shutdown_background_work()
        items = len(window._grid_items)
        filas = list(window.grid._visible_rows)
        pedidas.clear()

        window._populate_next_batch()
        window._schedule_visible_thumbnails()
        window._request_visible_thumbnails()
        window._clear_g_pending()
        qapp.processEvents()

        assert len(window._grid_items) == items
        assert window.grid._visible_rows == filas
        assert pedidas == []
        assert not window._populate_timer.isActive()
        assert not window._visible_timer.isActive()
        assert not window._g_timer.isActive()
        assert window._populate_state is None
    finally:
        window.close()


# ----------------------------------------------------------------------
# T2.3 — el worker de carga
# ----------------------------------------------------------------------
def test_worker_emits_loaded_with_pure_data(qapp, tmp_path, monkeypatch):
    """La señal entrega ``(fotos, etiquetas, ubicaciones)`` sin objetos Qt."""

    def fake_load_library(path, meta, *, should_continue=None):
        assert should_continue is not None
        assert should_continue() is True
        return [FIVE_PHOTOS[0]], ["etiqueta"], ("ubicación",)

    monkeypatch.setattr(app_module, "load_library", fake_load_library)
    worker = app_module.LibraryLoadWorker(tmp_path, tmp_path)
    results: list = []
    worker.loaded.connect(lambda p, t, l: results.append((p, t, l)))
    worker.failed.connect(lambda m: results.append(("failed", m)))

    worker.load()

    assert results == [([FIVE_PHOTOS[0]], ["etiqueta"], ("ubicación",))]


def test_worker_cancel_discards_the_result(qapp, tmp_path, monkeypatch):
    """Cancelado antes de cargar: la guardia corta y no se emite nada."""

    def fake_load_library(path, meta, *, should_continue=None):
        assert should_continue() is False
        raise LibraryLoadCancelled

    monkeypatch.setattr(app_module, "load_library", fake_load_library)
    worker = app_module.LibraryLoadWorker(tmp_path, tmp_path)
    results: list = []
    worker.loaded.connect(lambda *a: results.append("loaded"))
    worker.failed.connect(lambda *a: results.append("failed"))

    worker.cancel()
    worker.load()

    assert results == []


def test_worker_reports_load_failures(qapp, tmp_path, monkeypatch):
    """El fallo de la carga llega por ``failed`` con el mensaje del guard."""

    def fake_load_library(path, meta, *, should_continue=None):
        raise RuntimeError("No se pudo abrir el catálogo: boom")

    monkeypatch.setattr(app_module, "load_library", fake_load_library)
    worker = app_module.LibraryLoadWorker(tmp_path, tmp_path)
    results: list = []
    worker.loaded.connect(lambda *a: results.append("loaded"))
    worker.failed.connect(lambda m: results.append(m))

    worker.load()

    assert results == ["No se pudo abrir el catálogo: boom"]


def test_worker_reports_sqlite_errors(qapp, tmp_path, monkeypatch):
    """Un ``sqlite3.Error`` del catálogo llega por ``failed`` (A2)."""

    def fake_load_library(path, meta, *, should_continue=None):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_module, "load_library", fake_load_library)
    worker = app_module.LibraryLoadWorker(tmp_path, tmp_path)
    results: list = []
    worker.loaded.connect(lambda *a: results.append("loaded"))
    worker.failed.connect(lambda m: results.append(m))

    worker.load()

    assert results == ["database is locked"]


def test_worker_reports_unexpected_errors(qapp, tmp_path, monkeypatch):
    """Ninguna excepción escapa del worker: también llega por ``failed``."""

    def fake_load_library(path, meta, *, should_continue=None):
        raise ValueError("boom")

    monkeypatch.setattr(app_module, "load_library", fake_load_library)
    worker = app_module.LibraryLoadWorker(tmp_path, tmp_path)
    results: list = []
    worker.loaded.connect(lambda *a: results.append("loaded"))
    worker.failed.connect(lambda m: results.append(m))

    worker.load()

    assert results
    assert results[0].startswith("Fallo inesperado al cargar el catálogo:")
    assert "boom" in results[0]


# ----------------------------------------------------------------------
# T2.2/T2.3 — MainWindow: ventana inmediata, carga por señal
# ----------------------------------------------------------------------
def _seed_catalog(root: Path, photo_hash: str, *extra_hashes: str) -> Path:
    (root / ".foto").mkdir(parents=True, exist_ok=True)
    declared = (Location("default", root, "fast"),)
    catalog = Catalog.open(root / ".foto" / CATALOG_FILE_NAME, declared_locations=declared)
    try:
        location_id = catalog.insert_named_location(
            name="default", path=root, tier="fast", now=NOW
        )
        for indice, a_hash in enumerate((photo_hash, *extra_hashes)):
            rel_path = "2024/una.jpg" if indice == 0 else f"2024/extra{indice}.jpg"
            catalog.insert_photo(a_hash, ".jpg", "JPEG", NOW)
            catalog.insert_file(
                location_id=location_id,
                rel_path=rel_path,
                photo_hash=a_hash,
                size=5,
                mtime_ns=1,
                inode=1,
                device=1,
                now=NOW,
            )
        catalog.commit()
    finally:
        catalog.close()
    return root / ".foto"


def _config(root: Path) -> AppConfig:
    return AppConfig(
        config_path=root / "config.toml", library_path=root, version=2
    )


def test_macos_activation_does_not_block(qapp, monkeypatch):
    """``_activate_window`` lanza ``osascript`` una sola vez y sin esperar (R6)."""
    import os

    llamadas: list = []

    class FalsoQProcess:
        @staticmethod
        def startDetached(programa, argumentos):
            llamadas.append((programa, list(argumentos)))

    monkeypatch.setattr(app_module, "QProcess", FalsoQProcess)
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        window.show()
        app_module._activate_window(window, macos=True)

        assert len(llamadas) == 1
        programa, argumentos = llamadas[0]
        assert programa == "osascript"
        assert any(str(os.getpid()) in argumento for argumento in argumentos)
        assert window.isVisible()
    finally:
        window.close()


def test_activation_skips_osascript_off_macos(qapp, monkeypatch):
    """Fuera de macOS no se lanza ``osascript`` (R6)."""
    llamadas: list = []

    class FalsoQProcess:
        @staticmethod
        def startDetached(*argumentos):
            llamadas.append(argumentos)

    monkeypatch.setattr(app_module, "QProcess", FalsoQProcess)
    window = _ventana(qapp, FIVE_PHOTOS)
    try:
        window.show()
        tocados: list = []
        monkeypatch.setattr(window, "raise_", lambda: tocados.append("raise"))
        monkeypatch.setattr(
            window, "activateWindow", lambda: tocados.append("activate")
        )

        app_module._activate_window(window, macos=False)

        assert tocados == ["raise", "activate"]
        assert llamadas == []
    finally:
        window.close()


def test_main_window_shows_a_loading_state_and_then_the_library(qapp, tmp_path):
    """Ciclo completo: ventana vacía → señal → biblioteca con un solo catálogo.

    La ventana nace sin datos y con el estado de carga (T2.2); la
    biblioteca entra por señal del worker (T2.3) y el populate termina
    con la ventana poblada, banner sin aviso y carga terminada.
    """
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        assert window.photos == {}
        assert window._library_loading is True
        assert window._populate_active is False
        window.show()
        qapp.processEvents()
        assert window.isVisible()
        assert window.empty_label.text() == "Cargando el catálogo…"
        assert window.status._pinned == "cargando el catálogo…"

        cargada = lambda: len(window.photos) == 1 and not window._populate_active
        assert _bombear(qapp, cargada), "la carga no terminó"
        assert len(window.photos) == 1
        assert len(window.tag_catalog) == 1  # ``fav`` sembrada
        assert len(window._locations) == 1
        assert window._library_loading is False
        assert window.empty_label.isHidden()
        assert window.status._pinned == ""
    finally:
        window.close()


def test_main_window_opens_with_the_fav_tag_filter(qapp, tmp_path):
    """Historia de usuario: la app abre con «:tag fav» ya aplicado.

    Con favoritas en el catálogo, la biblioteca entra con el filtro de
    etiqueta activo antes del populate: solo las favoritas quedan visibles
    y el indicador «etiqueta: fav» queda fijado en la barra. Una biblioteca
    sin favoritas arranca sin filtro: el filtro dejaría la app vacía.
    """
    from foto.storage.tags import toggle_favorite
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "fav.jpg", b"favorita")
    write_image(root / "2024" / "extra1.jpg", b"normal")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64, "sha256:" + "e" * 64)

    connection = connect_read_write(metadata / CATALOG_FILE_NAME)
    try:
        toggle_favorite(connection, "sha256:" + "d" * 64, now=NOW)
    finally:
        connection.close()

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        qapp.processEvents()
        cargada = lambda: len(window.photos) == 2 and not window._populate_active
        assert _bombear(qapp, cargada), "la carga no terminó"
        assert window._tag_filter == "fav"
        assert window.status._pinned == "etiqueta: fav"
        favorita = next(p for p in window.photos.values() if p.favorite)
        assert window._visible_keys_cache == [favorita.key]
        assert len(window._grid_items) == 2
    finally:
        window.close()


def test_main_window_reports_a_missing_catalog_after_the_show(
    qapp, tmp_path, monkeypatch
):
    """Sin catálogo: mismo guard y cierre con código 1, ahora tras el show."""
    root = tmp_path / "vacia"
    metadata = root / ".foto"
    metadata.mkdir(parents=True)

    avisos: list = []
    salidas: list = []
    monkeypatch.setattr(
        app_module.QMessageBox, "critical", lambda *a, **k: avisos.append(a)
    )
    monkeypatch.setattr(
        app_module.QApplication, "exit", lambda code=0: salidas.append(code)
    )

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        qapp.processEvents()
        assert window.isVisible()  # el estado de carga llegó a verse

        assert _bombear(qapp, lambda: bool(salidas)), "no llegó el fallo"
        assert len(avisos) == 1
        assert "foto-index" in avisos[0][2]
        assert salidas == [1]
        assert window._library_loading is False
    finally:
        window.close()


def test_worker_thread_stops_after_success(qapp, tmp_path):
    """El hilo de carga termina al entregar ``loaded`` (A2)."""
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        qapp.processEvents()
        assert _bombear(
            qapp,
            lambda: len(window.photos) == 1
            and not window._loader_thread.isRunning(),
        ), "el hilo de carga siguió vivo tras el éxito"
    finally:
        window.close()


def test_worker_thread_stops_after_failure(qapp, tmp_path, monkeypatch):
    """Y también termina cuando la carga falla (A2)."""
    root = tmp_path / "biblioteca"
    metadata = root / ".foto"
    metadata.mkdir(parents=True)

    def failing_load_library(path, meta, *, should_continue=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "load_library", failing_load_library)
    avisos: list = []
    salidas: list = []
    monkeypatch.setattr(
        app_module.QMessageBox, "critical", lambda *a, **k: avisos.append(a)
    )
    monkeypatch.setattr(
        app_module.QApplication, "exit", lambda code=0: salidas.append(code)
    )

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        qapp.processEvents()
        assert _bombear(qapp, lambda: bool(salidas)), "no llegó el fallo"
        assert avisos
        assert not window._loader_thread.isRunning()
    finally:
        window.close()


def test_main_window_close_mid_load_cancels_cleanly(qapp, tmp_path, monkeypatch):
    """Cerrar la ventana durante la carga: worker parado y salida limpia."""
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64)

    def slow_load_library(path, meta, *, should_continue=None):
        time.sleep(0.05)  # carga «en marcha» cuando la ventana se cierra
        if should_continue is not None and not should_continue():
            raise LibraryLoadCancelled
        return [], [], ()

    monkeypatch.setattr(app_module, "load_library", slow_load_library)
    window = app_module.MainWindow(root, _config(root), metadata)
    window.show()
    qapp.processEvents()

    window.close()
    qapp.processEvents()

    assert window._load_cancelled is True
    assert window._loader._cancelled is True
    assert not window._loader_thread.isRunning()


def test_quit_mid_load_cancels_the_worker_about_to_quit(
    qapp, tmp_path, monkeypatch
):
    """Regresión Ctrl+q/``:q``: la salida por ``aboutToQuit`` para el worker.

    ``QApplication.quit()`` sale del ``exec()`` sin pasar por el
    ``closeEvent`` de la ventana; sin el gancho de ``aboutToQuit`` el
    ``QThread`` moría en plena carga («Destroyed while thread is still
    running»).
    """
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64)

    def slow_load_library(path, meta, *, should_continue=None):
        time.sleep(0.2)  # sigue en carga cuando llegue el ``quit``
        if should_continue is not None and not should_continue():
            raise LibraryLoadCancelled
        return [], [], ()

    monkeypatch.setattr(app_module, "load_library", slow_load_library)
    window = app_module.MainWindow(root, _config(root), metadata)
    window.show()
    qapp.processEvents()
    assert window._loader_thread.isRunning()

    QTimer.singleShot(50, qapp.quit)
    qapp.exec()

    assert window._load_cancelled is True
    assert not window._loader_thread.isRunning()


def test_quit_shuts_the_thumbnail_service_down_about_to_quit(
    qapp, tmp_path, monkeypatch
):
    """Regresión Ctrl+q con miniaturas en vuelo (T2.10).

    ``quit()`` salía del ``exec()`` sin pasar por el ``closeEvent``, que
    era el único sitio donde se apagaba el servicio de miniaturas: al
    salir mientras se generaban, las tareas en vuelo emitían hacia
    objetos ya destruidos («RuntimeError: Signal source has been
    deleted»). El apagado de fondo cubre ahora el servicio y espera a las
    tareas activas antes de que muera nada.
    """
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    ruta = write_image(root / "2024" / "una.jpg", b"visibles")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64)

    en_curso = threading.Event()

    def lenta(path, target, *, photo_hash=None, cache=None):
        en_curso.set()
        time.sleep(0.1)  # la tarea sigue en vuelo cuando llegue el ``quit``
        return QImage(QSize(4, 4), QImage.Format.Format_RGB32)

    monkeypatch.setattr("foto.ui.thumbnails.load_thumbnail", lenta)
    window = app_module.MainWindow(root, _config(root), metadata)
    window.show()
    assert _bombear(
        qapp, lambda: len(window.photos) == 1 and not window._populate_active
    )
    photo = next(iter(window.photos.values()))
    servicio = window._thumbnail_service
    servicio.load_visible([(photo.key, ruta, photo.photo_hash)])
    assert en_curso.wait(5)

    QTimer.singleShot(0, qapp.quit)
    qapp.exec()

    # El apagado pasó por ``aboutToQuit`` con la tarea en vuelo: generación
    # avanzada y pool drenado; nada queda emitiendo hacia objetos muertos.
    assert window._load_cancelled is True
    assert servicio._generation >= 1
    assert servicio._pool.activeThreadCount() == 0


# ----------------------------------------------------------------------
# T2.10 — emitir hacia un objeto de señales ya destruido no rompe
# ----------------------------------------------------------------------
def test_thumbnail_task_discards_the_result_when_the_source_is_gone(
    qapp, tmp_path
):
    """Una miniatura que supera la espera del apagado no lanza al emitir.

    El objeto de señales (sin padre) puede destruirse mientras la tarea
    sigue decodificando; el resultado es descartable y ``run()`` debe
    acabar sin excepción («Signal source has been deleted»).
    """
    imagen = QImage(QSize(8, 8), QImage.Format.Format_RGB32)
    imagen.fill(Qt.red)
    ruta = tmp_path / "una.jpg"
    assert imagen.save(str(ruta))

    task = ThumbnailTask(0, "clave", ruta, QSize(THUMB_W, THUMB_H))
    task.signals.deleteLater()
    qapp.processEvents()

    task.run()  # no debe lanzar


def test_image_task_discards_the_result_when_the_source_is_gone(qapp, tmp_path):
    """Lo mismo para la decodificación del original en el visor."""
    imagen = QImage(QSize(8, 8), QImage.Format.Format_RGB32)
    imagen.fill(Qt.blue)
    ruta = tmp_path / "una.png"
    assert imagen.save(str(ruta))

    task = ImageLoadTask("clave", ruta)
    task.signals.deleteLater()
    qapp.processEvents()

    task.run()  # no debe lanzar


# ----------------------------------------------------------------------
# T2.3 — el banner de ubicaciones se recompongo con ``set_library``
# ----------------------------------------------------------------------
def test_set_library_rebuilds_the_unavailable_locations_banner(qapp):
    """Ubicación no disponible en la señal: banner y panel al entrar los datos."""
    window = _ventana(qapp, [])
    try:
        assert window._banner_text is None
        assert window.banner.isHidden()

        fantasma = make_photo(
            "fantasma.jpg",
            "2024-05-01 08:00",
            canonical_location="usb",
            available=False,
        )
        status = LocationStatus(
            name="usb",
            tier="archive",
            path=Path("/Volumes/FotoVault"),
            state="active",
            available=False,
            photo_count=1,
        )
        window.set_library([fantasma], locations=(status,))

        assert "usb" in window._banner_text
        assert "fantasma" not in window._banner_text
        assert "1 foto no visible" in window._banner_text
        assert not window.banner.isHidden()
        assert len(window._locations) == 1
    finally:
        window.close()


def test_set_library_clears_the_banner_when_everything_is_available(qapp):
    """Todo a mano: sin banner (el estado de carga no dejaba ninguno)."""
    window = _ventana(qapp, [])
    try:
        status = LocationStatus(
            name="default",
            tier="fast",
            path=Path("/biblioteca"),
            state="active",
            available=True,
            photo_count=1,
        )
        window.set_library([FIVE_PHOTOS[0]], locations=(status,))

        assert window._banner_text is None
        assert window.banner.isHidden()
        assert window._visible_count() == 1
    finally:
        window.close()


def test_set_library_applies_the_initial_tag_filter(qapp):
    """El filtro inicial rige el populate: las que no cumplen nacen ocultas.

    La app real abre con ``fav`` (historia de usuario); la vía es la misma
    del filtro vivo a medio populate (A3), solo que entra antes del primer
    lote.
    """
    fav = PhotoTag(name="fav", kind="system", status="confirmed", source="manual")
    favorita = make_photo("fav.jpg", "2024-05-01 08:00", tags=(fav,))
    window = _ventana(qapp, [])
    try:
        window.set_library([favorita, *FIVE_PHOTOS], initial_tag_filter="fav")

        assert window._tag_filter == "fav"
        assert window.status._pinned == "etiqueta: fav"
        assert window._visible_keys_cache == [favorita.key]
        # La biblioteca completa está en la rejilla: las demás, ocultas.
        assert len(window._grid_items) == 6
    finally:
        window.close()


# ----------------------------------------------------------------------
# Guardas de la conexión RW del visor durante el arranque progresivo
# ----------------------------------------------------------------------
def test_main_window_keeps_its_own_rw_connection(qapp, tmp_path):
    """La conexión RW de favoritos sigue en el hilo de UI y la ventana la cierra."""
    from tests.conftest import write_image

    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    metadata = _seed_catalog(root, "sha256:" + "d" * 64)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        assert _bombear(qapp, lambda: len(window.photos) == 1)
        assert window.tag_connection is not None
    finally:
        window.close()
        assert window.tag_connection is None

    # La escritura de favoritos sigue funcionando tras la carga progresiva:
    # es la misma conexión del ADR 0006, abierta desde el arranque.
    connection = connect_read_write(metadata / CATALOG_FILE_NAME)
    try:
        from foto.storage.tags import toggle_favorite

        toggle_favorite(connection, "sha256:" + "d" * 64, now=NOW)
    except CatalogError as exc:  # pragma: no cover - solo ante esquema inesperado
        pytest.fail(f"la conexión RW dejó de funcionar: {exc}")
    finally:
        connection.close()
