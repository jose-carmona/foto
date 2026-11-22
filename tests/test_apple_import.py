"""Importación de favoritos y caras desde Photos.sqlite (ADR 0009, paso 3).

Cubre T3.3 (extractor read-only), T3.4 (fase ``apple-import`` del pipeline)
y T3.5 (vínculo e idempotencia) sobre una biblioteca Apple sintética con
una ``Photos.sqlite`` real construida por ``write_photos_sqlite``
(T3.1). La metainformación vive siempre fuera del paquete.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from foto.core.errors import ApplePhotosError
from foto.core.indexing import Indexer, IndexingOptions, IndexingSummary
from foto.core.scanner import LibraryScanner, apple_photos_scan_policy
from foto.indexer.cli import main
from foto.storage.apple_photos import (
    ApplePerson,
    ApplePhotosLibrary,
    apple_uuid_from_rel_path,
)
from foto.storage.catalog import Catalog
from foto.storage.config import Location, load_config, save_config
from foto.storage.tags import toggle_favorite
from tests.conftest import write_image, write_photos_sqlite

UUID_JPEG = "1A2B3C4D-5E6F-4A8B-9C0D-1E2F3A4B5C6D"
UUID_PNG = "ABCDEF01-2345-4678-9ABC-DEF012345678"
UUID_VIDEO = "CCCCCCCC-3333-4444-8888-999999999999"

LATER = "2026-11-22T12:00:00+00:00"

ORIGINAL_JPEG = f"originals/1/{UUID_JPEG}.jpeg"
ORIGINAL_PNG = f"originals/A/{UUID_PNG}.png"
VARIANT_JPEG = f"originals/1/{UUID_JPEG}_1.jpeg"

NOMBRE_PERSONA = "María García"


def _apple_scanner_factory(root: Path) -> LibraryScanner:
    return LibraryScanner(root, policy=apple_photos_scan_policy())


def _build_package(tmp_path: Path, *, with_database: bool = True) -> Path:
    """Biblioteca Apple sintética: dos originales, variante y derivados."""
    root = tmp_path / "Fotos.photoslibrary"
    write_image(root / ORIGINAL_JPEG, b"asset-base-jpeg")
    write_image(root / ORIGINAL_PNG, b"asset-base-png")
    write_image(root / VARIANT_JPEG, b"asset-editado")
    write_image(root / "resources" / "renders" / "1" / "render.jpeg", b"render")
    if with_database:
        write_photos_sqlite(
            root / "database" / "Photos.sqlite",
            assets=[
                # Favorita con una cara nombrada y una sin nombre.
                (UUID_JPEG, 1, [(NOMBRE_PERSONA, 0.5, 0.5, 0.2),
                                (None, 0.2, 0.8, 0.1)]),
                # Sin favorito y sin caras: solo vínculo.
                (UUID_PNG, 0, []),
                # Favorito que el catálogo no tiene (vídeo no indexado):
                # cuenta como no resuelto.
                (UUID_VIDEO, 1, []),
            ],
            album="Familia",
        )
    return root


def _run_index(
    root: Path,
    *,
    options: IndexingOptions | None = None,
    scanner=_apple_scanner_factory,
    apple_library_factory: type[ApplePhotosLibrary] | None = ApplePhotosLibrary,
) -> IndexingSummary:
    """Indexa con metainformación externa, como manda el ADR 0009."""
    metadata = root.parent / "metainfo"
    metadata.mkdir(parents=True, exist_ok=True)
    declared = (Location("default", root, "fast"),)
    catalog = Catalog.open(metadata / "catalog.sqlite", declared_locations=declared)
    try:
        return Indexer(
            catalog,
            declared,
            options=options or IndexingOptions(),
            scanner=scanner,
            apple_library_factory=apple_library_factory,
        ).run()
    finally:
        catalog.close()


def _open_catalog(root: Path) -> Catalog:
    return Catalog.open(root.parent / "metainfo" / "catalog.sqlite")


def _tags_state(root: Path) -> list[tuple[str, str, str, tuple | None]]:
    """Asignaciones ``(nombre, kind, source, región)`` del catálogo."""
    catalog = _open_catalog(root)
    try:
        rows = catalog._conn.execute(
            "SELECT t.name, t.kind, pt.source, pt.status, "
            "pt.region_x, pt.region_y, pt.region_w, pt.region_h "
            "FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id ORDER BY t.name"
        ).fetchall()
        return [
            (
                str(row["name"]),
                str(row["kind"]),
                str(row["source"]),
                None if row["region_x"] is None
                else (float(row["region_x"]), float(row["region_y"]),
                      float(row["region_w"]), float(row["region_h"])),
            )
            for row in rows
        ]
    finally:
        catalog.close()


def _apple_uuids(root: Path) -> dict[str, str]:
    catalog = _open_catalog(root)
    try:
        rows = catalog._conn.execute(
            "SELECT apple_uuid, hash FROM photos WHERE apple_uuid IS NOT NULL"
        ).fetchall()
        return {str(row["apple_uuid"]): str(row["hash"]) for row in rows}
    finally:
        catalog.close()


# ----------------------------------------------------------------------
# T3.3 — Extractor read-only
# ----------------------------------------------------------------------
def test_extractor_maps_favorites_faces_and_counts_unnamed(tmp_path):
    root = _build_package(tmp_path)
    with ApplePhotosLibrary(root) as library:
        snapshot = library.read_snapshot()

    assert snapshot.unnamed_faces == 1
    assert set(snapshot.assets) == {UUID_JPEG, UUID_PNG, UUID_VIDEO}
    favorite = snapshot.assets[UUID_JPEG]
    assert favorite.favorite is True
    assert favorite.persons == (
        # x = 0.5 − 0.2/2 = 0.4; y = 0.4; w = h = 0.2 (ADR 0009, § 11).
        ApplePerson(
            name=NOMBRE_PERSONA, region_x=0.4, region_y=0.4,
            region_w=0.2, region_h=0.2,
        ),
    )
    assert snapshot.assets[UUID_PNG].favorite is False
    assert snapshot.assets[UUID_PNG].persons == ()
    assert snapshot.assets[UUID_VIDEO].favorite is True


def test_extractor_ignores_albums_titles_and_descriptions(tmp_path):
    """La fixture trae álbum, títulos y descripciones; nada se mapea."""
    root = _build_package(tmp_path)
    with ApplePhotosLibrary(root) as library:
        snapshot = library.read_snapshot()

    serialized = repr(snapshot)
    assert "Familia" not in serialized
    assert "Título" not in serialized
    assert "Descripción" not in serialized


def test_extractor_is_strictly_read_only(tmp_path):
    """La lectura no crea ``-wal``/``-shm`` ni toca el fichero del paquete."""
    root = _build_package(tmp_path)
    database = root / "database" / "Photos.sqlite"
    before = database.stat()

    with ApplePhotosLibrary(root) as library:
        library.read_snapshot()

    after = database.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
    assert not (root / "database" / "Photos.sqlite-wal").exists()
    assert not (root / "database" / "Photos.sqlite-shm").exists()
    assert not (root / "database" / "Photos.sqlite-journal").exists()


def test_extractor_fails_without_the_database(tmp_path):
    root = _build_package(tmp_path, with_database=False)
    with ApplePhotosLibrary(root) as library, pytest.raises(ApplePhotosError):
        library.read_snapshot()


def test_extractor_fails_with_an_unexpected_model(tmp_path):
    """Una versión sin las columnas esperadas es un error explícito."""
    root = _build_package(tmp_path)
    database = root / "database" / "Photos.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP TABLE ZASSET")
        connection.execute(
            "CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT)"
        )
        connection.commit()
    finally:
        connection.close()

    with ApplePhotosLibrary(root) as library, pytest.raises(ApplePhotosError):
        library.read_snapshot()


def test_asset_uuid_from_rel_path():
    assert apple_uuid_from_rel_path(ORIGINAL_JPEG) == UUID_JPEG
    assert apple_uuid_from_rel_path("originals/1/1a2b3c4d-5e6f-4a8b-9c0d"
                                    "-1e2f3a4b5c6d.jpeg") == UUID_JPEG
    # Variantes editadas y ficheros sueltos no son assets indexados.
    assert apple_uuid_from_rel_path(VARIANT_JPEG) is None
    assert apple_uuid_from_rel_path("2024/vacaciones.jpeg") is None


# ----------------------------------------------------------------------
# T3.4 — Fase apple-import
# ----------------------------------------------------------------------
def test_phase_imports_favorites_and_persons(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root, apple_library_factory=None)  # escaneo sin importación

    summary = _run_index(root, options=IndexingOptions())

    apple = summary.apple_import
    assert apple.status == "ok"
    assert apple.favorites_imported == 1
    assert apple.persons_imported == 1
    assert apple.unresolved == 1  # el favorito del vídeo
    assert apple.errors == 0
    assert summary.status == "ok"

    uuids = _apple_uuids(root)
    assert set(uuids) == {UUID_JPEG, UUID_PNG}

    # Solo lo debido: la persona sugerida con región y el fav importado
    # (confirmed); ni la cara sin nombre ni el álbum crean nada. El orden
    # es el del catálogo (ORDER BY t.name).
    assert _tags_state(root) == [
        (NOMBRE_PERSONA, "person", "import", (0.4, 0.4, 0.2, 0.2)),
        ("fav", "system", "import", None),
    ]


def test_phase_is_recorded_in_run_phases(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)
    summary = _run_index(root)

    catalog = _open_catalog(root)
    try:
        run_id = catalog._conn.execute(
            "SELECT MAX(id) FROM scan_runs"
        ).fetchone()[0]
        rows = catalog._conn.execute(
            "SELECT phase, status FROM run_phases WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    finally:
        catalog.close()
    assert [(str(r["phase"]), str(r["status"])) for r in rows] == [
        ("scan", "ok"),
        ("geocode", "skipped"),
        ("apple-import", "ok"),
    ]
    assert summary.to_dict()["phases"]["apple-import"]["status"] == "ok"


def test_phase_without_factory_is_skipped_with_reason(tmp_path):
    root = _build_package(tmp_path)
    summary = _run_index(root, apple_library_factory=None)

    apple = summary.apple_import
    assert apple.status == "skipped"
    assert "[apple_photos]" in apple.reason
    assert summary.status == "ok"
    assert _tags_state(root) == []


def test_phase_without_apple_locations_is_skipped(tmp_path):
    root = tmp_path / "biblioteca-normal"
    write_image(root / "a.jpg", b"a")
    summary = _run_index(root, scanner=lambda path: LibraryScanner(path))

    apple = summary.apple_import
    assert apple.status == "skipped"
    assert "Ninguna ubicación" in apple.reason


def test_phase_respects_skip_and_dry_run(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)

    summary = _run_index(
        root, options=IndexingOptions(skip=frozenset({"apple-import"}))
    )
    assert summary.apple_import.status == "skipped"
    assert "--skip apple-import" in summary.apple_import.reason

    summary = _run_index(root, options=IndexingOptions(dry_run=True))
    assert summary.apple_import.status == "skipped"
    assert summary.apple_import.favorites_imported == 0


def test_phase_error_with_missing_database_does_not_abort(tmp_path):
    """Falta Photos.sqlite: fase en error explícito, la pasada sigue."""
    root = _build_package(tmp_path, with_database=False)
    _run_index(root)

    summary = _run_index(root)

    apple = summary.apple_import
    assert apple.status == "error"
    assert "Photos.sqlite" in apple.reason
    assert apple.errors == 1
    # No aborta la pasada: el escaneo quedó ok y el estado no es fatal.
    assert summary.scan_phase.status == "ok"
    assert summary.status == "partial"


def test_phase_interruption_is_recorded(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)

    class Interrupted(ApplePhotosLibrary):
        def read_snapshot(self):
            raise KeyboardInterrupt

    declared = (Location("default", root, "fast"),)
    catalog = _open_catalog(root)
    try:
        with pytest.raises(KeyboardInterrupt):
            Indexer(
                catalog,
                declared,
                options=IndexingOptions(),
                scanner=_apple_scanner_factory,
                apple_library_factory=Interrupted,
            ).run()
        rows = catalog._conn.execute(
            "SELECT phase, status, reason FROM run_phases ORDER BY id"
        ).fetchall()
    finally:
        catalog.close()
    statuses = {str(row["phase"]): str(row["status"]) for row in rows}
    assert statuses["apple-import"] == "partial"
    assert statuses["geocode"] == "skipped"


# ----------------------------------------------------------------------
# T3.5 — Vínculo e idempotencia
# ----------------------------------------------------------------------
def test_double_pass_is_idempotent(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)
    first = _run_index(root)
    state_before = (_tags_state(root), _apple_uuids(root))

    second = _run_index(root)

    assert first.apple_import.status == "ok"
    assert second.apple_import.favorites_imported == 0
    assert second.apple_import.persons_imported == 0
    assert second.apple_import.unresolved == 1
    assert (_tags_state(root), _apple_uuids(root)) == state_before


def test_unfavored_imported_favorite_survives_the_next_pass(tmp_path):
    """El dueño quita con ``f`` el favorito importado: no vuelve a entrar.

    La eliminación deja una fila ``manual``/``rejected`` (ADR 0006); la
    importación no revive filas rechazadas. Volver a conmutar borra la
    fila ``rejected`` y nace la asignación manual.
    """
    root = _build_package(tmp_path)
    _run_index(root, apple_library_factory=None)
    _run_index(root)  # importa el favorito de Apple

    catalog = _open_catalog(root)
    try:
        hash_jpeg = catalog._conn.execute(
            "SELECT photo_hash FROM photo_tags pt JOIN tags t "
            "ON t.id = pt.tag_id WHERE t.kind = 'system'"
        ).fetchone()["photo_hash"]
        assert toggle_favorite(catalog._conn, hash_jpeg, now=LATER) is False
    finally:
        catalog.close()

    # Apple sigue teniendo el favorito: no reviven filas rechazadas.
    summary = _run_index(root)
    assert summary.apple_import.favorites_imported == 0
    catalog = _open_catalog(root)
    try:
        rows = catalog._conn.execute(
            "SELECT source, status FROM photo_tags pt JOIN tags t "
            "ON t.id = pt.tag_id WHERE t.kind = 'system'"
        ).fetchall()
        assert [(str(r["source"]), str(r["status"])) for r in rows] == [
            ("manual", "rejected")
        ]
    finally:
        catalog.close()

    # El dueño vuelve a quererlo: asignación manual confirmed.
    catalog = _open_catalog(root)
    try:
        assert toggle_favorite(catalog._conn, hash_jpeg, now=LATER) is True
    finally:
        catalog.close()
    _run_index(root)
    catalog = _open_catalog(root)
    try:
        rows = catalog._conn.execute(
            "SELECT source, status FROM photo_tags pt JOIN tags t "
            "ON t.id = pt.tag_id WHERE t.kind = 'system'"
        ).fetchall()
        assert [(str(r["source"]), str(r["status"])) for r in rows] == [
            ("manual", "confirmed")
        ]
    finally:
        catalog.close()


def test_manual_favorite_is_never_overwritten(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)

    # El dueño quita el favorito importado y pone el suyo (source manual).
    catalog = _open_catalog(root)
    try:
        catalog._conn.execute(
            "UPDATE photo_tags SET source = 'manual' WHERE tag_id = "
            "(SELECT id FROM tags WHERE kind = 'system')"
        )
        catalog.commit()
    finally:
        catalog.close()

    summary = _run_index(root)

    assert summary.apple_import.favorites_imported == 0
    assert _tags_state(root) == [
        (NOMBRE_PERSONA, "person", "import", (0.4, 0.4, 0.2, 0.2)),
        ("fav", "system", "manual", None),
    ]


def test_favorite_removed_in_apple_revokes_import_only(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)
    assert ("fav", "system", "import", None) in _tags_state(root)

    # Apple retira el favorito y la cara: la segunda pasada lo revoca solo
    # en filas import.
    write_photos_sqlite(
        root / "database" / "Photos.sqlite",
        assets=[
            (UUID_JPEG, 0, []),
            (UUID_PNG, 0, []),
        ],
        album="Familia",
    )
    summary = _run_index(root)

    assert summary.apple_import.status == "ok"
    assert _tags_state(root) == []
    # El vínculo (apple_uuid) no se toca: es identidad del fichero.
    assert set(_apple_uuids(root)) == {UUID_JPEG, UUID_PNG}


def test_person_renamed_creates_new_tag_and_revokes_the_old(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)

    write_photos_sqlite(
        root / "database" / "Photos.sqlite",
        assets=[
            (UUID_JPEG, 1, [("María López", 0.5, 0.5, 0.2)]),
            (UUID_PNG, 0, []),
            (UUID_VIDEO, 1, []),
        ],
        album="Familia",
    )
    _run_index(root)

    assert _tags_state(root) == [
        ("María López", "person", "import", (0.4, 0.4, 0.2, 0.2)),
        ("fav", "system", "import", None),
    ]


def test_manual_person_assignment_survives_revocation(tmp_path):
    """Una cara puesta a mano no la revoca la importación ni la duplica."""
    root = _build_package(tmp_path)
    _run_index(root)
    # El dueño confirma la cara a mano (futuro :tag con confirmación): la
    # fila pasa a manual. Apple mantiene la cara.
    catalog = _open_catalog(root)
    try:
        catalog._conn.execute(
            "UPDATE photo_tags SET source = 'manual', status = 'confirmed' "
            "WHERE tag_id = (SELECT id FROM tags WHERE "
            f"name_normalized = '{NOMBRE_PERSONA.casefold()}')"
        )
        catalog.commit()
    finally:
        catalog.close()

    summary = _run_index(root)

    assert summary.apple_import.persons_imported == 0
    assert _tags_state(root) == [
        (NOMBRE_PERSONA, "person", "manual", (0.4, 0.4, 0.2, 0.2)),
        ("fav", "system", "import", None),
    ]

    # Apple retira la cara: la fila manual sigue, la importada se iba.
    write_photos_sqlite(
        root / "database" / "Photos.sqlite",
        assets=[(UUID_JPEG, 1, []), (UUID_PNG, 0, []), (UUID_VIDEO, 1, [])],
        album="Familia",
    )
    _run_index(root)
    assert _tags_state(root) == [
        (NOMBRE_PERSONA, "person", "manual", (0.4, 0.4, 0.2, 0.2)),
        ("fav", "system", "import", None),
    ]


def test_unresolved_assets_are_counted_without_error(tmp_path):
    root = _build_package(tmp_path)
    _run_index(root)

    summary = _run_index(root)

    assert summary.apple_import.unresolved == 1
    assert summary.apple_import.errors == 0
    # El asset no resuelto no creó vínculo ni etiqueta en el catálogo.
    assert UUID_VIDEO not in _apple_uuids(root)


# ----------------------------------------------------------------------
# T3.6 — Configuración y CLI
# ----------------------------------------------------------------------
def _cli_config(tmp_path: Path, root: Path, *, apple: bool = False) -> Path:
    config = tmp_path / "config.toml"
    section = "\n[apple_photos]\nenabled = true\n" if apple else ""
    config.write_text(
        f'version = 1\n\n[library]\npath = "{root.as_posix()}"\n{section}',
        encoding="utf-8",
    )
    return config


def _cli_args(tmp_path: Path, root: Path, *extra: str) -> list[str]:
    return [
        "--config", str(tmp_path / "config.toml"),
        "--metadata", str(tmp_path / "metainfo"),
        *extra,
    ]


def test_cli_imports_when_the_section_is_enabled(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = _build_package(tmp_path)
    _cli_config(tmp_path, root, apple=True)

    code = main(_cli_args(tmp_path, root))
    captured = capsys.readouterr()

    assert code == 0
    assert "apple-import: favoritos=1 personas=1 no_resueltos=1 errores=0" in captured.out


def test_cli_without_the_section_skips_the_phase(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = _build_package(tmp_path)
    _cli_config(tmp_path, root)

    code = main(_cli_args(tmp_path, root, "--json"))
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert code == 0
    apple = summary["phases"]["apple-import"]
    assert apple["status"] == "skipped"
    assert "[apple_photos]" in apple["reason"]
    assert "enabled = false" in captured.err

    # En texto (sin --json) la fase omitida se reporta con su motivo.
    code = main(_cli_args(tmp_path, root))
    captured = capsys.readouterr()
    assert code == 0
    assert (
        "apple-import: omitida (Importación de Apple no activada "
        "([apple_photos] enabled = true))" in captured.out
    )


def test_cli_respects_skip_apple_import(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FOTO_METADATA", raising=False)
    root = _build_package(tmp_path)
    _cli_config(tmp_path, root, apple=True)

    code = main(_cli_args(tmp_path, root, "--skip", "apple-import"))
    output = capsys.readouterr()

    assert code == 0
    assert "apple-import: omitida (Excluida con --skip apple-import)" in output.out


def test_cli_invalid_apple_section_exits_2(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        "[apple_photos]\nenabled = \"sí\"\n", encoding="utf-8"
    )

    code = main(["--config", str(config)])

    assert code == 2
    assert "enabled" in capsys.readouterr().err


def test_config_section_defaults_to_disabled():
    config = load_config(_missing_path := Path("/no/existe/config.toml"))
    assert config.apple_photos.enabled is False


def test_config_section_reads_enabled():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.toml"
        path.write_text("[apple_photos]\nenabled = true\n", encoding="utf-8")
        config = load_config(path)
    assert config.apple_photos.enabled is True


def test_save_config_never_writes_the_apple_section(tmp_path):
    """El canonical de save_config no incluye ``[apple_photos]`` (§ 13)."""
    path = tmp_path / "config.toml"
    path.write_text(
        'version = 1\n\n[library]\npath = "/fotos"\n'
        "\n[apple_photos]\nenabled = true\n",
        encoding="utf-8",
    )
    config = load_config(path)
    save_config(config)

    content = path.read_text(encoding="utf-8")
    assert "[apple_photos]" in content
    assert "enabled = true" in content
