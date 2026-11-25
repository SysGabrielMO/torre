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

Cuentas: cada persona se crea la suya desde la página de entrada («Crear
cuenta»), si el registro está abierto. Contra bots y abusos: como mucho 3
cuentas por IP y hora, un campo trampa invisible y un tope de usuarios
(TORRE_MAX_USERS, 50 por defecto). El primer usuario es administrador: puede
cerrar el registro, borrar cuentas y dar una contraseña temporal a quien la
olvide (que tendrá que cambiarla al entrar). Nadie, tampoco el administrador,
puede ver las contraseñas: solo se guarda su huella.

También desde la línea de comandos: python -m torre.users
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
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


def users_detail() -> list[dict]:
    """Para la página de administración (sin contraseñas, claro)."""
    with db.tx() as con:
        rows = con.execute("""
            SELECT u.username, u.created, u.is_admin, u.must_change,
                   (SELECT COUNT(*) FROM boards b WHERE b.owner = u.username) AS boards
              FROM users u ORDER BY u.username""").fetchall()
    return [dict(r) for r in rows]


def get_user(username: str | None) -> dict | None:
    if not username:
        return None
    with db.tx() as con:
        row = con.execute("SELECT username, is_admin, must_change FROM users WHERE username = ?",
                          (username,)).fetchone()
    return dict(row) if row else None


def is_admin(username: str | None) -> bool:
    user = get_user(username)
    return bool(user and user["is_admin"])


def set_admin(username: str, admin: bool) -> None:
    with db.tx() as con:
        if not admin and con.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND username != ?",
                                     (username,)).fetchone()[0] == 0:
            raise AuthError("Tiene que quedar al menos un administrador.")
        if not con.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                           (int(admin), username)).rowcount:
            raise AuthError(f"No existe el usuario «{username}».")


# --- Registro --------------------------------------------------------------------

def max_users() -> int:
    try:
        return int(os.environ.get("TORRE_MAX_USERS", "50"))
    except ValueError:
        return 50


def registration_open() -> bool:
    return db.get_setting("registration", "open") == "open"


def set_registration(open_: bool) -> None:
    db.set_setting("registration", "open" if open_ else "closed")


def register(username: str, password: str, password2: str) -> str:
    """Crea la cuenta de quien se registra desde la web."""
    if not registration_open():
        raise AuthError("El registro de cuentas nuevas está cerrado.")
    if user_count() >= max_users():
        raise AuthError("Se ha llegado al máximo de usuarios. Habla con el administrador.")
    if password != password2:
        raise AuthError("Las contraseñas no coinciden.")
    return create_user(username, password)


def user_count() -> int:
    with db.tx() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(username: str, password: str) -> str:
    """Crea un usuario. El primero de todos es administrador."""
    username = normalize_username(username)
    check_new_password(username, password)
    hashed = hash_password(password)
    with db.tx() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError(f"El usuario «{username}» ya existe.")
        first = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        con.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
                    (username, hashed, int(first)))
    return username


def set_password(username: str, password: str, temporary: bool = False) -> None:
    """Cambia la contraseña y cierra todas las sesiones abiertas de ese usuario.
    «temporary»: tendrá que cambiarla nada más entrar."""
    username = normalize_username(username)
    check_new_password(username, password)
    with db.tx() as con:
        cur = con.execute("UPDATE users SET password = ?, must_change = ? WHERE username = ?",
                          (hash_password(password), int(temporary), username))
        if not cur.rowcount:
            raise AuthError(f"No existe el usuario «{username}».")
        con.execute("DELETE FROM sessions WHERE username = ?", (username,))


def temporary_password(username: str) -> str:
    """Contraseña temporal para quien olvidó la suya (la ve el administrador una
    vez; al entrar con ella, el usuario tiene que poner una nueva)."""
    password = secrets.token_urlsafe(9)   # 12 caracteres
    set_password(username, password, temporary=True)
    return password


def delete_user(username: str) -> None:
    """Borra la cuenta y sus tableros (los que le compartieron siguen siendo de otros)."""
    username = username.strip().lower()
    with db.tx() as con:
        row = con.execute("SELECT is_admin FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise AuthError(f"No existe el usuario «{username}».")
        if row["is_admin"] and con.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] == 1:
            raise AuthError("No se puede borrar al único administrador.")
        con.execute("DELETE FROM users WHERE username = ?", (username,))


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
# Altas desde una misma IP: como mucho 3 por hora
signups = Throttle(per_ip=3, per_user=10 ** 9, window=60 * 60)
