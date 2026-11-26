"""Icono de la aplicación empaquetado con ella.

El PNG vive en ``foto/resources`` y ``foto.ui.app._app_icon`` lo carga en
memoria antes de crear ventanas: el icono de la app (y el del dock en
macOS) no depende de un fichero externo ni del directorio de trabajo.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from foto.ui.app import _app_icon


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def test_el_icono_empaquetado_se_carga(qapp):
    icon = _app_icon()

    assert not icon.isNull()
    assert not icon.pixmap(64, 64).isNull()
