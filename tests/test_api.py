def cards_of(client, board_id):
    data = client.get(f"/api/boards/{board_id}").json()
    return {lst["name"]: [c["title"] for c in lst["cards"]] for lst in data["lists"]}


def test_new_board_has_default_lists(board):
    assert [l["name"] for l in board["lists"]] == ["Por hacer", "En progreso", "Hecho"]
    assert board["color"] == "blue"


def test_board_validation(client):
    assert client.post("/api/boards", json={"name": "   "}).status_code == 422
    assert client.post("/api/boards", json={"name": "x", "color": "fucsia"}).status_code == 422


def test_rename_archive_delete_board(client, board):
    b = board["id"]
    assert client.patch(f"/api/boards/{b}", json={"name": "Hogar", "color": "green"}).status_code == 200
    assert client.get(f"/api/boards/{b}").json()["name"] == "Hogar"
    client.patch(f"/api/boards/{b}", json={"archived": True})
    assert "Hogar" in client.get("/").text   # sale en «Archivados»
    assert client.delete(f"/api/boards/{b}").status_code == 200
    assert client.get(f"/api/boards/{b}").status_code == 404
    assert client.get(f"/b/{b}").status_code == 404


def test_cards_create_and_move(client, board):
    todo, doing, done = (l["id"] for l in board["lists"])
    ids = [client.post(f"/api/lists/{todo}/cards", json={"title": t}).json()["id"]
           for t in ("A", "B", "C")]
    assert cards_of(client, board["id"])["Por hacer"] == ["A", "B", "C"]

    client.post(f"/api/cards/{ids[2]}/move", json={"list_id": todo, "index": 0})
    assert cards_of(client, board["id"])["Por hacer"] == ["C", "A", "B"]

    client.post(f"/api/cards/{ids[0]}/move", json={"list_id": doing, "index": 5})
    got = cards_of(client, board["id"])
    assert got["Por hacer"] == ["C", "B"] and got["En progreso"] == ["A"]

    # Una tarjeta nueva tras mover va al final
    client.post(f"/api/lists/{todo}/cards", json={"title": "D"})
    assert cards_of(client, board["id"])["Por hacer"] == ["C", "B", "D"]


def test_cannot_move_card_to_other_board(client, board):
    other = client.post("/api/boards", json={"name": "Otro"}).json()["id"]
    other_list = client.get(f"/api/boards/{other}").json()["lists"][0]["id"]
    card = client.post(f"/api/lists/{board['lists'][0]['id']}/cards", json={"title": "A"}).json()["id"]
    assert client.post(f"/api/cards/{card}/move", json={"list_id": other_list, "index": 0}).status_code == 404


def test_move_and_archive_lists(client, board):
    todo, doing, done = (l["id"] for l in board["lists"])
    client.post(f"/api/lists/{done}/move", json={"index": 0})
    names = lambda: [l["name"] for l in client.get(f"/api/boards/{board['id']}").json()["lists"]]
    assert names() == ["Hecho", "Por hacer", "En progreso"]

    client.patch(f"/api/lists/{todo}", json={"archived": True})
    assert names() == ["Hecho", "En progreso"]
    archive = client.get(f"/api/boards/{board['id']}/archive").json()
    assert [l["name"] for l in archive["lists"]] == ["Por hacer"]

    client.patch(f"/api/lists/{todo}", json={"archived": False, "name": "Pendiente"})
    assert names() == ["Hecho", "En progreso", "Pendiente"]


def test_card_details(client, board):
    lst = board["lists"][0]["id"]
    card = client.post(f"/api/lists/{lst}/cards", json={"title": "Comprar"}).json()["id"]
    r = client.patch(f"/api/cards/{card}", json={
        "description": "**leche** y [web](https://example.com) <script>x</script>",
        "due": "2026-12-01", "done": True})
    data = r.json()
    assert data["due"] == "2026-12-01" and data["done"] == 1
    assert "<strong>leche</strong>" in data["description_html"]
    assert 'target="_blank"' in data["description_html"]
    assert "<script>" not in data["description_html"]

    assert client.patch(f"/api/cards/{card}", json={"due": "30/11/2026"}).status_code == 422
    assert client.patch(f"/api/cards/{card}", json={"due": None}).json()["due"] is None


