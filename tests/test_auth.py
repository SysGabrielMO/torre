"""Torre en un servidor: usuarios, inicio de sesión, límite de intentos y modo servidor."""
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from torre import auth, background, db, users

ORIGIN = {"Origin": "http://127.0.0.1"}
PASSWORD = "caballo-bateria-grapa"


@pytest.fixture
def server(monkeypatch):
    """Torre como en el contenedor: con contraseña y sin avisos."""
    monkeypatch.setenv("TORRE_AUTH", "1")
    monkeypatch.setenv("TORRE_NO_REMINDERS", "1")
    monkeypatch.setattr(auth, "throttle", auth.Throttle())
    from torre.main import app
    return TestClient(app, base_url="http://127.0.0.1", headers=ORIGIN)


@pytest.fixture
def gabriel():
    return auth.create_user("Gabriel", PASSWORD)


def login(client, username="gabriel", password=PASSWORD, next_url="/"):
    return client.post("/login", data={"username": username, "password": password, "next": next_url},
                       follow_redirects=False)


# --- Contraseñas y usuarios ---------------------------------------------------------

def test_password_hash():
    stored = auth.hash_password(PASSWORD)
    assert PASSWORD not in stored and stored.startswith("scrypt$")
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password(PASSWORD + "x", stored)
    assert auth.hash_password(PASSWORD) != stored          # sal distinta cada vez
    assert not auth.verify_password(PASSWORD, "basura")


def test_user_rules():
    assert auth.create_user("  Ana.G ", PASSWORD) == "ana.g"
    with pytest.raises(auth.AuthError, match="ya existe"):
        auth.create_user("ana.g", PASSWORD)
    with pytest.raises(auth.AuthError, match="10 caracteres"):
        auth.create_user("luis", "corta")
    with pytest.raises(auth.AuthError, match="igual que el usuario"):
        auth.create_user("luis-perez-1", "luis-perez-1")
    for bad in ("a", "con espacio", "../root", "x" * 40):
        with pytest.raises(auth.AuthError):
            auth.create_user(bad, PASSWORD)
    assert auth.list_users() == ["ana.g"]


def test_authenticate(gabriel):
    assert auth.authenticate("GABRIEL ", PASSWORD) == "gabriel"
    assert auth.authenticate("gabriel", "otra-cosa-larga") is None
    assert auth.authenticate("nadie", PASSWORD) is None


def test_cli(monkeypatch, capsys):
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(users.getpass, "getpass", lambda prompt: next(answers))
    assert users.main(["añadir", "gabriel"]) == 0
    assert auth.list_users() == ["gabriel"]

    answers = iter(["nueva-clave-larga", "no-coincide-nada"])
    assert users.main(["contraseña", "gabriel"]) == 1
    assert "no coinciden" in capsys.readouterr().err

    assert users.main(["lista"]) == 0 and "gabriel" in capsys.readouterr().out
    assert users.main(["borrar", "gabriel"]) == 0 and auth.list_users() == []
    assert users.main(["inventado"]) == 2


# --- Inicio de sesión ---------------------------------------------------------------

def test_everything_needs_login(server, gabriel):
    r = server.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = server.get("/b/1?x=1", follow_redirects=False)
    assert r.headers["location"] == "/login?next=/b/1%3Fx%3D1"
    assert server.get("/api/search?q=a").status_code == 401
    assert server.post("/api/boards", json={"name": "x"}).status_code == 401
    assert server.get("/static/style.css").status_code == 200   # la página de entrada necesita estilos
    assert "Contraseña" in server.get("/login").text


def test_login_page_without_users(server):
    assert "Todavía no hay ningún usuario" in server.get("/login").text


def test_login_and_logout(server, gabriel):
    r = login(server, next_url="/b/7")
    assert r.status_code == 303 and r.headers["location"] == "/b/7"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=2592000" in cookie
    assert "secure" not in cookie                     # por http (solo pruebas); por https, sí

    assert server.get("/").status_code == 200
    assert "gabriel · Salir" in server.get("/").text
    assert server.post("/api/boards", json={"name": "Casa"}).status_code == 201

    token = server.cookies.get("torre_session")
    server.post("/logout")
    server.cookies.set("torre_session", token)        # aunque alguien guardara la cookie…
    assert server.get("/api/search?q=a").status_code == 401   # …ya no sirve


