"""Filtro por etiqueta de sistema en SQL y antes de materializar (A.1 y A.2).

Paridad entre ``list_present_files(system_tag=...)`` y filtrar en Python por
``.favorite`` la carga completa (ADR 0006, Decisiones 4 y 6): ``fav`` se
resuelve por ``kind='system'``, cuentan ``confirmed`` y ``suggested``, nunca
``rejected``, y una etiqueta libre con otro nombre no entra. Sin Qt.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from foto.core.errors import TagError
from foto.core.indexing import Indexer, IndexingOptions
from foto.core.library import LibraryLoadCancelled, load_from_catalog
from foto.storage.catalog import Catalog
from foto.storage.config import Location
from foto.storage.tags import FAVORITE_TAG, favorite_tag, toggle_favorite
from foto.ui.models import Photo
from tests.conftest import write_image

NOW = "2026-11-26T12:00:00+00:00"


def _index(root: Path, catalog: Catalog, declared: tuple[Location, ...]) -> None:
    """Indexa la raíz en el catálogo dado, sin geocodificación."""
    Indexer(
        catalog,
        declared,
        options=IndexingOptions(skip=frozenset({"geocode"})),
    ).run()


@pytest.fixture()
def three_photos(tmp_path):
    """Tres fotos indexadas en memoria, con sus hashes por contenido."""
    root = tmp_path / "fotos"
    for index in range(1, 4):
        write_image(root / f"foto{index}.jpg", f"contenido-{index}".encode())
    catalog = Catalog.open_in_memory()
    try:
        _index(root, catalog, (Location("default", root, "fast"),))
        hashes = [
            str(row["hash"])
            for row in catalog._conn.execute("SELECT hash FROM photos ORDER BY hash")
        ]
        assert len(hashes) == 3
        yield catalog, hashes
    finally:
        catalog.close()


def _set_status(catalog: Catalog, photo_hash: str, status: str) -> None:
    """Cambia el estado de la asignación de ``fav`` de una foto con SQL."""
    fav = favorite_tag(catalog._conn)
    catalog._conn.execute(
        "UPDATE photo_tags SET status = ?, updated_at = ? "
        "WHERE photo_hash = ? AND tag_id = ?",
        (status, NOW, photo_hash, fav.id),
    )
    catalog.commit()


def _add_suggested(catalog: Catalog, photo_hash: str) -> None:
    """Añade una asignación ``suggested`` de ``fav`` (como haría el ML)."""
    fav = favorite_tag(catalog._conn)
    catalog._conn.execute(
        "INSERT INTO photo_tags "
        "(photo_hash, tag_id, source, status, created_at, updated_at) "
        "VALUES (?, ?, 'ml', 'suggested', ?, ?)",
        (photo_hash, fav.id, NOW, NOW),
    )
    catalog.commit()


def test_list_present_files_filters_by_system_tag(three_photos):
    """Sin filtro devuelve tres filas; con ``fav`` solo la foto marcada."""
    catalog, hashes = three_photos
    all_rows = catalog.list_present_files()
    assert len(all_rows) == 3

    toggle_favorite(catalog._conn, hashes[0], now=NOW)
    filtered = catalog.list_present_files(system_tag=FAVORITE_TAG)
    assert [str(row["photo_hash"]) for row in filtered] == [hashes[0]]

    unfiltered = next(row for row in all_rows if row["photo_hash"] == hashes[0])
    assert filtered[0].keys() == unfiltered.keys()
    assert dict(filtered[0]) == dict(unfiltered)


def test_load_from_catalog_filters_before_materialization(three_photos):
    """La carga filtrada coincide con filtrar por ``.favorite`` la completa."""
    catalog, hashes = three_photos
    toggle_favorite(catalog._conn, hashes[0], now=NOW)

    filtered = load_from_catalog(catalog, system_tag=FAVORITE_TAG)
    expected = [photo for photo in load_from_catalog(catalog) if photo.favorite]
    assert [photo.photo_hash for photo in filtered] == [hashes[0]]
    assert [photo.photo_hash for photo in expected] == [hashes[0]]
    assert filtered[0] == expected[0]

    # ``models.py`` no importa Qt: la fábrica real de la UI también se respeta.
    ui_photos = load_from_catalog(
        catalog, factory=Photo.from_catalog, system_tag=FAVORITE_TAG
    )
    assert len(ui_photos) == 1
    assert ui_photos[0].favorite is True
    assert ui_photos[0].photo_hash == hashes[0]


def test_rejected_system_tag_does_not_count(three_photos):
    """``rejected`` excluye la foto; ``suggested`` la incluye."""
    catalog, hashes = three_photos
    toggle_favorite(catalog._conn, hashes[0], now=NOW)
    _set_status(catalog, hashes[0], "rejected")

    assert catalog.list_present_files(system_tag=FAVORITE_TAG) == []
    assert load_from_catalog(catalog, system_tag=FAVORITE_TAG) == []
    assert catalog.has_system_tag(FAVORITE_TAG) is False

    _add_suggested(catalog, hashes[1])
    filtered = catalog.list_present_files(system_tag=FAVORITE_TAG)
    assert [str(row["photo_hash"]) for row in filtered] == [hashes[1]]
    loaded = load_from_catalog(catalog, system_tag=FAVORITE_TAG)
    assert [photo.photo_hash for photo in loaded] == [hashes[1]]
    assert catalog.has_system_tag(FAVORITE_TAG) is True


def test_free_tag_with_same_name_does_not_count(three_photos):
    """Una etiqueta libre no activa el filtro de sistema, aunque esté asignada."""
    catalog, hashes = three_photos
    catalog._conn.execute(
        "INSERT INTO tags (name, name_normalized, kind, created_at, updated_at) "
        "VALUES (?, ?, 'free', ?, ?)",
        ("favorito", "favorito", NOW, NOW),
    )
    free_id = int(catalog._conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    catalog._conn.execute(
        "INSERT INTO photo_tags "
        "(photo_hash, tag_id, source, status, created_at, updated_at) "
        "VALUES (?, ?, 'manual', 'confirmed', ?, ?)",
        (hashes[0], free_id, NOW, NOW),
    )
    catalog.commit()

    assert catalog.list_present_files(system_tag="favorito") == []
    assert load_from_catalog(catalog, system_tag="favorito") == []
    assert catalog.has_system_tag("favorito") is False


def test_no_assignments_returns_empty_and_has_system_tag_is_false(three_photos):
    """Sin favoritas: lista y carga vacías, sin excepción y ``has`` a ``False``."""
    catalog, _hashes = three_photos
    assert catalog.list_present_files(system_tag=FAVORITE_TAG) == []
    assert load_from_catalog(catalog, system_tag=FAVORITE_TAG) == []
    assert catalog.has_system_tag(FAVORITE_TAG) is False


def test_filter_keeps_canonical_choice_and_other_locations(tmp_path):
    """Filtrar no altera la ruta canónica ni ``other_locations`` (ADR 0010)."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    write_image(ssd / "z" / "original.jpg", b"mismos-bytes")
    write_image(usb / "a" / "original.jpg", b"mismos-bytes")
    declared = (Location("ssd", ssd, "fast"), Location("usb", usb, "archive"))
    catalog = Catalog.open_in_memory()
    try:
        _index(ssd.parent, catalog, declared)
        photo_hash = str(catalog._conn.execute("SELECT hash FROM photos").fetchone()[0])
        toggle_favorite(catalog._conn, photo_hash, now=NOW)

        complete = [
            photo for photo in load_from_catalog(catalog) if photo.photo_hash == photo_hash
        ]
        filtered = load_from_catalog(catalog, system_tag=FAVORITE_TAG)

        assert len(complete) == len(filtered) == 1
        assert filtered[0].path == complete[0].path == (ssd / "z" / "original.jpg").resolve()
        assert filtered[0].canonical_rel_path == complete[0].canonical_rel_path
        assert filtered[0].other_locations == complete[0].other_locations == (
            "usb:a/original.jpg",
        )
    finally:
        catalog.close()


