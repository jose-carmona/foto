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
    from foto.ui.prototype import StatusBar

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

    from foto.ui.prototype import StatusBar

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
        Path("src/foto/ui/prototype.py"),
    )
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "set_counts" in line:
                assert "canonical" not in line, (source, line)
