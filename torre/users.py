"""Gestionar los usuarios de Torre desde la línea de comandos.

    python -m torre.users añadir NOMBRE      crea un usuario (pide la contraseña)
    python -m torre.users contraseña NOMBRE  cambia la contraseña (cierra sus sesiones)
    python -m torre.users borrar NOMBRE      borra el usuario
    python -m torre.users lista              muestra los usuarios

En el servidor, dentro del contenedor:

    docker compose exec torre python -m torre.users añadir gabriel

También valen los nombres en inglés: add, passwd, del, list.
"""
from __future__ import annotations

import getpass
import sys

from . import auth

COMMANDS = {
    "añadir": "add", "anadir": "add", "add": "add",
    "contraseña": "passwd", "contrasena": "passwd", "passwd": "passwd",
    "borrar": "del", "del": "del",
    "lista": "list", "list": "list",
}


def ask_password() -> str:
    first = getpass.getpass("Contraseña: ")
    if getpass.getpass("Repítela: ") != first:
        raise auth.AuthError("Las contraseñas no coinciden.")
    return first


def main(argv: list[str]) -> int:
    command = COMMANDS.get(argv[0]) if argv else None
    if command is None or (command != "list" and len(argv) != 2):
        print(__doc__.strip())
        return 2
    try:
        if command == "list":
            users = auth.list_users()
            print("\n".join(users) if users else "No hay usuarios. Crea uno con: python -m torre.users añadir NOMBRE")
        elif command == "add":
            name = auth.normalize_username(argv[1])
            auth.check_new_password(name, password := ask_password())
            auth.create_user(name, password)
            print(f"Usuario «{name}» creado. Ya puedes entrar en Torre.")
        elif command == "passwd":
            auth.set_password(argv[1], ask_password())
            print("Contraseña cambiada. Se han cerrado sus sesiones abiertas.")
        elif command == "del":
            auth.delete_user(argv[1])
            print(f"Usuario «{argv[1]}» borrado.")
    except auth.AuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
