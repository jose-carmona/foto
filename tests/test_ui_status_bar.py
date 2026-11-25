"""Comprobaciones del contenido de la barra de estado del visor.

Decisión de ADR 0003 (enmendada el 2026-11-03): la barra de estado muestra
solo el nombre del fichero de la selección; la ubicación canónica vive
únicamente en el panel de metadatos (V05).
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _status_bar(qapp):
    from foto.ui.panels import StatusBar

    return StatusBar()


def _right_text(status):
    return status.right_label.text()


def test_counts_suffix_shows_filename_only(qapp):
    status = _status_bar(qapp)

    status.set_counts(3, 10, "una.jpg")

    assert _right_text(status) == "3 de 10 · una.jpg"


def test_counts_without_filename_show_only_counts(qapp):
    status = _status_bar(qapp)

    status.set_counts(0, 12, "")

    assert _right_text(status) == "0 de 12"


def test_status_bar_accepts_no_location_parameter(qapp):
    import inspect

    from foto.ui.panels import StatusBar

    parameters = inspect.signature(StatusBar.set_counts).parameters
    assert "canonical_rel_path" not in parameters

    # La firma exige exactamente visible, total y filename.
    assert list(parameters)[1:] == ["visible", "total", "filename"]


def test_ref_updated_by_calls(qapp):
    status = _status_bar(qapp)

    status.set_counts(1, 7, "a.jpg")
    assert _right_text(status) == "1 de 7 · a.jpg"

    status.set_counts(6, 7, "b.jpg")
    assert _right_text(status) == "6 de 7 · b.jpg"


def test_no_callers_pass_the_canonical_location_to_the_status_bar():
    from pathlib import Path

    sources = (
        Path("src/foto/ui/app.py"),
        Path("src/foto/ui/window.py"),
    )
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "set_counts" in line:
                assert "canonical" not in line, (source, line)


# ----------------------------------------------------------------------
# Modo AYUDA (T2.5 del plan de la ventana de ayuda)
# ----------------------------------------------------------------------
def test_help_mode_shows_the_keys_hint_and_the_unified_close_hint(qapp):
    """T2.5: izquierda con las teclas y el salto 1–N; derecha, Esc/q."""
    status = _status_bar(qapp)

    status.set_counts(3, 10, "una.jpg")
    status.set_help_mode(True, sections=6)

    assert status.mode_label.text() == "AYUDA"
    assert status.message_label.text() == (
        "j/k · PgUp/PgDn · Home/End · 1–6 secciones"
    )
    assert _right_text(status) == "Esc/q cerrar"


def test_help_mode_without_sections_omits_the_jump_hint(qapp):
    status = _status_bar(qapp)

    status.set_help_mode(True)

    assert status.message_label.text() == "j/k · PgUp/PgDn · Home/End"
    assert _right_text(status) == "Esc/q cerrar"


def test_help_mode_right_text_survives_refresh_right(qapp):
    """T2.5: refresh_right no vuelve a colgar el «Esc cerrar» suelto."""
    status = _status_bar(qapp)

    status.set_help_mode(True, sections=6)
    status.refresh_right()

    assert _right_text(status) == "Esc/q cerrar"


def test_help_mode_exit_restores_the_message_and_the_normal_color(qapp):
    """T2.5: al salir se restaura el mensaje anterior y el color normal."""
    status = _status_bar(qapp)

    status.set_counts(3, 10, "una.jpg")
    status.show_message("filtro aplicado", 3000)
    status.set_help_mode(True, sections=6)
    status.set_help_mode(False)

    assert status.mode_label.text() == "NORMAL"
    assert status.message_label.text() == "filtro aplicado"
    assert _right_text(status) == "3 de 10 · una.jpg"
    assert status.message_label.styleSheet() == ""


def test_restored_message_does_not_keep_the_color_of_an_earlier_error(qapp):
    """T2.5: el color de un error previo no se cuela en el mensaje restaurado."""
    status = _status_bar(qapp)

    status.show_message("filtro aplicado", 3000)
    status.show_error("comando desconocido")
    status.clear_error()
    status.set_help_mode(True, sections=6)
    status.set_help_mode(False)

    assert status.message_label.styleSheet() == ""
