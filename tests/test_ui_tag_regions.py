"""Rectángulos de etiquetas en el visor con `t` (ADR 0006, revisión 2026-11-24).

Cubre el viaje completo de la región: `photo_tags` → `TagAssignment` →
`PhotoTag` → regla pura de geometría → lienzo, y el interruptor de sesión de
la tecla `t` en los cuatro contextos del visor. Ninguna prueba escribe en el
catálogo: la superposición es solo UI.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from foto.indexer.cli import main as index_main
from foto.storage.catalog import Catalog
from foto.storage.tags import (
    TagAssignment,
    assignments_by_hash,
    normalize_tag_name,
)
from foto.ui import canvas as canvas_module
from foto.ui.app import load_photos
from foto.ui.canvas import PhotoCanvas
from foto.ui.chips import favorite_chip_size
from foto.ui.models import Photo, PhotoTag
from foto.ui.presentation import (
    photo_search_text,
    tag_chips,
    tag_rect_pixels,
    visible_tag_regions,
    with_favorite_tag,
)
from foto.ui.theme import (
    CHIP_FAVORITE_COLOR,
    accent_color,
    apply_dark_palette,
    apply_light_palette,
)
from foto.ui.window import PhotoWindow
from tests.conftest import write_image, write_v1_config
from tests.ui_factories import make_photo

NOW = "2026-11-24T10:00:00+00:00"
HASH_A = "sha256:" + "a" * 64
REGION = (0.1, 0.2, 0.3, 0.4)


# ----------------------------------------------------------------------
# Reglas puras, sin Qt
# ----------------------------------------------------------------------
def _tag(
    name: str,
    status: str = "confirmed",
    region: tuple[float, float, float, float] | None = REGION,
    *,
    kind: str = "person",
) -> PhotoTag:
    return PhotoTag(name=name, kind=kind, status=status, source="import", region=region)


def _assert_rect(actual, expected) -> None:
    assert actual == pytest.approx(expected)


def test_tag_rect_pixels_uses_the_visible_image_and_clips():
    _assert_rect(tag_rect_pixels(REGION, 1000, 500), (100, 100, 400, 300))
    # y crece hacia abajo: el borde superior está en 100, no en 400.
    _assert_rect(tag_rect_pixels((0.0, 0.0, 1.0, 1.0), 200, 100), (0, 0, 200, 100))
    _assert_rect(tag_rect_pixels((-0.5, -0.5, 0.7, 0.7), 100, 100), (0, 0, 20, 20))
    _assert_rect(tag_rect_pixels((0.8, 0.8, 0.5, 0.5), 100, 100), (80, 80, 100, 100))
    # w/h positivos y destino vacío: cero en vez de un rectángulo degenerado.
    _assert_rect(tag_rect_pixels(REGION, 0, 500), (0, 0, 0, 0))
    _assert_rect(tag_rect_pixels(REGION, 100, -5), (0, 0, 0, 0))


def test_photo_tag_region_defaults_to_none():
    assert _tag("Ana", region=None).region is None


def test_visible_tag_regions_filters_status_without_filtering_kind_or_faces():
    photo = make_photo(
        "a.jpg",
        "2024-05-01 08:00",
        tags=(
            _tag("Ana", "confirmed", REGION),
            _tag("Ana", "suggested", (0.5, 0.5, 0.2, 0.2)),
            _tag("Ana", "rejected", (0.2, 0.2, 0.1, 0.1)),
            _tag("Lisboa", "confirmed", (0.3, 0.3, 0.1, 0.1), kind="place"),
            _tag("fav", "confirmed", None, kind="system"),
        ),
    )

    assert visible_tag_regions(photo) == (
        ("Ana", REGION),
        ("Ana", (0.5, 0.5, 0.2, 0.2)),
        ("Lisboa", (0.3, 0.3, 0.1, 0.1)),
    )
    assert visible_tag_regions(make_photo("b.jpg", "2024-05-02 08:00")) == ()


def test_region_survives_the_catalog_to_ui_conversion_without_changing_chips():
    base = make_photo("a.jpg", "2024-05-01 08:00")
    stored = (0.1, 0.2, 0.3, 0.4)
    assignment = TagAssignment(
        photo_hash=str(HASH_A),
        tag_id=7,
        tag_name="Ana",
        kind="person",
        source="manual",
        status="confirmed",
        region=stored,
    )
    fields = {key: value for key, value in vars(base).items() if key != "tags"}

    photo = Photo.from_catalog(tags=(assignment,), **fields)

    assert photo.tags[0].region == stored
    assert replace(photo, tags=(assignment,)).tags[0].region == stored
    assert with_favorite_tag(photo, True).tags[0].region == stored
    assert tag_chips(photo) == (("Ana", "Ana · person", False),)
    assert "ana" in photo_search_text(photo)


def test_imported_regions_flip_y_only_in_the_ui_boundary():
    """`ZDETECTEDFACE` guarda `y` desde abajo; el visor lo quiere desde arriba."""
    base = make_photo("a.jpg", "2024-05-01 08:00")
    fields = {key: value for key, value in vars(base).items() if key != "tags"}
    imported = TagAssignment(
        photo_hash=str(HASH_A),
        tag_id=7,
        tag_name="Ana",
        kind="person",
        source="import",
        status="confirmed",
        region=(0.1, 0.2, 0.3, 0.4),
    )
    manual = replace(imported, source="manual")

    assert Photo.from_catalog(tags=(imported,), **fields).tags[0].region == pytest.approx(
        (0.1, 0.4, 0.3, 0.4)
    )
    assert Photo.from_catalog(tags=(manual,), **fields).tags[0].region == (0.1, 0.2, 0.3, 0.4)
    # Sin región no hay nada que convertir.
    assert Photo.from_catalog(tags=(replace(imported, region=None),), **fields).tags[0].region is None


def test_assignments_by_hash_loads_regions_in_a_stable_order():
    catalog = Catalog.open_in_memory()
    try:
        catalog.insert_photo(HASH_A, ".jpg", "JPEG", NOW)
        catalog.insert_photo("sha256:" + "b" * 64, ".jpg", "JPEG", NOW)
        catalog.commit()
        catalog._conn.execute(
            "INSERT INTO tags (name, name_normalized, kind, created_at, updated_at) "
            "VALUES ('Ana', ?, 'person', ?, ?)",
            (normalize_tag_name("Ana"), NOW, NOW),
        )
        tag_id = catalog._conn.execute("SELECT id FROM tags WHERE name = 'Ana'").fetchone()[0]
        for x in (0.5, 0.1):
            catalog._conn.execute(
                "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
                "region_x, region_y, region_w, region_h, created_at, updated_at) "
                "VALUES (?, ?, 'import', 'confirmed', ?, 0.2, 0.3, 0.4, ?, ?)",
                (HASH_A, tag_id, x, NOW, NOW),
            )
        catalog._conn.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "created_at, updated_at) VALUES (?, ?, 'manual', 'confirmed', ?, ?)",
            (HASH_A, tag_id, NOW, NOW),
        )
        catalog.commit()

        grouped = assignments_by_hash(catalog._conn)

        assert set(grouped) == {HASH_A}
        regions = [item.region for item in grouped[HASH_A]]
        assert regions == [(0.5, 0.2, 0.3, 0.4), (0.1, 0.2, 0.3, 0.4), None]
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# Lienzo: pintado, preview, geometría y tooltip
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _canvas(qapp, *, preview: bool = False) -> PhotoCanvas:
    canvas = PhotoCanvas()
    canvas.resize(200, 100)
    pixmap = QPixmap(200, 100)
    pixmap.fill(Qt.darkGray)
    canvas.set_image(pixmap, preview=preview)
    canvas.set_tag_regions((("Ana", REGION),))
    canvas.set_tag_overlay_visible(True)
    qapp.processEvents()
    return canvas


def _expected_accent(canvas: PhotoCanvas) -> QColor:
    return accent_color(canvas, canvas._canvas_color())


def _border_colors(canvas: PhotoCanvas, rect) -> set[str]:
    image = canvas.grab().toImage()
    return {
        image.pixelColor(int(x), int(y)).name()
        for x in range(int(rect.left()) + 2, int(rect.right()) - 2, 4)
        for y in (int(rect.top()), int(rect.bottom()))
    }


def test_canvas_paints_only_the_border_when_the_overlay_is_on(qapp):
    canvas = _canvas(qapp)
    rect = canvas._tag_rect(REGION, canvas._image_target())
    accent = _expected_accent(canvas).name()

    assert accent in _border_colors(canvas, rect)
    # Sin relleno: el interior sigue siendo la foto.
    assert canvas.grab().toImage().pixelColor(int(rect.center().x()), int(rect.center().y())) != (
        _expected_accent(canvas)
    )

    canvas.set_tag_overlay_visible(False)
    qapp.processEvents()
    assert accent not in _border_colors(canvas, rect)


def test_favorite_chip_stays_above_the_rectangles(qapp):
    canvas = _canvas(qapp)
    canvas.set_tag_regions((("Ana", (0.0, 0.0, 1.0, 1.0)),))
    canvas.set_favorite(True)
    qapp.processEvents()

    accent = _expected_accent(canvas).name()
    rect = canvas._tag_rect((0.0, 0.0, 1.0, 1.0), canvas._image_target())
    assert accent in _border_colors(canvas, rect)

    size = favorite_chip_size(32)
    chip_left = canvas.width() - 20 - size.width()
    image = canvas.grab().toImage()
    assert (
        image.pixelColor(chip_left + 3, 20 + size.height() // 2).name()
        == CHIP_FAVORITE_COLOR.name()
    )


def test_canvas_uses_the_accent_in_both_themes(qapp):
    original = qapp.palette()
    try:
        for apply in (apply_light_palette, apply_dark_palette):
            apply(qapp)
            canvas = _canvas(qapp)
            rect = canvas._tag_rect(REGION, canvas._image_target())
            assert _expected_accent(canvas).name() in _border_colors(canvas, rect)
            assert canvas.accessibleName() == "foto actual"
    finally:
        qapp.setPalette(original)


def test_preview_never_paints_rectangles(qapp):
    canvas = _canvas(qapp, preview=True)
    rect = canvas._tag_rect(REGION, canvas._image_target())
    accent = _expected_accent(canvas).name()

    assert accent not in _border_colors(canvas, rect)

    pixmap = QPixmap(200, 100)
    pixmap.fill(Qt.darkGray)
    canvas.set_image(pixmap)  # imagen completa, sin preview
    qapp.processEvents()
    assert accent in _border_colors(canvas, rect)


def test_geometry_follows_zoom_pan_and_resize_with_a_fixed_stroke(qapp, monkeypatch):
    widths: list[int] = []
    real_pen = canvas_module.QPen

    def _pen(color, width):
        widths.append(width)
        return real_pen(color, width)

    monkeypatch.setattr(canvas_module, "QPen", _pen)
    canvas = _canvas(qapp)
    canvas.grab()
    assert widths and set(widths) == {2}

    canvas.zoom_in()
    canvas.pan_by(12, -8)
    canvas.toggle_zoom()
    canvas.resize(640, 480)
    qapp.processEvents()

    target = canvas._image_target()
    expected = (
        target.x() + REGION[0] * target.width(),
        target.y() + REGION[1] * target.height(),
        REGION[2] * target.width(),
        REGION[3] * target.height(),
    )
    rect = canvas._tag_rect(REGION, target)
    _assert_rect(
        (rect.left(), rect.top(), rect.width(), rect.height()), expected
    )
    widths.clear()
    canvas.grab()
    assert set(widths) == {2}


def test_tooltip_follows_the_hovered_rectangle_and_never_acts(qapp, monkeypatch):
    shown: list[tuple[str, str]] = []
    hidden: list[bool] = []
    monkeypatch.setattr(
        canvas_module.QToolTip,
        "showText",
        staticmethod(lambda point, text, widget=None, *rest: shown.append((point, text))),
    )
    monkeypatch.setattr(
        canvas_module.QToolTip,
        "hideText",
        staticmethod(lambda: hidden.append(True)),
    )
    canvas = _canvas(qapp)
    assert canvas.hasMouseTracking()  # el tooltip responde al pasar, sin clic
    rect = canvas._tag_rect(REGION, canvas._image_target())
    point = QPointF(rect.center())

    def move(at: QPointF) -> None:
        canvas.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                at,
                at,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    move(point)
    assert shown[-1][1] == "Ana"
    assert canvas._hover_tag == 0
    move(point)
    assert len(shown) == 1  # no reenvía el mismo tooltip

    move(QPointF(1, 1))
    assert hidden and canvas._hover_tag is None
    assert canvas.has_image()  # el hover no cambia la foto ni pide acción

    canvas.leaveEvent(QEvent(QEvent.Type.Leave))
    assert canvas._hover_tag is None
    move(point)
    assert len(shown) == 2  # al reentrar vuelve el tooltip

    canvas.set_tag_overlay_visible(False)
    move(point)
    assert len(shown) == 2  # con el overlay apagado no hay tooltip


# ----------------------------------------------------------------------
# Ventana: tecla, guardias, ciclo de vida y ausencia de escritura
# ----------------------------------------------------------------------
REGION_PHOTOS = [
    make_photo(
        "a.jpg",
        "2024-05-02 08:00",
        tags=(_tag("Ana", "confirmed", REGION),),
    ),
    make_photo(
        "b.jpg",
        "2024-05-01 08:00",
        tags=(_tag("Ana", "confirmed", (0.5, 0.5, 0.2, 0.2)),),
    ),
]


def _window(qapp, photos=None) -> PhotoWindow:
    window = PhotoWindow(
        photos if photos is not None else list(REGION_PHOTOS),
        initial_panel_visible=False,
        grid_row=True,
    )
    window.show()
    qapp.processEvents()
    return window


def _key(key: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


def _press(window: PhotoWindow, context: str) -> None:
    event = _key(Qt.Key_T)
    {
        "viewer": window.viewer.canvas.keyPressEvent,
        "grid_viewer": window.grid.keyPressEvent,
        "panel": window.panel.scroll.keyPressEvent,
        "window": window.keyPressEvent,
        "grid": window.grid.keyPressEvent,
    }[context](event)


@pytest.mark.parametrize("context", ("viewer", "grid_viewer", "panel", "window"))
def test_t_toggles_from_the_four_viewer_contexts(qapp, context):
    window = _window(qapp)
    try:
        window._open_viewer()
        _press(window, context)
        assert window._tag_overlay_visible is True
        assert window.viewer.canvas._tag_overlay_visible is True
        assert window.status.message_label.text() == "etiquetas sobre la foto"
        _press(window, context)
        assert window._tag_overlay_visible is False
        assert window.status.message_label.text() == "etiquetas ocultas"
    finally:
        window.close()


def test_t_is_dead_in_the_grid_and_without_a_viewer(qapp):
    window = _window(qapp)
    try:
        _press(window, "grid")
        _press(window, "panel")
        _press(window, "window")
        _press(window, "viewer")
        assert window._tag_overlay_visible is False
        assert window.viewer.canvas._tag_regions == ()
    finally:
        window.close()


def test_other_overlays_guard_t_and_ctrl_t_is_annulled(qapp):
    window = _window(qapp)
    try:
        window._open_viewer()
        window._toggle_help()
        window.keyPressEvent(_key(Qt.Key_T))
        assert window._tag_overlay_visible is False

        window._toggle_help()
        window.status.open_command()
        window.keyPressEvent(_key(Qt.Key_T))
        assert window._tag_overlay_visible is False

        window.status.prompt.keyPressEvent(
            _key(Qt.Key_Escape)
        )  # Esc sigue cerrando el prompt

        window._toggle_locations()
        window.keyPressEvent(_key(Qt.Key_T))
        assert window._tag_overlay_visible is False
        window._toggle_locations()

        window.keyPressEvent(
            _key(Qt.Key_T, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        assert window._tag_overlay_visible is False
    finally:
        window.close()


def test_overlay_state_survives_photo_changes_and_reopening_the_viewer(qapp):
    window = _window(qapp)
    try:
        window._open_viewer()
        _press(window, "viewer")
        assert window.viewer.canvas._tag_regions == (("Ana", REGION),)

        window.viewer.canvas._hover_tag = 0  # tooltip abierto antes del cambio
        window._select_key(window._visible_keys_cache[1])
        qapp.processEvents()
        assert window.viewer.canvas._hover_tag is None
        assert window._tag_overlay_visible is True
        assert window.viewer.canvas._tag_regions == (
            ("Ana", (0.5, 0.5, 0.2, 0.2)),
        )

        window._close_viewer()
        window._open_viewer()
        assert window._tag_overlay_visible is True
        assert window.viewer.canvas._tag_regions == (
            ("Ana", (0.5, 0.5, 0.2, 0.2)),
        )
    finally:
        window.close()

    fresh = _window(qapp)
    try:
        assert fresh._tag_overlay_visible is False
    finally:
        fresh.close()


def test_photo_without_regions_keeps_the_switch_on_and_explains(qapp):
    plain = [make_photo("sin-region.jpg", "2024-05-03 08:00")]
    window = _window(qapp, plain)
    try:
        window._open_viewer()
        _press(window, "window")
        assert window._tag_overlay_visible is True
        assert window.viewer.canvas._tag_regions == ()
        assert window.status.message_label.text() == "sin etiquetas con región en esta foto"
        assert window.status._message_timer.interval() == 3000

        window.set_photos(list(REGION_PHOTOS))
        qapp.processEvents()
        assert window._tag_overlay_visible is True
        assert window.viewer.canvas._tag_regions == (("Ana", REGION),)
    finally:
        window.close()


def _region_library(tmp_path, *, source="import", stored_region=REGION):
    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "a.jpg", b"one")
    (root / ".foto").mkdir()
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        location_id = catalog.insert_named_location(
            name="default", path=root, tier="fast", now=NOW
        )
        catalog.insert_photo(HASH_A, ".jpg", "JPEG", NOW)
        catalog.insert_file(
            location_id=location_id,
            rel_path="2024/a.jpg",
            photo_hash=HASH_A,
            size=4,
            mtime_ns=1,
            inode=1,
            device=1,
            now=NOW,
        )
        catalog.commit()
        catalog._conn.execute(
            "INSERT INTO tags (name, name_normalized, kind, created_at, updated_at) "
            "VALUES ('Ana', ?, 'person', ?, ?)",
            (normalize_tag_name("Ana"), NOW, NOW),
        )
        tag_id = catalog._conn.execute(
            "SELECT id FROM tags WHERE name = 'Ana'"
        ).fetchone()[0]
        catalog._conn.execute(
            "INSERT INTO photo_tags (photo_hash, tag_id, source, status, "
            "region_x, region_y, region_w, region_h, created_at, updated_at) "
            "VALUES (?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?)",
            (HASH_A, tag_id, source, *stored_region, NOW, NOW),
        )
        catalog.commit()
    finally:
        catalog.close()
    return root


def test_t_paints_a_real_catalog_region_without_writing_to_it(qapp, tmp_path):
    root = _region_library(tmp_path, source="manual")
    path = root / ".foto" / "catalog.sqlite"
    photos = load_photos(root)
    before = path.read_bytes()
    window = _window(qapp, photos)
    try:
        window._open_viewer()
        _press(window, "viewer")
        assert window.viewer.canvas._tag_regions == (("Ana", REGION),)
        assert path.read_bytes() == before
    finally:
        window.close()

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT region_x, region_y, region_w, region_h FROM photo_tags"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [REGION]
    assert {tag.region for photo in load_photos(root) for tag in photo.tags} == {REGION}


def test_t_flips_an_imported_real_catalog_region_without_writing_to_it(qapp, tmp_path):
    root = _region_library(tmp_path)
    path = root / ".foto" / "catalog.sqlite"
    before = path.read_bytes()
    window = _window(qapp, load_photos(root))
    try:
        window._open_viewer()
        _press(window, "viewer")
        # (0.1, 0.2, 0.3, 0.4) guardado → (0.1, 0.4, 0.3, 0.4) en la UI.
        assert len(window.viewer.canvas._tag_regions) == 1
        name, region = window.viewer.canvas._tag_regions[0]
        assert name == "Ana"
        assert region == pytest.approx((0.1, 0.4, 0.3, 0.4))
        assert path.read_bytes() == before
    finally:
        window.close()

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT region_x, region_y, region_w, region_h FROM photo_tags"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [REGION]


def test_indexer_dry_run_leaves_the_overlay_and_the_catalog_untouched(qapp, tmp_path, capsys):
    root = _region_library(tmp_path, source="manual")
    config = write_v1_config(tmp_path / "config.toml", root)
    path = root / ".foto" / "catalog.sqlite"
    window = _window(qapp, load_photos(root))
    try:
        window._open_viewer()
        _press(window, "viewer")
        before = path.read_bytes()

        code = index_main(
            [
                "--config",
                str(config),
                "--dry-run",
                "--skip",
                "geocode",
                "--skip",
                "apple-import",
                "--quiet",
            ]
        )
        capsys.readouterr()

        assert code == 0
        assert path.read_bytes() == before
        assert window._tag_overlay_visible is True
        assert window.viewer.canvas._tag_regions == (("Ana", REGION),)
    finally:
        window.close()
