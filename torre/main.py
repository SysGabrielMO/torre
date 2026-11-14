"""Torre: tableros, listas y tarjetas (un «Trello» personal) en este equipo.

Servidor web local: páginas con Jinja2 y una API JSON que usa la interfaz
(static/board.js). Los datos están en db.py.

Se arranca con ./run.sh (navegador) o ./torre-app (ventana propia).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import markdown
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from . import background, db

BASE = Path(__file__).resolve().parent

# La aplicación de escritorio arranca el servidor con una clave aleatoria para
# que solo su ventana pueda usarlo; en el navegador (./run.sh) no hay clave.
ACCESS_TOKEN = os.environ.get("TORRE_TOKEN", "")
TOKEN_COOKIE = "torre_token"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

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


@app.middleware("http")
async def guard(request: Request, call_next):
    """Solo este equipo y solo esta aplicación.

    - «Host» debe ser local: evita que una web externa llegue al servidor
      haciendo pasar su dominio por 127.0.0.1 (DNS rebinding).
    - Lo que modifica datos no puede venir de otra web (cabecera Origin).
    - Con clave (aplicación de escritorio), hace falta la cookie.
    """
    host = request.headers.get("host", "")
    if host.rsplit(":", 1)[0] not in ALLOWED_HOSTS:
        return PlainTextResponse("Torre solo se usa desde este equipo.", status_code=403)
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != host:
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
    return await call_next(request)


@app.exception_handler(db.NotFound)
async def not_found(request: Request, exc: db.NotFound):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "No existe (puede que se haya borrado)."}, status_code=404)
    return templates.TemplateResponse(request, "error.html", {"theme": db.get_theme()},
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


# --- Páginas -------------------------------------------------------------------

@app.get("/")
def index(request: Request):
    boards = db.list_boards()
    backups = background.list_backups()
    return templates.TemplateResponse(request, "index.html", {
        "theme": db.get_theme(),
        "boards": [b for b in boards if not b["archived"]],
        "archived": [b for b in boards if b["archived"]],
        "last_backup": backups[0] if backups else None,
        "backup_count": len(backups),
        "backup_dir": str(background.backup_dir()).replace(str(Path.home()), "~", 1),
        "reminders": db.reminders_enabled(),
        "today": date.today(),
    })


@app.get("/b/{board_id}")
def board_page(request: Request, board_id: int):
    return templates.TemplateResponse(request, "board.html", {
        "theme": db.get_theme(), "board": db.board_name(board_id)})


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


# --- API: tableros -------------------------------------------------------------

@app.post("/api/boards", status_code=201)
def api_create_board(body: BoardIn):
    board_id = db.create_board(body.name, body.color)
    for name in ("Por hacer", "En progreso", "Hecho"):
        db.create_list(board_id, name)
    return {"id": board_id}


@app.get("/api/boards/{board_id}")
def api_board(board_id: int):
    return db.get_board(board_id)


@app.patch("/api/boards/{board_id}")
def api_update_board(board_id: int, body: BoardPatch):
    db.update_board(board_id, **{k: int(v) if isinstance(v, bool) else v
                                 for k, v in _no_nulls(body.changes()).items()})
    return OK


@app.delete("/api/boards/{board_id}")
def api_delete_board(board_id: int):
    db.delete_board(board_id)
    return OK


@app.get("/api/boards/{board_id}/archive")
def api_archive(board_id: int):
    return db.get_archive(board_id)


# --- API: listas ---------------------------------------------------------------

@app.post("/api/boards/{board_id}/lists", status_code=201)
def api_create_list(board_id: int, body: NameIn):
    return {"id": db.create_list(board_id, body.name)}


@app.patch("/api/lists/{list_id}")
def api_update_list(list_id: int, body: ListPatch):
    db.update_list(list_id, name=body.name, archived=body.archived)
    return OK


@app.post("/api/lists/{list_id}/move")
def api_move_list(list_id: int, body: IndexIn):
    db.move_list(list_id, body.index)
    return OK


@app.delete("/api/lists/{list_id}")
def api_delete_list(list_id: int):
    db.delete_list(list_id)
    return OK


# --- API: tarjetas -------------------------------------------------------------

def _card(card_id: int) -> dict:
    card = db.get_card(card_id)
    card["description_html"] = render_markdown(card["description"])
    return card


@app.post("/api/lists/{list_id}/cards", status_code=201)
def api_create_card(list_id: int, body: CardIn):
    return {"id": db.create_card(list_id, body.title)}


@app.get("/api/cards/{card_id}")
def api_card(card_id: int):
    return _card(card_id)


@app.patch("/api/cards/{card_id}")
def api_update_card(card_id: int, body: CardPatch):
    db.update_card(card_id, **_no_nulls(body.changes(), nullable=("due",)))
    return _card(card_id)


@app.post("/api/cards/{card_id}/move")
def api_move_card(card_id: int, body: CardMove):
    db.move_card(card_id, body.list_id, body.index)
    return OK


@app.delete("/api/cards/{card_id}")
def api_delete_card(card_id: int):
    db.delete_card(card_id)
    return OK


@app.put("/api/cards/{card_id}/labels")
def api_card_labels(card_id: int, body: LabelIds):
    db.set_card_labels(card_id, body.label_ids)
    return _card(card_id)


# --- API: etiquetas ------------------------------------------------------------

@app.post("/api/boards/{board_id}/labels", status_code=201)
def api_create_label(board_id: int, body: LabelIn):
    return {"id": db.create_label(board_id, body.name, body.color)}


@app.patch("/api/labels/{label_id}")
def api_update_label(label_id: int, body: LabelPatch):
    db.update_label(label_id, **_no_nulls(body.changes()))
    return OK


@app.delete("/api/labels/{label_id}")
def api_delete_label(label_id: int):
    db.delete_label(label_id)
    return OK


# --- API: checklist ------------------------------------------------------------

@app.post("/api/cards/{card_id}/items", status_code=201)
def api_create_item(card_id: int, body: ItemIn):
    db.create_item(card_id, body.text)
    return _card(card_id)


@app.patch("/api/items/{item_id}")
def api_update_item(item_id: int, body: ItemPatch):
    db.update_item(item_id, **_no_nulls(body.changes()))
    return OK


@app.delete("/api/items/{item_id}")
def api_delete_item(item_id: int):
    db.delete_item(item_id)
    return OK


# --- API: búsqueda y ajustes ---------------------------------------------------

@app.get("/api/search")
def api_search(q: str = ""):
    q = q.strip()
    return db.search(q) if q else []


@app.put("/api/settings/theme")
def api_theme(body: ThemeIn):
    db.set_theme(body.theme)
    return OK


class RemindersIn(Model):
    enabled: bool


@app.put("/api/settings/reminders")
def api_reminders(body: RemindersIn):
    db.set_setting("reminders", "1" if body.enabled else "0")
    return OK


@app.post("/api/reminders/test")
def api_reminders_test():
    """«Probar aviso»: muestra ya lo pendiente (o un aviso de ejemplo)."""
    if background.check_reminders(force=True):
        return {"shown": "pending"}
    if background.notify("Torre: los avisos funcionan",
                         "Ahora mismo no tienes tarjetas vencidas ni para hoy o mañana."):
        return {"shown": "sample"}
    raise HTTPException(503, "No se pudo mostrar la notificación (¿está instalado libnotify-bin?).")


@app.post("/api/backup")
def api_backup():
    path = background.make_backup(force=True)
    if path is None:
        raise HTTPException(409, "Todavía no hay datos que copiar.")
    return {"path": str(path)}
