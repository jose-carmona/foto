"""Etiquetas y favoritos en el visor (ADR 0006, paso 2).

Cubre el lado UI del ADR: ``f`` escribe en el catálogo (y revierte si la
escritura falla), ``:tag <nombre>`` filtra por etiqueta y compone en AND con
la búsqueda ``/``, el panel muestra la sección «Etiquetas» y las reglas puras
de ``ui/presentation.py`` cuentan ``confirmed`` + ``suggested`` —jamás
``rejected``— con taxonomía plana.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication

from foto.storage.catalog import Catalog
from foto.storage.library import ensure_library
from foto.storage.tags import (
    assign_tag,
    connect_read_write,
    find_tag_by_name,
    normalize_tag_name,
    toggle_favorite,
)
from foto.ui.app import load_tags
from foto.ui.canvas import PhotoCanvas
from foto.ui.chips import PANEL_CHIP_H, TagChipFlow
from foto.ui.grid import FAVORITE_ROLE
from foto.ui.models import Photo, PhotoTag
from foto.ui.presentation import (
    find_tag_in_catalog,
    photo_matches_tag_name,
    photo_search_text,
    tag_chips,
    with_favorite_tag,
)
from foto.ui.prototype import make_photos
from foto.ui.theme import CHIP_FAVORITE_COLOR
from foto.ui.window import PhotoWindow
from tests.conftest import write_image
from tests.test_ui_load_photos import load_photos

NOW = "2026-11-11T10:00:00+00:00"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def _pure_photo(tags: tuple[PhotoTag, ...]) -> Photo:
    """Photo mínima para las reglas puras, sin catálogo ni ficheros."""
    return Photo(
        name="a.jpg",
        taken="2024-05-01 12:34",
        camera="Nikon Z 6",
        lens="Nikkor 35mm",
        focal="35 mm",
        aperture="f/1,8",
        shutter="1/250 s",
        iso="200",
        folder="2024",
        fmt="JPEG",
        dimensions="6000 × 4000",
        size="2 MB",
        location="",
        photo_hash=HASH_A,
        tags=tags,
    )


def _fav(status: str = "confirmed") -> PhotoTag:
    return PhotoTag(name="fav", kind="system", status=status, source="manual")


@pytest.fixture()
def library(tmp_path):
    """Biblioteca con dos fotos; a.jpg lleva fav, playa, montaña y ciudad."""
    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "a.jpg", b"one")
    write_image(root / "2024" / "b.jpg", b"two")
    ensure_library(root)

    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        for photo_hash, rel_path in ((HASH_A, "2024/a.jpg"), (HASH_B, "2024/b.jpg")):
            catalog.insert_photo(photo_hash, ".jpg", "JPEG", NOW)
            catalog.insert_location(
                rel_path=rel_path,
                photo_hash=photo_hash,
                size=4,
                mtime_ns=1,
                inode=1,
                device=1,
                now=NOW,
            )
        catalog.commit()
    finally:
        catalog.close()

    connection = connect_read_write(root / ".foto" / "catalog.sqlite")
    try:
        # Vocabulario libre (el visor no crea etiquetas; los tests sí).
        connection.executemany(
            "INSERT INTO tags (name, name_normalized, kind, created_at, "
            "updated_at) VALUES (?, ?, 'free', ?, ?)",
            [
                (name, normalize_tag_name(name), NOW, NOW)
                for name in ("Playa", "Montaña", "Ciudad")
            ],
        )
        connection.commit()
        # fav sembrado por la migración v4: el conmutado crea la asignación.
        toggle_favorite(connection, HASH_A, now=NOW)
        for name in ("Playa", "Montaña", "Ciudad"):
            assign_tag(
                connection, HASH_A, find_tag_by_name(connection, name), now=NOW
            )
        montana = find_tag_by_name(connection, "montaña")
        ciudad = find_tag_by_name(connection, "ciudad")
        connection.execute(
            "UPDATE photo_tags SET status = 'suggested', source = 'ml' "
            "WHERE tag_id = ?",
            (montana.id,),
        )
        connection.execute(
            "UPDATE photo_tags SET status = 'rejected' WHERE tag_id = ?",
            (ciudad.id,),
        )
        connection.commit()
    finally:
        connection.close()
    return root


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _window(qapp, root) -> PhotoWindow:
    """Ventana real: catálogo v4, conexión RW y vocabulario cargado."""
    photos = load_photos(root)
    return PhotoWindow(
        photos,
        tag_connection=connect_read_write(root / ".foto" / "catalog.sqlite"),
        tag_catalog=load_tags(root),
    )


def _select(window: PhotoWindow, index: int) -> None:
    window._select_key(window._visible_keys_cache[index])


def _photo_by_name(window: PhotoWindow, name: str) -> Photo:
    for photo in window.photos.values():
        if photo.name == name:
            return photo
    raise AssertionError(name)


def _key(key: int) -> QKeyEvent:
    return QKeyEvent(QEvent.KeyPress, key, Qt.KeyboardModifier.NoModifier)


# ----------------------------------------------------------------------
# Reglas puras (sin Qt)
# ----------------------------------------------------------------------
def test_favorite_counts_confirmed_and_suggested_but_not_rejected():
    assert _pure_photo(tags=(_fav("confirmed"),)).favorite is True
    assert _pure_photo(tags=(_fav("suggested"),)).favorite is True
    assert _pure_photo(tags=(_fav("rejected"),)).favorite is False
    assert _pure_photo(tags=()).favorite is False


def test_with_favorite_tag_keeps_the_other_tags():
    playa = PhotoTag(name="playa", kind="free", status="confirmed", source="manual")
    photo = _pure_photo(tags=(playa,))

    tagged = with_favorite_tag(photo, True)

    assert tagged.favorite is True
    assert [tag.name for tag in tagged.tags] == ["playa", "fav"]
    fav = tagged.tags[-1]
    assert (fav.kind, fav.status, fav.source) == ("system", "confirmed", "manual")

    untagged = with_favorite_tag(tagged, False)

    assert untagged.favorite is False
    assert [tag.name for tag in untagged.tags] == ["playa"]


def test_photo_search_text_includes_visible_tag_names_only():
    tags = (
        _fav(),
        PhotoTag(name="Playa", kind="free", status="confirmed", source="manual"),
        PhotoTag(name="secreto", kind="free", status="rejected", source="manual"),
    )

    text = photo_search_text(_pure_photo(tags=tags))

    assert "fav" in text
    assert "playa" in text
    assert "secreto" not in text


def test_photo_matches_tag_name_normalizes_and_ignores_rejected():
    tags = (
        _fav(),
        PhotoTag(name="Año 2024", kind="free", status="confirmed", source="manual"),
        PhotoTag(name="secreto", kind="free", status="rejected", source="manual"),
    )
    photo = _pure_photo(tags=tags)

    assert photo_matches_tag_name(photo, "año 2024") is True
    assert photo_matches_tag_name(photo, "AÑO  2024") is True
    assert photo_matches_tag_name(photo, "fav") is True
    # «rejected» jamás filtra, aunque el nombre coincida.
    assert photo_matches_tag_name(photo, "secreto") is False
    assert photo_matches_tag_name(photo, "otra") is False


def test_find_tag_in_catalog_resolves_by_normalized_name():
    catalog = (
        PhotoTag(name="fav", kind="system", status="confirmed", source="manual"),
        PhotoTag(name="Año 2024", kind="free", status="confirmed", source="manual"),
    )

    assert find_tag_in_catalog("año 2024", catalog) is not None
    assert find_tag_in_catalog("AÑO 2024", catalog) is not None
    assert find_tag_in_catalog("inexistente", catalog) is None


def test_tag_chips_carry_name_tooltip_and_favorite_flag():
    tags = (
        _fav(),
        PhotoTag(name="playa", kind="free", status="confirmed", source="manual"),
        PhotoTag(name="montaña", kind="free", status="suggested", source="ml"),
        PhotoTag(name="secreto", kind="free", status="rejected", source="manual"),
    )

    chips = tag_chips(_pure_photo(tags=tags))

    assert chips == (
        ("", "favorita", True),
        ("playa", "playa", False),
        ("montaña", "montaña · sugerida · machine learning", False),
    )


def test_tag_chips_without_favorite_have_no_heart_chip():
    chips = tag_chips(
        _pure_photo(
            tags=(
                PhotoTag(name="playa", kind="free", status="confirmed", source="manual"),
            )
        )
    )

    assert chips == (("playa", "playa", False),)


def test_prototype_photos_still_carry_the_favorite_tag():
    photos = make_photos()

    favorites = [photo for photo in photos if photo.favorite]
    assert len(favorites) == 6
    assert all(
        any(tag.kind == "system" for tag in photo.tags) for photo in favorites
    )
    assert any(any(tag.name == "playa" for tag in photo.tags) for photo in photos)


# ----------------------------------------------------------------------
# Carga: asignaciones del catálogo → modelo de UI
# ----------------------------------------------------------------------
def test_load_photos_maps_the_assignments(library):
    by_name = {photo.name: photo for photo in load_photos(library)}

    a = by_name["a.jpg"]
    assert {tag.name for tag in a.tags} == {"fav", "Playa", "Montaña", "Ciudad"}
    fav = next(tag for tag in a.tags if tag.kind == "system")
    assert (fav.status, fav.source) == ("confirmed", "manual")
    assert a.favorite is True

    assert by_name["b.jpg"].tags == ()
    assert by_name["b.jpg"].favorite is False


def test_load_tags_returns_the_whole_vocabulary(library):
    tags = load_tags(library)

    assert [tag.name for tag in tags] == ["fav", "Playa", "Montaña", "Ciudad"]


# ----------------------------------------------------------------------
# `f`: escritura en el catálogo con la conexión RW del visor
# ----------------------------------------------------------------------
def test_favorite_toggle_persists_in_the_catalog(qapp, library):
    window = _window(qapp, library)
    _select(window, 0)  # a.jpg, favorita de partida
    assert window._current_photo().favorite is True

    # `f` por la vía real de teclado: la rejilla emite «favorite» al dispatcher.
    window.grid.keyPressEvent(_key(Qt.Key_F))
    assert window._current_photo().favorite is False
    assert window.status.message_label.text() == "sin favorito"

    reloaded = {photo.name: photo for photo in load_photos(library)}
    assert reloaded["a.jpg"].favorite is False

    window._toggle_favorite()
    reloaded = {photo.name: photo for photo in load_photos(library)}
    assert reloaded["a.jpg"].favorite is True

    window.close()
    # La ventana cierra su conexión RW en closeEvent.
    assert window.tag_connection is None


def test_favorite_write_failure_reverts_and_reports(qapp, library):
    window = _window(qapp, library)
    _select(window, 0)
    assert window._current_photo().favorite is True

    # Bloqueo de escritura externo (otro proceso mantiene el lock); con
    # busy_timeout 0 el DELETE falla al instante.
    window.tag_connection.execute("PRAGMA busy_timeout = 0")
    blocker = sqlite3.connect(
        library / ".foto" / "catalog.sqlite", isolation_level=None
    )
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        window._toggle_favorite()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # Revertido en memoria y error persistente en la barra.
    photo = window._current_photo()
    assert photo.favorite is True
    assert bool(window.grid.currentItem().data(FAVORITE_ROLE)) is True
    assert window.status.error_text
    assert "error:" in window.status.message_label.text()
    assert window.status.right_label.text() == "Esc descartar"

    # Y el catálogo queda como estaba.
    reloaded = {p.name: p for p in load_photos(library)}
    assert reloaded["a.jpg"].favorite is True

    window.close()


def test_favorite_toggle_without_connection_stays_in_memory(qapp, library):
    window = PhotoWindow(load_photos(library))
    _select(window, 0)
    assert window._current_photo().favorite is True

    window._toggle_favorite()

    assert window._current_photo().favorite is False
    # Sin conexión no hay escritura: el catálogo no cambia.
    reloaded = {p.name: p for p in load_photos(library)}
    assert reloaded["a.jpg"].favorite is True


# ----------------------------------------------------------------------
# `:tag <nombre>`: filtro por etiqueta
# ----------------------------------------------------------------------
def test_tag_command_filters_by_tag(qapp, library):
    window = _window(qapp, library)

    window._run_command("tag playa")

    assert len(window._visible_keys_cache) == 1
    assert window._visible_keys_cache[0] == _photo_by_name(window, "a.jpg").key
    assert window.status._pinned == "etiqueta: playa"
    assert window.status.message_label.text() == "etiqueta: playa"


def test_tag_command_counts_suggested_and_ignores_rejected(qapp, library):
    window = _window(qapp, library)

    window._run_command("tag montaña")
    assert window._visible_keys_cache == [_photo_by_name(window, "a.jpg").key]

    window._run_command("tag ciudad")
    assert window._visible_keys_cache == []


def test_tag_command_normalizes_case_and_repeats_to_deactivate(qapp, library):
    window = _window(qapp, library)

    window._run_command("tag PLAYA")
    assert window._tag_filter == "playa"

    # Repetir la etiqueta activa la desactiva.
    window._run_command("tag playa")
    assert window._tag_filter is None
    assert window.status._pinned == ""
    assert len(window._visible_keys_cache) == 2


def test_tag_command_without_argument_toggles(qapp, library):
    window = _window(qapp, library)

    window._run_command("tag")
    assert window.status.error_text == "uso: :tag <nombre>"

    window._run_command("tag playa")
    window._run_command("tag")
    assert window._tag_filter is None
    assert window.status._pinned == ""
    assert window.status.error_text is None


def test_tag_command_rejects_unknown_names(qapp, library):
    window = _window(qapp, library)

    window._run_command("tag rio")

    assert window.status.error_text
    assert "rio" in window.status.message_label.text()
    assert window._tag_filter is None


def test_tag_filter_composes_with_search_in_and(qapp, library):
    window = _window(qapp, library)
    window._run_command("tag playa")

    window._commit_search("b")
    assert window._visible_keys_cache == []

    window._commit_search("a")
    assert window._visible_keys_cache == [_photo_by_name(window, "a.jpg").key]


def test_set_photos_clears_the_tag_filter(qapp, library):
    window = _window(qapp, library)
    window._run_command("tag playa")
    assert window._tag_filter == "playa"

    window.set_photos(load_photos(library))

    assert window._tag_filter is None
    assert window.status._pinned == ""
    assert len(window._visible_keys_cache) == 2


def test_empty_result_of_tag_filter_explains_itself(qapp, library):
    window = _window(qapp, library)

    window._run_command("tag ciudad")

    assert "Sin coincidencias de etiqueta" in window.empty_label.text()


# ----------------------------------------------------------------------
# Búsqueda `/` por nombre de etiqueta (incluido fav)
# ----------------------------------------------------------------------
def test_search_finds_photos_by_tag_name(qapp, library):
    window = _window(qapp, library)

    window._commit_search("fav")
    assert window._visible_keys_cache == [_photo_by_name(window, "a.jpg").key]

    window._commit_search("playa")
    assert window._visible_keys_cache == [_photo_by_name(window, "a.jpg").key]


# ----------------------------------------------------------------------
# Panel de metadatos: sección «Etiquetas» con chips
# ----------------------------------------------------------------------
def test_metadata_panel_shows_the_tag_chips(qapp, library):
    window = _window(qapp, library)
    window.panel.set_photo(_photo_by_name(window, "a.jpg"))

    flows = window.panel.findChildren(TagChipFlow)

    assert len(flows) == 1
    flow = flows[0]
    assert flow.labels() == ("", "Montaña", "Playa")
    assert flow._chips[0] == ("", "favorita", True)
    assert flow._chips[1] == ("Montaña", "Montaña · sugerida · machine learning", False)


def test_metadata_panel_without_tags_has_no_tags_section(qapp, library):
    window = _window(qapp, library)
    window.panel.set_photo(_photo_by_name(window, "b.jpg"))

    assert window.panel.findChildren(TagChipFlow) == []


# ----------------------------------------------------------------------
# Dibujo de los chips (rejilla, visor y panel)
# ----------------------------------------------------------------------
def test_chip_flow_wraps_into_rows_by_width(qapp):
    flow = TagChipFlow()
    flow.set_chips(
        (
            ("una", "una", False),
            ("dos", "dos", False),
            ("tres", "tres", False),
            ("cuatro", "cuatro", False),
        )
    )
    flow.resize(160, 40)

    assert flow.heightForWidth(160) > PANEL_CHIP_H  # dos filas como mínimo
    assert flow.heightForWidth(1200) == PANEL_CHIP_H  # todo en una fila


def test_chip_flow_paints_and_shows_the_tooltip(qapp):
    flow = TagChipFlow()
    flow.set_chips((("", "favorita", True), ("playa", "playa · playa", False)))
    flow.resize(300, PANEL_CHIP_H)
    pixmap = flow.grab()

    assert not pixmap.isNull()
    # El chip de favorito es carmesí: algún píxel de la zona izquierda.
    colors = {
        pixmap.toImage().pixelColor(x, PANEL_CHIP_H // 2).name()
        for x in range(40)
    }
    assert CHIP_FAVORITE_COLOR.name() in colors


def test_canvas_paints_the_favorite_chip(qapp):
    canvas = PhotoCanvas()
    pixmap = QPixmap(200, 120)
    pixmap.fill(Qt.darkGray)
    canvas.resize(200, 120)
    canvas.set_image(pixmap)
    canvas.set_favorite(True)

    grabbed = canvas.grab().toImage()
    colors = {
        grabbed.pixelColor(x, y).name()
        for x in range(140, 200, 2)
        for y in range(16, 56, 2)
    }
    assert CHIP_FAVORITE_COLOR.name() in colors

    canvas.set_favorite(False)
    grabbed = canvas.grab().toImage()
    colors = {
        grabbed.pixelColor(x, y).name()
        for x in range(140, 200, 2)
        for y in range(16, 56, 2)
    }
    assert CHIP_FAVORITE_COLOR.name() not in colors
