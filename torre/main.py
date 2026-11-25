"""Torre: tableros, listas y tarjetas (un «Trello» personal) en este equipo.

Servidor web local: páginas con Jinja2 y una API JSON que usa la interfaz
(static/board.js). Los datos están en db.py.

Se arranca con ./run.sh (navegador) o ./torre-app (ventana propia). En un
servidor, con Docker (ver Dockerfile): ahí pide usuario y contraseña.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import markdown
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from . import auth, background, db, trello

BASE = Path(__file__).resolve().parent

# La aplicación de escritorio arranca el servidor con una clave aleatoria para
# que solo su ventana pueda usarlo; en el navegador (./run.sh) no hay clave.
ACCESS_TOKEN = os.environ.get("TORRE_TOKEN", "")
TOKEN_COOKIE = "torre_token"
SESSION_COOKIE = "torre_session"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}

log = logging.getLogger("torre")


# --- Configuración (variables de entorno; la imagen de Docker pone las de servidor) ---

def auth_enabled() -> bool:
    """TORRE_AUTH=1: pide usuario y contraseña (Torre en un servidor)."""
    return os.environ.get("TORRE_AUTH") == "1"


def allowed_hosts() -> set[str]:
    """Nombres con los que se puede llegar a Torre: este equipo y los de
    TORRE_ALLOWED_HOSTS (p. ej. «torre.midominio.com»), separados por comas."""
    extra = os.environ.get("TORRE_ALLOWED_HOSTS", "")
    return LOCAL_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def reminders_available() -> bool:
    """En un servidor no hay escritorio donde mostrar avisos (TORRE_NO_REMINDERS=1)."""
    return not os.environ.get("TORRE_NO_REMINDERS")


@asynccontextmanager
async def lifespan(_app):
    """Copias diarias y avisos de fechas mientras el servidor está en marcha."""
    task = None if os.environ.get("TORRE_NO_BACKGROUND") else asyncio.create_task(background.loop())
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Torre", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def static(name: str) -> str:
    """URL de un estático con su fecha, para que el navegador no use uno viejo."""
    return f"/static/{name}?v={int((BASE / 'static' / name).stat().st_mtime)}"


templates.env.globals.update(static=static, colors=db.COLORS, color_names=db.COLOR_NAMES)


def token_ok(given: str | None) -> bool:
    return bool(given) and secrets.compare_digest(given.encode(), ACCESS_TOKEN.encode())


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}

# Sin sesión se puede ver la página de entrada y sus estilos, nada más
PUBLIC_PATHS = ("/login", "/registro", "/static/")


def same_origin(request: Request, host: str) -> bool:
    """¿La petición viene de una página de la propia Torre?"""
    origin = request.headers.get("origin")
    if origin is not None:
        return urlsplit(origin).netloc == host
    if auth_enabled():
        # En un servidor no se acepta una escritura que no diga de dónde viene
        return request.headers.get("sec-fetch-site") == "same-origin"
    return True


@app.middleware("http")
async def guard(request: Request, call_next):
    """Quién puede usar Torre.

    - «Host» debe ser este equipo o uno de TORRE_ALLOWED_HOSTS: evita que una
      web externa llegue al servidor haciendo pasar su dominio por el nuestro
      (DNS rebinding).
    - Lo que modifica datos no puede venir de otra web (cabecera Origin).
    - Con clave (aplicación de escritorio), hace falta la cookie de la clave.
    - Con TORRE_AUTH=1 (servidor), hace falta haber iniciado sesión.
    """
    host = request.headers.get("host", "")
    if host.rsplit(":", 1)[0].lower() not in allowed_hosts():
        return PlainTextResponse("Torre no responde a este nombre.", status_code=403)
    if request.method not in ("GET", "HEAD") and not same_origin(request, host):
        return PlainTextResponse("Petición de otro origen rechazada.", status_code=403)
    if ACCESS_TOKEN:
        given = request.query_params.get("token")
        if given is not None and token_ok(given):
            # La ventana entra con ?token=…: se guarda en una cookie y se quita de la URL
            url = request.url.remove_query_params("token")
            target = url.path + (f"?{url.query}" if url.query else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(TOKEN_COOKIE, ACCESS_TOKEN, httponly=True, samesite="strict")
            return response
        if not token_ok(request.cookies.get(TOKEN_COOKIE)):
            return PlainTextResponse("Acceso no autorizado.", status_code=403)
    if auth_enabled():
        request.state.user = await asyncio.to_thread(auth.session_user, request.cookies.get(SESSION_COOKIE))
        info = await asyncio.to_thread(auth.get_user, request.state.user)
        request.state.admin = bool(info and info["is_admin"])
        request.state.must_change = bool(info and info["must_change"])
        path = request.url.path
        if request.state.user is None and not path.startswith(PUBLIC_PATHS):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Tu sesión ha caducado: vuelve a entrar."}, status_code=401)
            target = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login" + (f"?next={quote(target)}" if path != "/" else ""),
                                    status_code=303)
        # Con contraseña temporal (la puso el administrador), primero hay que cambiarla
        if request.state.must_change and not path.startswith(("/cuenta", "/logout", "/static/")):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Antes tienes que cambiar tu contraseña."}, status_code=403)
            return RedirectResponse("/cuenta", status_code=303)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.exception_handler(db.NotFound)
async def not_found(request: Request, exc: db.NotFound):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "No existe (puede que se haya borrado)."}, status_code=404)
    return templates.TemplateResponse(request, "error.html", {"theme": db.get_theme(me(request))},
                                      status_code=404)


# --- Markdown ------------------------------------------------------------------

def render_markdown(text: str) -> str:
    """Descripción de la tarjeta a HTML. El HTML escrito a mano se muestra como
    texto (no se interpreta) y los enlaces solo pueden ser http(s) o mailto."""
    md = markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    html = md.convert(text)
    html = re.sub(r'(href|src)="(?!https?://|mailto:|#)[^"]*"', r'\1="#"', html)
    return html.replace("<a href=", '<a target="_blank" rel="noopener" href=')


# --- Inicio de sesión, registro y cuenta (solo con TORRE_AUTH=1) ------------------

def safe_next(value: str | None) -> str:
    """Solo rutas de la propia Torre: nunca «//otra-web.com» ni «https://…»."""
    if value and value.startswith("/") and not value.startswith(("//", "/\\")):
        return value
    return "/"


