"""Comprobaciones del vocabulario de comandos del visor.

Dependencia de la decisión enmendada en ADR 0003: no existe ``:rescan``; la
reindexación y la recarga del índice son operaciones externas de shell,
servicio o cron.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from foto.storage.library import ensure_library
from foto.ui.commands import COMMANDS
from foto.ui.window import PhotoWindow
from tests.conftest import write_image
from tests.test_ui_load_photos import _seed_catalog, load_photos

_UI_SOURCES = (
    Path("src/foto/ui/app.py"),
    Path("src/foto/ui/window.py"),
)


def test_rescan_is_not_in_the_command_vocabulary():
    names = {command.name for command in COMMANDS}

    assert "rescan" not in names
    assert not any("indexa" in name or "rescan" in name for name in names)


def test_window_does_not_accept_on_rescan():
    parameters = inspect.signature(PhotoWindow.__init__).parameters

    assert "on_rescan" not in parameters


def test_ui_sources_contain_no_rescan_trace():
    for source in _UI_SOURCES:
        text = source.read_text(encoding="utf-8")
        assert "rescan" not in text.lower(), source


def test_viewer_still_loads_from_the_catalog_after_removal(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "2024" / "una.jpg", b"visibles")
    ensure_library(root)
    _seed_catalog(root, "sha256:" + "f" * 64)

    photos = load_photos(root)

    assert len(photos) == 1
