"""Comprobaciones de la búsqueda del visor contra los datos del índice (V03).

La búsqueda filtrará por nombre, ruta relativa, ubicación canónica, réplicas
y campos indexados (cámara, objetivo, tipo, fecha…), en memoria y una entrada
por foto, según ADR 0003.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QListWidgetItem

from foto.ui.grid import HEADER_ROLE, PHOTO_ROLE
from foto.ui.models import Photo
from foto.ui.window import PhotoWindow


def _photo(
    name: str,
    folder: str = "2024",
    *,
    taken: str = "2024-05-01 12:34",
    camera: str = "Nikon Z 6",
    lens: str = "Nikkor 35mm",
    fmt: str = "JPEG",
    iso: str = "200",
    canonical_rel_path: str | None = None,
    other_locations: tuple[str, ...] = (),
) -> Photo:
    return Photo(
        name=name,
        taken=taken,
        camera=camera,
        lens=lens,
        focal="35 mm",
        aperture="f/1,8",
        shutter="1/250 s",
        iso=iso,
        folder=folder,
        fmt=fmt,
        dimensions="6000 × 4000",
        size="2 MB",
        location="",
        canonical_rel_path=(
            canonical_rel_path if canonical_rel_path is not None else f"{folder}/{name}"
        ),
        other_locations=other_locations,
        photo_hash=f"sha256:{abs(hash(name)) % 100000:08d}",
    )


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp):
    photos = [
        _photo("bosque.jpg", "2024/parque"),
        _photo(
            "playa.jpg",
            "2024/costa",
            camera="Canon EOS R5",
            lens="RF 50mm",
            fmt="RAW (CR3)",
            taken="2023-07-02 08:00",
            other_locations=("2025/copia.jpg", "sigmar/replica.jpg"),
        ),
        _photo("ciudad.jpg", "2025/centro", fmt="TIFF", iso="800"),    ]
    return PhotoWindow(
        photos,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )


# ----------------------------------------------------------------------
# Filtrado contra el índice
# ----------------------------------------------------------------------
def test_filter_by_name(window):
    window._apply_filter("bosque")

    assert window._visible_count() == 1


def test_filter_by_relative_path(window):
    window._apply_filter("2025/centro")

    assert window._visible_count() == 1

    window._apply_filter("centro")
    assert window._visible_count() == 1


def test_filter_by_camera_make_and_model(window):
    window._apply_filter("canon")

    assert window._visible_count() == 1

    window._apply_filter("eos r5")
    assert window._visible_count() == 1


def test_filter_by_lens(window):
    window._apply_filter("sonnar")

    assert window._visible_count() == 0

    window._apply_filter("rf 50")
    assert window._visible_count() == 1


def test_filter_by_file_type(window):
    window._apply_filter("cr3")

    assert window._visible_count() == 1

    window._apply_filter("tiff")
    assert window._visible_count() == 1


def test_filter_by_captured_date(window):
    window._apply_filter("2023-07")

    assert window._visible_count() == 1


def test_filter_by_replica_location_shows_a_single_entry(window):
    window._apply_filter("copia")

    assert window._visible_count() == 1

    window._apply_filter("replica")
    assert window._visible_count() == 1


def test_filter_is_case_insensitive(window):
    window._apply_filter("BOSQUE")
    assert window._visible_count() == 1

    window._apply_filter("BosQue")
    assert window._visible_count() == 1


def test_filter_without_matches_leaves_zero_visible(window):
    window._apply_filter("no-existe-nada")

    assert window._visible_count() == 0


def test_empty_filter_restores_everything(window):
    window._apply_filter("bosque")
    assert window._visible_count() == 1

    window._apply_filter("")
    assert window._visible_count() == 3


def test_filter_updates_status_counts(window):
    window._apply_filter("canon")

    right = window.status.right_label.text()
    assert right.startswith("1 de 3")
    assert "playa.jpg" in right


# ----------------------------------------------------------------------
# Una sola pasada: setHidden solo cuando cambia (R2)
# ----------------------------------------------------------------------
def test_filter_only_changes_visibility_when_needed(window, monkeypatch):
    """Reaplicar el mismo filtro no vuelve a llamar a ``setHidden`` (R2)."""
    originales: list = []
    setHidden_original = QListWidgetItem.setHidden

    def espia_set_hidden(self, hidden):
        originales.append((self, hidden))
        setHidden_original(self, hidden)

    monkeypatch.setattr(
        "PySide6.QtWidgets.QListWidgetItem.setHidden", espia_set_hidden
    )

    window._apply_filter("canon")
    assert originales  # el cambio de filtro sí produce llamadas
    originales.clear()

    window._apply_filter("canon")
    assert originales == []  # reaplicar el mismo filtro no toca nada

    window._apply_filter("bosque")
    assert originales  # cambiar de filtro vuelve a producir llamadas


def test_filter_keeps_headers_and_counts_in_one_pass(qapp):
    """Cabeceras, caché de visibles y recuentos salen de la misma pasada (R2)."""
    from datetime import UTC, datetime, timedelta

    base = datetime(2024, 5, 1, tzinfo=UTC)
    photos = [
        _photo(
            f"mayo-{indice}.jpg",
            "2024/mayo",
            taken=(base + timedelta(days=indice)).strftime("%Y-%m-%d %H:%M"),
        )
        for indice in range(3)
    ] + [
        _photo(
            f"junio-{indice}.jpg",
            "2024/junio",
            taken=(base + timedelta(days=31 + indice)).strftime("%Y-%m-%d %H:%M"),
        )
        for indice in range(2)
    ]
    window = PhotoWindow(
        photos,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )
    try:
        window._apply_filter("mayo")

        visibles = [item for item in window._grid_items.values() if not item.isHidden()]
        assert len(visibles) == 3
        # La caché de visibles coincide con lo visible y las posiciones también.
        claves_visibles = sorted(
            item.data(PHOTO_ROLE) for item in visibles
        )
        assert sorted(window._visible_keys_cache) == claves_visibles
        assert window._visible_positions_by_key == {
            key: index for index, key in enumerate(window._visible_keys_cache)
        }
        assert window._visible_count() == 3
        assert window.status.right_label.text().startswith("3 de 5")

        # Cabeceras: solo la del grupo con fotos visibles queda a la vista.
        cabeceras = [
            (item.text(), item.isHidden())
            for row in range(window.grid.count())
            if (item := window.grid.item(row)) is not None and item.data(HEADER_ROLE)
        ]
        visibles_cabeceras = [texto for texto, oculta in cabeceras if not oculta]
        assert len(visibles_cabeceras) == 1
        assert "mayo" in visibles_cabeceras[0].lower()

        window._apply_filter("")
        assert window._visible_count() == 5
    finally:
        window.close()
