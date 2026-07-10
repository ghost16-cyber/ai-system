from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.path_utils import (
    normalize_path_for_platform,
    resolve_user_path,
    windows_path_to_wsl,
)


def test_windows_backslash_path_converts_to_wsl_suggestion():
    assert windows_path_to_wsl(r"C:\Users\palla\Desktop\file.csv") == "/mnt/c/Users/palla/Desktop/file.csv"
    normalized = normalize_path_for_platform(r"C:\Users\palla\Desktop\file.csv")
    assert normalized.suggested_path == "/mnt/c/Users/palla/Desktop/file.csv"


def test_windows_forward_slash_path_converts_to_wsl_suggestion():
    assert windows_path_to_wsl("C:/Users/palla/Desktop/file.csv") == "/mnt/c/Users/palla/Desktop/file.csv"
    normalized = normalize_path_for_platform("C:/Users/palla/Desktop/file.csv")
    assert normalized.suggested_path == "/mnt/c/Users/palla/Desktop/file.csv"


def test_linux_path_remains_valid(tmp_path: Path):
    dataset = tmp_path / "events.csv"
    dataset.write_text("timestamp,value\n2026-01-01,1\n", encoding="utf-8")

    resolved = resolve_user_path(dataset, base_root=tmp_path, expected="file", supported_extensions={".csv"}, label="Dataset file")

    assert resolved == dataset.resolve()


def test_traversal_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="traversal"):
        resolve_user_path("../secret.csv", base_root=tmp_path, expected="file", label="Dataset file")


def test_folder_used_as_dataset_file_returns_clear_error(tmp_path: Path):
    folder = tmp_path / "data"
    folder.mkdir()

    with pytest.raises(ValueError, match="folder"):
        resolve_user_path(folder, base_root=tmp_path, expected="file", supported_extensions={".csv", ".txt", ".tsv"}, label="Dataset file")


def test_outside_root_path_returns_clear_error(tmp_path: Path):
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("x\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the allowed workspace root"):
        resolve_user_path(outside, base_root=tmp_path, expected="file", label="Dataset file")


def test_path_resolution_output_is_deterministic(tmp_path: Path):
    dataset = tmp_path / "events.csv"
    dataset.write_text("timestamp,value\n2026-01-01,1\n", encoding="utf-8")

    first = str(resolve_user_path("events.csv", base_root=tmp_path, expected="file", label="Dataset file"))
    second = str(resolve_user_path("events.csv", base_root=tmp_path, expected="file", label="Dataset file"))

    assert first == second
