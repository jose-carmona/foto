"""Caché de rutas en ``load_from_catalog`` sin cambiar el resultado (S3).

Las rutas del catálogo son POSIX. La carga divide cada rel_path una sola
vez, reutiliza el ``Path`` de cada raíz y resuelve el padre una sola vez por
directorio distinto; la hoja se une después por nombre. Estos tests fijan
la paridad de campos y el número de resoluciones (T1.8).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foto.core.library import _suffix_from_name, load_from_catalog
from foto.storage.catalog import Catalog

NOW = "2026-11-27T00:00:00+00:00"


def _catalog(tmp_path: Path, *, with_usb: bool = True) -> tuple[Catalog, Path, Path]:
    """Catálogo en memoria con una raíz ``fast`` y, opcionalmente, una ``archive``."""
    ssd = tmp_path / "ssd"
    usb = tmp_path / "usb"
    ssd.mkdir()
    if with_usb:
        usb.mkdir()
    catalog = Catalog.open_in_memory()
    connection = catalog._conn
    declared = [("ssd", str(ssd), "fast")]
    if with_usb:
        declared.append(("usb", str(usb), "archive"))
    for name, path, tier in declared:
        connection.execute(
            "INSERT INTO locations (name, path, tier, state, created_at) "
            "VALUES (?, ?, ?, 'active', ?)",
            (name, path, tier, NOW),
        )
    connection.commit()
    return catalog, ssd, usb


def _add_photo(
    catalog: Catalog,
    location_name: str,
    rel_path: str,
    *,
    photo_hash: str,
    extension: str = "",
    file_type: str | None = None,
) -> None:
    """Añade una aparición presente, con metadatos mínimos, al catálogo."""
    connection = catalog._conn
    location_id = connection.execute(
        "SELECT id FROM locations WHERE name = ?", (location_name,)
    ).fetchone()["id"]
    connection.execute(
        "INSERT OR IGNORE INTO photos (hash, extension, file_type, captured_at, "
        "metadata_status, created_at, updated_at) VALUES (?, ?, ?, ?, 'ok', ?, ?)",
        (photo_hash, extension, file_type, "2026-11-26T12:34:56+00:00", NOW, NOW),
    )
    connection.execute(
        "INSERT INTO files (location_id, rel_path, photo_hash, size, mtime_ns, "
        "status, first_seen_at, last_indexed_at) VALUES (?, ?, ?, 10, 1, "
        "'present', ?, ?)",
        (location_id, rel_path, photo_hash, NOW, NOW),
    )
    connection.commit()


@pytest.mark.parametrize(
    "name",
    ["hoja", ".oculto", "a.b.JPG", "a.", "..foo", "a..jpg", ""],
)
def test_suffix_helper_keeps_path_suffix_semantics(name: str):
    assert _suffix_from_name(name) == Path(name).suffix


def test_suffix_helper_covers_the_required_cases():
    assert _suffix_from_name("hoja") == ""
    assert _suffix_from_name(".oculto") == ""
    assert _suffix_from_name("a.b.JPG") == ".JPG"


def test_cached_paths_keep_every_field_for_all_shapes(tmp_path):
    catalog, ssd, _usb = _catalog(tmp_path, with_usb=False)
    try:
        cases = [
            ("simple.jpg", ".jpg", "JPEG"),
            ("2024/nested.jpg", "", None),
            ("2024/viaje/a.b.foto.JPG", "", None),
            ("2024/.oculto.jpg", "", None),
            (".oculto", "", None),
            ("sin_extension", "", None),
        ]
        for index, (rel_path, extension, file_type) in enumerate(cases):
            _add_photo(
                catalog,
                "ssd",
                rel_path,
                photo_hash=f"h-{index}",
                extension=extension,
                file_type=file_type,
            )

        photos = {photo.name: photo for photo in load_from_catalog(catalog)}

        expected = {
            "simple.jpg": (".", ".jpg", "JPEG", "simple.jpg"),
            "nested.jpg": ("2024", ".jpg", "JPG", "2024/nested.jpg"),
            "a.b.foto.JPG": (
                "2024/viaje",
                ".JPG",
                "JPG",
                "2024/viaje/a.b.foto.JPG",
            ),
            ".oculto.jpg": ("2024", ".jpg", "JPG", "2024/.oculto.jpg"),
            ".oculto": (".", "", "—", ".oculto"),
            "sin_extension": (".", "", "—", "sin_extension"),
        }
        for name, (folder, _extension, fmt, rel_path) in expected.items():
            photo = photos[name]
            assert photo.folder == folder
            assert photo.fmt == fmt
            assert photo.canonical_rel_path == rel_path
            assert photo.path == (ssd / rel_path).resolve()
            assert photo.other_locations == ()
    finally:
        catalog.close()


def test_cached_paths_keep_two_locations_and_tier_choice(tmp_path):
    catalog, ssd, usb = _catalog(tmp_path)
    try:
        _add_photo(
            catalog, "ssd", "copia.jpg", photo_hash="h-doble", extension=".jpg"
        )
        _add_photo(
            catalog,
            "usb",
            "archivos/copia.jpg",
            photo_hash="h-doble",
            extension=".jpg",
        )

        photo = load_from_catalog(catalog)[0]

        assert photo.path == (ssd / "copia.jpg").resolve()
        assert photo.canonical_rel_path == "copia.jpg"
        assert photo.folder == "."
        assert photo.fmt == "JPG"
        assert photo.available is True
        assert photo.other_locations == ("usb:archivos/copia.jpg",)

        # La raíz fast desaparece: gana la copia disponible, sin cambiar
        # el cálculo de estados ni de ``other_locations``.
        ssd.rename(tmp_path / "ssd-desmontado")
        fallback = load_from_catalog(catalog)[0]

        assert fallback.path == (usb / "archivos" / "copia.jpg").resolve()
        assert fallback.canonical_rel_path == "archivos/copia.jpg"
        assert fallback.folder == "archivos"
        assert fallback.available is True
        assert fallback.other_locations == ("ssd:copia.jpg",)
    finally:
        catalog.close()


def test_resolve_runs_once_per_distinct_directory(monkeypatch, tmp_path):
    catalog, ssd, usb = _catalog(tmp_path)
    try:
        for name, rel_paths in (
            ("ssd", ["a.jpg", "b.jpg", "2024/c.jpg", "2024/mas/d.jpg", "2024/mas/e.jpg"]),
            ("usb", ["archivos/f.jpg", "archivos/g.jpg"]),
        ):
            for index, rel_path in enumerate(rel_paths):
                _add_photo(
                    catalog,
                    name,
                    rel_path,
                    photo_hash=f"h-{name}-{index}",
                    extension=".jpg",
                )

        real_resolve = Path.resolve
        calls: list[Path] = []

        def spy(self: Path) -> Path:
            calls.append(self)
            return real_resolve(self)

        monkeypatch.setattr(Path, "resolve", spy)
        load_from_catalog(catalog)

        assert len(calls) == 4
        assert set(calls) == {
            ssd,
            ssd / "2024",
            ssd / "2024" / "mas",
            usb / "archivos",
        }
    finally:
        catalog.close()


def test_leaf_symlink_is_left_unresolved(tmp_path):
    """Salvedad de T1.8: solo se resuelve el padre; la hoja enlazada no."""
    catalog, ssd, _usb = _catalog(tmp_path, with_usb=False)
    try:
        target = ssd / "2024" / "nested.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"destino")
        link = ssd / "enlace.jpg"
        try:
            link.symlink_to(target)
        except OSError:  # pragma: no cover - Windows sin privilegios
            pytest.skip("el entorno no permite crear enlaces simbólicos")
        _add_photo(
            catalog, "ssd", "enlace.jpg", photo_hash="h-enlace", extension=".jpg"
        )

        photo = load_from_catalog(catalog)[0]

        assert photo.path == link
        assert photo.path != target
    finally:
        catalog.close()
