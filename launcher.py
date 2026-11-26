#!/usr/bin/env python3
"""SkillNova Profile Hub v0.1 — portable launcher.

Double-click entry point. Starts the local web app on your own
machine and opens it in your browser. Nothing to install.

Works both from source (python3 launcher.py) and from a frozen
PyInstaller build (the double-click executable).
"""
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path


def app_dir() -> Path:
    """Folder holding the executable (frozen) or this file (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = app_dir()

os.environ.setdefault("PROFILE_HUB_BASE", str(APP_DIR))
sys.path.insert(0, str(APP_DIR))

import profile_hub  # noqa: E402


def free_port(start: int = 8000) -> int:
    import socket
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def wait_ready(url: str, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    port = free_port(8000)
    url = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), profile_hub.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("SkillNova Profile Hub v0.1", flush=True)
    print(f"Open {url} in your browser.", flush=True)
    print("Close this window to stop the tool. Nothing keeps running.",
          flush=True)
    if wait_ready(url):
        if os.environ.get("PROFILE_HUB_NO_BROWSER") != "1":
            try:
                webbrowser.open(url)
            except Exception:
                print("Could not open the browser automatically — "
                      f"please open {url} yourself.", flush=True)
    else:
        print(f"Server did not answer at {url} — please open it manually.",
              flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