def test_markdown_blocks_js_links(client, board):
    lst = board["lists"][0]["id"]
    card = client.post(f"/api/lists/{lst}/cards", json={"title": "x"}).json()["id"]
    html = client.patch(f"/api/cards/{card}", json={"description": "[a](javascript:alert(1))"}).json()["description_html"]
    assert "javascript:" not in html


def test_checklist(client, board):
    lst = board["lists"][0]["id"]
    card = client.post(f"/api/lists/{lst}/cards", json={"title": "Viaje"}).json()["id"]
    client.post(f"/api/cards/{card}/items", json={"text": "Billetes"})
    items = client.post(f"/api/cards/{card}/items", json={"text": "Maleta"}).json()["items"]
    client.patch(f"/api/items/{items[0]['id']}", json={"done": True})
    summary = client.get(f"/api/boards/{board['id']}").json()["lists"][0]["cards"][0]
    assert (summary["items_done"], summary["items_total"]) == (1, 2)
    client.delete(f"/api/items/{items[1]['id']}")
    assert len(client.get(f"/api/cards/{card}").json()["items"]) == 1


def test_labels(client, board):
    b = board["id"]
    red = client.post(f"/api/boards/{b}/labels", json={"name": "Urgente", "color": "red"}).json()["id"]
    other_board = client.post("/api/boards", json={"name": "Otro"}).json()["id"]
    foreign = client.post(f"/api/boards/{other_board}/labels", json={"color": "green"}).json()["id"]
    card = client.post(f"/api/lists/{board['lists'][0]['id']}/cards", json={"title": "x"}).json()["id"]

    data = client.put(f"/api/cards/{card}/labels", json={"label_ids": [red, foreign]}).json()
    assert data["label_ids"] == [red]          # la de otro tablero se ignora
    client.delete(f"/api/labels/{red}")
    assert client.get(f"/api/cards/{card}").json()["label_ids"] == []


def test_archive_and_restore_card(client, board):
    lst = board["lists"][0]["id"]
    a = client.post(f"/api/lists/{lst}/cards", json={"title": "A"}).json()["id"]
    client.post(f"/api/lists/{lst}/cards", json={"title": "B"})
    client.patch(f"/api/cards/{a}", json={"archived": True})
    assert cards_of(client, board["id"])["Por hacer"] == ["B"]
    assert [c["title"] for c in client.get(f"/api/boards/{board['id']}/archive").json()["cards"]] == ["A"]
    client.patch(f"/api/cards/{a}", json={"archived": False})
    assert cards_of(client, board["id"])["Por hacer"] == ["B", "A"]


def test_search(client, board):
    lst = board["lists"][0]["id"]
    client.post(f"/api/lists/{lst}/cards", json={"title": "Llamar al 100% dentista"})
    c = client.post(f"/api/lists/{lst}/cards", json={"title": "Otra"}).json()["id"]
    client.patch(f"/api/cards/{c}", json={"description": "hablar con el dentista"})
    assert [r["title"] for r in client.get("/api/search?q=dentista").json()] == ["Llamar al 100% dentista", "Otra"]
    assert len(client.get("/api/search?q=100%").json()) == 1
    assert client.get("/api/search?q=").json() == []


def test_theme(client):
    assert client.put("/api/settings/theme", json={"theme": "dark"}).status_code == 200
    assert 'data-theme="dark"' in client.get("/").text
    assert client.put("/api/settings/theme", json={"theme": "rosa"}).status_code == 422


def test_pages(client, board):
    assert "Casa" in client.get("/").text
    assert client.get(f"/b/{board['id']}").status_code == 200
    assert client.get("/api/cards/999").status_code == 404
