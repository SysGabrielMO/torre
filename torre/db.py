"""Datos de Torre: un único fichero SQLite (data/torre.db).

Todo lo que toca la base de datos está aquí; main.py solo traduce HTTP a
estas funciones. Cada función abre su propia conexión y la cierra al acabar.

Orden: listas y tarjetas tienen «position» (0, 1, 2…) dentro de su tablero o
lista. Lo archivado conserva su fila pero no cuenta para el orden; al
restaurarlo vuelve al final.

Varios usuarios (solo en un servidor, con TORRE_AUTH=1): cada tablero tiene
un dueño («owner») y puede compartirse con otros usuarios («board_members»),
que pueden editar su contenido. En el portátil no hay usuarios: «owner» queda
vacío y las funciones se llaman sin «user» (sin restricciones).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Colores disponibles para tableros y etiquetas (los valores están en style.css)
COLOR_NAMES = {
    "blue": "Azul", "teal": "Turquesa", "green": "Verde", "yellow": "Amarillo", "orange": "Naranja",
    "red": "Rojo", "pink": "Rosa", "purple": "Morado", "gray": "Gris",
}
COLORS = tuple(COLOR_NAMES)
THEMES = ("auto", "light", "dark")

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    color    TEXT NOT NULL DEFAULT 'blue',
    archived INTEGER NOT NULL DEFAULT 0,
    created  TEXT NOT NULL DEFAULT (datetime('now')),
    owner    TEXT REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE IF NOT EXISTS lists (
    id       INTEGER PRIMARY KEY,
    board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY,
    list_id     INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due         TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS labels (
    id       INTEGER PRIMARY KEY,
    board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    name     TEXT NOT NULL DEFAULT '',
    color    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_labels (
    card_id  INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (card_id, label_id)
);
CREATE TABLE IF NOT EXISTS items (
    id       INTEGER PRIMARY KEY,
    card_id  INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    text     TEXT NOT NULL,
    done     INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password TEXT NOT NULL,
    created  TEXT NOT NULL DEFAULT (datetime('now')),
    is_admin INTEGER NOT NULL DEFAULT 0,
    must_change INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS board_members (
    board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE,
    added    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (board_id, username)
);

CREATE TABLE IF NOT EXISTS sessions (
    token    TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE,
    created  TEXT NOT NULL DEFAULT (datetime('now')),
    expires  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lists_board ON lists(board_id);
CREATE INDEX IF NOT EXISTS cards_list ON cards(list_id);
CREATE INDEX IF NOT EXISTS items_card ON items(card_id);
CREATE INDEX IF NOT EXISTS members_user ON board_members(username);
"""


