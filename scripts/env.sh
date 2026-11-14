# Preparación del entorno de Python, compartida por run.sh, run-tests.sh y el
# lanzador de escritorio (./torre-app). Se carga con «source»; no se ejecuta sola.
#
#   prepare_env requirements.txt [requirements-desktop.txt …]
#
# Crea .venv la primera vez, lo repara si quedó a medias y reinstala las
# dependencias cuando cambia alguno de los ficheros.

fail() { echo -e "\n\e[31mError:\e[0m $*\n" >&2; exit 1; }

prepare_env() {
    command -v python3 >/dev/null || fail "falta Python 3: sudo apt install python3"
    python3 -c "import venv, ensurepip" 2>/dev/null ||
        fail "falta el módulo venv de Python: sudo apt install python3-venv"

    # Si un intento anterior falló a medias, .venv existe pero está incompleto
    if [[ -d .venv && ! -x .venv/bin/pip ]]; then
        echo "El entorno virtual (.venv) está incompleto: se vuelve a crear."
        rm -rf .venv
    fi
    if [[ ! -d .venv ]]; then
        echo "Creando el entorno virtual (.venv)…"
        python3 -m venv .venv
    fi

    # Un sello por conjunto de ficheros: se reinstala si cambian o falta uvicorn
    local stamp wanted
    stamp=".venv/.requirements-$(echo "$*" | tr ' /' '__').sha256"
    wanted=$(cat "$@" | sha256sum | cut -d' ' -f1)
    if [[ ! -x .venv/bin/uvicorn || "$(cat "$stamp" 2>/dev/null)" != "$wanted" ]]; then
        echo "Instalando dependencias ($*)…"
        rm -f "$stamp"
        local args=()
        for f in "$@"; do args+=(-r "$f"); done
        .venv/bin/pip install -q --disable-pip-version-check "${args[@]}" ||
            fail "no se pudieron instalar las dependencias (¿hay conexión a Internet?).
       Vuelve a intentarlo cuando lo arregles; retomará la instalación."
        echo "$wanted" > "$stamp"
    fi
}
