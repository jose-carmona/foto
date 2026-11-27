"""Fase B: el worker entrega índice y orden, la UI no los recalcula.

Cubre el contrato nuevo de ``PreparedPhotos`` de extremo a extremo: el hilo
de UI no llama a ``build_search_index`` ni a ``sorted_photos`` durante el
arranque, las vistas preparada y recalculada son idénticas, la fase 2
amplía el índice sin reconstruirlo y el append diferido conserva la
preparación entregada por el worker.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from foto.core.library import LibraryLoadCancelled
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import AppConfig, Location
from foto.storage.tags import Tag, connect_read_write, toggle_favorite
from foto.ui import app as app_module
from foto.ui import presentation
from foto.ui import window as window_module
from foto.ui.grid import HEADER_ROLE, PHOTO_ROLE
from foto.ui.presentation import prepare_photos

NOW = "2026-11-19T09:00:00+00:00"

FAV_TAG = Tag(id=1, name="fav", kind="system", created_at=NOW, updated_at=NOW)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def progresivo(qapp, monkeypatch):
    """Populate por lotes determinista para solapar las dos fases."""
    monkeypatch.setattr(window_module, "POPULATE_SYNC_LIMIT", 2)
    monkeypatch.setattr(window_module, "POPULATE_BATCH_SIZE", 2)
    return qapp


def _hash(indice: int) -> str:
    return "sha256:" + f"{indice:064x}"


def _seed(root: Path, count: int, favorites: tuple[int, ...]) -> Path:
    """Catálogo con ``count`` fotos y las favoritas indicadas."""
    (root / ".foto").mkdir(parents=True, exist_ok=True)
    declared = (Location("default", root, "fast"),)
    catalog = Catalog.open(root / ".foto" / CATALOG_FILE_NAME, declared_locations=declared)
    try:
        location_id = catalog.insert_named_location(
            name="default", path=root, tier="fast", now=NOW
        )
        for indice in range(count):
            catalog.insert_photo(_hash(indice), ".jpg", "JPEG", NOW)
            catalog.insert_file(
                location_id=location_id,
                rel_path=f"2024/foto{indice:03d}.jpg",
                photo_hash=_hash(indice),
                size=5,
                mtime_ns=1,
                inode=indice + 1,
                device=1,
                now=NOW,
            )
        catalog.commit()
    finally:
        catalog.close()
    if favorites:
        connection = connect_read_write(root / ".foto" / CATALOG_FILE_NAME)
        try:
            for indice in favorites:
                toggle_favorite(connection, _hash(indice), now=NOW)
        finally:
            connection.close()
    return root / ".foto"


def _config(root: Path) -> AppConfig:
    return AppConfig(config_path=root / "config.toml", library_path=root, version=2)


def _bombear(qapp, condicion, intentos: int = 4000) -> bool:
    for _ in range(intentos):
        qapp.processEvents()
        if condicion():
            return True
        time.sleep(0.002)
    return condicion()


def _arranque_completo(qapp, root: Path, metadata: Path) -> app_module.MainWindow:
    window = app_module.MainWindow(root, _config(root), metadata)
    window.show()
    qapp.processEvents()
    assert _bombear(
        qapp,
        lambda: (
            len(window.photos) == 60
            and not window._has_more
            and not window._populate_active
        ),
    ), "el arranque en dos fases no terminó"
    return window


def _contar_llamadas(monkeypatch, destino: list[tuple[str, bool]], ui: bool):
    """Envuelve las dos funciones en un módulo y anota si corren en la UI."""
    modulo = window_module if ui else presentation
    original_build = modulo.build_search_index
    original_sort = modulo.sorted_photos
    principal = QApplication.instance().thread()

    def build(photos):
        destino.append(("build", QThread.currentThread() == principal))
        return original_build(photos)

    def sort(photos, key):
        destino.append(("sort", QThread.currentThread() == principal))
        return original_sort(photos, key)

    monkeypatch.setattr(modulo, "build_search_index", build)
    monkeypatch.setattr(modulo, "sorted_photos", sort)


def _vista(window) -> tuple[list[str], list[str], dict[str, str]]:
    """Orden de la rejilla, claves visibles y documento de búsqueda."""
    orden = [
        str(window.grid.item(fila).data(PHOTO_ROLE))
        for fila in range(window.grid.count())
        if window.grid.item(fila) is not None
        and not window.grid.item(fila).data(HEADER_ROLE)
    ]
    return (
        orden,
        list(window._visible_keys_cache),
        {key: window._search_index.get(key, "") for key in window.photos},
    )


def test_startup_does_not_build_index_or_sort_in_the_ui_thread(
    qapp, tmp_path, monkeypatch
):
    """Fase B: índice y orden se calculan en el worker, nunca en la UI."""
    root = tmp_path / "biblioteca"
    metadata = _seed(root, 60, (0,))
    ui_llamadas: list[tuple[str, bool]] = []
    worker_llamadas: list[tuple[str, bool]] = []
    _contar_llamadas(monkeypatch, ui_llamadas, ui=True)
    _contar_llamadas(monkeypatch, worker_llamadas, ui=False)

    window = _arranque_completo(qapp, root, metadata)
    try:
        assert ui_llamadas == []
        assert any(not en_ui for _, en_ui in worker_llamadas)
        assert {"build", "sort"} <= {nombre for nombre, _ in worker_llamadas}
    finally:
        window.close()


def test_prepared_and_recomputed_views_are_identical(qapp, tmp_path, monkeypatch):
    """La vista con ``prepared`` y la que recalcula en la UI coinciden."""
    root = tmp_path / "biblioteca"
    metadata = _seed(root, 60, (0,))

    preparada = _arranque_completo(qapp, root, metadata)
    vista_preparada = _vista(preparada)
    preparada.close()

    # La segunda ventana simula datos mínimos del worker: ``prepared=None``.
    monkeypatch.setattr(app_module, "prepare_photos", lambda photos: None)
    recalculada = _arranque_completo(qapp, root, metadata)
    try:
        assert _vista(recalculada) == vista_preparada
    finally:
        recalculada.close()


def test_second_phase_finds_non_favorite_without_rebuilding_index(
    qapp, tmp_path, monkeypatch
):
    """Tras la fase 2, `/` halla una no favorita sin reconstruir el índice."""
    root = tmp_path / "biblioteca"
    metadata = _seed(root, 60, (0,))
    llamadas: list[tuple[str, bool]] = []
    _contar_llamadas(monkeypatch, llamadas, ui=True)

    window = _arranque_completo(qapp, root, metadata)
    try:
        assert ("build", True) not in llamadas
        # Quitar ``fav`` es acción del usuario: puede reordenar, pero no
        # reconstruir el índice; la búsqueda debe usar el que trajo el worker.
        window._set_tag_filter(None)
        qapp.processEvents()
        window.status.search_changed.emit("foto042")
        qapp.processEvents()

        assert any("foto042" in key for key in window._visible_keys_cache)
        assert ("build", True) not in llamadas
    finally:
        window.close()


def test_pending_append_carries_prepared_data(progresivo, tmp_path, monkeypatch):
    """El append diferido de la fase 2 usa el ``prepared`` entregado."""
    qapp = progresivo
    root = tmp_path / "biblioteca"
    metadata = _seed(root, 60, tuple(range(6)))
    compuerta = threading.Event()

    def bloqueada(*args, **kwargs):
        compuerta.wait(2.0)
        raise LibraryLoadCancelled

    monkeypatch.setattr(app_module, "load_library_phases", bloqueada)
    llamadas: list[tuple[str, bool]] = []
    _contar_llamadas(monkeypatch, llamadas, ui=True)

    window = app_module.MainWindow(root, _config(root), metadata)
    window.show()
    qapp.processEvents()
    try:
        favoritas = [
            photo for photo in _cargar(root, metadata) if photo.favorite
        ]
        resto = [photo for photo in _cargar(root, metadata) if not photo.favorite]
        preparada = prepare_photos(favoritas)
        preparada_resto = prepare_photos(resto)
        app_module.MainWindow._on_library_loaded(
            window, favoritas, [FAV_TAG], (), 60, True, preparada
        )
        assert window._populate_active, "la fase 1 debía quedar en lotes"

        app_module.MainWindow._on_library_complete(window, resto, preparada_resto)

        assert window._pending_append == resto
        assert window._pending_prepared is preparada_resto
        assert _bombear(
            qapp,
            lambda: not window._populate_active and len(window.photos) == 60,
        ), "el append diferido no terminó"
        assert window._pending_append == []
        assert window._pending_prepared is None
        assert ("build", True) not in llamadas
        assert all(photo.key in window._search_index for photo in resto)
    finally:
        compuerta.set()
        window.close()
        qapp.processEvents()


def _cargar(root: Path, metadata: Path):
    """Fotos de la biblioteca sembrada, en el mismo orden que el catálogo."""
    from foto.ui.app import load_photos

    return load_photos(root, metadata)