def _columns(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _migrate(con) -> None:
    """Pone al día una base de datos de una versión anterior de Torre."""
    if "owner" not in _columns(con, "boards"):
        con.execute("ALTER TABLE boards ADD COLUMN owner TEXT "
                    "REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE")
    if "is_admin" not in _columns(con, "users"):
        con.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "must_change" not in _columns(con, "users"):
        con.execute("ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
    con.execute("CREATE INDEX IF NOT EXISTS boards_owner ON boards(owner)")
    # Si hay usuarios pero ningún administrador (Torre de antes de los permisos),
    # el usuario más antiguo pasa a serlo y se queda los tableros sin dueño
    if not con.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
        first = con.execute("SELECT username FROM users ORDER BY created, username LIMIT 1").fetchone()
        if first:
            con.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (first[0],))
    admin = con.execute("SELECT username FROM users WHERE is_admin = 1 "
                        "ORDER BY created, username LIMIT 1").fetchone()
    if admin:
        con.execute("UPDATE boards SET owner = ? WHERE owner IS NULL", (admin[0],))
    con.commit()


class NotFound(Exception):
    """El tablero, lista, tarjeta… no existe (o ya se borró)."""


def db_path() -> Path:
    return Path(os.environ.get("TORRE_DB") or ROOT / "data" / "torre.db")


_ready: set[str] = set()


@contextmanager
def tx():
    """Conexión con transacción: confirma al salir, deshace si hay error."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if str(path) not in _ready or not path.exists() or not path.stat().st_size:
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript(SCHEMA)
            _migrate(con)
            _ready.add(str(path))
        with con:
            yield con
    finally:
        con.close()


def _one(con, sql: str, *args) -> sqlite3.Row:
    row = con.execute(sql, args).fetchone()
    if row is None:
        raise NotFound
    return row


def _changed(cur) -> None:
    if cur.rowcount == 0:
        raise NotFound


def _update(con, table: str, row_id: int, fields: dict) -> None:
    """UPDATE con los campos dados (los nombres vienen del código, no del usuario)."""
    if not fields:
        _one(con, f"SELECT id FROM {table} WHERE id = ?", row_id)
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    _changed(con.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id)))


def _place(con, table: str, parent: str, parent_id: int, row_id: int, index: int | None) -> None:
    """Coloca la fila en «index» entre las no archivadas de su padre (None = al
    final) y renumera las posiciones."""
    ids = [r["id"] for r in con.execute(
        f"SELECT id FROM {table} WHERE {parent} = ? AND archived = 0 AND id != ? "
        "ORDER BY position, id", (parent_id, row_id))]
    index = len(ids) if index is None else max(0, min(index, len(ids)))
    ids.insert(index, row_id)
    con.executemany(f"UPDATE {table} SET position = ? WHERE id = ?",
                    [(pos, i) for pos, i in enumerate(ids)])


# --- Ajustes -------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with tx() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with tx() as con:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def get_theme(user: str | None = None) -> str:
    """El tema de cada usuario (en el portátil, uno solo para todo)."""
    theme = get_setting(f"theme:{user}" if user else "theme", "auto")
    return theme if theme in THEMES else "auto"


def set_theme(theme: str, user: str | None = None) -> None:
    set_setting(f"theme:{user}" if user else "theme", theme)


def reminders_enabled() -> bool:
    return get_setting("reminders", "1") == "1"


def due_cards(until: str) -> list[dict]:
    """Tarjetas pendientes (no hechas ni archivadas) con fecha hasta «until»
    incluida, de las más atrasadas a las más lejanas."""
    with tx() as con:
        rows = con.execute("""
            SELECT c.id, c.title, c.due, b.id AS board_id, b.name AS board_name
              FROM cards c JOIN lists l ON l.id = c.list_id JOIN boards b ON b.id = l.board_id
             WHERE c.due IS NOT NULL AND c.due <= ? AND c.done = 0
               AND c.archived = 0 AND l.archived = 0 AND b.archived = 0
             ORDER BY c.due, c.title COLLATE NOCASE""", (until,)).fetchall()
    return [dict(r) for r in rows]


# --- Tableros ------------------------------------------------------------------

# Condición SQL «el usuario :user puede ver el tablero b» (dueño o miembro)
VISIBLE = """(b.owner = :user OR EXISTS (SELECT 1 FROM board_members m
                                         WHERE m.board_id = b.id AND m.username = :user))"""


def list_boards(user: str | None = None) -> list[dict]:
    """Tableros que ve el usuario (todos si no hay usuario), con su papel:
    «owner» si es suyo, «member» si se lo han compartido."""
    with tx() as con:
        rows = con.execute(f"""
            SELECT b.id, b.name, b.color, b.archived, b.owner,
                   CASE WHEN :user IS NULL OR b.owner = :user THEN 'owner' ELSE 'member' END AS role,
                   (SELECT COUNT(*) FROM board_members m WHERE m.board_id = b.id) AS members,
                   (SELECT COUNT(*) FROM cards c JOIN lists l ON l.id = c.list_id
                     WHERE l.board_id = b.id AND c.archived = 0 AND l.archived = 0) AS cards
              FROM boards b
             WHERE :user IS NULL OR {VISIBLE}
             ORDER BY b.name COLLATE NOCASE, b.id""", {"user": user}).fetchall()
    return [dict(r) for r in rows]


def create_board(name: str, color: str = "blue", owner: str | None = None) -> int:
    with tx() as con:
        return con.execute("INSERT INTO boards (name, color, owner) VALUES (?, ?, ?)",
                           (name, color, owner)).lastrowid


def role(board_id: int, user: str | None) -> str:
    """«owner» o «member». Si el usuario no puede ver el tablero (o no existe),
    NotFound: así no se puede saber qué tableros de otros existen."""
    with tx() as con:
        row = _one(con, "SELECT owner FROM boards WHERE id = ?", board_id)
        if user is None or row["owner"] == user:
            return "owner"
        if con.execute("SELECT 1 FROM board_members WHERE board_id = ? AND username = ?",
                       (board_id, user)).fetchone():
            return "member"
    raise NotFound


_PARENT = {
    "lists": "SELECT board_id FROM lists WHERE id = ?",
    "cards": "SELECT l.board_id FROM cards c JOIN lists l ON l.id = c.list_id WHERE c.id = ?",
    "labels": "SELECT board_id FROM labels WHERE id = ?",
    "items": """SELECT l.board_id FROM items i JOIN cards c ON c.id = i.card_id
                  JOIN lists l ON l.id = c.list_id WHERE i.id = ?""",
}


def board_of(kind: str, row_id: int) -> int:
    """Tablero al que pertenece una lista, tarjeta, etiqueta o elemento."""
    with tx() as con:
        return _one(con, _PARENT[kind], row_id)[0]


def members(board_id: int) -> dict:
    with tx() as con:
        owner = _one(con, "SELECT owner FROM boards WHERE id = ?", board_id)["owner"]
        names = [r[0] for r in con.execute(
            "SELECT username FROM board_members WHERE board_id = ? ORDER BY username", (board_id,))]
    return {"owner": owner, "members": names}


class NotAllowed(ValueError):
    """Operación no permitida (el mensaje se muestra tal cual)."""


def add_member(board_id: int, username: str) -> None:
    with tx() as con:
        owner = _one(con, "SELECT owner FROM boards WHERE id = ?", board_id)["owner"]
        if not con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise NotAllowed(f"No existe el usuario «{username}».")
        if username == owner:
            raise NotAllowed("Es el dueño del tablero: ya tiene acceso.")
        con.execute("INSERT OR IGNORE INTO board_members (board_id, username) VALUES (?, ?)",
                    (board_id, username))


def remove_member(board_id: int, username: str) -> None:
    with tx() as con:
        _changed(con.execute("DELETE FROM board_members WHERE board_id = ? AND username = ?",
                             (board_id, username)))


def update_board(board_id: int, **fields) -> None:
    with tx() as con:
        _update(con, "boards", board_id, fields)


def delete_board(board_id: int) -> None:
    with tx() as con:
        _changed(con.execute("DELETE FROM boards WHERE id = ?", (board_id,)))


def board_name(board_id: int) -> dict:
    with tx() as con:
        return dict(_one(con, "SELECT id, name, color, archived, owner FROM boards WHERE id = ?", board_id))


def get_board(board_id: int) -> dict:
    """El tablero entero, listo para pintarlo: listas, tarjetas y etiquetas."""
    with tx() as con:
        board = dict(_one(con, "SELECT id, name, color, archived, owner FROM boards WHERE id = ?", board_id))
        board["labels"] = [dict(r) for r in con.execute(
            "SELECT id, name, color FROM labels WHERE board_id = ? ORDER BY id", (board_id,))]
        lists = [dict(r) | {"cards": []} for r in con.execute(
            "SELECT id, name FROM lists WHERE board_id = ? AND archived = 0 "
            "ORDER BY position, id", (board_id,))]
        by_id = {lst["id"]: lst for lst in lists}
        cards = {}
        for r in con.execute("""
                SELECT c.id, c.list_id, c.title, c.due, c.done, c.description != '' AS has_description,
                       (SELECT COUNT(*) FROM items i WHERE i.card_id = c.id) AS items_total,
                       (SELECT COUNT(*) FROM items i WHERE i.card_id = c.id AND i.done) AS items_done
                  FROM cards c JOIN lists l ON l.id = c.list_id
                 WHERE l.board_id = ? AND l.archived = 0 AND c.archived = 0
                 ORDER BY c.position, c.id""", (board_id,)):
            card = dict(r) | {"label_ids": []}
            cards[card["id"]] = card
            by_id[card.pop("list_id")]["cards"].append(card)
        for r in con.execute("""
                SELECT cl.card_id, cl.label_id FROM card_labels cl
                  JOIN labels lb ON lb.id = cl.label_id
                 WHERE lb.board_id = ? ORDER BY cl.label_id""", (board_id,)):
            if r["card_id"] in cards:
                cards[r["card_id"]]["label_ids"].append(r["label_id"])
    board["lists"] = lists
    return board


def get_archive(board_id: int) -> dict:
    with tx() as con:
        _one(con, "SELECT id FROM boards WHERE id = ?", board_id)
        lists = con.execute(
            "SELECT id, name FROM lists WHERE board_id = ? AND archived = 1 ORDER BY name",
            (board_id,)).fetchall()
        cards = con.execute("""
            SELECT c.id, c.title, l.name AS list_name, l.archived AS list_archived
              FROM cards c JOIN lists l ON l.id = c.list_id
             WHERE l.board_id = ? AND c.archived = 1 ORDER BY c.title""", (board_id,)).fetchall()
    return {"lists": [dict(r) for r in lists], "cards": [dict(r) for r in cards]}


# --- Listas --------------------------------------------------------------------

def create_list(board_id: int, name: str) -> int:
    with tx() as con:
        _one(con, "SELECT id FROM boards WHERE id = ?", board_id)
        list_id = con.execute("INSERT INTO lists (board_id, name) VALUES (?, ?)",
                              (board_id, name)).lastrowid
        _place(con, "lists", "board_id", board_id, list_id, None)
        return list_id


def update_list(list_id: int, name: str | None = None, archived: bool | None = None) -> None:
    with tx() as con:
        row = _one(con, "SELECT board_id, archived FROM lists WHERE id = ?", list_id)
        fields = {}
        if name is not None:
            fields["name"] = name
        if archived is not None and bool(row["archived"]) != archived:
            fields["archived"] = int(archived)
        _update(con, "lists", list_id, fields)
        if fields.get("archived") == 0:
            _place(con, "lists", "board_id", row["board_id"], list_id, None)


def move_list(list_id: int, index: int) -> None:
    with tx() as con:
        row = _one(con, "SELECT board_id FROM lists WHERE id = ? AND archived = 0", list_id)
        _place(con, "lists", "board_id", row["board_id"], list_id, index)


def delete_list(list_id: int) -> None:
    with tx() as con:
        _changed(con.execute("DELETE FROM lists WHERE id = ?", (list_id,)))


# --- Tarjetas ------------------------------------------------------------------

def create_card(list_id: int, title: str) -> int:
    with tx() as con:
        _one(con, "SELECT id FROM lists WHERE id = ?", list_id)
        card_id = con.execute("INSERT INTO cards (list_id, title) VALUES (?, ?)",
                              (list_id, title)).lastrowid
        _place(con, "cards", "list_id", list_id, card_id, None)
        return card_id


def get_card(card_id: int) -> dict:
    with tx() as con:
        card = dict(_one(con, """
            SELECT c.id, c.list_id, c.title, c.description, c.due, c.done, c.archived,
                   l.name AS list_name, l.board_id
              FROM cards c JOIN lists l ON l.id = c.list_id WHERE c.id = ?""", card_id))
        card["label_ids"] = [r["label_id"] for r in con.execute(
            "SELECT label_id FROM card_labels WHERE card_id = ? ORDER BY label_id", (card_id,))]
        card["items"] = [dict(r) for r in con.execute(
            "SELECT id, text, done FROM items WHERE card_id = ? ORDER BY position, id", (card_id,))]
    return card


def update_card(card_id: int, **fields) -> None:
    """Campos: title, description, due (ISO o None), done, archived."""
    with tx() as con:
        row = _one(con, "SELECT list_id, archived FROM cards WHERE id = ?", card_id)
        restored = "archived" in fields and not fields["archived"] and row["archived"]
        fields = {k: int(v) if isinstance(v, bool) else v for k, v in fields.items()}
        _update(con, "cards", card_id, fields)
        if restored:
            _place(con, "cards", "list_id", row["list_id"], card_id, None)


def move_card(card_id: int, list_id: int, index: int) -> None:
    """Mueve la tarjeta a otra lista del mismo tablero (o dentro de la suya)."""
    with tx() as con:
        src = _one(con, """SELECT c.list_id, l.board_id FROM cards c JOIN lists l ON l.id = c.list_id
                            WHERE c.id = ? AND c.archived = 0""", card_id)
        dst = _one(con, "SELECT board_id FROM lists WHERE id = ? AND archived = 0", list_id)
        if dst["board_id"] != src["board_id"]:
            raise NotFound
        con.execute("UPDATE cards SET list_id = ? WHERE id = ?", (list_id, card_id))
        _place(con, "cards", "list_id", list_id, card_id, index)
        if src["list_id"] != list_id:   # cerrar el hueco en la lista de origen
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM cards WHERE list_id = ? AND archived = 0 ORDER BY position, id",
                (src["list_id"],))]
            con.executemany("UPDATE cards SET position = ? WHERE id = ?",
                            [(pos, i) for pos, i in enumerate(ids)])


def delete_card(card_id: int) -> None:
    with tx() as con:
        _changed(con.execute("DELETE FROM cards WHERE id = ?", (card_id,)))


# --- Etiquetas -----------------------------------------------------------------

def create_label(board_id: int, name: str, color: str) -> int:
    with tx() as con:
        _one(con, "SELECT id FROM boards WHERE id = ?", board_id)
        return con.execute("INSERT INTO labels (board_id, name, color) VALUES (?, ?, ?)",
                           (board_id, name, color)).lastrowid


def update_label(label_id: int, **fields) -> None:
    with tx() as con:
        _update(con, "labels", label_id, fields)


def delete_label(label_id: int) -> None:
    with tx() as con:
        _changed(con.execute("DELETE FROM labels WHERE id = ?", (label_id,)))


def set_card_labels(card_id: int, label_ids: list[int]) -> None:
    """Deja en la tarjeta exactamente esas etiquetas (solo las de su tablero)."""
    with tx() as con:
        board_id = _one(con, """SELECT l.board_id FROM cards c JOIN lists l ON l.id = c.list_id
                                 WHERE c.id = ?""", card_id)["board_id"]
        valid = {r["id"] for r in con.execute("SELECT id FROM labels WHERE board_id = ?", (board_id,))}
        con.execute("DELETE FROM card_labels WHERE card_id = ?", (card_id,))
        con.executemany("INSERT INTO card_labels (card_id, label_id) VALUES (?, ?)",
                        [(card_id, i) for i in sorted(set(label_ids) & valid)])


# --- Checklist -----------------------------------------------------------------

def create_item(card_id: int, text: str) -> int:
    with tx() as con:
        _one(con, "SELECT id FROM cards WHERE id = ?", card_id)
        pos = con.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM items WHERE card_id = ?",
                          (card_id,)).fetchone()[0]
        return con.execute("INSERT INTO items (card_id, text, position) VALUES (?, ?, ?)",
                           (card_id, text, pos)).lastrowid


def update_item(item_id: int, **fields) -> None:
    with tx() as con:
        _update(con, "items", item_id,
                {k: int(v) if isinstance(v, bool) else v for k, v in fields.items()})


def delete_item(item_id: int) -> None:
    with tx() as con:
        _changed(con.execute("DELETE FROM items WHERE id = ?", (item_id,)))


# --- Búsqueda ------------------------------------------------------------------

def search(query: str, limit: int = 20, user: str | None = None) -> list[dict]:
    """Tarjetas (no archivadas) cuyo título o descripción contiene el texto,
    solo de los tableros que ve el usuario."""
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with tx() as con:
        rows = con.execute(f"""
            SELECT c.id, c.title, l.name AS list_name, b.id AS board_id, b.name AS board_name,
                   b.color AS board_color
              FROM cards c JOIN lists l ON l.id = c.list_id JOIN boards b ON b.id = l.board_id
             WHERE c.archived = 0 AND l.archived = 0 AND b.archived = 0
               AND (:user IS NULL OR {VISIBLE})
               AND (c.title LIKE :like ESCAPE '\\' OR c.description LIKE :like ESCAPE '\\')
             ORDER BY c.title LIKE :like ESCAPE '\\' DESC, c.title COLLATE NOCASE LIMIT :limit""",
            {"like": like, "limit": limit, "user": user}).fetchall()
    return [dict(r) for r in rows]


def valid_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
