"""Arranque en dos fases: solo favoritas en la rejilla y resto en fondo (A.3–A.5).

Cubre la capa sin Qt (recuento de presentes y sesión de dos fases sobre una
sola conexión) y el ciclo real de ``MainWindow`` con catálogos sembrados: la
fase 1 entrega solo favoritas con el total conocido, la fase 2 funde el resto
sin ``grid.clear()`` y sin perder selección, filtro ni scroll. La pausa entre
fases se hace con un ``threading.Event`` en el worker, nunca con esperas
fijas, para observar el estado intermedio sin carreras.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from foto.core.library import LibraryLoadCancelled, load_from_catalog
from foto.storage.catalog import CATALOG_FILE_NAME, Catalog
from foto.storage.config import AppConfig, Location
from foto.storage.tags import FAVORITE_TAG, connect_read_write, toggle_favorite
from foto.ui import app as app_module
from foto.ui import window as window_module
from foto.ui.grid import HEADER_ROLE, PHOTO_ROLE
from foto.ui.models import PhotoTag
from foto.ui.presentation import sorted_photos
from foto.ui.window import PhotoWindow
from tests.conftest import write_image
from tests.ui_factories import make_photo

NOW = "2026-11-26T12:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def progresivo(qapp, monkeypatch):
    """Fuerza el populate por lotes con dos ítems por tanda."""
    monkeypatch.setattr(window_module, "POPULATE_SYNC_LIMIT", 2)
    monkeypatch.setattr(window_module, "POPULATE_BATCH_SIZE", 2)
    return qapp


def _bombear(qapp, condicion, intentos: int = 4000) -> bool:
    """Procesa eventos hasta que se cumpla la condición (sin esperas fijas)."""
    for _ in range(intentos):
        qapp.processEvents()
        if condicion():
            return True
        time.sleep(0.001)
    return condicion()


def _config(root: Path) -> AppConfig:
    return AppConfig(config_path=root / "config.toml", library_path=root, version=2)


def _hashes(cantidad: int) -> list[str]:
    return [f"sha256:{indice:064x}" for indice in range(cantidad)]


def _seed(
    root: Path,
    hashes: list[str],
    *,
    favorites: tuple[int, ...] = (),
    usb_desde: int | None = None,
) -> Path:
    """Catálogo real con fotos en ``fast`` y, si se pide, en ``usb`` ausente."""
    metadata = root / ".foto"
    metadata.mkdir(parents=True, exist_ok=True)
    fast = root / "fast"
    fast.mkdir(parents=True, exist_ok=True)
    declaradas = [Location("fast", fast, "fast")]
    if usb_desde is not None:
        declaradas.append(Location("usb", root / "usb", "archive"))
    catalog = Catalog.open(
        metadata / CATALOG_FILE_NAME, declared_locations=tuple(declaradas)
    )
    try:
        fast_id = catalog.insert_named_location(
            name="fast", path=fast, tier="fast", now=NOW
        )
        usb_id = None
        if usb_desde is not None:
            usb_id = catalog.insert_named_location(
                name="usb", path=root / "usb", tier="archive", now=NOW
            )
        for indice, photo_hash in enumerate(hashes):
            en_usb = usb_desde is not None and indice >= usb_desde
            location_id = usb_id if en_usb else fast_id
            rel = f"2024/usb{indice:02d}.jpg" if en_usb else f"2024/foto{indice:02d}.jpg"
            if not en_usb:
                write_image(fast / rel, f"contenido-{indice}".encode())
            catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
            catalog.insert_file(
                location_id=location_id,
                rel_path=rel,
                photo_hash=photo_hash,
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
        connection = connect_read_write(metadata / CATALOG_FILE_NAME)
        try:
            for indice in favorites:
                toggle_favorite(connection, hashes[indice], now=NOW)
        finally:
            connection.close()
    return metadata


def _pausar_entre_fases(monkeypatch):
    """Deja al worker detenido justo tras entregar la fase 1."""
    compuerta = threading.Event()
    real = app_module.load_library_phases

    def pausada(*args, **kwargs):
        callback = kwargs.get("on_first_phase")

        def on_first(first):
            if callback is not None:
                callback(first)
            compuerta.wait(2.0)

        kwargs["on_first_phase"] = on_first
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "load_library_phases", pausada)
    return compuerta


def _contar_clear(monkeypatch):
    """Cuenta los ``grid.clear()`` para probar que la fase 2 no repuebla."""
    original = window_module.PhotoGrid.clear
    llamadas: list[int] = []

    def contada(self):
        llamadas.append(1)
        return original(self)

    monkeypatch.setattr(window_module.PhotoGrid, "clear", contada)
    return llamadas


# ----------------------------------------------------------------------
# Capa sin Qt (S1): recuento y sesión de dos fases en una sola conexión
# ----------------------------------------------------------------------
def _catalogo_memoria(tmp_path):
    catalog = Catalog.open_in_memory()
    try:
        fast = tmp_path / "fast"
        fast.mkdir()
        archive = tmp_path / "archive"
        archive.mkdir()
        catalog.insert_named_location(name="fast", path=fast, tier="fast", now=NOW)
        catalog.insert_named_location(
            name="archive", path=archive, tier="archive", now=NOW
        )
        yield catalog, fast, archive
    finally:
        catalog.close()


@pytest.fixture()
def catalogo_memoria(tmp_path):
    yield from _catalogo_memoria(tmp_path)


def _insertar(catalog, location_id, photo_hash, rel_path):
    catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
    catalog.insert_file(
        location_id=location_id,
        rel_path=rel_path,
        photo_hash=photo_hash,
        size=5,
        mtime_ns=1,
        inode=1,
        device=1,
        now=NOW,
    )


def test_count_present_photos_matches_loaded_photos(catalogo_memoria):
    """El recuento coincide con la carga con 0, 1 y N favoritas."""
    catalog, _fast, _archive = catalogo_memoria
    location_id = catalog.list_named_locations()[0].id
    assert catalog.count_present_photos() == len(load_from_catalog(catalog)) == 0

    hashes = _hashes(3)
    for indice, photo_hash in enumerate(hashes):
        _insertar(catalog, location_id, photo_hash, f"{indice}.jpg")
    catalog.commit()
    assert catalog.count_present_photos() == 3

    toggle_favorite(catalog._conn, hashes[0], now=NOW)
    assert catalog.count_present_photos() == 3
    assert len(load_from_catalog(catalog)) == 3
    assert len(load_from_catalog(catalog, system_tag=FAVORITE_TAG)) == 1

    toggle_favorite(catalog._conn, hashes[1], now=NOW)
    assert catalog.count_present_photos() == 3
    assert len(load_from_catalog(catalog, system_tag=FAVORITE_TAG)) == 2


def test_count_present_photos_counts_photos_not_appearances(catalogo_memoria):
    """Con dos ubicaciones y hash repetido cuenta la foto una sola vez."""
    catalog, _fast, _archive = catalogo_memoria
    ids = [row["id"] for row in catalog._conn.execute("SELECT id FROM locations")]
    photo_hash = _hashes(1)[0]
    _insertar(catalog, ids[0], photo_hash, "copia-rapida.jpg")
    _insertar(catalog, ids[1], photo_hash, "copia-archivo.jpg")
    catalog.commit()

    assert catalog.count_present_photos() == 1
    assert len(catalog.list_present_files()) == 2


def test_load_library_phases_splits_favorites_and_remaining(tmp_path, monkeypatch):
    """Fase 1 = favoritas, ``remaining`` sin sus claves, unión con ``total`` únicos."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(4)
    metadata = _seed(root, hashes, favorites=(0, 2))
    orden: list[str] = []
    real = app_module._library_snapshot

    def observada(*args, **kwargs):
        orden.append("materializa")
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "_library_snapshot", observada)

    def on_first(first):
        orden.append("fase 1")

    phases = app_module.load_library_phases(
        root, metadata, on_first_phase=on_first
    )

    assert phases.first.total == 4
    assert phases.first.has_more is True
    assert len(phases.first.photos) == 2
    assert all(photo.favorite for photo in phases.first.photos)
    first_keys = {photo.key for photo in phases.first.photos}
    assert not first_keys.intersection(photo.key for photo in phases.remaining)
    assert len({photo.key for photo in [*phases.first.photos, *phases.remaining]}) == 4
    # El callback se entrega antes de la segunda materialización.
    assert orden == ["materializa", "fase 1", "materializa"]


