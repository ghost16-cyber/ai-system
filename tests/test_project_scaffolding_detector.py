from __future__ import annotations

from pathlib import Path

from backend.app.project_scaffolding.detector import detect_scaffold_context


def test_detects_fastapi_repository(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")

    result = detect_scaffold_context(tmp_path)

    assert "fastapi" in result.frameworks
    assert result.suggested_category == "fastapi_feature_module"


def test_detects_react_typescript_repository(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.0.0"}}', encoding="utf-8"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.tsx").write_text("export const App = () => null;\n", encoding="utf-8")

    result = detect_scaffold_context(tmp_path)

    assert "react" in result.frameworks
    assert "typescript" in result.languages
    assert result.suggested_category == "react_ts_feature_component"


def test_ambiguous_or_empty_repository_suggests_no_category(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# empty project\n", encoding="utf-8")

    result = detect_scaffold_context(tmp_path)

    assert result.frameworks == ()
    assert result.suggested_category is None


def test_nonexistent_repository_root_returns_empty_result(tmp_path: Path) -> None:
    result = detect_scaffold_context(tmp_path / "does-not-exist")

    assert result.frameworks == ()
    assert result.languages == ()
    assert result.suggested_category is None
