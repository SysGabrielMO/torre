"""Importar tableros de Trello (torre/trello.py y /api/import/trello).

El fichero de ejemplo imita la exportación JSON real de Trello: solo con los
campos que usa Torre, más algunos que debe ignorar.
"""
import json
import time

import pytest

from torre import db, trello


@pytest.fixture(autouse=True)
def madrid(monkeypatch):
    """Las fechas de Trello vienen en UTC; Torre las pasa a la hora local."""
    monkeypatch.setenv("TZ", "Europe/Madrid")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def export(**over):
    data = {
        "id": "b1", "name": "Mudanza", "closed": False, "desc": "",
        "prefs": {"background": "sky", "backgroundColor": "#00AECC"},
        "members": [{"id": "m1", "fullName": "Ana"}], "customFields": [], "powerUps": [],
        "labels": [
            {"id": "l1", "name": "Urgente", "color": "red"},
            {"id": "l2", "name": "Compras", "color": "lime_dark"},
            {"id": "l3", "name": "", "color": None},
        ],
        "lists": [
            {"id": "L2", "name": "Hecho", "closed": False, "pos": 300},
            {"id": "L1", "name": "Por hacer", "closed": False, "pos": 100.5},
            {"id": "L3", "name": "Viejo", "closed": True, "pos": 50},
        ],
        "cards": [
            {"id": "c2", "name": "Cajas", "idList": "L1", "pos": 2000, "closed": False, "desc": "",
             "due": None, "dueComplete": False, "idLabels": ["l2"], "idChecklists": [], "attachments": []},
            {"id": "c1", "name": "Contratar  camión\n", "idList": "L1", "pos": 1000, "closed": False,
             "desc": "Pedir **3** presupuestos", "due": "2026-11-20T23:30:00.000Z", "dueComplete": False,
             "idLabels": ["l1", "l3", "no-existe"], "idMembers": ["m1"], "cover": {"color": "red"},
             "attachments": [{"id": "a1", "name": "presupuesto.pdf", "url": "https://trello.com/1/cards/c1/attachments/a1/download/presupuesto.pdf"}]},
            {"id": "c3", "name": "Dar de baja la luz", "idList": "L2", "pos": 1, "closed": False, "desc": "",
             "due": "2026-11-10T09:00:00.000Z", "dueComplete": True, "idLabels": [], "attachments": []},
            {"id": "c4", "name": "Idea descartada", "idList": "L1", "pos": 1500, "closed": True, "desc": ""},
            {"id": "c5", "name": "Huérfana", "idList": "NO-EXISTE", "pos": 1, "closed": False},
        ],
        "checklists": [
            {"id": "k2", "idCard": "c1", "name": "Después", "pos": 2,
             "checkItems": [{"id": "i3", "name": "Pagar", "state": "incomplete", "pos": 1}]},
            {"id": "k1", "idCard": "c1", "name": "Antes", "pos": 1,
             "checkItems": [{"id": "i2", "name": "Medir", "state": "incomplete", "pos": 2},
                            {"id": "i1", "name": "Llamar", "state": "complete", "pos": 1}]},
            {"id": "k3", "idCard": "c2", "name": "Checklist", "pos": 1,
             "checkItems": [{"id": "i4", "name": "Comprar cinta", "state": "complete", "pos": 1}]},
        ],
        "actions": [
            {"type": "commentCard", "date": "2026-11-02T10:00:00.000Z", "memberCreator": {"fullName": "Ana"},
             "data": {"text": "Segundo", "card": {"id": "c1"}}},
            {"type": "commentCard", "date": "2026-11-01T10:00:00.000Z", "memberCreator": {"fullName": "Luis"},
             "data": {"text": "Primero\ncon dos líneas", "card": {"id": "c1"}}},
            {"type": "updateCard", "date": "2026-11-03T10:00:00.000Z", "data": {"card": {"id": "c1"}}},
        ],
    }
    data.update(over)
    return data


def test_import_structure(client):
    result = trello.import_board(export())
    assert result["name"] == "Mudanza"
    assert {k: result[k] for k in ("lists", "cards", "labels", "items", "comments", "archived")} == \
        {"lists": 3, "cards": 4, "labels": 3, "items": 4, "comments": 2, "archived": 2}

    board = db.get_board(result["id"])
    assert board["color"] == "teal"                                     # sky → turquesa
    assert [l["name"] for l in board["lists"]] == ["Por hacer", "Hecho"]  # por «pos»; la archivada no se ve
    todo, done = board["lists"]
    assert [c["title"] for c in todo["cards"]] == ["Contratar camión", "Cajas"]
    assert [c["title"] for c in done["cards"]] == ["Dar de baja la luz"]
    assert [(l["name"], l["color"]) for l in board["labels"]] == \
        [("Urgente", "red"), ("Compras", "green"), ("", "gray")]


