#!/bin/bash
# Añade Torre al menú de aplicaciones de tu usuario (sin root) y programa la
# copia y el aviso diarios, que funcionan aunque Torre esté cerrado.
#
#   ./scripts/install-desktop.sh            instala (prepara el entorno, crea el acceso
#                                           y activa el temporizador diario)
#   ./scripts/install-desktop.sh --remove   quita el acceso del menú y el temporizador
#
# Hora del aviso diario: TORRE_HORA=08:30 ./scripts/install-desktop.sh (por defecto 09:00)
set -euo pipefail
root=$(dirname "$(dirname "$(realpath "$0")")")
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
entry="$apps/torre.desktop"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
hora="${TORRE_HORA:-09:00}"

if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$entry"
    update-desktop-database "$apps" 2>/dev/null || true
    systemctl --user disable --now torre-diario.timer 2>/dev/null || true
    rm -f "$units/torre-diario.timer" "$units/torre-diario.service"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Torre quitado del menú de aplicaciones y del aviso diario."
    exit 0
fi
[[ $EUID -ne 0 ]] || { echo "Ejecútalo con tu usuario, sin sudo."; exit 1; }
[[ "$hora" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "TORRE_HORA debe ser HH:MM, p. ej. 08:30"; exit 1; }

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

# --- Copia y aviso diarios (systemd de tu usuario) --------------------------------
mkdir -p "$units"
cat > "$units/torre-diario.service" <<UNIT
[Unit]
Description=Torre: copia de seguridad y aviso de fechas del día

[Service]
Type=exec
WorkingDirectory=$root
ExecStart=$root/.venv/bin/python -m torre.daily
# Espera a que respondas al aviso; si nadie lo hace, que no se quede para siempre
RuntimeMaxSec=12h
UNIT
cat > "$units/torre-diario.timer" <<UNIT
[Unit]
Description=Torre: copia y aviso diarios a las $hora

[Timer]
OnCalendar=*-*-* $hora:00
# Si a esa hora el portátil estaba apagado, al entrar en la sesión
Persistent=true
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
UNIT
if systemctl --user daemon-reload 2>/dev/null && systemctl --user enable --now torre-diario.timer 2>/dev/null; then
    echo "Aviso diario activado: cada día a las $hora (aunque Torre esté cerrado)."
else
    echo "Aviso: no se pudo activar el temporizador diario (¿no hay sesión de systemd?)."
fi

echo "Listo: busca «Torre» en el menú de aplicaciones."
echo "Si mueves la carpeta del proyecto, vuelve a ejecutar este script."
