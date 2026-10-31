"""Adaptador de solo lectura sobre ``exiftool``."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from foto.core.metadata import (
    ExtractionOutcome,
    MetadataExtractor,
    map_exiftool_record,
)

logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
DEFAULT_BATCH_SIZE = 100


def _sanitize_error(message: str, path: Path) -> str:
    """Quita rutas absolutas del mensaje para no filtrar la ubicación."""
    sanitized = message
    candidates = {str(path)}
    try:
        candidates.add(str(path.resolve()))
    except OSError:
        pass
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate:
            sanitized = sanitized.replace(candidate, path.name)
    return sanitized


class ExifToolExtractor(MetadataExtractor):
    """Extractor por lotes que invoca ``exiftool`` sin shell y en solo lectura."""

    name = "exiftool"

    def __init__(self, executable: str, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size debe ser positivo")
        self.executable = executable
        self.batch_size = batch_size

    @classmethod
    def from_environment(cls) -> ExifToolExtractor | None:
        """Devuelve un extractor si ``exiftool`` está instalado."""
        executable = shutil.which("exiftool")
        if executable is None:
            return None
        return cls(executable)

    def extract_batch(self, paths: Sequence[Path]) -> dict[Path, ExtractionOutcome]:
        outcomes: dict[Path, ExtractionOutcome] = {}
        for start in range(0, len(paths), self.batch_size):
            batch = list(paths[start : start + self.batch_size])
            outcomes.update(self._extract_small_batch(batch))
        return outcomes

    def _extract_small_batch(self, paths: Sequence[Path]) -> dict[Path, ExtractionOutcome]:
        if not paths:
            return {}

        command = [
            self.executable,
            "-json",
            "-n",
            "-q",
            "-q",
            "-d",
            DATE_FORMAT,
            *(str(path) for path in paths),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            message = f"No se pudo ejecutar exiftool: {exc.strerror or type(exc).__name__}"
            return {path: ExtractionOutcome(error=message) for path in paths}

        fallback_error = (
            f"exiftool terminó con código {result.returncode}"
            if result.returncode != 0
            else "exiftool no devolvió metadatos para el fichero"
        )

        try:
            payload: Any = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            logger.warning("exiftool devolvió una salida JSON inválida")
            return {
                path: ExtractionOutcome(error="exiftool devolvió una salida JSON inválida")
                for path in paths
            }

        if not isinstance(payload, list):
            return {
                path: ExtractionOutcome(error="exiftool devolvió un JSON inesperado")
                for path in paths
            }

        by_path: dict[str, Any] = {}
        by_resolved: dict[str, Any] = {}
        for record in payload:
            if not isinstance(record, dict):
                continue
            source = record.get("SourceFile")
            if not isinstance(source, str):
                continue
            by_path[source] = record
            try:
                by_resolved[str(Path(source).resolve())] = record
            except OSError:
                continue

        outcomes: dict[Path, ExtractionOutcome] = {}
        for path in paths:
            record = by_path.get(str(path))
            if record is None:
                try:
                    record = by_resolved.get(str(path.resolve()))
                except OSError:
                    record = None
            if record is None:
                outcomes[path] = ExtractionOutcome(error=fallback_error)
                continue

            record_error = record.get("Error")
            if isinstance(record_error, str) and record_error.strip():
                outcomes[path] = ExtractionOutcome(
                    error=_sanitize_error(record_error.strip(), path)
                )
                continue

            outcomes[path] = ExtractionOutcome(metadata=map_exiftool_record(record))
        return outcomes