def test_load_library_phases_opens_one_connection(tmp_path, monkeypatch):
    """Una sola apertura de solo lectura para las dos fases (ADR 0011)."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes, favorites=(0,))
    aperturas: list[object] = []
    real = Catalog.open_read_only

    @classmethod
    def contada(cls, path):
        aperturas.append(path)
        return real.__func__(cls, path)

    monkeypatch.setattr(Catalog, "open_read_only", contada)
    app_module.load_library_phases(root, metadata)
    assert len(aperturas) == 1


def test_load_library_phases_without_favorites_skips_phase_two(tmp_path):
    """Sin favoritas la fase 1 ya es la biblioteca completa."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes)
    phases = app_module.load_library_phases(root, metadata)

    assert phases.first.has_more is False
    assert len(phases.first.photos) == 3
    assert phases.remaining == []


def test_load_library_phases_cancellation_between_phases(tmp_path):
    """Si se cancela tras la fase 1 no se entrega ``remaining``."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes, favorites=(0,))
    entregada: list[bool] = []

    def should_continue():
        return not entregada

    def on_first(first):
        entregada.append(True)

    with pytest.raises(LibraryLoadCancelled):
        app_module.load_library_phases(
            root, metadata, should_continue=should_continue, on_first_phase=on_first
        )
    assert len(entregada) == 1


# ----------------------------------------------------------------------
# Bugfix: orden canónico al quitar ``fav`` tras la fase 2
# ----------------------------------------------------------------------
def _claves_en_rejilla(window) -> list[str]:
    """Claves de foto en orden de fila, sin las cabeceras de grupo."""
    return [
        str(window.grid.item(fila).data(PHOTO_ROLE))
        for fila in range(window.grid.count())
        if window.grid.item(fila) is not None
        and not window.grid.item(fila).data(HEADER_ROLE)
    ]


def test_removing_fav_filter_restores_canonical_order(qapp, tmp_path):
    """Al quitar ``fav`` el orden es el de ``date``, no favoritas primero."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(6)
    metadata = _seed(root, hashes, favorites=(0, 4))

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: len(window.photos) == 6 and not window._populate_active
        )
        favoritas = [p.key for p in window.photos.values() if p.favorite]
        # Antes: el append dejó las favoritas delante del resto.
        assert _claves_en_rejilla(window)[: len(favoritas)] == favoritas
        assert window._canonical_order_dirty is True

        window._handle_tag_command("")
        qapp.processEvents()
        esperado = [p.key for p in sorted_photos(list(window.photos.values()), "date")]
        assert window._tag_filter is None
        assert window._visible_keys_cache == esperado
        assert _claves_en_rejilla(window) == esperado
        assert window._canonical_order_dirty is False
    finally:
        window.close()


