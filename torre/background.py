"""Tareas de fondo del servidor: copia de seguridad diaria y avisos de fechas.

Corren mientras Torre está abierto, tanto en la ventana (./torre-app) como en
el navegador (./run.sh): al arrancar y luego cada media hora.

- Copias: una al día en ~/.local/share/torre/copias/torre-AAAA-MM-DD.db (fuera
  de la carpeta del proyecto, por si se borra); se guardan las últimas 14.
- Avisos: una notificación del escritorio al día como mucho, si hay tarjetas
  vencidas o que vencen hoy o mañana. Se pueden desactivar en la portada.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db

log = logging.getLogger("torre")

KEEP = 14                      # copias diarias que se conservan
INTERVAL = 30 * 60             # segundos entre comprobaciones
ICON = Path(__file__).resolve().parent / "static" / "icon.svg"


# --- Copias de seguridad ---------------------------------------------------------

def backup_dir() -> Path:
    default = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "torre" / "copias"
    return Path(os.environ.get("TORRE_BACKUP_DIR") or default)


@dataclass
class Backup:
    path: Path
    day: date
    size: int
    modified: datetime


def list_backups() -> list[Backup]:
    """Copias existentes, de la más reciente a la más antigua."""
    found = []
    folder = backup_dir()
    for f in folder.glob("torre-*.db") if folder.is_dir() else ():
        try:
            day = date.fromisoformat(f.stem.removeprefix("torre-"))
        except ValueError:
            continue
        st = f.stat()
        found.append(Backup(f, day, st.st_size, datetime.fromtimestamp(st.st_mtime)))
    return sorted(found, key=lambda b: b.day, reverse=True)


def make_backup(today: date | None = None, force: bool = False) -> Path | None:
    """Copia la base de datos a la carpeta de copias, una por día.

    Sin «force», si hoy ya hay copia no hace nada. Usa la copia en caliente de
    SQLite, así que es segura con Torre abierto. Devuelve la ruta creada.
    """
    source = db.db_path()
    if not source.exists():
        return None
    today = today or date.today()
    folder = backup_dir()
    target = folder / f"torre-{today.isoformat()}.db"
    if target.exists() and not force:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".db.tmp")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(tmp)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    os.replace(tmp, target)   # o está entera o no está: nunca una copia a medias
    prune()
    return target


def prune(keep: int = KEEP) -> None:
    for old in list_backups()[keep:]:
        old.path.unlink(missing_ok=True)


# --- Avisos de fechas ------------------------------------------------------------

def reminder_text(cards: list[dict], today: date) -> tuple[str, str] | None:
    """Título y cuerpo de la notificación, o None si no hay nada que avisar."""
    if not cards:
        return None
    t, tomorrow = today.isoformat(), (today + timedelta(days=1)).isoformat()
    overdue = [c for c in cards if c["due"] < t]
    due_today = [c for c in cards if c["due"] == t]
    due_tomorrow = [c for c in cards if c["due"] == tomorrow]
    parts = []
    if overdue:
        parts.append(f"{len(overdue)} vencida{'s' if len(overdue) != 1 else ''}")
    if due_today:
        parts.append(f"{len(due_today)} para hoy")
    if due_tomorrow:
        parts.append(f"{len(due_tomorrow)} para mañana")
    title = "Torre: " + ", ".join(parts)

    def when(c):
        if c["due"] < t:
            return "vencida"
        return "hoy" if c["due"] == t else "mañana"

    lines = [f"• {c['title']} ({when(c)}, {c['board_name']})" for c in cards[:5]]
    if len(cards) > 5:
        lines.append(f"…y {len(cards) - 5} más")
    return title, "\n".join(lines)


def notify(title: str, body: str, urgent: bool = False) -> bool:
    """Notificación del escritorio con notify-send (paquete libnotify-bin)."""
    if not shutil.which("notify-send"):
        log.warning("Sin notify-send: instala libnotify-bin para recibir avisos")
        return False
    try:
        subprocess.run(["notify-send", "-a", "Torre", "-i", str(ICON),
                        "-u", "critical" if urgent else "normal", title, body],
                       timeout=10, check=True, capture_output=True)
        return True
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("No se pudo mostrar el aviso: %s", e)
        return False


def pending_reminder(today: date | None = None, force: bool = False) -> tuple[str, str, bool] | None:
    """(título, cuerpo, urgente) del aviso de hoy, o None si no toca: avisos
    desactivados, ya se avisó hoy o no hay nada pendiente. «force» ignora lo
    de desactivados y ya avisado (para «Probar aviso»)."""
    today = today or date.today()
    if not force and (not db.reminders_enabled()
                      or db.get_setting("reminded_on") == today.isoformat()):
        return None
    cards = db.due_cards((today + timedelta(days=1)).isoformat())
    text = reminder_text(cards, today)
    if text is None:
        return None
    return *text, any(c["due"] < today.isoformat() for c in cards)


def check_reminders(today: date | None = None, force: bool = False) -> bool:
    """Avisa si hay algo pendiente y aún no se avisó hoy. Devuelve si avisó."""
    today = today or date.today()
    reminder = pending_reminder(today, force)
    if reminder is None:
        return False
    title, body, urgent = reminder
    if notify(title, body, urgent=urgent):
        db.set_setting("reminded_on", today.isoformat())
        return True
    return False


# --- Bucle ------------------------------------------------------------------------

def tick() -> None:
    for job in (make_backup, check_reminders):
        try:
            job()
        except Exception:   # que un fallo en una tarea no pare las demás ni el bucle
            log.exception("Fallo en la tarea de fondo %s", job.__name__)


async def loop() -> None:
    await asyncio.sleep(3)   # deja que la ventana se abra antes del primer aviso
    while True:
        await asyncio.to_thread(tick)
        await asyncio.sleep(INTERVAL)


# --- Temporizador diario (systemd) --------------------------------------------------

def timer_file() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config / "systemd" / "user" / "torre-diario.timer"


def daily_timer() -> dict:
    """Estado del aviso diario con Torre cerrado: si está activo y a qué hora."""
    hour = None
    try:
        for line in timer_file().read_text().splitlines():
            if line.startswith("OnCalendar="):
                hour = line.rsplit(" ", 1)[-1][:5]   # "*-*-* 09:00:00" → "09:00"
    except OSError:
        return {"active": False, "hour": None}
    try:
        state = subprocess.run(["systemctl", "--user", "is-active", "torre-diario.timer"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        state = ""
    return {"active": state == "active", "hour": hour}
