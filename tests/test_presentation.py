"""Suite de la capa de presentación pura de la UI.

Estas pruebas ejercitan ``foto.ui.presentation`` y ``foto.ui.models`` sin
importar PySide6 ni crear ``QApplication``: la lógica de presentación —orden,
grupos, documento de búsqueda, navegación vertical, teclas de grupo y la
aritmética del token ``fg.muted``— no depende de Qt. La tabla de teclas de la
UI, ``foto.ui.keybindings``, queda cubierta en detalle por
``tests/test_keybindings.py`` y también se protege aquí como módulo puro. El
suite incluye un test guardián que impide que los módulos puros vuelvan a
importar Qt o la ventana.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from foto.ui.models import Photo
from foto.ui.presentation import (
    KEY_0,
    KEY_4,
    KEY_5,
    KEY_DOLLAR,
    is_group_end,
    is_group_start,
    muted_rgb,
    photo_accessible_name,
    photo_group,
    photo_search_text,
    photo_sort_value,
    sorted_photos,
    vertical_row_target,
)


def _photo(
    name: str,
    folder: str = "2024",
    *,
    taken: str = "2024-05-01 12:34",
    camera: str = "Nikon Z 6",
    favorite: bool = False,
    canonical_rel_path: str | None = None,
    other_locations: tuple[str, ...] = (),
) -> Photo:
    return Photo(
        name=name,
        taken=taken,
        camera=camera,
        lens="Nikkor 35mm",
        focal="35 mm",
        aperture="f/1,8",
        shutter="1/250 s",
        iso="200",
        folder=folder,
        fmt="JPEG",
        dimensions="6000 × 4000",
        size="2 MB",
        location="",
        favorite=favorite,
        canonical_rel_path=(
            canonical_rel_path if canonical_rel_path is not None else f"{folder}/{name}"
        ),
        other_locations=other_locations,
        photo_hash=f"sha256:{abs(hash(name)) % 100000:08d}",
    )


# ----------------------------------------------------------------------
# Navegación vertical: regresión de las filas parciales
# ----------------------------------------------------------------------
# Regresión del bug de la agrupación por fecha o cámara: las cabeceras de
# grupo cortan el flujo en filas completas y la última fila de cada grupo es
# parcial, así que el paso fijo de ``columns`` elementos desalineaba el
# movimiento arriba/abajo. El objetivo vertical se deduce de la geometría
# renderizada (``vertical_row_target``).

# Cualquier altura de línea constante vale para la geometría del movimiento;
# 263 es la de la rejilla real (THUMB_H + GRID_GAP), constante de widget que
# vive en la ventana y no se importa en esta suite sin Qt.
ROW_H = 263  # THUMB_H + GRID_GAP

# Rejilla agrupada renderizada: cuatro fotos, cabecera (no aparece como línea
# con fotos), tres fotos y dos fotos. Cada línea es (top, centros_x).
# En un viewport con sitio para 4 columnas, el paso antiguo de 4 elementos
# saltaría líneas enteras desde las filas parciales.
GROUPED_ROWS: tuple[tuple[int, tuple[int, ...]], ...] = (
    (0, (200, 600, 1000, 1400)),
    (ROW_H, (200, 600, 1000)),
    (2 * ROW_H, (200, 600)),
)


def test_k_from_a_partial_row_lands_in_the_previous_line():
    # Antes: desde la primera foto de la tercera línea (posición 7) el paso de
    # 4 elementos llevaba a la posición 3, la última de la primera línea.
    target = vertical_row_target(
        "k", GROUPED_ROWS, row_index=2, step_top=2 * ROW_H
    )

    assert target == 1


def test_j_from_a_partial_row_lands_in_the_next_line():
    target = vertical_row_target(
        "j", GROUPED_ROWS, row_index=1, step_top=2 * ROW_H
    )

    assert target == 2


def test_vertical_movement_clamps_at_the_edges():
    down = vertical_row_target("j", GROUPED_ROWS, row_index=2, step_top=2 * ROW_H)
    up = vertical_row_target("k", GROUPED_ROWS, row_index=0, step_top=2 * ROW_H)

    assert down == 2
    assert up == 0


def test_pagedown_jumps_by_viewport_height_not_by_a_fixed_count():
    # Una página (2 líneas de alto): desde la primera línea, la primera línea
    # con top >= 2·ROW_H es la tercera.
    target = vertical_row_target(
        "pagedown", GROUPED_ROWS, row_index=0, step_top=2 * ROW_H
    )

    assert target == 2


def test_half_page_moves_one_line_when_each_line_is_one_item_high():
    # Media página (1 línea): primera línea con top >= ROW_H.
    target = vertical_row_target(
        "halfdown", GROUPED_ROWS, row_index=0, step_top=ROW_H
    )

    assert target == 1


def test_pageup_returns_to_the_top_when_nothing_is_above():
    target = vertical_row_target(
        "pageup", GROUPED_ROWS, row_index=1, step_top=2 * ROW_H
    )

    assert target == 0


def test_unknown_vertical_action_is_rejected():
    with pytest.raises(ValueError):
        vertical_row_target("h", GROUPED_ROWS, row_index=0, step_top=ROW_H)


# ----------------------------------------------------------------------
# Documento de búsqueda: entradas únicas que consolidan el índice
# ----------------------------------------------------------------------
def test_search_includes_canonical_and_replicas():
    photo = _photo(
        "playa.jpg",
        "2024/costa",
        canonical_rel_path="2024/costa/playa.jpg",
        other_locations=("2025/copia.jpg",),
    )
    document = photo_search_text(photo)

    assert "2024/costa/playa.jpg" in document
    assert "2025/copia.jpg" in document


def test_search_document_is_lowercase_normalized():
    photo = _photo("Añoño.JPG", "2024", camera="Cámara Óptica")

    document = photo_search_text(photo)

    assert document == document.casefold()
    assert "cámara óptica" in document


# ----------------------------------------------------------------------
# Teclas de grupo: 0 y $ sin depender de Qt
# ----------------------------------------------------------------------
def test_key_0_is_the_group_start_and_not_a_reserved_digit():
    assert is_group_start(KEY_0, False)
    assert not is_group_start(KEY_5, False)
    assert not is_group_start(KEY_0, True)


def test_dollar_is_detected_on_every_layout_variant():
    # En algunas distribuciones el ``$`` es su propia tecla sin mayúsculas; en
    # otras vive en Shift+4 (español) o Shift+5 (inglés).
    assert is_group_end(KEY_DOLLAR, False)
    assert is_group_end(KEY_4, True)
    assert is_group_end(KEY_5, True)
    assert not is_group_end(KEY_4, False)


def test_group_key_constants_match_the_ascii_qt_key_codes():
    # KEY_0, KEY_DOLLAR, KEY_4 y KEY_5 son los enteros que devuelve
    # QKeyEvent.key() para Qt::Key_0, Qt::Key_Dollar, Qt::Key_4 y Qt::Key_5;
    # coinciden con sus caracteres ASCII.
    assert KEY_0 == ord("0")
    assert KEY_DOLLAR == ord("$")
    assert KEY_4 == ord("4")
    assert KEY_5 == ord("5")


# ----------------------------------------------------------------------
# Orden y grupos
# ----------------------------------------------------------------------
def test_sorted_photos_by_date_puts_the_newest_first():
    photos = [
        _photo("antigua.jpg", taken="2023-01-01 08:00"),
        _photo("reciente.jpg", taken="2025-12-31 23:59"),
        _photo("media.jpg", taken="2024-06-15 12:00"),
    ]

    assert [photo.name for photo in sorted_photos(photos, "date")] == [
        "reciente.jpg",
        "media.jpg",
        "antigua.jpg",
    ]


def test_sorted_photos_by_camera_sorts_by_camera_name():
    photos = [
        _photo("z.jpg", camera="Sony ILCE-7RM5"),
        _photo("a.jpg", camera="Canon EOS R5"),
        _photo("f.jpg", camera="Fujifilm X100V"),
    ]

    assert [photo.name for photo in sorted_photos(photos, "camera")] == [
        "a.jpg",
        "f.jpg",
        "z.jpg",
    ]


def test_sorted_photos_put_photos_without_date_at_the_end():
    photos = [
        _photo("sin-fecha.jpg", taken=""),
        _photo("con-fecha.jpg", taken="2024-05-01 12:34"),
    ]

    assert [photo.name for photo in sorted_photos(photos, "date")] == [
        "con-fecha.jpg",
        "sin-fecha.jpg",
    ]


def test_sorted_photos_put_photos_without_camera_at_the_end():
    photos = [
        _photo("sin-camara.jpg", camera=""),
        _photo("con-camara.jpg", camera="Nikon Z 6"),
    ]

    assert [photo.name for photo in sorted_photos(photos, "camera")] == [
        "con-camara.jpg",
        "sin-camara.jpg",
    ]


def test_sorted_photos_breaks_ties_by_name_with_casefold():
    photos = [
        _photo("Beta.jpg"),
        _photo("Zeta.jpg"),
        _photo("alfa.jpg"),
    ]

    # Misma fecha: desempata el nombre plegando mayúsculas, de modo que
    # ``alfa`` va antes que ``Beta`` y ``Zeta``.
    assert [photo.name for photo in sorted_photos(photos, "date")] == [
        "alfa.jpg",
        "Beta.jpg",
        "Zeta.jpg",
    ]


def test_photo_sort_value_flags_the_date_to_keep_empties_last():
    with_date = _photo("a.jpg", taken="2024-05-01 12:34")
    without_date = _photo("b.jpg", taken="")

    # Con reverse=True (fecha), True ordena antes: los vacíos caen al final.
    assert photo_sort_value(with_date, "date") == (True, "2024-05-01 12:34")
    assert photo_sort_value(without_date, "date") == (False, "")


def test_photo_sort_value_flags_the_camera_to_keep_empties_last():
    with_camera = _photo("a.jpg", camera="Nikon Z 6")
    without_camera = _photo("b.jpg", camera="")

    # Sin reverse (cámara), False ordena antes: los vacíos caen al final.
    assert photo_sort_value(with_camera, "camera") == (False, "nikon z 6")
    assert photo_sort_value(without_camera, "camera") == (True, "")


def test_photo_group_labels_the_month_in_spanish():
    key, label = photo_group(_photo("a.jpg", taken="2024-05-10 08:00"), "date")

    assert key == "2024-05"
    assert label == "mayo de 2024"


def test_photo_group_uses_the_camera_as_its_own_label():
    key, label = photo_group(_photo("a.jpg", camera="Fujifilm X100V"), "camera")

    assert key == "fujifilm x100v"
    assert label == "Fujifilm X100V"


def test_photo_group_reports_missing_camera_and_date_as_empty_groups():
    assert photo_group(_photo("a.jpg", camera=""), "camera") == ("", "Sin cámara")
    assert photo_group(_photo("a.jpg", taken=""), "date") == ("", "Sin fecha")


def test_photo_group_falls_back_to_the_date_when_malformed():
    key, label = photo_group(_photo("a.jpg", taken="2024-xx-01 08:00"), "date")

    assert (key, label) == ("2024-xx-01", "2024-xx-01")


def test_photo_group_falls_back_to_the_date_when_month_is_out_of_range():
    key, label = photo_group(_photo("a.jpg", taken="2024-13-01 08:00"), "date")

    assert (key, label) == ("2024-13-01", "2024-13-01")


# ----------------------------------------------------------------------
# Nombre accesible
# ----------------------------------------------------------------------
def test_photo_accessible_name_lists_the_file_name():
    assert photo_accessible_name(_photo("duna.jpg")) == "duna.jpg"


def test_photo_accessible_name_appends_the_favorite_state():
    assert photo_accessible_name(_photo("duna.jpg", favorite=True)) == (
        "duna.jpg, favorito"
    )


# ----------------------------------------------------------------------
# Color atenuado (token fg.muted), aritmética pura
# ----------------------------------------------------------------------
def test_muted_rgb_interpolates_each_channel_on_a_light_background():
    # Fondo claro ((250 + 150) / 2 = 200 > 128): ratio 0.62.
    assert muted_rgb((10, 20, 30), (250, 200, 150)) == (101, 88, 76)


def test_muted_rgb_interpolates_each_channel_on_a_dark_background():
    # Fondo oscuro ((40 + 20) / 2 = 30): ratio 0.72.
    assert muted_rgb((240, 230, 220), (20, 30, 40)) == (178, 174, 170)


def test_muted_rgb_is_symmetric_across_channels():
    # La interpolación es canal a canal: permutar los canales de la entrada
    # permuta igual el resultado.
    assert muted_rgb((30, 20, 10), (150, 200, 250)) == tuple(
        reversed(muted_rgb((10, 20, 30), (250, 200, 150)))
    )


def test_muted_rgb_treats_a_background_of_exactly_128_as_dark():
    # (156 + 100) / 2 = 128 exacto: no supera el umbral, ratio 0.72. Sin
    # redondear, 128 > 128 es falso, igual que el lightness() de Qt.
    assert muted_rgb((0, 0, 0), (156, 100, 100)) == (44, 28, 28)


def test_muted_rgb_treats_a_background_of_128_and_a_half_as_light():
    # (157 + 100) / 2 = 128,5: fondo claro, ratio 0.62. Qt redondea 128,5 a
    # 129 y también lo trata como claro; un round() de Python (banquero)
    # dejaría 128 y lo volvería oscuro, divergiendo del render.
    assert muted_rgb((0, 0, 0), (157, 100, 100)) == (60, 38, 38)


# ----------------------------------------------------------------------
# Guardián de pureza: nada de Qt ni de ventana en los módulos puros
# ----------------------------------------------------------------------
_PURE_SOURCES = (
    Path("src/foto/ui/models.py"),
    Path("src/foto/ui/presentation.py"),
    Path("src/foto/ui/keybindings.py"),
)
_FORBIDDEN_TOKENS = (
    "pyside",
    "qtcore",
    "qtgui",
    "qtwidgets",
    "foto.ui.window",
    "foto.ui.thumbnails",
    "foto.ui.app",
    "foto.ui.prototype",
)


def test_pure_modules_never_mention_qt_or_the_window():
    for source in _PURE_SOURCES:
        text = source.read_text(encoding="utf-8").casefold()
        for token in _FORBIDDEN_TOKENS:
            assert token not in text, (source, token)


def test_pure_modules_import_without_pyside6():
    # Importar la presentación en un intérprete limpio no debe arrastrar
    # PySide6; ``models`` queda cubierto por transitividad. No depende de
    # QT_QPA_PLATFORM: si alguien importase Qt aquí, el fallo sería el propio
    # módulo colado en sys.modules.
    code = (
        "import sys\n"
        "import foto.ui.presentation\n"
        "leaked = [name for name in sys.modules if name.startswith('PySide6')]\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