def test_sort_date_alone_restores_canonical_order(qapp, tmp_path):
    """Tras la fase 2, ``:sort date`` basta sin el doble salto por otra clave."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(6)
    metadata = _seed(root, hashes, favorites=(1, 3))

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: len(window.photos) == 6 and not window._populate_active
        )
        esperado = [p.key for p in sorted_photos(list(window.photos.values()), "date")]
        assert _claves_en_rejilla(window) != esperado

        window._run_command("sort date")
        qapp.processEvents()
        assert _claves_en_rejilla(window) == esperado
        assert window._canonical_order_dirty is False
        window._handle_tag_command("")
        qapp.processEvents()
        assert window._visible_keys_cache == esperado
    finally:
        window.close()


def test_filter_removed_mid_phase_two_ends_canonical(
    qapp, tmp_path, monkeypatch, progresivo
):
    """Quitar ``fav`` con la fase 2 en curso acaba en orden canónico igualmente."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(5)
    metadata = _seed(root, hashes, favorites=(0, 1, 2))
    compuerta = _pausar_entre_fases(monkeypatch)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(qapp, lambda: window._has_more and window._populate_active)
        window._handle_tag_command("")
        compuerta.set()
        assert _bombear(
            qapp, lambda: len(window.photos) == 5 and not window._populate_active
        )
        esperado = [p.key for p in sorted_photos(list(window.photos.values()), "date")]
        assert _claves_en_rejilla(window) == esperado
        assert window._visible_keys_cache == esperado
    finally:
        compuerta.set()
        window.close()


