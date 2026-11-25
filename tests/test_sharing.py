"""Varios usuarios en un servidor: registro, tableros propios y compartidos,
permisos en toda la API y administración."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from torre import auth, db, trello

PASSWORD = "caballo-bateria-grapa"
ORIGIN = {"Origin": "http://127.0.0.1"}


@pytest.fixture(autouse=True)
def server_mode(monkeypatch):
    monkeypatch.setenv("TORRE_AUTH", "1")
    monkeypatch.setenv("TORRE_NO_REMINDERS", "1")
    monkeypatch.setattr(auth, "throttle", auth.Throttle())
    monkeypatch.setattr(auth, "signups", auth.Throttle(per_ip=3, per_user=10 ** 9, window=3600))


def browser():
    from torre.main import app
    return TestClient(app, base_url="http://127.0.0.1", headers=ORIGIN)


def signup(username, password=PASSWORD, password2=None, **extra):
    c = browser()
    r = c.post("/registro", data={"username": username, "password": password,
                                  "password2": password if password2 is None else password2, **extra},
               follow_redirects=False)
    return c, r


def as_user(username):
    """Navegador con sesión iniciada (crea el usuario si no existe)."""
    if username not in auth.list_users():
        auth.create_user(username, PASSWORD)
    c = browser()
    r = c.post("/login", data={"username": username, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    return c


def new_board(c, name="Casa"):
    board_id = c.post("/api/boards", json={"name": name}).json()["id"]
    board = c.get(f"/api/boards/{board_id}").json()
    lst = board["lists"][0]["id"]
    card = c.post(f"/api/lists/{lst}/cards", json={"title": "Secreto"}).json()["id"]
    label = c.post(f"/api/boards/{board_id}/labels", json={"name": "Rojo", "color": "red"}).json()["id"]
    item = c.post(f"/api/cards/{card}/items", json={"text": "Paso"}).json()["items"][0]["id"]
    return {"board": board_id, "list": lst, "card": card, "label": label, "item": item}


# --- Registro --------------------------------------------------------------------------

def test_signup_creates_account_and_logs_in():
    c, r = signup("Ana")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/").status_code == 200 and ">ana<" in c.get("/").text
    assert auth.is_admin("ana")                     # la primera cuenta es administradora
    _, r = signup("luis")
    assert r.status_code == 303 and not auth.is_admin("luis")
    with sqlite3.connect(db.db_path()) as con:      # la contraseña no se guarda tal cual
        assert all(PASSWORD not in row[0] for row in con.execute("SELECT password FROM users"))


@pytest.mark.parametrize("username,password,password2,message", [
    ("ana", PASSWORD, "otra-cosa-larga", "no coinciden"),
    ("ana", "corta", "corta", "10 caracteres"),
    ("a b", PASSWORD, PASSWORD, "2 a 32 caracteres"),
])
def test_signup_validation(username, password, password2, message):
    _, r = signup(username, password, password2)
    assert r.status_code == 400 and message in r.text
    assert auth.list_users() == []


def test_signup_duplicate():
    signup("ana")
    _, r = signup("ANA")
    assert r.status_code == 400 and "ya existe" in r.text


def test_signup_closed():
    auth.set_registration(False)
    assert "está cerrado" in browser().get("/registro").text
    _, r = signup("ana")
    assert r.status_code == 400 and auth.list_users() == []
    assert "Crear cuenta" not in browser().get("/login").text


def test_signup_bot_trap():
    _, r = signup("bot", website="http://spam.example")
    assert r.status_code == 400 and auth.list_users() == []


def test_signup_limit_per_ip():
    for name in ("uno", "dos", "tres"):
        assert signup(name)[1].status_code == 303
    _, r = signup("cuatro")
    assert r.status_code == 429 and "demasiadas cuentas" in r.text
    assert len(auth.list_users()) == 3


def test_signup_max_users(monkeypatch):
    monkeypatch.setenv("TORRE_MAX_USERS", "1")
    signup("ana")
    _, r = signup("luis")
    assert r.status_code == 400 and "máximo de usuarios" in r.text


# --- Cada uno lo suyo -------------------------------------------------------------------

def test_others_cannot_see_or_touch_my_board():
    ana, luis = as_user("ana"), as_user("luis")
    ids = new_board(ana)
    b, lst, card, label, item = ids["board"], ids["list"], ids["card"], ids["label"], ids["item"]
    own_list = new_board(luis, "Lo mío")["list"]

    assert "Casa" not in luis.get("/").text
    assert luis.get(f"/b/{b}").status_code == 404
    found = luis.get("/api/search?q=Secreto").json()
    assert found and all(r["board_id"] != b for r in found)      # solo la suya, nunca la de Ana
    attempts = [
        ("get", f"/api/boards/{b}", None), ("patch", f"/api/boards/{b}", {"name": "x"}),
        ("delete", f"/api/boards/{b}", None), ("get", f"/api/boards/{b}/archive", None),
        ("get", f"/api/boards/{b}/members", None), ("post", f"/api/boards/{b}/members", {"username": "luis"}),
        ("post", f"/api/boards/{b}/lists", {"name": "x"}), ("patch", f"/api/lists/{lst}", {"name": "x"}),
        ("post", f"/api/lists/{lst}/move", {"index": 0}), ("delete", f"/api/lists/{lst}", None),
        ("post", f"/api/lists/{lst}/cards", {"title": "x"}), ("get", f"/api/cards/{card}", None),
        ("patch", f"/api/cards/{card}", {"title": "x"}), ("delete", f"/api/cards/{card}", None),
        ("post", f"/api/cards/{card}/move", {"list_id": own_list, "index": 0}),
        ("put", f"/api/cards/{card}/labels", {"label_ids": []}),
        ("post", f"/api/boards/{b}/labels", {"color": "red"}), ("patch", f"/api/labels/{label}", {"name": "x"}),
        ("delete", f"/api/labels/{label}", None), ("post", f"/api/cards/{card}/items", {"text": "x"}),
        ("patch", f"/api/items/{item}", {"done": True}), ("delete", f"/api/items/{item}", None),
    ]
    for method, url, body in attempts:
        r = getattr(luis, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    # Todo sigue intacto
    card_now = ana.get(f"/api/cards/{card}").json()
    assert card_now["title"] == "Secreto" and len(card_now["items"]) == 1
    assert ana.get(f"/api/boards/{b}").json()["name"] == "Casa"


def test_cannot_pull_card_into_my_board():
    ana, luis = as_user("ana"), as_user("luis")
    theirs = new_board(ana)
    mine = new_board(luis)
    # Mover una tarjeta mía a una lista de Ana tampoco
    r = luis.post(f"/api/cards/{mine['card']}/move", json={"list_id": theirs["list"], "index": 0})
    assert r.status_code == 404


# --- Compartir ----------------------------------------------------------------------------

def test_share_and_collaborate():
    ana, luis = as_user("ana"), as_user("luis")
    ids = new_board(ana, "2º ASIR")
    b = ids["board"]
    assert ana.get("/api/users").json() == ["luis"]
    r = ana.post(f"/api/boards/{b}/members", json={"username": "Luis"})
    assert r.status_code == 201 and r.json() == {"owner": "ana", "members": ["luis"]}

    page = luis.get("/").text
    assert "Compartidos contigo" in page and "2º ASIR" in page and "de ana" in page
    assert luis.get(f"/api/boards/{b}").json()["role"] == "member"
    assert "Salir del tablero" in luis.get(f"/b/{b}").text or luis.get(f"/b/{b}").status_code == 200
    assert [r["title"] for r in luis.get("/api/search?q=Secreto").json()] == ["Secreto"]

    # El miembro edita el contenido…
    assert luis.patch(f"/api/cards/{ids['card']}", json={"title": "Práctica 3"}).status_code == 200
    assert luis.post(f"/api/lists/{ids['list']}/cards", json={"title": "Nueva"}).status_code == 201
    assert luis.post(f"/api/boards/{b}/labels", json={"color": "blue"}).status_code == 201
    assert ana.get(f"/api/cards/{ids['card']}").json()["title"] == "Práctica 3"
    # …pero lo del tablero es del dueño
    assert luis.patch(f"/api/boards/{b}", json={"name": "Mío"}).status_code == 403
    assert luis.delete(f"/api/boards/{b}").status_code == 403
    assert luis.post(f"/api/boards/{b}/members", json={"username": "ana"}).status_code == 403

    # Salir del tablero
    assert luis.delete(f"/api/boards/{b}/members/luis").status_code == 200
    assert luis.get(f"/api/boards/{b}").status_code == 404


def test_member_cannot_remove_others():
    ana, luis, eva = as_user("ana"), as_user("luis"), as_user("eva")
    b = new_board(ana)["board"]
    for u in ("luis", "eva"):
        ana.post(f"/api/boards/{b}/members", json={"username": u})
    assert luis.delete(f"/api/boards/{b}/members/eva").status_code == 403
    assert ana.delete(f"/api/boards/{b}/members/eva").status_code == 200
    assert eva.get(f"/api/boards/{b}").status_code == 404


def test_share_errors():
    ana = as_user("ana")
    b = new_board(ana)["board"]
    r = ana.post(f"/api/boards/{b}/members", json={"username": "nadie"})
    assert r.status_code == 400 and "No existe" in r.json()["detail"]
    r = ana.post(f"/api/boards/{b}/members", json={"username": "ana"})
    assert r.status_code == 400 and "dueño" in r.json()["detail"]


def test_board_page_for_member():
    ana, luis = as_user("ana"), as_user("luis")
    b = new_board(ana, "Común")["board"]
    ana.post(f"/api/boards/{b}/members", json={"username": "luis"})
    page = luis.get(f"/b/{b}").text
    assert 'data-role="member"' in page and "de ana" in page
    assert 'id="board-colors"' not in page and 'id="archive-board"' not in page
    owner_page = ana.get(f"/b/{b}").text
    assert 'data-role="owner"' in owner_page and 'id="archive-board"' in owner_page


def test_theme_is_per_user():
    ana, luis = as_user("ana"), as_user("luis")
    ana.put("/api/settings/theme", json={"theme": "dark"})
    assert 'data-theme="dark"' in ana.get("/").text
    assert 'data-theme="dark"' not in luis.get("/").text


def test_trello_import_belongs_to_importer():
    ana, luis = as_user("ana"), as_user("luis")
    data = {"name": "De Trello", "lists": [{"id": "L", "name": "A", "pos": 1}], "cards": []}
    b = ana.post("/api/import/trello", json=data).json()["id"]
    assert luis.get(f"/api/boards/{b}").status_code == 404
    assert ana.get(f"/api/boards/{b}").status_code == 200


# --- Administración --------------------------------------------------------------------------

def test_admin_pages_only_for_admins():
    admin, luis = as_user("ana"), as_user("luis")          # ana es la primera: admin
    assert admin.get("/admin").status_code == 200 and "luis" in admin.get("/admin").text
    assert 'href="/admin"' in admin.get("/").text and 'href="/admin"' not in luis.get("/").text
    assert "Copia de seguridad" in admin.get("/").text and "Copia de seguridad" not in luis.get("/").text
    for method, url, body in [("get", "/admin", None), ("put", "/api/admin/registration", {"open": False}),
                              ("put", "/api/admin/users/ana/admin", {"admin": False}),
                              ("post", "/api/admin/users/ana/password", None),
                              ("delete", "/api/admin/users/ana", None), ("post", "/api/backup", None)]:
        r = getattr(luis, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, url)
    assert auth.list_users() == ["ana", "luis"] and auth.registration_open()


def test_admin_closes_registration():
    admin = as_user("ana")
    assert admin.put("/api/admin/registration", json={"open": False}).status_code == 200
    assert not auth.registration_open()


def test_temporary_password_flow():
    admin, luis = as_user("ana"), as_user("luis")
    temp = admin.post("/api/admin/users/luis/password").json()["password"]
    assert luis.get("/api/search?q=a").status_code == 401           # sus sesiones se cerraron
    c = browser()
    c.post("/login", data={"username": "luis", "password": temp})
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cuenta"
    assert c.get("/api/search?q=a").status_code == 403
    assert "contraseña temporal" in c.get("/cuenta").text
    r = c.post("/cuenta", data={"current": temp, "new": "mi-clave-nueva-1", "new2": "mi-clave-nueva-1"},
               follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/").status_code == 200                              # ya puede usar Torre
    assert auth.authenticate("luis", "mi-clave-nueva-1") == "luis"


def test_change_own_password():
    c = as_user("ana")
    r = c.post("/cuenta", data={"current": "mala-mala-mala", "new": "nueva-clave-12", "new2": "nueva-clave-12"})
    assert r.status_code == 400 and "actual no es correcta" in r.text
    r = c.post("/cuenta", data={"current": PASSWORD, "new": "nueva-clave-12", "new2": "otra-clave-123"})
    assert r.status_code == 400 and "no coinciden" in r.text
    r = c.post("/cuenta", data={"current": PASSWORD, "new": "nueva-clave-12", "new2": "nueva-clave-12"})
    assert "Contraseña cambiada" in r.text
    assert auth.authenticate("ana", "nueva-clave-12") == "ana"


def test_admin_deletes_user_and_their_boards():
    admin, luis = as_user("ana"), as_user("luis")
    theirs = new_board(luis, "De Luis")["board"]
    shared = new_board(admin, "Común")["board"]
    admin.post(f"/api/boards/{shared}/members", json={"username": "luis"})
    assert admin.delete("/api/admin/users/luis").status_code == 200
    assert auth.list_users() == ["ana"]
    assert db.members(shared)["members"] == []
    with pytest.raises(db.NotFound):
        db.role(theirs, "ana")
    assert admin.delete("/api/admin/users/ana").status_code == 400  # a sí mismo no


def test_last_admin_is_protected():
    admin = as_user("ana")
    r = admin.put("/api/admin/users/ana/admin", json={"admin": False})
    assert r.status_code == 400 and "al menos un administrador" in r.json()["detail"]


# --- Bases de datos de antes -------------------------------------------------------------------

def test_old_database_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "vieja.db"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE boards (id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT 'blue',
                                 archived INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE users (username TEXT PRIMARY KEY, password TEXT NOT NULL,
                                created TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO boards (name) VALUES ('Antiguo');
            INSERT INTO users (username, password, created) VALUES ('gabriel', 'x', '2026-01-01'),
                                                                   ('otro', 'x', '2026-02-01');
        """)
    monkeypatch.setenv("TORRE_DB", str(path))
    assert [(b["name"], b["owner"]) for b in db.list_boards()] == [("Antiguo", "gabriel")]
    assert auth.is_admin("gabriel") and not auth.is_admin("otro")


def test_laptop_mode_has_no_owners(monkeypatch):
    monkeypatch.delenv("TORRE_AUTH")
    from torre.main import app
    c = TestClient(app, base_url="http://127.0.0.1")
    b = c.post("/api/boards", json={"name": "Local"}).json()["id"]
    assert db.board_name(b)["owner"] is None
    assert c.get(f"/api/boards/{b}").json()["role"] == "owner"
    assert trello.import_board({"name": "T", "lists": [], "cards": []})["id"]