def test_cookie_is_secure_over_https(monkeypatch, gabriel):
    monkeypatch.setenv("TORRE_AUTH", "1")
    from torre.main import app
    c = TestClient(app, base_url="https://127.0.0.1", headers={"Origin": "https://127.0.0.1"})
    assert "secure" in login(c).headers["set-cookie"].lower()


def test_wrong_password(server, gabriel):
    r = login(server, password="no-es-esta-clave")
    assert r.status_code == 401 and "Usuario o contraseña incorrectos" in r.text
    assert "torre_session" not in r.headers.get("set-cookie", "")
    # Mismo mensaje si el usuario no existe: no se puede saber qué usuarios hay
    r = login(server, username="nadie")
    assert r.status_code == 401 and "Usuario o contraseña incorrectos" in r.text


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example", "javascript:x"])
def test_no_open_redirect(server, gabriel, target):
    assert login(server, next_url=target).headers["location"] == "/"


def test_too_many_attempts(server, gabriel):
    for _ in range(5):
        assert login(server, password="no-es-esta-clave").status_code == 401
    r = login(server)                                 # ni con la buena
    assert r.status_code == 429 and "Demasiados intentos" in r.text


def test_throttle_windows():
    t = auth.Throttle(per_ip=3, per_user=5, window=100)
    for i in range(3):
        t.failed("1.1.1.1", "ana", now=i)
    assert t.wait_seconds("1.1.1.1", "ana", now=10) > 0
    assert t.wait_seconds("2.2.2.2", "luis", now=10) == 0        # otra IP y otro usuario
    assert t.wait_seconds("1.1.1.1", "ana", now=103) == 0        # pasó la ventana
    # Contra un mismo usuario desde muchas IP
    for i in range(5):
        t.failed(f"9.9.9.{i}", "luis", now=200)
    assert t.wait_seconds("8.8.8.8", "luis", now=201) > 0
    t.succeeded("1.1.1.1")


def test_password_change_closes_sessions(server, gabriel):
    login(server)
    auth.set_password("gabriel", "otra-clave-muy-larga")
    assert server.get("/api/search?q=a").status_code == 401


def test_expired_session(server, gabriel):
    login(server)
    with sqlite3.connect(db.db_path()) as con:
        con.execute("UPDATE sessions SET expires = '2000-01-01T00:00:00+00:00'")
    assert server.get("/api/search?q=a").status_code == 401


def test_session_token_not_stored(server, gabriel):
    login(server)
    token = server.cookies.get("torre_session")
    with sqlite3.connect(db.db_path()) as con:
        stored = con.execute("SELECT token FROM sessions").fetchone()[0]
    assert token and stored != token and len(stored) == 64


def test_writes_need_origin(server, gabriel):
    login(server)
    bare = TestClient(server.app, base_url="http://127.0.0.1", cookies=server.cookies)
    assert bare.post("/api/boards", json={"name": "x"}).status_code == 403
    r = bare.post("/api/boards", json={"name": "x"}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 201
    r = server.post("/api/boards", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# --- Nombre del servidor y modo servidor ------------------------------------------------

def test_allowed_hosts(monkeypatch, client):
    from torre.main import app
    c = TestClient(app, base_url="https://torre.example.com")
    assert c.get("/").status_code == 403
    monkeypatch.setenv("TORRE_ALLOWED_HOSTS", "otra.com, Torre.Example.com")
    assert c.get("/").status_code == 200


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_server_has_no_reminders(server, gabriel, notifications):
    login(server)
    page = server.get("/").text
    assert "Probar aviso" not in page and "Hacer copia ahora" in page
    assert server.post("/api/reminders/test").status_code == 409
    b = server.post("/api/boards", json={"name": "Casa"}).json()["id"]
    lst = server.get(f"/api/boards/{b}").json()["lists"][0]["id"]
    card = server.post(f"/api/lists/{lst}/cards", json={"title": "Hoy"}).json()["id"]
    server.patch(f"/api/cards/{card}", json={"due": date.today().isoformat()})
    assert not background.check_reminders()
    assert notifications == []


def test_local_mode_unchanged(client):
    """En el portátil (sin TORRE_AUTH) no se pide contraseña."""
    assert client.get("/").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
