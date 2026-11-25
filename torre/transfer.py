"""Pasar los tableros de otra base de datos de Torre (p. ej. la del portátil) a esta.

    python -m torre.transfer ORIGEN.db USUARIO

Copia todos los tableros de ORIGEN (con sus listas, tarjetas, etiquetas,
checklists y lo archivado) y los pone a nombre de USUARIO. No borra ni cambia
nada de lo que ya hay. Todo en una transacción: o se copian todos, o ninguno.

En el servidor, con el fichero subido a ~/torre-portatil.db:

    docker compose cp ~/torre-portatil.db torre:/tmp/origen.db
    docker compose exec torre python -m torre.transfer /tmp/origen.db gabriel
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from . import auth, db


def transfer(source: Path, owner: str | None) -> list[str]:
    """Copia los tableros; devuelve sus nombres."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        src.execute("SELECT id, name FROM boards LIMIT 1")
    except sqlite3.DatabaseError as e:
        raise SystemExit(f"Error: {source} no es una base de datos de Torre ({e}).")
    names = []
    try:
        with db.tx() as con:
            if owner is not None and not con.execute("SELECT 1 FROM users WHERE username = ?", (owner,)).fetchone():
                raise SystemExit(f"Error: no existe el usuario «{owner}».")
            for board in src.execute("SELECT * FROM boards ORDER BY id"):
                bid = con.execute("INSERT INTO boards (name, color, archived, created, owner) VALUES (?, ?, ?, ?, ?)",
                                  (board["name"], board["color"], board["archived"], board["created"], owner)).lastrowid
                labels = {}
                for lb in src.execute("SELECT * FROM labels WHERE board_id = ?", (board["id"],)):
                    labels[lb["id"]] = con.execute("INSERT INTO labels (board_id, name, color) VALUES (?, ?, ?)",
                                                   (bid, lb["name"], lb["color"])).lastrowid
                for lst in src.execute("SELECT * FROM lists WHERE board_id = ?", (board["id"],)):
                    lid = con.execute("INSERT INTO lists (board_id, name, position, archived) VALUES (?, ?, ?, ?)",
                                      (bid, lst["name"], lst["position"], lst["archived"])).lastrowid
                    for card in src.execute("SELECT * FROM cards WHERE list_id = ?", (lst["id"],)):
                        cid = con.execute(
                            "INSERT INTO cards (list_id, title, description, due, done, position, archived, created) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (lid, card["title"], card["description"], card["due"], card["done"],
                             card["position"], card["archived"], card["created"])).lastrowid
                        con.executemany("INSERT INTO card_labels (card_id, label_id) VALUES (?, ?)",
                                        [(cid, labels[r[0]]) for r in src.execute(
                                            "SELECT label_id FROM card_labels WHERE card_id = ?", (card["id"],))
                                         if r[0] in labels])
                        con.executemany("INSERT INTO items (card_id, text, done, position) VALUES (?, ?, ?, ?)",
                                        [(cid, i["text"], i["done"], i["position"]) for i in src.execute(
                                            "SELECT * FROM items WHERE card_id = ?", (card["id"],))])
                names.append(board["name"])
    finally:
        src.close()
    return names


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    source, user = Path(argv[0]), argv[1].strip().lower()
    if not source.is_file():
        print(f"Error: no existe {source}", file=sys.stderr)
        return 1
    names = transfer(source, user if auth.user_count() else None)
    print(f"Copiados {len(names)} tablero{'s' if len(names) != 1 else ''} a nombre de {user}: "
          + ", ".join(f"«{n}»" for n in names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
