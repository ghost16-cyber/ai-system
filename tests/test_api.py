from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["service"] == "ai-system-backend"
    assert data["version"] == "0.2.0"
    assert "timestamp" in data


def test_analyze_endpoint_detects_inefficient_loop():
    response = client.post(
        "/analyze",
        json={
            "code": "for i in range(len(arr)):\n    print(arr[i])",
            "language": "python",
            "filename": "demo.py",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["success"] is True
    assert data["language"] == "python"
    assert data["filename"] == "demo.py"
    assert len(data["issues"]) == 1
    assert data["issues"][0]["type"] == "inefficient_loop"
    assert data["metadata"]["engine"] == "trained-code-analyzer"


def test_analyze_endpoint_rejects_empty_code():
    response = client.post(
        "/analyze",
        json={
            "code": "",
            "language": "python",
        },
    )

    assert response.status_code == 422