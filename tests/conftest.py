"""Utilidades compartidas por la suite del indexador."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from foto.core.hashing import hash_bytes
from foto.core.indexing import Indexer, IndexingOptions, IndexingSummary
from foto.core.metadata import ExtractionOutcome, PhotoMetadata
from foto.storage.catalog import Catalog
from foto.storage.library import ensure_library


@pytest.fixture(autouse=True)
def _reset_logging():
    """Evita que un handler apunte a un stderr cerrado entre tests."""
    root = logging.getLogger()
    root.handlers.clear()
    yield
    root.handlers.clear()


@pytest.fixture(autouse=True)
def _disable_host_exiftool(monkeypatch):
    """Aísla la suite de un ``exiftool`` instalado en la máquina.

    Los tests que necesitan metadatos usan ``FakeExtractor``; los que prueban la
    CLI esperan el comportamiento sin extractor. Sin este parche, el resultado
    depende de lo que haya en el ``PATH`` del desarrollador.
    """
    monkeypatch.setattr("foto.storage.exiftool.shutil.which", lambda name: None)


def write_image(path: Path, data: bytes = b"foto-de-prueba") -> Path:
    """Crea un fichero pequeño con extensión de imagen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class CountingHasher:
    """Hasher real que registra cada fichero leído."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def hash_file(self, path: Path) -> str:
        self.calls.append(Path(path))
        return hash_bytes(Path(path).read_bytes())

    def reset(self) -> None:
        self.calls.clear()


class FakeExtractor:
    """Extractor determinista que registra los lotes recibidos."""

    def __init__(
        self,
        *,
        metadata: PhotoMetadata | None = None,
        errors: Mapping[Path, str] | None = None,
        results: Mapping[Path, ExtractionOutcome] | None = None,
    ) -> None:
        self.metadata = metadata or PhotoMetadata(
            file_type="JPEG",
            width=640,
            height=480,
            captured_at="2024-05-01T12:34:56+02:00",
            camera_make="Fabricante",
            camera_model="Modelo",
            lens_model="Objetivo",
            iso=200,
            f_number=2.8,
            exposure_time=0.004,
            focal_length=35.0,
            gps_latitude=40.4168,
            gps_longitude=-3.7038,
            orientation=1,
        )
        self.errors = dict(errors or {})
        self.results = dict(results or {})
        self.calls: list[list[Path]] = []

    def extract_batch(self, paths: Sequence[Path]) -> dict[Path, ExtractionOutcome]:
        batch = [Path(path) for path in paths]
        self.calls.append(batch)
        outcomes: dict[Path, ExtractionOutcome] = {}
        for path in batch:
            if path in self.results:
                outcomes[path] = self.results[path]
            elif path in self.errors:
                outcomes[path] = ExtractionOutcome(error=self.errors[path])
            else:
                outcomes[path] = ExtractionOutcome(metadata=self.metadata)
        return outcomes

    def reset(self) -> None:
        self.calls.clear()


def run_index(
    root: Path,
    *,
    options: IndexingOptions | None = None,
    hasher: CountingHasher | None = None,
    extractor: FakeExtractor | None = None,
    catalog: Catalog | None = None,
) -> IndexingSummary:
    """Ejecuta el indexador sobre una biblioteca temporal."""
    ensure_library(root)
    own_catalog = catalog is None
    active_catalog = catalog or Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        return Indexer(
            active_catalog,
            root,
            options=options or IndexingOptions(),
            hasher=hasher,
            extractor=extractor,
        ).run()
    finally:
        if own_catalog:
            active_catalog.close()


def read_locations(root: Path) -> dict[str, dict[str, object]]:
    """Lee las ubicaciones del catálogo para las aserciones."""
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        rows = catalog._conn.execute(
            "SELECT rel_path, photo_hash, status, missing_since, size, mtime_ns "
            "FROM locations ORDER BY rel_path"
        ).fetchall()
        return {str(row["rel_path"]): dict(row) for row in rows}
    finally:
        catalog.close()


def read_photos(root: Path) -> dict[str, dict[str, object]]:
    """Lee las fotos del catálogo para las aserciones."""
    catalog = Catalog.open(root / ".foto" / "catalog.sqlite")
    try:
        rows = catalog._conn.execute("SELECT * FROM photos ORDER BY hash").fetchall()
        return {str(row["hash"]): dict(row) for row in rows}
    finally:
        catalog.close()
