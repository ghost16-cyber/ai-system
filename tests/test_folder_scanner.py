from pathlib import Path

from backend.app.folders.scanner import build_inventory


def test_windows_zone_identifier_is_ignored_metadata(tmp_path: Path) -> None:
    project_file = tmp_path / "household_power_consumption.csv"
    download_metadata = tmp_path / "household_power_consumption.csv:Zone.Identifier"
    project_file.write_text("timestamp,value\n1,2\n", encoding="utf-8")
    download_metadata.write_text("[ZoneTransfer]\nZoneId=3\n", encoding="utf-8")

    scan = build_inventory(tmp_path)
    by_path = {item["relative_path"]: item for item in scan["inventory"]}

    assert by_path[project_file.name]["status"] == "readable"
    marker = by_path[download_metadata.name]
    assert marker["status"] == "ignored"
    assert marker["classification"] == "ignored"
    assert marker["ignore_reason"] == "windows_download_metadata"
    assert scan["summary"]["total_discovered"] == 2
    assert scan["summary"]["readable"] == 1
    assert scan["summary"]["datasets"] == 1
    assert scan["summary"]["ignored"] == 1


def test_zone_identifier_match_is_case_insensitive_and_does_not_read_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marker = tmp_path / "report.pdf:zone.identifier"
    marker.write_bytes(b"metadata that must not become a readable project file")

    def content_read_must_not_run(*args, **kwargs):
        raise AssertionError("Zone.Identifier content was read")

    monkeypatch.setattr(Path, "read_bytes", content_read_must_not_run)
    monkeypatch.setattr(Path, "read_text", content_read_must_not_run)

    scan = build_inventory(tmp_path)

    assert len(scan["inventory"]) == 1
    item = scan["inventory"][0]
    assert item["classification"] == "ignored"
    assert item["status"] == "ignored"
    assert item["ignore_reason"] == "windows_download_metadata"
    assert scan["summary"]["readable"] == 0
    assert scan["summary"]["ignored"] == 1