# ----------------------------------------------------------------------
# PhotoWindow (S4): append_photos sin repoblar ni perder estado
# ----------------------------------------------------------------------
def _favoritas(cantidad: int) -> list:
    tag = PhotoTag(name="fav", kind="system", status="confirmed", source="manual")
    return [
        make_photo(f"fav{indice}.jpg", "2024-05-01 08:00", tags=(tag,))
        for indice in range(cantidad)
    ]


def _ventana_vacia(qapp):
    return PhotoWindow(
        [],
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )


def test_append_photos_keeps_selection_filter_and_scroll(qapp, monkeypatch):
    """Fase 2 sobre PhotoWindow: sin ``clear()``, sin duplicados ni pérdidas."""
    window = _ventana_vacia(qapp)
    try:
        favoritas = _favoritas(4)
        window.set_photos(favoritas, tag_filter=FAVORITE_TAG)
        seleccion = window.grid.currentItem().data(PHOTO_ROLE)
        scroll = window.grid.verticalScrollBar().value()
        clears = _contar_clear(monkeypatch)
        clears.clear()

        window.append_photos(
            [
                make_photo(f"extra{indice}.jpg", "2024-06-01 08:00")
                for indice in range(6)
            ]
        )

        assert clears == []
        assert len(window._grid_items) == 10
        assert len(set(window._grid_items)) == 10
        assert window.grid.currentItem().data(PHOTO_ROLE) == seleccion
        assert window._tag_filter == FAVORITE_TAG
        assert window._filter_text == ""
        assert window.grid.verticalScrollBar().value() == scroll
        assert set(window._visible_keys_cache) == {foto.key for foto in favoritas}

        window._handle_tag_command("")
        qapp.processEvents()
        assert window._tag_filter is None
        assert window._visible_count() == 10
        claves = window._visible_keys_cache
        window._dispatch("last")
        qapp.processEvents()
        assert window.grid.currentItem().data(PHOTO_ROLE) == claves[-1]
        window._dispatch("first")
        qapp.processEvents()
        assert window.grid.currentItem().data(PHOTO_ROLE) == claves[0]
    finally:
        window.close()


def test_append_photos_extends_search_index_without_rescan(qapp, monkeypatch):
    """``/`` encuentra una foto añadida sin reconstruir el índice completo."""
    window = _ventana_vacia(qapp)
    try:
        window.set_photos(_favoritas(2), tag_filter=FAVORITE_TAG)
        real = window_module.build_search_index
        consultados: list[int] = []

        def contada(photos):
            consultados.append(len(photos))
            return real(photos)

        monkeypatch.setattr(window_module, "build_search_index", contada)
        añadida = make_photo("no-favorita-unica.jpg", "2024-06-01 08:00")
        window.append_photos([añadida])
        # Solo se indexan las fotos nuevas, nunca las dos ya cargadas.
        assert consultados == [1]
        assert añadida.key in window._search_index

        # Quitar ``fav`` para que la foto nueva pueda hacerse visible.
        window._handle_tag_command("")
        window.status.search_changed.emit("no-favorita-unica")
        qapp.processEvents()
        assert window._visible_keys_cache == [añadida.key]
    finally:
        window.close()