def me(request: Request) -> str | None:
    """Usuario que hace la petición (None en el portátil: sin usuarios)."""
    return getattr(request.state, "user", None) if auth_enabled() else None


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def read_form(request: Request) -> dict[str, str]:
    raw = await request.body()
    form = parse_qs(raw[:10_000].decode(errors="replace"), keep_blank_values=True)
    return {k: v[0] for k, v in form.items()}


def start_session(request: Request, user: str, target: str) -> RedirectResponse:
    token = auth.create_session(user)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")
    return response


def login_page(request: Request, error: str = "", status: int = 200, next_url: str = "/", username: str = ""):
    return templates.TemplateResponse(request, "login.html", {
        "theme": "auto", "error": error, "next": next_url, "username": username,
        "no_users": auth.user_count() == 0, "registration": auth.registration_open(),
    }, status_code=status)


@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    if getattr(request.state, "user", None):
        return RedirectResponse(safe_next(next), status_code=303)
    return login_page(request, next_url=safe_next(next))


@app.post("/login")
async def login(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    form = await read_form(request)
    username = form.get("username", "").strip().lower()[:64]
    password = form.get("password", "")[:1024]
    next_url = safe_next(form.get("next"))
    ip = client_ip(request)

    wait = auth.throttle.wait_seconds(ip, username)
    if wait:
        minutes = max(1, round(wait / 60))
        log.warning("Inicio de sesión bloqueado: usuario=%s ip=%s", username, ip)
        return login_page(request, f"Demasiados intentos fallidos. Prueba de nuevo en {minutes} "
                                   f"minuto{'s' if minutes != 1 else ''}.", 429, next_url, username)
    user = await asyncio.to_thread(auth.authenticate, username, password)
    if user is None:
        auth.throttle.failed(ip, username)
        log.warning("Inicio de sesión fallido: usuario=%s ip=%s", username, ip)
        return login_page(request, "Usuario o contraseña incorrectos.", 401, next_url, username)
    auth.throttle.succeeded(ip)
    return await asyncio.to_thread(start_session, request, user, next_url)


def register_page(request: Request, error: str = "", status: int = 200, username: str = ""):
    return templates.TemplateResponse(request, "register.html", {
        "theme": "auto", "error": error, "username": username,
        "open": auth.registration_open(), "min_password": auth.MIN_PASSWORD,
    }, status_code=status)


@app.get("/registro")
def register_form(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=303)
    return register_page(request)


@app.post("/registro")
async def register(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    form = await read_form(request)
    username = form.get("username", "").strip().lower()[:64]
    ip = client_ip(request)
    if form.get("website"):
        # Campo trampa: invisible para personas, los bots lo rellenan
        log.warning("Registro de bot descartado: ip=%s", ip)
        return register_page(request, "No se ha podido crear la cuenta.", 400)
    if auth.signups.wait_seconds(ip, ""):
        log.warning("Registro bloqueado por exceso: ip=%s", ip)
        return register_page(request, "Se han creado demasiadas cuentas desde tu conexión. "
                                      "Prueba dentro de una hora.", 429, username)
    try:
        user = await asyncio.to_thread(auth.register, username, form.get("password", "")[:1024],
                                       form.get("password2", "")[:1024])
    except auth.AuthError as e:
        return register_page(request, str(e), 400, username)
    auth.signups.failed(ip, "")     # cuenta como un alta de esta IP
    log.warning("Cuenta creada: usuario=%s ip=%s", user, ip)
    return await asyncio.to_thread(start_session, request, user, "/")


@app.post("/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login" if auth_enabled() else "/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def account_page(request: Request, error: str = "", done: str = "", status: int = 200):
    user = me(request)
    return templates.TemplateResponse(request, "account.html", {
        "theme": db.get_theme(user), "error": error, "done": done,
        "must_change": getattr(request.state, "must_change", False),
        "min_password": auth.MIN_PASSWORD,
        "boards": [b for b in db.list_boards(user) if b["role"] == "owner"],
    }, status_code=status)


@app.get("/cuenta")
def account(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    return account_page(request)


@app.post("/cuenta")
async def change_password(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    user = me(request)
    form = await read_form(request)
    ip = client_ip(request)
    if auth.throttle.wait_seconds(ip, user):
        return account_page(request, "Demasiados intentos fallidos. Prueba más tarde.", status=429)
    if await asyncio.to_thread(auth.authenticate, user, form.get("current", "")[:1024]) is None:
        auth.throttle.failed(ip, user)
        return account_page(request, "La contraseña actual no es correcta.", status=400)
    if form.get("new", "") != form.get("new2", ""):
        return account_page(request, "Las contraseñas nuevas no coinciden.", status=400)
    try:
        await asyncio.to_thread(auth.set_password, user, form.get("new", "")[:1024])
    except auth.AuthError as e:
        return account_page(request, str(e), status=400)
    # Cambiar la contraseña cierra todas las sesiones; esta se abre de nuevo
    response = await asyncio.to_thread(start_session, request, user, "/cuenta?hecho=1")
    return response


# --- Páginas -------------------------------------------------------------------

@app.get("/")
def index(request: Request):
    user = me(request)
    boards = db.list_boards(user)
    show_settings = not auth_enabled() or auth.is_admin(user)
    backups = background.list_backups() if show_settings else []
    return templates.TemplateResponse(request, "index.html", {
        "theme": db.get_theme(user),
        "boards": [b for b in boards if not b["archived"] and b["role"] == "owner"],
        "shared": [b for b in boards if not b["archived"] and b["role"] == "member"],
        "archived": [b for b in boards if b["archived"] and b["role"] == "owner"],
        "show_settings": show_settings,
        "last_backup": backups[0] if backups else None,
        "backup_count": len(backups),
        "backup_dir": str(background.backup_dir()).replace(str(Path.home()), "~", 1),
        "reminders": db.reminders_enabled(),
        "reminders_available": reminders_available(),
        "daily": background.daily_timer() if reminders_available() else None,
        "today": date.today(),
    })


@app.get("/b/{board_id}")
def board_page(request: Request, board_id: int):
    user = me(request)
    role = db.role(board_id, user)
    return templates.TemplateResponse(request, "board.html", {
        "theme": db.get_theme(user), "board": db.board_name(board_id), "role": role})


# --- API: modelos --------------------------------------------------------------

def _clean(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("no puede estar vacío")
    return value


def _color(value: str) -> str:
    if value not in db.COLORS:
        raise ValueError("color desconocido")
    return value


class Model(BaseModel):
    def changes(self) -> dict:
        """Solo los campos que venían en la petición."""
        return self.model_dump(exclude_unset=True)


class BoardIn(Model):
    name: str = Field(max_length=100)
    color: str = "blue"
    _name = field_validator("name")(_clean)
    _color = field_validator("color")(_color)


class BoardPatch(Model):
    name: str | None = Field(None, max_length=100)
    color: str | None = None
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else _clean(v)

    @field_validator("color")
    @classmethod
    def _c(cls, v):
        return None if v is None else _color(v)


class NameIn(Model):
    name: str = Field(max_length=100)
    _name = field_validator("name")(_clean)


class ListPatch(Model):
    name: str | None = Field(None, max_length=100)
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else _clean(v)


class IndexIn(Model):
    index: int = Field(ge=0)


class CardIn(Model):
    title: str = Field(max_length=300)
    _title = field_validator("title")(_clean)


class CardPatch(Model):
    title: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=50_000)
    due: str | None = None        # "2026-11-30"; null la quita
    done: bool | None = None
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def _t(cls, v):
        return None if v is None else _clean(v)

    @field_validator("due")
    @classmethod
    def _d(cls, v):
        if v is not None and not db.valid_date(v):
            raise ValueError("fecha no válida")
        return v


class CardMove(Model):
    list_id: int
    index: int = Field(ge=0)


class LabelIn(Model):
    name: str = Field("", max_length=40)
    color: str
    _color = field_validator("color")(_color)

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return " ".join(v.split())


class LabelPatch(Model):
    name: str | None = Field(None, max_length=40)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else " ".join(v.split())

    @field_validator("color")
    @classmethod
    def _c(cls, v):
        return None if v is None else _color(v)


class LabelIds(Model):
    label_ids: list[int]


class ItemIn(Model):
    text: str = Field(max_length=300)
    _text = field_validator("text")(_clean)


class ItemPatch(Model):
    text: str | None = Field(None, max_length=300)
    done: bool | None = None

    @field_validator("text")
    @classmethod
    def _t(cls, v):
        return None if v is None else _clean(v)


class ThemeIn(Model):
    theme: str

    @field_validator("theme")
    @classmethod
    def _t(cls, v):
        if v not in db.THEMES:
            raise ValueError("tema desconocido")
        return v


def _no_nulls(changes: dict, nullable: tuple[str, ...] = ()) -> dict:
    """Quita los campos enviados como null, salvo los que admiten null."""
    return {k: v for k, v in changes.items() if v is not None or k in nullable}


OK = {"ok": True}


# --- Permisos ----------------------------------------------------------------------
# En un servidor (TORRE_AUTH=1) cada operación comprueba que quien la hace puede
# tocar el tablero al que pertenece lo que toca. Si no puede verlo, la respuesta
# es la misma que si no existiera (404). En el portátil no hay restricciones.

def can(request: Request, board_id: int, owner: bool = False) -> str:
    role = db.role(board_id, me(request))
    if owner and role != "owner":
        raise HTTPException(403, "Solo el dueño del tablero puede hacer esto.")
    return role


def can_in(request: Request, kind: str, row_id: int) -> int:
    """Comprueba el acceso a una lista, tarjeta, etiqueta o elemento; devuelve su tablero."""
    board_id = db.board_of(kind, row_id)
    can(request, board_id)
    return board_id


def admin_only(request: Request) -> str:
    user = me(request)
    if auth_enabled() and not auth.is_admin(user):
        raise HTTPException(403, "Solo para administradores.")
    return user


@app.exception_handler(db.NotAllowed)
async def not_allowed(request: Request, exc: db.NotAllowed):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(auth.AuthError)
async def auth_error(request: Request, exc: auth.AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# --- API: tableros -------------------------------------------------------------

@app.post("/api/boards", status_code=201)
def api_create_board(request: Request, body: BoardIn):
    board_id = db.create_board(body.name, body.color, owner=me(request))
    for name in ("Por hacer", "En progreso", "Hecho"):
        db.create_list(board_id, name)
    return {"id": board_id}


@app.get("/api/boards/{board_id}")
def api_board(request: Request, board_id: int):
    role = can(request, board_id)
    return db.get_board(board_id) | {"role": role}


@app.patch("/api/boards/{board_id}")
def api_update_board(request: Request, board_id: int, body: BoardPatch):
    can(request, board_id, owner=True)
    db.update_board(board_id, **{k: int(v) if isinstance(v, bool) else v
                                 for k, v in _no_nulls(body.changes()).items()})
    return OK


@app.delete("/api/boards/{board_id}")
def api_delete_board(request: Request, board_id: int):
    can(request, board_id, owner=True)
    db.delete_board(board_id)
    return OK


@app.get("/api/boards/{board_id}/archive")
def api_archive(request: Request, board_id: int):
    can(request, board_id)
    return db.get_archive(board_id)


# --- API: compartir tableros -------------------------------------------------------

class MemberIn(Model):
    username: str = Field(max_length=64)


@app.get("/api/boards/{board_id}/members")
def api_members(request: Request, board_id: int):
    return db.members(board_id) | {"role": can(request, board_id)}


@app.post("/api/boards/{board_id}/members", status_code=201)
def api_add_member(request: Request, board_id: int, body: MemberIn):
    can(request, board_id, owner=True)
    db.add_member(board_id, body.username.strip().lower())
    return db.members(board_id)


@app.delete("/api/boards/{board_id}/members/{username}")
def api_remove_member(request: Request, board_id: int, username: str):
    role = can(request, board_id)
    if role != "owner" and username != me(request):
        raise HTTPException(403, "Solo el dueño puede quitar a otros del tablero.")
    db.remove_member(board_id, username)
    return OK


@app.get("/api/users")
def api_users(request: Request):
    """Nombres de usuario, para elegir con quién compartir."""
    user = me(request)
    return [u for u in auth.list_users() if u != user] if auth_enabled() else []


# --- API: listas ---------------------------------------------------------------

@app.post("/api/boards/{board_id}/lists", status_code=201)
def api_create_list(request: Request, board_id: int, body: NameIn):
    can(request, board_id)
    return {"id": db.create_list(board_id, body.name)}


@app.patch("/api/lists/{list_id}")
def api_update_list(request: Request, list_id: int, body: ListPatch):
    can_in(request, "lists", list_id)
    db.update_list(list_id, name=body.name, archived=body.archived)
    return OK


@app.post("/api/lists/{list_id}/move")
def api_move_list(request: Request, list_id: int, body: IndexIn):
    can_in(request, "lists", list_id)
    db.move_list(list_id, body.index)
    return OK


@app.delete("/api/lists/{list_id}")
def api_delete_list(request: Request, list_id: int):
    can_in(request, "lists", list_id)
    db.delete_list(list_id)
    return OK


# --- API: tarjetas -------------------------------------------------------------

def _card(card_id: int) -> dict:
    card = db.get_card(card_id)
    card["description_html"] = render_markdown(card["description"])
    return card


@app.post("/api/lists/{list_id}/cards", status_code=201)
def api_create_card(request: Request, list_id: int, body: CardIn):
    can_in(request, "lists", list_id)
    return {"id": db.create_card(list_id, body.title)}


@app.get("/api/cards/{card_id}")
def api_card(request: Request, card_id: int):
    can_in(request, "cards", card_id)
    return _card(card_id)


@app.patch("/api/cards/{card_id}")
def api_update_card(request: Request, card_id: int, body: CardPatch):
    can_in(request, "cards", card_id)
    db.update_card(card_id, **_no_nulls(body.changes(), nullable=("due",)))
    return _card(card_id)


@app.post("/api/cards/{card_id}/move")
def api_move_card(request: Request, card_id: int, body: CardMove):
    can_in(request, "cards", card_id)     # la lista de destino debe ser del mismo tablero (db)
    db.move_card(card_id, body.list_id, body.index)
    return OK


@app.delete("/api/cards/{card_id}")
def api_delete_card(request: Request, card_id: int):
    can_in(request, "cards", card_id)
    db.delete_card(card_id)
    return OK


@app.put("/api/cards/{card_id}/labels")
def api_card_labels(request: Request, card_id: int, body: LabelIds):
    can_in(request, "cards", card_id)     # solo se aplican etiquetas de su tablero (db)
    db.set_card_labels(card_id, body.label_ids)
    return _card(card_id)


# --- API: etiquetas ------------------------------------------------------------

@app.post("/api/boards/{board_id}/labels", status_code=201)
def api_create_label(request: Request, board_id: int, body: LabelIn):
    can(request, board_id)
    return {"id": db.create_label(board_id, body.name, body.color)}


@app.patch("/api/labels/{label_id}")
def api_update_label(request: Request, label_id: int, body: LabelPatch):
    can_in(request, "labels", label_id)
    db.update_label(label_id, **_no_nulls(body.changes()))
    return OK


@app.delete("/api/labels/{label_id}")
def api_delete_label(request: Request, label_id: int):
    can_in(request, "labels", label_id)
    db.delete_label(label_id)
    return OK


# --- API: checklist ------------------------------------------------------------

@app.post("/api/cards/{card_id}/items", status_code=201)
def api_create_item(request: Request, card_id: int, body: ItemIn):
    can_in(request, "cards", card_id)
    db.create_item(card_id, body.text)
    return _card(card_id)


@app.patch("/api/items/{item_id}")
def api_update_item(request: Request, item_id: int, body: ItemPatch):
    can_in(request, "items", item_id)
    db.update_item(item_id, **_no_nulls(body.changes()))
    return OK


@app.delete("/api/items/{item_id}")
def api_delete_item(request: Request, item_id: int):
    can_in(request, "items", item_id)
    db.delete_item(item_id)
    return OK


# --- API: búsqueda y ajustes ---------------------------------------------------

@app.get("/api/search")
def api_search(request: Request, q: str = ""):
    q = q.strip()
    return db.search(q, user=me(request)) if q else []


@app.put("/api/settings/theme")
def api_theme(request: Request, body: ThemeIn):
    db.set_theme(body.theme, user=me(request))
    return OK


class RemindersIn(Model):
    enabled: bool


@app.put("/api/settings/reminders")
def api_reminders(request: Request, body: RemindersIn):
    admin_only(request)
    db.set_setting("reminders", "1" if body.enabled else "0")
    return OK


@app.post("/api/reminders/test")
def api_reminders_test():
    """«Probar aviso»: muestra ya lo pendiente (o un aviso de ejemplo)."""
    if not reminders_available():
        raise HTTPException(409, "En el servidor no hay avisos del escritorio.")
    if background.check_reminders(force=True):
        return {"shown": "pending"}
    if background.notify("Torre: los avisos funcionan",
                         "Ahora mismo no tienes tarjetas vencidas ni para hoy o mañana."):
        return {"shown": "sample"}
    raise HTTPException(503, "No se pudo mostrar la notificación (¿está instalado libnotify-bin?).")


MAX_IMPORT = 50 * 1024 * 1024   # 50 MB: los tableros de Trello más grandes rondan unos pocos MB


@app.post("/api/import/trello", status_code=201)
async def api_import_trello(request: Request):
    """Recibe la exportación JSON de un tablero de Trello (tal cual, en el cuerpo)."""
    if int(request.headers.get("content-length") or 0) > MAX_IMPORT:
        raise HTTPException(413, "El fichero es demasiado grande (más de 50 MB).")
    raw = await request.body()
    if len(raw) > MAX_IMPORT:
        raise HTTPException(413, "El fichero es demasiado grande (más de 50 MB).")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "El fichero no es JSON. En Trello: menú del tablero → "
                                 "«Imprimir, exportar y compartir» → «Exportar como JSON».")
    try:
        return await asyncio.to_thread(trello.import_board, data, me(request))
    except trello.NotTrello as e:
        raise HTTPException(422, str(e))


@app.post("/api/backup")
def api_backup(request: Request):
    admin_only(request)
    path = background.make_backup(force=True)
    if path is None:
        raise HTTPException(409, "Todavía no hay datos que copiar.")
    return {"path": str(path)}


# --- Administración (solo administradores, en un servidor) ---------------------------

@app.get("/admin")
def admin_page(request: Request):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    admin_only(request)
    return templates.TemplateResponse(request, "admin.html", {
        "theme": db.get_theme(me(request)), "users": auth.users_detail(),
        "registration": auth.registration_open(), "max_users": auth.max_users(),
    })


class RegistrationIn(Model):
    open: bool


@app.put("/api/admin/registration")
def api_admin_registration(request: Request, body: RegistrationIn):
    admin_only(request)
    auth.set_registration(body.open)
    return OK


class AdminIn(Model):
    admin: bool


@app.put("/api/admin/users/{username}/admin")
def api_admin_set_admin(request: Request, username: str, body: AdminIn):
    admin_only(request)
    auth.set_admin(username, body.admin)
    return OK


@app.post("/api/admin/users/{username}/password")
def api_admin_reset(request: Request, username: str):
    admin_only(request)
    return {"password": auth.temporary_password(username)}


@app.delete("/api/admin/users/{username}")
def api_admin_delete(request: Request, username: str):
    if username == admin_only(request):
        raise HTTPException(400, "No puedes borrar tu propia cuenta desde aquí.")
    auth.delete_user(username)
    return OK
