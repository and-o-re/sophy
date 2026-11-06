"""
Launcher do Painel de Configurações Desktop Desacoplado (FASE 4).
- Sobe o servidor FastAPI leve em `127.0.0.1:8080`.
- Abre uma janela Webview Desktop dedicada e leve (modo `--app` nativo ou pywebview,
  sem camadas visuais Python pesadas), economizando RAM e VRAM para o Ollama.
"""

import argparse
import shutil
import subprocess
import threading
import time
import webbrowser

import uvicorn

from config import settings
from gui.backend.server import app


def open_lightweight_desktop_window(url: str, delay: float = 0.8) -> None:
    """
    Abre a interface em janela dedicada estilo App Nativo (sem barra de endereços/abas)
    usando o motor Webview disponível no sistema Linux.
    """
    if delay > 0:
        time.sleep(delay)
    app_browsers = [
        "xdg-open",
        "chromium",
        "brave",
        "google-chrome-stable",
        "thorium-browser",
        "firefox",
    ]
    for bin_name in ["chromium", "brave", "google-chrome-stable", "thorium-browser"]:
        path = shutil.which(bin_name)
        if path:
            subprocess.Popen(
                [path, f"--app={url}", "--window-size=1180,760"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Painel de Configurações Desktop - IA Local Híbrida")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Inicia apenas o backend FastAPI em 127.0.0.1:8080 sem abrir janela gráfica automaticamente.",
    )
    args = parser.parse_args()

    url = f"http://{settings.GUI_HOST}:{settings.GUI_PORT}"
    if not args.headless:
        threading.Thread(target=open_lightweight_desktop_window, args=(url,), daemon=True).start()

    uvicorn.run(
        app,
        host=settings.GUI_HOST,
        port=settings.GUI_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
