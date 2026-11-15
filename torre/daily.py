"""Copia y aviso diarios, aunque Torre esté cerrado.

Lo lanza el temporizador de systemd de tu usuario (torre-diario.timer, que
instala scripts/install-desktop.sh) cada día a las 9:00, o al entrar en la
sesión si a esa hora el portátil estaba apagado:

1. Hace la copia de seguridad del día (si no está hecha ya).
2. Si hay tarjetas vencidas o para hoy o mañana y aún no se avisó hoy, muestra
   la notificación. Con Torre cerrado lleva un botón «Abrir Torre»; con Torre
   abierto, no (ya lo tienes delante).

Usa los mismos datos y el mismo «ya avisé hoy» que Torre abierto, así que
nunca llegan dos avisos el mismo día.

A mano: .venv/bin/python -m torre.daily
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import background, db
from .instance import app_running

log = logging.getLogger("torre")
ROOT = Path(__file__).resolve().parent.parent


def notify_with_action(title: str, body: str, urgent: bool) -> str | None:
    """Notificación con botón «Abrir Torre». Espera a que se responda o se
    cierre: devuelve "open" si se pulsó, "" si se cerró, None si falló."""
    if not shutil.which("notify-send"):
        log.warning("Sin notify-send: instala libnotify-bin para recibir avisos")
        return None
    try:
        proc = subprocess.run(
            ["notify-send", "-a", "Torre", "-i", str(background.ICON),
             "-u", "critical" if urgent else "normal",
             "-A", "open=Abrir Torre", title, body],
            capture_output=True, text=True)
    except OSError as e:
        log.warning("No se pudo mostrar el aviso: %s", e)
        return None
    if proc.returncode != 0:
        log.warning("notify-send falló: %s", proc.stderr.strip())
        return None
    return proc.stdout.strip()


def launch_app() -> None:
    """Abre la ventana de Torre como un proceso independiente de este
    servicio (si no, systemd la cerraría al terminar el aviso)."""
    app = str(ROOT / "torre-app")
    if shutil.which("systemd-run"):
        subprocess.run(["systemd-run", "--user", "--collect", "--quiet", app], check=False)
    else:
        subprocess.Popen([app], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(today: date | None = None) -> None:
    today = today or date.today()
    try:
        path = background.make_backup(today)
        if path:
            log.info("Copia de seguridad: %s", path)
    except Exception:
        log.exception("Fallo al hacer la copia de seguridad")

    reminder = background.pending_reminder(today)
    if reminder is None:
        return
    title, body, urgent = reminder

    if app_running():
        if background.notify(title, body, urgent=urgent):
            db.set_setting("reminded_on", today.isoformat())
        return

    # Se apunta antes de esperar la respuesta (que puede tardar horas): si
    # mientras tanto abres Torre, no te vuelve a avisar
    db.set_setting("reminded_on", today.isoformat())
    choice = notify_with_action(title, body, urgent)
    if choice is None:
        db.set_setting("reminded_on", "")   # no se vio: que se reintente
    elif choice == "open" and not app_running():
        launch_app()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
