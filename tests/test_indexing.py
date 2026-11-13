"""Tests de indexación incremental, borrados y reconciliación."""

from __future__ import annotations

import importlib
import os

import pytest

from foto.core.errors import HashError, LibraryError
from foto.core.hashing import hash_bytes
from foto.core.indexing import Indexer, IndexingOptions
from foto.core.metadata import PhotoMetadata
from foto.storage.catalog import Catalog
from tests.conftest import (
    CountingHasher,
    FakeExtractor,
    read_locations,
    read_photos,
    run_index,
    write_image,
)


def test_same_bytes_in_two_paths_is_one_photo_with_two_locations(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a" / "una.jpg", b"mismos-bytes")
    write_image(root / "b" / "dos.jpeg", b"mismos-bytes")
    extractor = FakeExtractor()
    hasher = CountingHasher()

    summary = run_index(root, hasher=hasher, extractor=extractor)

    assert summary.status == "ok"
    assert summary.added == 2
    assert summary.photos_total == 1
    assert summary.locations_total == 2
    assert summary.duplicate_photos == 1
    assert len(hasher.calls) == 2
    assert sum(len(batch) for batch in extractor.calls) == 1

    locations = read_locations(root)
    assert set(locations) == {"a/una.jpg", "b/dos.jpeg"}
    assert len({row["photo_hash"] for row in locations.values()}) == 1


def test_different_bytes_are_different_photos(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "una.jpg", b"contenido-a")
    write_image(root / "otra.jpg", b"contenido-b")

    summary = run_index(root)

    assert summary.photos_total == 2
    assert summary.locations_total == 2
    assert summary.duplicate_photos == 0
    assert len(read_photos(root)) == 2


def test_second_run_without_changes_does_not_read_anything(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    write_image(root / "sub" / "b.jpg", b"b")
    run_index(root, extractor=FakeExtractor())

    hasher = CountingHasher()
    extractor = FakeExtractor()
    summary = run_index(root, hasher=hasher, extractor=extractor)

    assert summary.unchanged == 2
    assert summary.hashed_bytes == 0
    assert summary.hashed_files == 0
    assert summary.metadata_extracted == 0
    assert hasher.calls == []
    assert extractor.calls == []


def test_new_file_is_hashed_and_metadata_extracted(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "uno.jpg", b"uno")
    run_index(root, extractor=FakeExtractor())

    write_image(root / "dos.jpg", b"dos")
    hasher = CountingHasher()
    extractor = FakeExtractor()
    summary = run_index(root, hasher=hasher, extractor=extractor)

    assert summary.added == 1
    assert summary.photos_total == 2
    assert len(hasher.calls) == 1
    assert sum(len(batch) for batch in extractor.calls) == 1


def test_copy_of_known_hash_adds_location_without_metadata(tmp_path):
    root = tmp_path / "biblioteca"
    original = write_image(root / "original.jpg", b"duplicada")
    run_index(root, extractor=FakeExtractor())

    write_image(root / "copia" / "copia.jpg", b"duplicada")
    hasher = CountingHasher()
    extractor = FakeExtractor()
    summary = run_index(root, hasher=hasher, extractor=extractor)

    assert summary.added == 1
    assert summary.photos_total == 1
    assert summary.locations_total == 2
    assert summary.duplicate_photos == 1
    assert len(hasher.calls) == 1
    assert extractor.calls == []
    assert read_locations(root)["copia/copia.jpg"]["photo_hash"] == hash_bytes(
        original.read_bytes()
    )


def test_touch_rehashes_but_does_not_reextract_metadata(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"estable")
    run_index(root, extractor=FakeExtractor())
    stat = path.stat()
    os.utime(path, ns=(stat.st_mtime_ns + 2_000_000_000, stat.st_mtime_ns + 2_000_000_000))

    hasher = CountingHasher()
    extractor = FakeExtractor()
    summary = run_index(root, hasher=hasher, extractor=extractor)

    assert summary.unchanged == 1
    assert summary.modified == 0
    assert len(hasher.calls) == 1
    assert extractor.calls == []
    assert read_locations(root)["foto.jpg"]["photo_hash"] == hash_bytes(b"estable")


def test_modified_content_gets_new_hash_and_location_is_reassigned(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"version-1")
    run_index(root)
    old_hash = hash_bytes(b"version-1")

    path.write_bytes(b"version-2")
    summary = run_index(root)

    assert summary.modified == 1
    assert summary.photos_total == 2
    assert summary.locations_total == 1
    locations = read_locations(root)
    assert locations["foto.jpg"]["photo_hash"] == hash_bytes(b"version-2")
    assert old_hash in read_photos(root)


def test_deleted_file_is_marked_missing_and_photo_kept(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"se-borra")
    run_index(root)

    path.unlink()
    summary = run_index(root)

    assert summary.missing == 1
    assert summary.photos_total == 1
    assert summary.locations_total == 1
    location = read_locations(root)["foto.jpg"]
    assert location["status"] == "missing"
    assert location["missing_since"] is not None


def test_reappearing_file_clears_missing_and_stays_present(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"vuelve")
    run_index(root)
    path.unlink()
    run_index(root)

    write_image(root / "foto.jpg", b"vuelve")
    summary = run_index(root)

    assert summary.missing == 0
    location = read_locations(root)["foto.jpg"]
    assert location["status"] == "present"
    assert location["missing_since"] is None


def test_prune_removes_old_missing_locations_and_orphan_photos(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"a-limpiar")
    run_index(root)
    path.unlink()
    run_index(root)
    assert read_locations(root)["foto.jpg"]["status"] == "missing"

    summary = run_index(root, options=IndexingOptions(prune=True, prune_after_days=0))

    assert summary.pruned_locations == 1
    assert summary.pruned_photos == 1
    assert read_locations(root) == {}
    assert read_photos(root) == {}


def test_full_rehashes_and_reextracts_everything(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"a")
    write_image(root / "b.jpg", b"b")
    run_index(root, extractor=FakeExtractor())

    hasher = CountingHasher()
    extractor = FakeExtractor()
    summary = run_index(
        root,
        options=IndexingOptions(full=True),
        hasher=hasher,
        extractor=extractor,
    )

    assert summary.status == "ok"
    assert len(hasher.calls) == 2
    assert sum(len(batch) for batch in extractor.calls) == 2
    assert summary.hashed_bytes == 2
    assert summary.hashed_files == 2
    assert summary.metadata_extracted == 2


def test_full_refreshes_metadata_values(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "foto.jpg", b"metadatos")
    run_index(
        root,
        extractor=FakeExtractor(metadata=PhotoMetadata(file_type="JPEG", camera_model="Antiguo")),
    )

    extractor = FakeExtractor(
        metadata=PhotoMetadata(file_type="JPEG", camera_model="Nuevo", width=123)
    )
    summary = run_index(root, options=IndexingOptions(full=True), extractor=extractor)

    photo = next(iter(read_photos(root).values()))
    assert summary.metadata_extracted == 1
    assert photo["camera_model"] == "Nuevo"
    assert photo["width"] == 123


def test_dry_run_does_not_create_catalog_or_library(tmp_path):
    root = tmp_path / "biblioteca"
    root.mkdir()
    write_image(root / "foto.jpg", b"simulada")

    catalog = Catalog.open_dry_run(root / ".foto" / "catalog.sqlite")
    try:
        summary = Indexer(
            catalog,
            root,
            options=IndexingOptions(dry_run=True),
            hasher=CountingHasher(),
        ).run()
    finally:
        catalog.close()

    assert summary.dry_run is True
    assert summary.added == 1
    assert not (root / ".foto").exists()


def test_dry_run_does_not_modify_existing_catalog(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "una.jpg", b"una")
    run_index(root)
    write_image(root / "dos.jpg", b"dos")

    catalog = Catalog.open_dry_run(root / ".foto" / "catalog.sqlite")
    try:
        summary = Indexer(
            catalog,
            root,
            options=IndexingOptions(dry_run=True),
            hasher=CountingHasher(),
        ).run()
    finally:
        catalog.close()

    assert summary.added == 1
    assert len(read_locations(root)) == 1


def test_partial_scan_does_not_mark_absences(tmp_path, monkeypatch):
    root = tmp_path / "biblioteca"
    write_image(root / "visible.jpg", b"visible")
    blocked = root / "bloqueado"
    write_image(blocked / "oculta.jpg", b"oculta")
    run_index(root)

    scanner_module = importlib.import_module("foto.core.scanner")
    real_scandir = os.scandir

    def failing_scandir(path):
        if os.fspath(path) == os.fspath(blocked):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", failing_scandir)
    summary = run_index(root)

    assert summary.status == "partial"
    assert summary.missing == 0
    assert all(row["status"] == "present" for row in read_locations(root).values())


def test_nonexistent_root_raises_without_touching_catalog(tmp_path):
    root = tmp_path / "no-existe"
    catalog = Catalog.open_in_memory()
    try:
        with pytest.raises(LibraryError):
            Indexer(catalog, root, options=IndexingOptions()).run()
        assert catalog.counts().photos == 0
        assert catalog.counts().locations == 0
    finally:
        catalog.close()


def test_hash_errors_do_not_abort_run_and_are_retried(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"falla-una-vez")

    class FailingHasher(CountingHasher):
        def hash_file(self, candidate):
            self.calls.append(candidate)
            raise HashError("lectura inestable")

    first = run_index(root, hasher=FailingHasher())
    assert first.status == "partial"
    assert first.errors == 1
    assert read_locations(root) == {}

    second = run_index(root, hasher=CountingHasher())
    assert second.status == "ok"
    assert read_locations(root)["foto.jpg"]["status"] == "present"
    assert second.added == 1
    assert path.exists()


def test_move_library_preserves_relative_paths_and_photos(tmp_path):
    original = tmp_path / "biblioteca"
    write_image(original / "2024" / "foto.jpg", b"portable")
    run_index(original)
    before = read_locations(original)

    moved = tmp_path / "biblioteca-movida"
    original.rename(moved)
    # ADR 0008: el descriptor lleva el campo root. Al relocalizar la
    # fototeca el dueño actualiza esa línea (o borra el campo, que es
    # opcional); la app nunca mezcla catálogos en silencio.
    descriptor = moved / ".foto" / "library.toml"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8")
        .replace(f'root = "{original}"', f'root = "{moved}"'),
        encoding="utf-8",
    )
    summary = run_index(moved)

    after = read_locations(moved)
    assert summary.photos_total == 1
    assert summary.locations_total == 1
    assert set(after) == set(before) == {"2024/foto.jpg"}
    assert after["2024/foto.jpg"]["photo_hash"] == before["2024/foto.jpg"]["photo_hash"]


def test_deleting_catalog_rebuilds_it_from_disk(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a" / "una.jpg", b"una")
    write_image(root / "b" / "dos.jpg", b"dos")
    run_index(root)
    before = read_locations(root)

    for suffix in ("", "-wal", "-shm"):
        catalog_path = root / ".foto" / f"catalog.sqlite{suffix}"
        if catalog_path.exists():
            catalog_path.unlink()

    summary = run_index(root)
    after = read_locations(root)

    assert summary.photos_total == 2
    assert summary.locations_total == 2
    assert {key: row["photo_hash"] for key, row in after.items()} == {
        key: row["photo_hash"] for key, row in before.items()
    }


def test_symlinks_are_ignored(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "real.jpg", b"real")
    write_image(root / "sub" / "otra.jpg", b"otra")
    try:
        os.symlink(root / "real.jpg", root / "enlace.jpg")
        os.symlink(root / "sub", root / "enlace-dir")
    except OSError:
        pytest.skip("el sistema no permite crear symlinks")

    summary = run_index(root)

    assert summary.locations_total == 2
    assert set(read_locations(root)) == {"real.jpg", "sub/otra.jpg"}


def test_foto_directory_is_always_ignored(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "visible.jpg", b"visible")
    write_image(root / ".foto" / "oculta.jpg", b"oculta")

    summary = run_index(root)

    assert summary.locations_total == 1
    assert set(read_locations(root)) == {"visible.jpg"}


def test_pending_metadata_is_retried_when_extractor_appears(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "foto.jpg", b"pendiente")

    first = run_index(root, extractor=None)
    assert first.status == "ok"
    assert first.metadata_pending == 1
    assert read_photos(root)[next(iter(read_photos(root)))]["metadata_status"] == "pending"

    hasher = CountingHasher()
    extractor = FakeExtractor()
    second = run_index(root, hasher=hasher, extractor=extractor)

    assert second.status == "ok"
    assert hasher.calls == []
    assert sum(len(batch) for batch in extractor.calls) == 1
    photo = next(iter(read_photos(root).values()))
    assert photo["metadata_status"] == "ok"
    assert photo["camera_make"] == "Fabricante"


def test_metadata_error_is_only_retried_with_full(tmp_path):
    root = tmp_path / "biblioteca"
    path = write_image(root / "foto.jpg", b"metadatos")

    first = run_index(root, extractor=FakeExtractor(errors={path: "fallo permanente"}))
    assert first.status == "partial"
    assert first.errors == 1
    photo = next(iter(read_photos(root).values()))
    assert photo["metadata_status"] == "error"
    assert photo["metadata_error"] == "fallo permanente"

    extractor = FakeExtractor()
    second = run_index(root, extractor=extractor)
    assert second.status == "ok"
    assert extractor.calls == []
    assert next(iter(read_photos(root).values()))["metadata_status"] == "error"

    extractor = FakeExtractor()
    third = run_index(
        root,
        options=IndexingOptions(full=True),
        extractor=extractor,
    )
    assert third.status == "ok"
    assert sum(len(batch) for batch in extractor.calls) == 1
    assert next(iter(read_photos(root).values()))["metadata_status"] == "ok"


def test_metadata_is_extracted_once_per_hash(tmp_path):
    root = tmp_path / "biblioteca"
    write_image(root / "a.jpg", b"igual")
    write_image(root / "b.jpg", b"igual")
    write_image(root / "c.jpg", b"distinta")

    extractor = FakeExtractor()
    summary = run_index(root, extractor=extractor)

    assert summary.photos_total == 2
    assert sum(len(batch) for batch in extractor.calls) == 2


def test_rename_within_library_marks_old_missing_and_new_present(tmp_path):
    root = tmp_path / "biblioteca"
    original = write_image(root / "vieja.jpg", b"renombrada")
    run_index(root)

    original.rename(root / "nueva.jpg")
    hasher = CountingHasher()
    summary = run_index(root, hasher=hasher)

    assert summary.photos_total == 1
    assert summary.locations_total == 2
    assert summary.missing == 1
    locations = read_locations(root)
    assert locations["vieja.jpg"]["status"] == "missing"
    assert locations["nueva.jpg"]["status"] == "present"
    assert locations["vieja.jpg"]["photo_hash"] == locations["nueva.jpg"]["photo_hash"]
    assert len(hasher.calls) == 1
