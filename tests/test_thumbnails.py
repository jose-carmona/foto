"""Tests de la geometría de miniaturas."""

from __future__ import annotations

import pytest

from foto.core.thumbnails import cover_size, reader_scaled_size


def test_cover_size_landscape_covers_target() -> None:
    assert cover_size(6000, 4000, 400, 247) == (400, 267)


def test_cover_size_portrait_covers_target() -> None:
    assert cover_size(4000, 6000, 400, 247) == (400, 600)


def test_cover_size_exact_ratio_keeps_target() -> None:
    assert cover_size(4000, 2470, 400, 247) == (400, 247)


def test_cover_size_upscales_tiny_images() -> None:
    assert cover_size(100, 100, 400, 247) == (400, 400)


def test_cover_size_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError):
        cover_size(0, 4000, 400, 247)
    with pytest.raises(ValueError):
        cover_size(6000, 4000, 0, 247)


def test_reader_scaled_size_keeps_landscape_without_rotation() -> None:
    assert reader_scaled_size(6000, 4000, 400, 247, rotate_90=False) == (400, 267)


def test_reader_scaled_size_swaps_rotated_request() -> None:
    # La imagen final es vertical, pero QImageReader escala antes de girar.
    assert reader_scaled_size(6000, 4000, 400, 247, rotate_90=True) == (600, 400)


def test_reader_scaled_size_rotates_portrait_source() -> None:
    assert reader_scaled_size(4000, 6000, 400, 247, rotate_90=True) == (267, 400)
