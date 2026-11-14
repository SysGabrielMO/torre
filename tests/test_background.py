from datetime import date

from torre import background, db

TODAY = date(2026, 11, 14)


def make_card(client, board, title, due=None, **fields):
    card = client.post(f"/api/lists/{board['lists'][0]['id']}/cards", json={"title": title}).json()["id"]
    if due or fields:
        client.patch(f"/api/cards/{card}", json={"due": due, **fields})
    return card


# --- Copias -------------------------------------------------------------------------

def test_backup_once_a_day(client, board):
    path = background.make_backup(TODAY)
    assert path.name == "torre-2026-11-14.db"
    assert background.make_backup(TODAY) is None          # hoy ya hay copia
    assert background.make_backup(TODAY, force=True) == path

    # La copia es una base de datos completa y legible
    import sqlite3
    con = sqlite3.connect(path)
    assert con.execute("SELECT name FROM boards").fetchone()[0] == "Casa"
    con.close()


def test_backup_without_data_does_nothing():
    assert background.make_backup(TODAY) is None
    assert background.list_backups() == []


def test_backup_keeps_last_14(client, board):
    for day in range(1, 21):
        background.make_backup(date(2026, 10, day))
    days = [b.day.day for b in background.list_backups()]
    assert days == list(range(20, 6, -1))
    assert not list(background.backup_dir().glob("*.tmp"))


def test_backup_ignores_foreign_files(client, board):
    folder = background.backup_dir()
    folder.mkdir(parents=True)
    (folder / "torre-copia-manual.db").write_text("x")
    background.make_backup(TODAY)
    assert [b.day for b in background.list_backups()] == [TODAY]
    assert (folder / "torre-copia-manual.db").exists()     # nunca se borra lo que no es nuestro


def test_backup_api_and_home(client, board):
    assert "Aún no hay ninguna" in client.get("/").text
    assert client.post("/api/backup").status_code == 200
    assert "Última: hoy" in client.get("/").text


def test_backup_api_without_data(client):
    assert client.post("/api/backup").status_code == 409


# --- Avisos -------------------------------------------------------------------------

def test_reminder_text():
    cards = [{"title": "A", "due": "2026-11-10", "board_name": "Casa"},
             {"title": "B", "due": "2026-11-14", "board_name": "Casa"},
             {"title": "C", "due": "2026-11-15", "board_name": "Trabajo"}]
    title, body = background.reminder_text(cards, TODAY)
    assert title == "Torre: 1 vencida, 1 para hoy, 1 para mañana"
    assert body.splitlines() == ["• A (vencida, Casa)", "• B (hoy, Casa)", "• C (mañana, Trabajo)"]
    assert background.reminder_text([], TODAY) is None

    many = [{"title": str(i), "due": "2026-11-14", "board_name": "X"} for i in range(8)]
    assert background.reminder_text(many, TODAY)[1].endswith("…y 3 más")


def test_reminders_once_a_day(client, board, notifications):
    make_card(client, board, "Pagar luz", due="2026-11-13")
    make_card(client, board, "Llamar", due="2026-11-14")
    make_card(client, board, "Lejos", due="2026-11-30")             # demasiado lejos
    make_card(client, board, "Hecha", due="2026-11-14", done=True)   # ya hecha
    archived = make_card(client, board, "Archivada", due="2026-11-14")
    client.patch(f"/api/cards/{archived}", json={"archived": True})

    assert background.check_reminders(TODAY)
    assert len(notifications) == 1
    assert notifications[0]["title"] == "Torre: 1 vencida, 1 para hoy"
    assert notifications[0]["urgent"]

    assert not background.check_reminders(TODAY)                     # ya avisó hoy
    assert background.check_reminders(date(2026, 11, 15))            # otro día, otra vez
    assert len(notifications) == 2


def test_no_reminder_when_nothing_due(client, board, notifications):
    make_card(client, board, "Lejos", due="2026-12-30")
    assert not background.check_reminders(TODAY)
    assert notifications == []


def test_reminders_can_be_disabled(client, board, notifications):
    make_card(client, board, "Hoy", due="2026-11-14")
    assert client.put("/api/settings/reminders", json={"enabled": False}).status_code == 200
    assert not db.reminders_enabled()
    assert not background.check_reminders(TODAY)
    client.put("/api/settings/reminders", json={"enabled": True})
    assert background.check_reminders(TODAY)


def test_failed_notification_retries(client, board, monkeypatch):
    make_card(client, board, "Hoy", due="2026-11-14")
    monkeypatch.setattr(background, "notify", lambda *a, **k: False)
    assert not background.check_reminders(TODAY)
    assert db.get_setting("reminded_on") == ""        # no se da por avisado: lo reintentará


def test_test_button(client, board, notifications):
    assert client.post("/api/reminders/test").json() == {"shown": "sample"}
    make_card(client, board, "Hoy", due=date.today().isoformat())
    assert client.post("/api/reminders/test").json() == {"shown": "pending"}
    assert len(notifications) == 2


def test_one_failing_job_does_not_stop_the_other(monkeypatch):
    ran = []
    monkeypatch.setattr(background, "make_backup", lambda: 1 / 0)
    monkeypatch.setattr(background, "check_reminders", lambda: ran.append(True))
    background.tick()
    assert ran == [True]
