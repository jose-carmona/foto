"""Navegación vertical de la rejilla con líneas de tamaño desigual.

Regresión del bug de la agrupación por fecha o cámara: las cabeceras de grupo
cortan el flujo en filas completas y la última fila de cada grupo es parcial,
así que el paso fijo de ``columns`` elementos desalineaba el movimiento
arriba/abajo. El objetivo vertical se deduce ahora de la geometría renderizada
(``vertical_row_target``), que se prueba aquí sin Qt ni pantalla.
"""

from __future__ import annotations

import pytest

from foto.ui.window import ITEM_H, vertical_row_target

# Rejilla agrupada renderizada: cuatro fotos, cabecera (no aparece como línea
# con fotos), tres fotos y dos fotos. Cada línea es (top, centros_x).
# En un viewport con sitio para 4 columnas, el paso antiguo de 4 elementos
# saltaría líneas enteras desde las filas parciales.
GROUPED_ROWS: tuple[tuple[int, tuple[int, ...]], ...] = (
    (0, (200, 600, 1000, 1400)),
    (ITEM_H, (200, 600, 1000)),
    (2 * ITEM_H, (200, 600)),
)


def test_k_from_a_partial_row_lands_in_the_previous_line():
    # Antes: desde la primera foto de la tercera línea (posición 7) el paso de
    # 4 elementos llevaba a la posición 3, la última de la primera línea.
    target = vertical_row_target(
        "k", GROUPED_ROWS, row_index=2, step_top=2 * ITEM_H
    )

    assert target == 1


def test_j_from_a_partial_row_lands_in_the_next_line():
    target = vertical_row_target(
        "j", GROUPED_ROWS, row_index=1, step_top=2 * ITEM_H
    )

    assert target == 2


def test_vertical_movement_clamps_at_the_edges():
    down = vertical_row_target("j", GROUPED_ROWS, row_index=2, step_top=2 * ITEM_H)
    up = vertical_row_target("k", GROUPED_ROWS, row_index=0, step_top=2 * ITEM_H)

    assert down == 2
    assert up == 0


def test_pagedown_jumps_by_viewport_height_not_by_a_fixed_count():
    # Una página (2 líneas de alto): desde la primera línea, la primera línea
    # con top >= 2·ITEM_H es la tercera.
    target = vertical_row_target(
        "pagedown", GROUPED_ROWS, row_index=0, step_top=2 * ITEM_H
    )

    assert target == 2


def test_half_page_moves_one_line_when_each_line_is_one_item_high():
    # Media página (1 línea): primera línea con top >= ITEM_H.
    target = vertical_row_target(
        "halfdown", GROUPED_ROWS, row_index=0, step_top=ITEM_H
    )

    assert target == 1


def test_pageup_returns_to_the_top_when_nothing_is_above():
    target = vertical_row_target(
        "pageup", GROUPED_ROWS, row_index=1, step_top=2 * ITEM_H
    )

    assert target == 0


def test_unknown_vertical_action_is_rejected():
    with pytest.raises(ValueError):
        vertical_row_target("h", GROUPED_ROWS, row_index=0, step_top=ITEM_H)
