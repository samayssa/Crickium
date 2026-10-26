#!/usr/bin/env python3
"""Run Crickium's Telegram bot and Mini App web server in one Railway service."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = os.environ.get("PORT", "8000").strip() or "8000"
PROCESSES: list[tuple[str, subprocess.Popen]] = []
STOP_REQUESTED = False


def _keepalive_url() -> str:
    """Public URL Render gives this service (RENDER_EXTERNAL_URL), or KEEPALIVE_URL / BACKEND_URL."""
    base = (
        os.environ.get("KEEPALIVE_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("BACKEND_URL")
        or ""
    ).strip().rstrip("/")
    return f"{base}/api/health" if base else ""


def keepalive_loop() -> None:
    """Render free web services sleep after 15 min without inbound HTTP traffic.

    Every KEEPALIVE_SECONDS (default 600 = 10 min) the service requests its own
    public /api/health URL. That endpoint never touches the database, so it
    costs no Neon compute. Runs as a daemon thread in the launcher only: the bot
    and the web server code are untouched.
    """
    if os.environ.get("KEEPALIVE_ENABLED", "1").strip() == "0":
        return
    url = _keepalive_url()
    if not url:
        print("[keepalive] No RENDER_EXTERNAL_URL/KEEPALIVE_URL/BACKEND_URL set; keep-awake disabled.", flush=True)
        return
    try:
        every = max(60, min(840, int(os.environ.get("KEEPALIVE_SECONDS", "600"))))
    except ValueError:
        every = 600
    print(f"[keepalive] Pinging {url} every {every}s", flush=True)
    time.sleep(30)  # let the web server come up first
    while not STOP_REQUESTED:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crickium-keepalive"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read(64)
        except Exception as exc:
            print(f"[keepalive] ping failed: {exc!r}", flush=True)
        for _ in range(every):
            if STOP_REQUESTED:
                return
            time.sleep(1)


def handle_signal(signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print(f"[railway_start] Received signal {signum}; stopping bot and Mini App...", flush=True)


def stop_children() -> None:
    for name, process in PROCESSES:
        if process.poll() is None:
            print(f"[railway_start] Stopping {name}...", flush=True)
            try:
                process.terminate()
            except Exception:
                pass

    deadline = time.monotonic() + 10
    for name, process in PROCESSES:
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                print(f"[railway_start] Force-stopping {name}...", flush=True)
                try:
                    process.kill()
                except Exception:
                    pass


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    exit_code = 0
    try:
        print("[railway_start] Starting Telegram bot: python main.py", flush=True)
        bot = subprocess.Popen(
            [sys.executable, "-u", "main.py"],
            cwd=str(ROOT),
            env=env,
        )
        PROCESSES.append(("Telegram bot", bot))

        print(f"[railway_start] Starting Mini App on 0.0.0.0:{PORT}", flush=True)
        web = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "uvicorn",
                "miniapp_backend.server:app",
                "--host",
                "0.0.0.0",
                "--port",
                PORT,
                "--lifespan",
                "on",
            ],
            cwd=str(ROOT),
            env=env,
        )
        PROCESSES.append(("Mini App web server", web))

        threading.Thread(target=keepalive_loop, name="keepalive", daemon=True).start()

        print("[railway_start] Both processes started. Logs from both will appear below.", flush=True)
        while not STOP_REQUESTED:
            for name, process in PROCESSES:
                result = process.poll()
                if result is not None:
                    print(
                        f"[railway_start] {name} exited with code {result}. "
                        "Stopping the other process so Railway can restart the service.",
                        flush=True,
                    )
                    exit_code = result if result != 0 else 1
                    return exit_code
            time.sleep(1)
        return 0
    except Exception as exc:
        print(f"[railway_start][ERROR] {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        stop_children()


if __name__ == "__main__":
    raise SystemExit(main())
