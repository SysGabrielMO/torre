"""Usuarios, contraseñas e inicio de sesión (para Torre en un servidor).

Solo se usa con TORRE_AUTH=1 (lo pone la imagen de Docker). En el portátil,
Torre no pide contraseña: solo responde a este equipo.

- Contraseñas: scrypt (de la librería estándar), con sal aleatoria. Nunca se
  guarda la contraseña, solo su huella.
- Sesiones: un valor aleatorio en una cookie HttpOnly; en la base de datos se
  guarda solo su SHA-256, así que una copia de la base de datos no sirve
  para entrar. Duran 30 días y se pueden cerrar.
- Intentos fallidos: tras 5 desde la misma IP (o 10 contra el mismo usuario)
  en 15 minutos, se bloquea durante 15 minutos.

Los usuarios se crean desde la línea de comandos (python -m torre.users), no
hay registro desde la web.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from . import db

SESSION_DAYS = 30
MIN_PASSWORD = 10
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")

# scrypt: 16 MB de memoria y unos 50 ms por intento: barato para ti, caro para quien adivina
_N, _R, _P = 2 ** 14, 8, 1


class AuthError(ValueError):
    """Usuario o contraseña no válidos (mensaje para mostrar tal cual)."""


# --- Contraseñas -----------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# Se comprueba contra esta huella cuando el usuario no existe, para que tarde
# lo mismo y no se pueda averiguar qué usuarios hay midiendo el tiempo
_DUMMY = hash_password(secrets.token_urlsafe(16))


def check_new_password(username: str, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.")
    if password.strip().lower() == username.lower():
        raise AuthError("La contraseña no puede ser igual que el usuario.")


def normalize_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise AuthError("El usuario debe tener de 2 a 32 caracteres: letras minúsculas, "
                        "números, punto, guion o guion bajo.")
    return username


# --- Usuarios --------------------------------------------------------------------

def list_users() -> list[str]:
    with db.tx() as con:
        return [r["username"] for r in con.execute("SELECT username FROM users ORDER BY username")]


def user_count() -> int:
    with db.tx() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(username: str, password: str) -> str:
    username = normalize_username(username)
    check_new_password(username, password)
    with db.tx() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError(f"El usuario «{username}» ya existe.")
        con.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                    (username, hash_password(password)))
    return username


def set_password(username: str, password: str) -> None:
    """Cambia la contraseña y cierra todas las sesiones abiertas de ese usuario."""
    username = normalize_username(username)
    check_new_password(username, password)
    with db.tx() as con:
        cur = con.execute("UPDATE users SET password = ? WHERE username = ?",
                          (hash_password(password), username))
        if not cur.rowcount:
            raise AuthError(f"No existe el usuario «{username}».")
        con.execute("DELETE FROM sessions WHERE username = ?", (username,))


def delete_user(username: str) -> None:
    with db.tx() as con:
        if not con.execute("DELETE FROM users WHERE username = ?", (username.strip().lower(),)).rowcount:
            raise AuthError(f"No existe el usuario «{username}».")


def authenticate(username: str, password: str) -> str | None:
    """Devuelve el usuario si la contraseña es correcta; None si no."""
    username = username.strip().lower()
    with db.tx() as con:
        row = con.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
    ok = verify_password(password, row["password"] if row else _DUMMY)
    return username if ok and row else None


# --- Sesiones --------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (_now() + timedelta(days=SESSION_DAYS)).isoformat()
    with db.tx() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (_now().isoformat(),))
        con.execute("INSERT INTO sessions (token, username, expires) VALUES (?, ?, ?)",
                    (_digest(token), username, expires))
    return token


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    with db.tx() as con:
        row = con.execute("SELECT username FROM sessions WHERE token = ? AND expires > ?",
                          (_digest(token), _now().isoformat())).fetchone()
    return row["username"] if row else None


def end_session(token: str | None) -> None:
    if token:
        with db.tx() as con:
            con.execute("DELETE FROM sessions WHERE token = ?", (_digest(token),))


# --- Límite de intentos ----------------------------------------------------------

class Throttle:
    """Cuenta los fallos recientes por IP y por usuario (en memoria: Torre
    corre en un solo proceso)."""

    def __init__(self, per_ip: int = 5, per_user: int = 10, window: float = 15 * 60):
        self.per_ip, self.per_user, self.window = per_ip, per_user, window
        self._fails: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque:
        q = self._fails.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def wait_seconds(self, ip: str, username: str, now: float | None = None) -> int:
        """Segundos que faltan para poder intentarlo; 0 si se puede ya."""
        now = time.monotonic() if now is None else now
        with self._lock:
            waits = []
            for key, limit in ((f"ip:{ip}", self.per_ip), (f"user:{username}", self.per_user)):
                q = self._recent(key, now)
                if len(q) >= limit:
                    waits.append(q[-limit] + self.window - now)
            return int(max(waits, default=0)) + (1 if waits else 0)

    def failed(self, ip: str, username: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            for key in (f"ip:{ip}", f"user:{username}"):
                self._recent(key, now).append(now)

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(f"ip:{ip}", None)


throttle = Throttle()
