from backend.app.project_retrieval.smoke import run_smoke


def test_disposable_rag_smoke() -> None:
    result = run_smoke()
    assert result["status"] == "ok"
    assert result["exact_replay"] is True
    assert result["stale_phase5b_rejected"] is True
