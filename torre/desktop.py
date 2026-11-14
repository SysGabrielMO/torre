"""Torre como aplicación de escritorio: arranca el servidor como proceso aparte,
en un puerto libre y con una clave aleatoria, y muestra la interfaz en una
ventana propia (Qt WebEngine) en vez de en el navegador. Al cerrar la ventana
se apaga el servidor.

El servidor no va en un hilo de este mismo proceso a propósito (igual que en
Hangar): junto a Chromium (QtWebEngine) era menos estable.

Se lanza con ./torre-app (que prepara el entorno) o con: python -m torre.desktop
"""
from __future__ import annotations

import ctypes
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ICON = Path(__file__).resolve().parent / "static" / "icon.svg"
LOG = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "torre" / "server.log"

# Lo crea ./torre-app si Chromium se cae con la GPU: desde entonces, sin ella
NO_GPU_MARK = LOG.parent / "no-gpu"


def use_gpu() -> bool:
    """La GPU hace el scroll y el arrastre más suaves. Se desactiva sin pantalla
    real, sin tarjeta gráfica, si se pide (TORRE_NO_GPU=1) o si ya se cayó antes
    en este equipo; TORRE_GPU=1 la fuerza."""
    if os.environ.get("TORRE_GPU"):
        return True
    if os.environ.get("TORRE_NO_GPU") or NO_GPU_MARK.exists():
        return False
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    return any(Path("/dev/dri").glob("renderD*"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _die_with_parent() -> None:
    """En el proceso hijo: que el kernel lo termine si muere la ventana,
    aunque sea de golpe, para no dejar servidores huérfanos."""
    PR_SET_PDEATHSIG = 1
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


def _start_server(port: int, token: str) -> subprocess.Popen:
    root = Path(__file__).resolve().parent.parent
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as log:   # el hijo hereda el descriptor; aquí se puede cerrar
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "torre.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=root, env={**os.environ, "TORRE_TOKEN": token},
            stdout=log, stderr=subprocess.STDOUT, preexec_fn=_die_with_parent)


def _wait_ready(proc: subprocess.Popen, port: int, timeout: float = 30) -> str | None:
    """Espera a que el servidor responda; devuelve el error si no arranca."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return LOG.read_text(errors="replace").strip()[-1500:] or \
                f"terminó con código {proc.returncode}"
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return None
        except urllib.error.HTTPError:
            return None   # responde (un 403 sin clave es lo esperado): ya está arrancado
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return "tardó demasiado en responder"


def main() -> int:
    token = secrets.token_urlsafe(32)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    if not use_gpu():
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} --disable-gpu".strip()

    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    server = _start_server(port, token)   # antes de Qt: menos hilos al lanzar el hijo

    app = QApplication(sys.argv)
    app.setApplicationName("Torre")
    app.setApplicationDisplayName("Torre")
    app.setDesktopFileName("torre")       # enlaza la ventana con torre.desktop (icono del dock)
    app.setWindowIcon(QIcon(str(ICON)))

    # Ctrl+C o «kill» cierran la ventana con orden; el temporizador deja que
    # Python atienda las señales mientras Qt tiene el control
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    ticker = QTimer()
    ticker.timeout.connect(lambda: None)
    ticker.start(300)

    error = _wait_ready(server, port)
    if error:
        server.kill()
        QMessageBox.critical(None, "Torre",
                             f"No se pudo arrancar el servidor interno de Torre:\n\n{error}")
        return 1

    # Perfil sin disco: la cookie con la clave muere al cerrar la aplicación
    profile = QWebEngineProfile(app)

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            # Los enlaces de fuera (p. ej. en una descripción) van al navegador
            if url.scheme() in ("http", "https", "mailto") and not url.toString().startswith(base):
                QDesktopServices.openUrl(url)
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        def createWindow(self, _type):
            # target="_blank": se abre en el navegador vía acceptNavigationRequest
            return Page(profile, self)

        def javaScriptConfirm(self, origin, msg):
            box = QMessageBox(QMessageBox.Icon.Question, "Torre", msg,
                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                              window)
            box.button(QMessageBox.StandardButton.Yes).setText("Sí")
            box.button(QMessageBox.StandardButton.Cancel).setText("Cancelar")
            return box.exec() == QMessageBox.StandardButton.Yes

    window = QMainWindow()
    view = QWebEngineView(window)
    view.setPage(Page(profile, view))
    view.titleChanged.connect(window.setWindowTitle)
    if os.environ.get("TORRE_DEBUG"):
        view.loadFinished.connect(lambda ok: print(
            f"[torre] carga {'ok' if ok else 'FALLIDA'}: "
            f"{view.url().path() or '/'} · {view.title()}", flush=True))
    window.setCentralWidget(view)
    window.resize(1360, 860)
    for keys, action in (("F5", view.reload), ("Ctrl+R", view.reload),
                         ("Alt+Left", view.back), ("Alt+Right", view.forward),
                         ("Ctrl+W", window.close), ("Ctrl+Q", window.close),
                         ("F11", lambda: window.showNormal() if window.isFullScreen()
                          else window.showFullScreen())):
        QShortcut(QKeySequence(keys), window, activated=action)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    view.load(QUrl(f"{base}/?token={token}"))
    window.show()

    try:
        return app.exec()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