def test_cancellation_still_checks_every_emitted_photo(three_photos):
    """La guardia se consulta una vez por foto emitida (solo las favoritas)."""
    catalog, hashes = three_photos
    toggle_favorite(catalog._conn, hashes[0], now=NOW)
    toggle_favorite(catalog._conn, hashes[2], now=NOW)

    with pytest.raises(LibraryLoadCancelled):
        load_from_catalog(
            catalog,
            system_tag=FAVORITE_TAG,
            should_continue=lambda: False,
        )

    calls: list[bool] = []

    def cont() -> bool:
        calls.append(True)
        return True

    load_from_catalog(catalog, system_tag=FAVORITE_TAG, should_continue=cont)
    assert len(calls) == 2


def test_normalization_and_invalid_name(three_photos):
    """``FAV`` normaliza a ``fav``; un nombre vacío lanza ``TagError``."""
    catalog, hashes = three_photos
    toggle_favorite(catalog._conn, hashes[0], now=NOW)

    assert [str(row["photo_hash"]) for row in catalog.list_present_files("FAV")] == [
        hashes[0]
    ]
    assert [photo.photo_hash for photo in load_from_catalog(catalog, system_tag="FAV")] == [
        hashes[0]
    ]
    assert catalog.has_system_tag("FAV") is True

    with pytest.raises(TagError):
        catalog.list_present_files("   ")
    with pytest.raises(TagError):
        load_from_catalog(catalog, system_tag="   ")
    with pytest.raises(TagError):
        catalog.has_system_tag("   ")


def test_factory_is_respected_with_filter(three_photos):
    """El ``factory`` inyectado recibe los mismos kwargs que sin filtrar."""
    catalog, hashes = three_photos
    toggle_favorite(catalog._conn, hashes[0], now=NOW)

    def factory(**kwargs):
        return SimpleNamespace(**kwargs)

    filtered = load_from_catalog(
        catalog, factory=factory, system_tag=FAVORITE_TAG
    )
    complete = load_from_catalog(catalog, factory=factory)
    expected = next(
        vars(photo) for photo in complete if photo.photo_hash == hashes[0]
    )

    assert len(filtered) == 1
    assert vars(filtered[0]) == expected
    assert vars(filtered[0])["tags"] == expected["tags"]
