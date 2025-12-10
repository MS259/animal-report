from datetime import datetime

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_create_report():
    payload = {
        "type": "dead",
        "latitude": 52.0,
        "longitude": 1.0,
        "timestamp": datetime.utcnow().isoformat(),
    }
    resp = client.post("/report", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "id" in data


def test_invalid_type():
    payload = {
        "type": "something_else",
        "latitude": 52.0,
        "longitude": 1.0,
        "timestamp": datetime.utcnow().isoformat(),
    }
    resp = client.post("/report", json=payload)
    assert resp.status_code == 422  # validation error


def test_create_and_list_reports():
    # Create a new report
    payload = {
        "type": "injured",
        "latitude": 51.5,
        "longitude": 0.1,
        "timestamp": datetime.utcnow().isoformat(),
    }
    resp = client.post("/report", json=payload)
    assert resp.status_code == 200

    # List reports
    resp2 = client.get("/reports?limit=50")
    assert resp2.status_code == 200
    data = resp2.json()
    assert isinstance(data, list)
    assert any(item["type"] in ["dead", "injured"] for item in data)