def test_set_photos_shows_total_count_before_full_library(qapp):
    """«4 de 29567» con solo 4 fotos en memoria (barra sin agrupar)."""
    window = _ventana_vacia(qapp)
    try:
        window.set_photos(
            _favoritas(4), tag_filter=FAVORITE_TAG, total_count=29_567
        )
        assert "4 de 29567" in window.status._idle_right
        assert len(window._grid_items) == 4
    finally:
        window.close()


# ----------------------------------------------------------------------
# MainWindow (S3/S6): estado intermedio y biblioteca complete al final
# ----------------------------------------------------------------------
def test_two_phase_startup_loads_favorites_only_first(qapp, tmp_path, monkeypatch):
    """3 fotos, 1 favorita: 1 ítem cargando → 3 ítems con «etiqueta: fav»."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes, favorites=(0,))
    compuerta = _pausar_entre_fases(monkeypatch)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: window._has_more and len(window.photos) == 1
        ), "no llegó la fase 1"
        favorita = next(iter(window.photos.values()))
        assert window._total_count == 3
        assert len(window._grid_items) == 1
        assert window._tag_filter == "fav"
        assert "1 de 3" in window.status._idle_right
        assert "cargando el resto" in window.status._pinned
        assert window._library_loading is True

        compuerta.set()
        assert _bombear(
            qapp,
            lambda: len(window.photos) == 3
            and len(window._grid_items) == 3
            and not window._populate_active,
        ), "no llegó la fase 2"
        assert window._has_more is False
        assert window._tag_filter == "fav"
        assert window.status._pinned == "etiqueta: fav"
        assert window._visible_keys_cache == [favorita.key]
        assert "1 de 3" in window.status._idle_right
        assert window._library_loading is False
    finally:
        compuerta.set()
        window.close()


def test_no_favorites_skips_second_phase(qapp, tmp_path):
    """Sin favoritas: una sola fase, sin filtro ni aviso de carga."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: len(window.photos) == 3 and not window._populate_active
        )
        assert window._has_more is False
        assert window._tag_filter is None
        assert window.status._pinned == ""
        assert len(window._grid_items) == 3
        assert window._visible_count() == 3
        assert window._library_loading is False
    finally:
        window.close()


def test_second_phase_keeps_selection_filter_and_scroll(
    qapp, tmp_path, monkeypatch
):
    """La fase 2 no llama a ``clear()`` y conserva selección, filtro y scroll."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(4)
    metadata = _seed(root, hashes, favorites=(0,))
    compuerta = _pausar_entre_fases(monkeypatch)
    clears = _contar_clear(monkeypatch)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: window._has_more and len(window._grid_items) == 1
        )
        favorita = next(iter(window.photos.values()))
        item = window._grid_items[favorita.key]
        window.grid.setCurrentItem(item)
        window.status.search_changed.emit("foto00")
        qapp.processEvents()
        seleccion = window.grid.currentItem().data(PHOTO_ROLE)
        filtro = window._filter_text
        scroll = window.grid.verticalScrollBar().value()
        clears.clear()

        compuerta.set()
        assert _bombear(
            qapp, lambda: len(window.photos) == 4 and not window._populate_active
        )
        assert clears == []
        assert window.grid.currentItem().data(PHOTO_ROLE) == seleccion
        assert window._filter_text == filtro
        assert window.grid.verticalScrollBar().value() == scroll
        assert len(set(window._grid_items)) == 4
    finally:
        compuerta.set()
        window.close()


def test_remove_tag_filter_after_phase_two_reveals_everything(qapp, tmp_path):
    """``:tag`` sin nombre revela todo; ``0``/``$`` y ``/`` siguen activos."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(5)
    metadata = _seed(root, hashes, favorites=(0,))

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: len(window.photos) == 5 and not window._populate_active
        )
        window._handle_tag_command("")
        qapp.processEvents()
        assert window._tag_filter is None
        assert window._visible_count() == 5
        assert "5 de 5" in window.status._idle_right

        claves = window._visible_keys_cache
        window._dispatch("last")
        qapp.processEvents()
        assert window.grid.currentItem().data(PHOTO_ROLE) == claves[-1]
        window._dispatch("first")
        qapp.processEvents()
        assert window.grid.currentItem().data(PHOTO_ROLE) == claves[0]

        window.status.search_changed.emit("foto03")
        qapp.processEvents()
        assert window._visible_count() == 1
        assert window._visible_keys_cache[0] in window.photos
        assert "foto03" in window.photos[window._visible_keys_cache[0]].name
    finally:
        window.close()


