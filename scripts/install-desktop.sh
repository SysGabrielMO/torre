#!/bin/bash
# Añade Torre al menú de aplicaciones de tu usuario (sin root).
#
#   ./scripts/install-desktop.sh            instala (prepara el entorno y crea el acceso)
#   ./scripts/install-desktop.sh --remove   quita el acceso del menú
set -euo pipefail
root=$(dirname "$(dirname "$(realpath "$0")")")
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
entry="$apps/torre.desktop"

if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$entry"
    update-desktop-database "$apps" 2>/dev/null || true
    echo "Torre quitado del menú de aplicaciones."
    exit 0
fi
[[ $EUID -ne 0 ]] || { echo "Ejecútalo con tu usuario, sin sudo."; exit 1; }

cd "$root"
source scripts/env.sh
echo "Preparando el entorno (la primera vez descarga unos 700 MB de Qt)…"
prepare_env requirements.txt requirements-desktop.txt

mkdir -p "$apps"
cat > "$entry" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Torre
GenericName=Organizador de tareas
Comment=Tableros, listas y tarjetas para organizarte
Exec="$root/torre-app"
Icon=$root/torre/static/icon.svg
Terminal=false
Categories=Office;ProjectManagement;
Keywords=tareas;kanban;tablero;tarjetas;organizador;trello;
StartupWMClass=torre
StartupNotify=true
DESKTOP
update-desktop-database "$apps" 2>/dev/null || true

echo "Listo: busca «Torre» en el menú de aplicaciones."
echo "Si mueves la carpeta del proyecto, vuelve a ejecutar este script."
