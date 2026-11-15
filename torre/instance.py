"""¿Está abierta la ventana de Torre?

La ventana (desktop.py) bloquea un fichero mientras está abierta. El aviso
diario lo consulta: con Torre ya abierto no ofrece «Abrir Torre», que abriría
una segunda ventana. El bloqueo lo suelta el sistema al cerrarse el proceso,
aunque sea de golpe, así que nunca queda «abierta» por error.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


def lock_path() -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "torre" / "app.lock"


def hold() -> object:
    """Bloquea el fichero mientras viva el objeto devuelto (no lo sueltes)."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        pass   # ya hay otra ventana abierta: basta con que una lo tenga
    return f


def app_running() -> bool:
    path = lock_path()
    if not path.exists():
        return False
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
