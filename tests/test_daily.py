"""El aviso diario con Torre cerrado (torre.daily) y la detección de la ventana."""
import subprocess
import sys
from datetime import date

import pytest

from torre import background, daily, db, instance

TODAY = date(2026, 11, 14)


@pytest.fixture
def due_card(client, board):
    card = client.post(f"/api/lists/{board['lists'][0]['id']}/cards", json={"title": "Pagar luz"}).json()["id"]
    client.patch(f"/api/cards/{card}", json={"due": "2026-11-14"})
    return card


@pytest.fixture
def fake(monkeypatch):
    """Sustituye la notificación con botón y la apertura de la ventana."""
    calls = {"action": [], "launched": 0, "answer": "open"}
    monkeypatch.setattr(daily, "notify_with_action",
                        lambda title, body, urgent: calls["action"].append(title) or calls["answer"])
    monkeypatch.setattr(daily, "launch_app", lambda: calls.__setitem__("launched", calls["launched"] + 1))
    return calls


def test_closed_app_offers_open_button(due_card, fake, notifications):
    daily.run(TODAY)
    assert fake["action"] == ["Torre: 1 para hoy"]
    assert fake["launched"] == 1
    assert notifications == []                        # el aviso sencillo no se usa
    assert db.get_setting("reminded_on") == "2026-11-14"
    assert [b.day for b in background.list_backups()] == [TODAY]


def test_dismissed_notification_does_not_open(due_card, fake):
    fake["answer"] = ""
    daily.run(TODAY)
    assert fake["launched"] == 0
    assert db.get_setting("reminded_on") == "2026-11-14"


def test_failed_notification_is_retried(due_card, fake):
    fake["answer"] = None
    daily.run(TODAY)
    assert db.get_setting("reminded_on") == ""
    fake["answer"] = ""
    daily.run(TODAY)
    assert len(fake["action"]) == 2


def test_open_app_gets_plain_notification(due_card, fake, notifications):
    lock = instance.hold()
    try:
        assert instance.app_running()
        daily.run(TODAY)
    finally:
        lock.close()
    assert fake["action"] == [] and fake["launched"] == 0
    assert len(notifications) == 1


def test_only_once_a_day_even_with_app_open(due_card, fake, notifications):
    background.check_reminders(TODAY)                 # Torre abierto ya avisó por la mañana
    daily.run(TODAY)
    assert len(notifications) == 1 and fake["action"] == []


def test_nothing_due_still_backs_up(client, board, fake):
    daily.run(TODAY)
    assert fake["action"] == []
    assert [b.day for b in background.list_backups()] == [TODAY]


def test_lock_released_when_window_dies(tmp_path):
    """Si la ventana muere de golpe, el sistema suelta el bloqueo."""
    code = "from torre import instance; lock = instance.hold(); import time; time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(50):
            if instance.app_running():
                break
            import time
            time.sleep(0.1)
        assert instance.app_running()
    finally:
        proc.kill()
        proc.wait()
    assert not instance.app_running()


def test_daily_timer_status(monkeypatch):
    assert background.daily_timer() == {"active": False, "hour": None}
    path = background.timer_file()
    path.parent.mkdir(parents=True)
    path.write_text("[Timer]\nOnCalendar=*-*-* 08:30:00\nPersistent=true\n")

    class Done:
        stdout = "active\n"
    monkeypatch.setattr(background.subprocess, "run", lambda *a, **k: Done)
    assert background.daily_timer() == {"active": True, "hour": "08:30"}


def test_home_shows_timer_hint(client):
    assert "./scripts/install-desktop.sh" in client.get("/").text