def test_pending_append_when_first_populate_is_still_running(
    qapp, tmp_path, monkeypatch, progresivo
):
    """La fase 2 llega con la fase 1 a medio populate y se aplica al terminar."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(5)
    metadata = _seed(root, hashes, favorites=(0, 1, 2))
    compuerta = _pausar_entre_fases(monkeypatch)
    clears = _contar_clear(monkeypatch)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp,
            lambda: window._has_more
            and window._populate_active
            and len(window._grid_items) >= 2,
        ), "la fase 1 no quedó en lotes"
        item = window._grid_items[window._visible_keys_cache[0]]
        window.grid.setCurrentItem(item)
        seleccion = item.data(PHOTO_ROLE)
        clears.clear()

        compuerta.set()
        assert _bombear(
            qapp,
            lambda: len(window.photos) == 5
            and len(window._grid_items) == 5
            and not window._populate_active,
        ), "el append pendiente no se aplicó"
        assert clears == []
        assert window.grid.currentItem().data(PHOTO_ROLE) == seleccion
        assert window._library_loading is False
        assert window._visible_count() == 3  # sigue el filtro fav
    finally:
        compuerta.set()
        window.close()


@pytest.mark.parametrize("via", ["close", "quit"])
def test_quit_and_close_cancel_both_phases(qapp, tmp_path, monkeypatch, via):
    """Cerrar o salir cancela el worker único con la fase 2 en curso."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes, favorites=(0,))
    real = app_module.load_library_phases

    def en_vuelo(*args, **kwargs):
        callback = kwargs.get("on_first_phase")

        def on_first(first):
            if callback is not None:
                callback(first)
            # El worker sigue vivo entre fases, igual que en la carga real.
            time.sleep(0.2)
            should_continue = kwargs.get("should_continue")
            if should_continue is not None and not should_continue():
                raise LibraryLoadCancelled

        kwargs["on_first_phase"] = on_first
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "load_library_phases", en_vuelo)
    window = app_module.MainWindow(root, _config(root), metadata)
    window.show()
    qapp.processEvents()
    assert _bombear(qapp, lambda: window._has_more), "no llegó la fase 1"

    if via == "quit":
        # Se invoca el mismo handler que ``aboutToQuit`` conecta, sin emitir
        # la señal global: en la suite completa hay ventanas de otros tests
        # conectadas y emitirla toca objetos Qt ya destruidos (T2.9). La
        # conexión real está cubierta por
        # ``test_quit_mid_load_cancels_the_worker_about_to_quit``.
        window._shutdown_background_work()
    window.close()
    qapp.processEvents()

    assert window._load_cancelled is True
    assert window._loader._cancelled is True
    assert not window._loader_thread.isRunning()
    # Sin resultados parciales: la fase 2 nunca sustituyó a la fase 1.
    assert len(window.photos) <= 1


def test_banner_uses_full_library_after_phase_two(qapp, tmp_path):
    """El banner cuenta las degradadas de la biblioteca completa, no 1 favorita."""
    root = tmp_path / "biblioteca"
    hashes = _hashes(3)
    metadata = _seed(root, hashes, favorites=(0,), usb_desde=1)

    window = app_module.MainWindow(root, _config(root), metadata)
    try:
        window.show()
        assert _bombear(
            qapp, lambda: len(window.photos) == 3 and not window._populate_active
        )
        assert "usb" in window._banner_text
        assert "2 fotos no visibles" in window._banner_text
        degradadas = [photo for photo in window.photos.values() if not photo.available]
        assert len(degradadas) == 2
        assert all(photo.canonical_location == "usb" for photo in degradadas)
    finally:
        window.close()