def test_card_details(client):
    board = db.get_board(trello.import_board(export())["id"])
    first = db.get_card(board["lists"][0]["cards"][0]["id"])
    assert first["due"] == "2026-11-21"           # 23:30 UTC = 00:30 del día siguiente en Madrid
    assert first["done"] == 0
    names = {l["id"]: l["name"] for l in board["labels"]}
    assert sorted(names[i] for i in first["label_ids"]) == ["", "Urgente"]
    # Varias checklists: juntas, en orden, con el nombre de cada una delante
    assert [(i["text"], i["done"]) for i in first["items"]] == \
        [("Antes: Llamar", 1), ("Antes: Medir", 0), ("Después: Pagar", 0)]
    assert first["description"].startswith("Pedir **3** presupuestos\n\n---\n\n**Adjuntos (en Trello):**")
    assert "[presupuesto.pdf](https://trello.com/1/cards/c1/attachments/a1/download/presupuesto.pdf)" in first["description"]
    # Comentarios en orden de fecha, con autor
    assert first["description"].index("Luis, 2026-11-01: Primero\n  con dos líneas") < \
        first["description"].index("Ana, 2026-11-02: Segundo")

    cajas = db.get_card(board["lists"][0]["cards"][1]["id"])
    assert [i["text"] for i in cajas["items"]] == ["Comprar cinta"]      # una sola checklist: sin prefijo
    assert cajas["description"] == ""

    luz = db.get_card(board["lists"][1]["cards"][0]["id"])
    assert (luz["due"], luz["done"]) == ("2026-11-10", 1)


def test_archived_things_can_be_restored(client):
    board_id = trello.import_board(export())["id"]
    archive = db.get_archive(board_id)
    assert [l["name"] for l in archive["lists"]] == ["Viejo"]
    assert [c["title"] for c in archive["cards"]] == ["Idea descartada"]
    card = archive["cards"][0]["id"]
    client.patch(f"/api/cards/{card}", json={"archived": False})
    titles = [c["title"] for c in db.get_board(board_id)["lists"][0]["cards"]]
    assert titles == ["Contratar camión", "Cajas", "Idea descartada"]


def test_archived_board(client):
    board_id = trello.import_board(export(closed=True))["id"]
    assert db.board_name(board_id)["archived"] == 1


def test_limits_and_missing_names(client):
    data = export(name="  ", lists=[{"id": "L1", "name": "x" * 500, "pos": 1}],
                  cards=[{"id": "c1", "name": "", "idList": "L1", "pos": 1, "desc": "d" * 60_000}],
                  checklists=[], labels=[], actions=[])
    board = db.get_board(trello.import_board(data)["id"])
    assert board["name"] == "Tablero de Trello"
    assert len(board["lists"][0]["name"]) == 100
    card = db.get_card(board["lists"][0]["cards"][0]["id"])
    assert card["title"] == "Sin título" and len(card["description"]) == 50_000


def test_unknown_colors_and_bad_dates(client):
    data = export(prefs={"background": "4f8a1c2e9b"}, labels=[{"id": "l1", "name": "?", "color": "magenta"}],
                  cards=[{"id": "c1", "name": "A", "idList": "L1", "pos": 1, "due": "mañana"}], checklists=[])
    board = db.get_board(trello.import_board(data)["id"])
    assert board["color"] == "blue"                       # fondo de imagen → azul
    assert board["labels"][0]["color"] == "gray"
    assert board["lists"][0]["cards"][0]["due"] is None


# --- API -------------------------------------------------------------------------

def test_api_import(client):
    r = client.post("/api/import/trello", content=json.dumps(export()),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 201
    assert r.json()["cards"] == 4
    assert "Mudanza" in client.get("/").text


def test_api_rejects_non_json(client):
    r = client.post("/api/import/trello", content=b"\x89PNG...", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "no es JSON" in r.json()["detail"]


def test_api_rejects_other_json(client):
    r = client.post("/api/import/trello", json={"hola": "mundo"})
    assert r.status_code == 422 and "Trello" in r.json()["detail"]
    r = client.post("/api/import/trello", json=[1, 2, 3])
    assert r.status_code == 422


def test_damaged_file_imports_nothing(client):
    data = export()
    data["cards"].append("esto no es una tarjeta")
    r = client.post("/api/import/trello", json=data)
    assert r.status_code == 422 and "no se ha importado nada" in r.json()["detail"]
    assert db.list_boards() == []                         # ni el tablero a medias


def test_api_rejects_huge_file(client):
    r = client.post("/api/import/trello", content=b"{}",
                    headers={"Content-Type": "application/json", "Content-Length": str(60 * 1024 * 1024)})
    assert r.status_code == 413


def test_import_twice_creates_two_boards(client):
    for _ in range(2):
        client.post("/api/import/trello", json=export())
    assert [b["name"] for b in db.list_boards()] == ["Mudanza", "Mudanza"]
