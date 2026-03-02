from __future__ import annotations

import sys
import webbrowser

from api_server import app, assistant
from constants import DEFAULT_WEB_HOST, DEFAULT_WEB_PORT
from storage import find_available_port


def run_web_server() -> None:
    import uvicorn

    host = DEFAULT_WEB_HOST
    port = find_available_port(host, DEFAULT_WEB_PORT)
    if port != DEFAULT_WEB_PORT:
        print(f"Port {DEFAULT_WEB_PORT} is busy. Using port {port} instead.")

    app_url = f"http://{host}:{port}/index.html"
    print(f"Starting Jarvis FastAPI on {app_url}")
    try:
        webbrowser.open_new_tab(app_url)
    except Exception:
        pass

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    if "--cli" in sys.argv:
        assistant.run()
    else:
        run_web_server()
