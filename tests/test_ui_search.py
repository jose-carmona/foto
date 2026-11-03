"""Comprobaciones de la búsqueda del visor contra los datos del índice (V03).

La búsqueda filtrará por nombre, ruta relativa, ubicación canónica, réplicas
y campos indexados (cámara, objetivo, tipo, fecha…), en memoria y una entrada
por foto, según ADR 0003.
"""

from __future__ import annotations

import os

import pytest

from foto.ui.prototype import Photo, PrototypeWindow, photo_search_text


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
    return PrototypeWindow(
        photos,
        thumbnails={},
        placeholder_icon=None,
        thumbnail_loader=None,
        title="test",
        initial_panel_visible=False,
        grid_row=True,
    )


# ----------------------------------------------------------------------
# Documento de búsqueda: entradas únicas que consolidan el índice (solo prueba de contrato)
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
