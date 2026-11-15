"""Importar un tablero de Trello desde su exportación en JSON.

En Trello: abre el tablero → menú (⋯) → «Imprimir, exportar y compartir» →
«Exportar como JSON». Ese fichero se sube desde la portada de Torre.

Qué se trae:
- el tablero (con un color parecido al de su fondo), sus listas y tarjetas,
  en el mismo orden;
- lo archivado en Trello llega archivado a Torre (se puede restaurar);
- descripción, fecha límite (en tu zona horaria) y si está completada;
- etiquetas, con el color más parecido de los de Torre;
- checklists: Torre tiene una por tarjeta, así que si en Trello había varias
  se juntan y cada elemento lleva delante el nombre de su checklist;
- comentarios y adjuntos: se añaden al final de la descripción (los adjuntos
  como enlaces).

Lo que no tiene equivalente en Torre (miembros, portadas, campos
personalizados, power-ups…) se ignora. Todo va en una transacción: o se
importa el tablero entero, o nada.
"""
from __future__ import annotations

from datetime import datetime

from . import db

# Colores de Trello → los de Torre. Trello además tiene variantes «_dark» y
# «_light» (p. ej. «green_dark»), que se tratan como el color base.
COLOR_MAP = {
    "green": "green", "yellow": "yellow", "orange": "orange", "red": "red",
    "purple": "purple", "blue": "blue", "sky": "teal", "lime": "green",
    "pink": "pink", "black": "gray", "grey": "gray", "gray": "gray",
}

LIMITS = {"board": 100, "list": 100, "card": 300, "label": 40, "item": 300, "description": 50_000}


class NotTrello(ValueError):
    """El fichero no es una exportación de tablero de Trello."""


def _color(value: str | None, default: str = "gray") -> str:
    if not value:
        return default
    return COLOR_MAP.get(str(value).split("_")[0], default)


def _text(value, limit: int, fallback: str = "") -> str:
    """Una línea limpia (sin saltos ni espacios de más), recortada al límite."""
    text = " ".join(str(value or "").split())[:limit].strip()
    return text or fallback


def _local_date(value: str | None) -> str | None:
    """«2026-11-20T23:30:00.000Z» → fecha en tu zona horaria («2026-11-21»)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, TypeError):
        return None


def _by_pos(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda x: (float(x.get("pos") or 0), str(x.get("id", ""))))


def _extras(card: dict, comments: list[dict]) -> str:
    """Comentarios y adjuntos, en Markdown, para el final de la descripción."""
    parts = []
    links = [a for a in card.get("attachments") or [] if a.get("url")]
    if links:
        parts.append("**Adjuntos (en Trello):**\n" + "\n".join(
            f"- [{(a.get('name') or a['url']).replace(']', ')')}]({a['url']})" for a in links))
    if comments:
        lines = []
        for c in sorted(comments, key=lambda c: c.get("date") or ""):
            day = _local_date(c.get("date")) or ""
            who = (c.get("memberCreator") or {}).get("fullName") or "Alguien"
            text = (c.get("data") or {}).get("text", "").strip().replace("\n", "\n  ")
            lines.append(f"- {who}, {day}: {text}")
        parts.append("**Comentarios (de Trello):**\n" + "\n".join(lines))
    return "\n\n".join(parts)


def validate(data) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list) \
            or not isinstance(data.get("cards"), list) or "name" not in data:
        raise NotTrello("El fichero no parece una exportación JSON de un tablero de Trello.")


def import_board(data: dict) -> dict:
    """Crea el tablero en Torre. Devuelve su id y un resumen de lo importado."""
    validate(data)
    try:
        return _import(data)
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        # Algo con forma inesperada a medio camino: la transacción ya se deshizo
        raise NotTrello(f"El fichero de Trello está incompleto o dañado ({type(e).__name__}); "
                        "no se ha importado nada.") from e


def _import(data: dict) -> dict:
    prefs = data.get("prefs") or {}
    board_color = _color(prefs.get("background"), default="blue")
    board_name = _text(data.get("name"), LIMITS["board"], "Tablero de Trello")
    stats = {"lists": 0, "cards": 0, "labels": 0, "items": 0, "comments": 0, "archived": 0}

    comments: dict[str, list[dict]] = {}
    for action in data.get("actions") or []:
        card_id = ((action.get("data") or {}).get("card") or {}).get("id")
        if action.get("type") == "commentCard" and card_id:
            comments.setdefault(card_id, []).append(action)

    checklists: dict[str, list[dict]] = {}
    for cl in _by_pos(data.get("checklists") or []):
        checklists.setdefault(cl.get("idCard"), []).append(cl)

    with db.tx() as con:
        board_id = con.execute("INSERT INTO boards (name, color, archived) VALUES (?, ?, ?)",
                               (board_name, board_color, int(bool(data.get("closed"))))).lastrowid

        labels = {}
        for lb in data.get("labels") or []:
            labels[lb.get("id")] = con.execute(
                "INSERT INTO labels (board_id, name, color) VALUES (?, ?, ?)",
                (board_id, _text(lb.get("name"), LIMITS["label"]), _color(lb.get("color")))).lastrowid
            stats["labels"] += 1

        lists = {}
        for pos, lst in enumerate(_by_pos(data["lists"])):
            archived = bool(lst.get("closed"))
            lists[lst.get("id")] = con.execute(
                "INSERT INTO lists (board_id, name, position, archived) VALUES (?, ?, ?, ?)",
                (board_id, _text(lst.get("name"), LIMITS["list"], "Sin nombre"), pos, int(archived))).lastrowid
            stats["lists"] += 1
            stats["archived"] += archived

        positions: dict[int, int] = {}
        for card in _by_pos(data["cards"]):
            list_id = lists.get(card.get("idList"))
            if list_id is None:
                continue   # tarjeta de una lista que no viene en el fichero
            description = (card.get("desc") or "").strip()
            extra = _extras(card, comments.get(card.get("id"), []))
            if extra:
                description = f"{description}\n\n---\n\n{extra}" if description else extra
            archived = bool(card.get("closed"))
            pos = positions[list_id] = positions.get(list_id, -1) + 1
            card_id = con.execute(
                "INSERT INTO cards (list_id, title, description, due, done, position, archived) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (list_id, _text(card.get("name"), LIMITS["card"], "Sin título"),
                 description[:LIMITS["description"]], _local_date(card.get("due")),
                 int(bool(card.get("dueComplete"))), pos, int(archived))).lastrowid
            stats["cards"] += 1
            stats["archived"] += archived
            stats["comments"] += len(comments.get(card.get("id"), []))

            con.executemany("INSERT OR IGNORE INTO card_labels (card_id, label_id) VALUES (?, ?)",
                            [(card_id, labels[i]) for i in card.get("idLabels") or [] if i in labels])

            groups = checklists.get(card.get("id"), [])
            item_pos = 0
            for cl in groups:
                prefix = f"{_text(cl.get('name'), 60)}: " if len(groups) > 1 and cl.get("name") else ""
                for item in _by_pos(cl.get("checkItems") or []):
                    con.execute("INSERT INTO items (card_id, text, done, position) VALUES (?, ?, ?, ?)",
                                (card_id, _text(prefix + str(item.get("name") or ""), LIMITS["item"], "—"),
                                 int(item.get("state") == "complete"), item_pos))
                    item_pos += 1
                    stats["items"] += 1

    return {"id": board_id, "name": board_name, **stats}
