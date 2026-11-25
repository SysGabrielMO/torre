import sqlite3

import pytest

from torre import auth, db, transfer


def test_transfer_laptop_boards(tmp_path, monkeypatch, capsys):
    # Torre del portátil (sin usuarios), con algo de todo
    laptop = tmp_path / "portatil.db"
    monkeypatch.setenv("TORRE_DB", str(laptop))
    b = db.create_board("2º ASIR", "purple")
    todo = db.create_list(b, "Por hacer")
    gone = db.create_list(b, "Vieja")
    db.update_list(gone, archived=True)
    label = db.create_label(b, "Examen", "red")
    card = db.create_card(todo, "Práctica DNS")
    db.update_card(card, description="**bind9**", due="2026-12-01", done=True)
    db.set_card_labels(card, [label])
    db.update_item(db.create_item(card, "Zona directa"), done=True)
    db.update_card(db.create_card(todo, "Archivada"), archived=True)

    # Servidor con un usuario y un tablero suyo
    monkeypatch.setenv("TORRE_DB", str(tmp_path / "servidor.db"))
    auth.create_user("gabriel", "caballo-bateria-grapa")
    db.create_board("Ya estaba", owner="gabriel")

    assert transfer.main([str(laptop), "Gabriel"]) == 0
    assert "«2º ASIR»" in capsys.readouterr().out
    boards = {x["name"]: x for x in db.list_boards("gabriel")}
    assert set(boards) == {"Ya estaba", "2º ASIR"} and boards["2º ASIR"]["color"] == "purple"
    copied = db.get_board(boards["2º ASIR"]["id"])
    assert [l["name"] for l in copied["lists"]] == ["Por hacer"]
    first = db.get_card(copied["lists"][0]["cards"][0]["id"])
    assert (first["title"], first["due"], first["done"]) == ("Práctica DNS", "2026-12-01", 1)
    assert [db_label["name"] for db_label in copied["labels"] if db_label["id"] in first["label_ids"]] == ["Examen"]
    assert [(i["text"], i["done"]) for i in first["items"]] == [("Zona directa", 1)]
    archive = db.get_archive(copied["id"])
    assert [l["name"] for l in archive["lists"]] == ["Vieja"] and [c["title"] for c in archive["cards"]] == ["Archivada"]


def test_transfer_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TORRE_DB", str(tmp_path / "servidor.db"))
    auth.create_user("gabriel", "caballo-bateria-grapa")
    assert transfer.main([str(tmp_path / "no-existe.db"), "gabriel"]) == 1
    bad = tmp_path / "otra.db"
    bad.write_text("esto no es sqlite")
    with pytest.raises(SystemExit, match="no es una base de datos de Torre"):
        transfer.main([str(bad), "gabriel"])
    origin = tmp_path / "vacia.db"
    sqlite3.connect(origin).executescript(db.SCHEMA)
    with pytest.raises(SystemExit, match="no existe el usuario"):
        transfer.main([str(origin), "nadie"])
