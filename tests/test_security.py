from fastapi.testclient import TestClient


def test_foreign_host_rejected():
    from torre.main import app
    c = TestClient(app, base_url="http://evil.example")
    assert c.get("/").status_code == 403


def test_cross_origin_write_rejected(client):
    r = client.post("/api/boards", json={"name": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/boards", json={"name": "x"}, headers={"Origin": "http://127.0.0.1"})
    assert r.status_code == 201


def test_token_required(monkeypatch):
    from torre import main
    monkeypatch.setattr(main, "ACCESS_TOKEN", "secreto")
    c = TestClient(main.app, base_url="http://127.0.0.1")
    assert c.get("/").status_code == 403
    r = c.get("/?token=secreto", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/").status_code == 200        # ya con la cookie
