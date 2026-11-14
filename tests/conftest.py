import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    """Cada test con su propia base de datos vacía."""
    monkeypatch.setenv("TORRE_DB", str(tmp_path / "torre.db"))


@pytest.fixture
def client():
    from torre.main import app
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def board(client):
    """Un tablero nuevo (con sus tres listas iniciales) ya cargado."""
    board_id = client.post("/api/boards", json={"name": "Casa"}).json()["id"]
    return client.get(f"/api/boards/{board_id}").json()
