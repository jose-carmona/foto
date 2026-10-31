"""Tests del modelo de metadatos y del adaptador de exiftool."""

from __future__ import annotations

import json
import subprocess

from foto.core.metadata import map_exiftool_record, normalize_datetime
from foto.storage.exiftool import ExifToolExtractor
from tests.conftest import write_image


def test_map_exiftool_record_normalizes_fields():
    metadata = map_exiftool_record(
        {
            "FileType": "JPEG",
            "ImageWidth": 6000,
            "ImageHeight": 4000,
            "DateTimeOriginal": "2024:05:01 12:34:56",
            "Make": "Fujifilm",
            "Model": "X-T5",
            "LensModel": "XF 35mm F1.4",
            "ISO": 400,
            "FNumber": "2.8",
            "ExposureTime": "1/250",
            "FocalLength": 35,
            "GPSLatitude": 40.4168,
            "GPSLatitudeRef": "South",
            "GPSLongitude": 3.7038,
            "GPSLongitudeRef": "West",
            "Orientation": 6,
        }
    )

    assert metadata.file_type == "JPEG"
    assert metadata.width == 6000
    assert metadata.height == 4000
    assert metadata.captured_at == "2024-05-01T12:34:56"
    assert metadata.camera_make == "Fujifilm"
    assert metadata.camera_model == "X-T5"
    assert metadata.lens_model == "XF 35mm F1.4"
    assert metadata.iso == 400
    assert metadata.f_number == 2.8
    assert metadata.exposure_time == 0.004
    assert metadata.focal_length == 35.0
    assert metadata.gps_latitude == -40.4168
    assert metadata.gps_longitude == -3.7038
    assert metadata.orientation == 6


def test_missing_fields_are_none_not_empty_strings():
    metadata = map_exiftool_record({})

    assert metadata.file_type is None
    assert metadata.captured_at is None
    assert metadata.camera_make is None
    assert metadata.iso is None
    assert metadata.gps_latitude is None
    assert metadata.width is None
    assert metadata.height is None
    assert metadata.f_number is None
    assert metadata.exposure_time is None
    assert metadata.focal_length is None


def test_datetime_normalization_and_invalid_dates():
    assert normalize_datetime("2024:05:01 12:34:56") == "2024-05-01T12:34:56"
    assert (
        normalize_datetime("2024:05:01 12:34:56+02:00")
        == "2024-05-01T12:34:56+02:00"
    )
    assert normalize_datetime("2024-05-01T12:34:56Z") == "2024-05-01T12:34:56+00:00"
    assert normalize_datetime("0000:00:00 00:00:00") is None
    assert normalize_datetime("no es una fecha") is None
    assert normalize_datetime(None) is None


def test_gps_sign_and_image_size_fallback():
    positive = map_exiftool_record(
        {
            "GPSLatitude": 40.0,
            "GPSLatitudeRef": "North",
            "GPSLongitude": 3.0,
            "GPSLongitudeRef": "East",
            "ImageSize": "8000x6000",
        }
    )
    assert positive.gps_latitude == 40.0
    assert positive.gps_longitude == 3.0
    assert positive.width == 8000
    assert positive.height == 6000

    negative = map_exiftool_record(
        {"GPSLatitude": 40.0, "GPSLatitudeRef": "S", "GPSLongitude": 3.0}
    )
    assert negative.gps_latitude == -40.0
    assert negative.gps_longitude == 3.0


def test_exiftool_adapter_maps_records_and_per_file_errors(tmp_path, monkeypatch):
    good = write_image(tmp_path / "buena.jpg", b"buena")
    bad = write_image(tmp_path / "mala.jpg", b"mala")

    def fake_run(command, *, capture_output, text, check):
        assert command[0] == "/usr/bin/exiftool"
        assert "-json" in command and "-n" in command
        payload = [
            {
                "SourceFile": str(good),
                "FileType": "JPEG",
                "DateTimeOriginal": "2024:05:01 12:34:56",
            },
            {"SourceFile": str(bad), "Error": "File not found"},
        ]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("foto.storage.exiftool.subprocess.run", fake_run)
    extractor = ExifToolExtractor("/usr/bin/exiftool")
    outcomes = extractor.extract_batch([good, bad])

    assert outcomes[good].error is None
    assert outcomes[good].metadata is not None
    assert outcomes[good].metadata.captured_at == "2024-05-01T12:34:56"
    assert outcomes[bad].metadata is None
    assert outcomes[bad].error == "File not found"


def test_exiftool_adapter_splits_batches(tmp_path, monkeypatch):
    paths = [write_image(tmp_path / f"{index}.jpg", bytes([index])) for index in range(5)]
    commands: list[list[str]] = []

    def fake_run(command, *, capture_output, text, check):
        commands.append(list(command))
        selected = [argument for argument in command if argument.endswith(".jpg")]
        payload = [
            {"SourceFile": argument, "FileType": "JPEG"} for argument in selected
        ]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("foto.storage.exiftool.subprocess.run", fake_run)
    extractor = ExifToolExtractor("/usr/bin/exiftool", batch_size=2)
    outcomes = extractor.extract_batch(paths)

    assert len(commands) == 3
    assert all(outcome.metadata is not None for outcome in outcomes.values())


def test_exiftool_adapter_reports_invalid_json(tmp_path, monkeypatch):
    path = write_image(tmp_path / "foto.jpg", b"foto")

    def fake_run(command, *, capture_output, text, check):
        return subprocess.CompletedProcess(command, 1, "no es json", "error")

    monkeypatch.setattr("foto.storage.exiftool.subprocess.run", fake_run)
    outcomes = ExifToolExtractor("/usr/bin/exiftool").extract_batch([path])

    assert outcomes[path].metadata is None
    assert "JSON" in (outcomes[path].error or "")


def test_exiftool_adapter_missing_metadata_for_file(tmp_path, monkeypatch):
    path = write_image(tmp_path / "foto.jpg", b"foto")

    def fake_run(command, *, capture_output, text, check):
        return subprocess.CompletedProcess(command, 0, "[]", "")

    monkeypatch.setattr("foto.storage.exiftool.subprocess.run", fake_run)
    outcomes = ExifToolExtractor("/usr/bin/exiftool").extract_batch([path])

    assert outcomes[path].metadata is None
    assert outcomes[path].error is not None


def test_exiftool_error_does_not_leak_absolute_paths(tmp_path, monkeypatch):
    path = write_image(tmp_path / "privada" / "foto.jpg", b"foto")

    def fake_run(command, *, capture_output, text, check):
        payload = [{"SourceFile": str(path), "Error": f"Error opening {path}"}]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("foto.storage.exiftool.subprocess.run", fake_run)
    outcome = ExifToolExtractor("/usr/bin/exiftool").extract_batch([path])[path]

    assert outcome.error == "Error opening foto.jpg"
    assert str(tmp_path) not in (outcome.error or "")
