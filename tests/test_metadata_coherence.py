"""Coherencia de las rutas de metainformación entre los cuatro puntos de entrada.

Paso 1 del ADR 0008 (estado actual): ``foto-index``, ``foto-export``,
``foto-import`` y la UI (vía los helpers de ``storage/library.py``) derivan
la misma ``.foto/`` a partir de la misma raíz de fototeca. El paso 2 solo
tendrá que cambiar el directorio derivado por el resuelto.
"""

from __future__ import annotations

import json

from foto.indexer.cli import main as index_main
from foto.storage.catalog import CATALOG_FILE_NAME
from foto.storage.library import foto_dir, library_file, thumbs_dir
from foto.storage.lock import LOCK_FILE_NAME
from foto.transfer.cli_export import main as export_main
from foto.transfer.cli_import import main as import_main
from foto.ui.app import load_photos
from tests.conftest import write_image


def test_the_four_entry_points_derive_the_same_metadata_dir(tmp_path, capsys):
    root = tmp_path / "biblioteca"
    write_image(root / "2026" / "una.jpg", b"coherencia")
    # Config inexistente: la suite no debe depender de la del usuario.
    config = tmp_path / "config.toml"
    derived = foto_dir(root)

    # foto-index crea el directorio de metainformación derivado.
    code = index_main(
        [
            "--library",
            str(root),
            "--config",
            str(config),
            "--skip",
            "geocode",
            "--json",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "ok"

    assert sorted(path.name for path in root.iterdir()) == [".foto", "2026"]
    assert (derived / CATALOG_FILE_NAME).is_file()
    assert (derived / LOCK_FILE_NAME).is_file()
    assert library_file(root) == derived / "library.toml"
    assert library_file(root).is_file()
    assert thumbs_dir(root) == derived / "thumbs"

    # foto-export lee el catálogo de esa misma .foto.
    out_dir = tmp_path / "salida"
    code = export_main(
        ["--library", str(root), "--config", str(config), "--out", str(out_dir)]
    )
    assert code == 0
    capsys.readouterr()
    assert (out_dir / "places.json").is_file()

    # foto-import valida contra el catálogo de esa misma .foto (dry-run).
    code = import_main(
        [
            "--library",
            str(root),
            "--config",
            str(config),
            "--places",
            str(out_dir / "places.json"),
            "--dry-run",
        ]
    )
    assert code == 0
    capsys.readouterr()

    # La UI, vía los helpers, consume el mismo catálogo sin crear nada.
    photos = load_photos(root)
    assert len(photos) == 1
    assert photos[0].name == "una.jpg"
    assert sorted(path.name for path in root.iterdir()) == [".foto", "2026"]
