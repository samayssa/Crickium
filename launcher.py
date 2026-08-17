#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

PORT = int(os.environ.get("MINIAPP_PORT", "8000"))
HOST = os.environ.get("MINIAPP_HOST", "127.0.0.1")
LOCAL_URL = os.environ.get("MINIAPP_LOCAL_URL", f"http://{HOST}:{PORT}")
URL_FILE = os.environ.get("MINIAPP_URL_FILE", "miniapp_url.txt")

# Do NOT accept api.trycloudflare.com as a public URL.
URL_RE = re.compile(r"https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com")

STOP_EVENT = threading.Event()
BACKEND_PROC: Optional[subprocess.Popen[str]] = None
TUNNEL_PROC: Optional[subprocess.Popen[str]] = None


def find_project_root(start: Path) -> Path:
    for current in [start, *start.parents]:
        if (current / "miniapp_backend" / "main.py").exists():
            return current
    raise FileNotFoundError(
        "miniapp_backend/main.py was not found. Put launcher.py inside the project folder."
    )


def pick_cloudflared() -> str:
    env_bin = os.environ.get("CLOUDFLARED_BIN", "").strip()
    if env_bin:
        p = Path(env_bin).expanduser()
        if p.exists():
            return str(p)

    home_bin = Path.home() / "cloudflared"
    if home_bin.exists():
        return str(home_bin)

    if shutil.which("cloudflared"):
        return "cloudflared"

    local = Path(__file__).resolve().parent / "cloudflared"
    if local.exists():
        return str(local)

    raise FileNotFoundError(
        "cloudflared was not found. Copy the binary to ~/cloudflared or set CLOUDFLARED_BIN."
    )


def stream_until_phrase(proc: subprocess.Popen[str], phrase: str) -> None:
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if line == "" and proc.poll() is not None:
            raise RuntimeError("Backend exited before it finished starting.")
        if line:
            print(line, end="")
            if phrase in line:
                return


def start_backend(project_root: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "uvicorn",
        "miniapp_backend.main:app",
        "--host",
        HOST,
        "--port",
        str(PORT),
    ]

    print(f"[launcher] Starting backend at {LOCAL_URL}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    stream_until_phrase(proc, "Application startup complete")
    print("[launcher] Backend is ready.\n")
    return proc


def terminate_process(proc: Optional[subprocess.Popen[str]]) -> None:
    if not proc:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except Exception:
        pass


def start_tunnel_process(
    cloudflared: str,
    extra_args: Sequence[str],
    local_url: str,
    url_file: str,
) -> tuple[Optional[str], bool]:
    """
    Starts cloudflared, returns (public_url, process_alive).
    If public_url is None and process_alive is False, the attempt failed.
    If public_url is not None and process_alive is False, it created a URL and then exited.
    """
    global TUNNEL_PROC

    # Keep the child environment as close as possible to the successful
    # direct shell invocation you already tested.
    env = os.environ.copy()
    env.pop("GODEBUG", None)

    cmd = [cloudflared, *extra_args, "tunnel", "--url", local_url]
    pretty_args = " ".join(extra_args) if extra_args else "(default)"
    print(f"[launcher] Starting Cloudflare Tunnel: {pretty_args}\n")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    TUNNEL_PROC = proc

    assert proc.stdout is not None
    public_url: Optional[str] = None

    while not STOP_EVENT.is_set():
        line = proc.stdout.readline()
        if line == "" and proc.poll() is not None:
            break

        if line:
            print(line, end="")
            if public_url is None:
                match = URL_RE.search(line)
                if match:
                    public_url = match.group(0)
                    try:
                        Path(url_file).write_text(public_url, encoding="utf-8")
                    except Exception:
                        pass
                    print("\n" + "=" * 60)
                    print("CRICIUM MINI APP HTTPS URL:")
                    print(public_url)
                    print("=" * 60)
                    print(f"[launcher] Saved to {url_file}\n")

    alive = proc.poll() is None
    if not alive:
        terminate_process(proc)
    return public_url, alive


def tunnel_worker(project_root: Path, stop_event: threading.Event) -> None:
    global TUNNEL_PROC

    cloudflared = pick_cloudflared()
    url_file = str(project_root / URL_FILE)

    # Start with the exact default mode that worked in your direct shell test.
    # Fallbacks are only tried if needed.
    attempt_sets: list[list[str]] = [
        [],
        ["--edge-ip-version", "4"],
        ["--protocol", "http2"],
        ["--edge-ip-version", "4", "--protocol", "http2"],
        ["--edge-ip-version", "4", "--protocol", "quic"],
    ]

    try:
        while not stop_event.is_set():
            any_success = False

            for extra_args in attempt_sets:
                if stop_event.is_set():
                    break

                public_url, alive = start_tunnel_process(
                    cloudflared=cloudflared,
                    extra_args=extra_args,
                    local_url=LOCAL_URL,
                    url_file=url_file,
                )

                if public_url:
                    any_success = True

                if alive and not stop_event.is_set():
                    while not stop_event.is_set() and TUNNEL_PROC and TUNNEL_PROC.poll() is None:
                        time.sleep(1)
                    break

                terminate_process(TUNNEL_PROC)
                TUNNEL_PROC = None

            if stop_event.is_set():
                break

            if not any_success:
                print("[launcher] Tunnel failed. Retrying in 5 seconds...\n")
                time.sleep(5)
            else:
                print("[launcher] Tunnel dropped. Restarting in 5 seconds...\n")
                time.sleep(5)

    finally:
        pass


def shutdown(*_: object) -> None:
    print("\n[launcher] Shutting down...")
    STOP_EVENT.set()
    terminate_process(TUNNEL_PROC)
    terminate_process(BACKEND_PROC)


def main() -> int:
    global BACKEND_PROC

    script_dir = Path(__file__).resolve().parent
    project_root = find_project_root(script_dir)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        BACKEND_PROC = start_backend(project_root)
    except Exception as e:
        print(f"[launcher][ERROR] Backend failed: {e}")
        shutdown()
        return 1

    worker = threading.Thread(target=tunnel_worker, args=(project_root, STOP_EVENT), daemon=True)
    worker.start()

    print("[launcher] Both backend and tunnel are running. Press Ctrl+C to stop.")

    try:
        while not STOP_EVENT.is_set():
            if BACKEND_PROC and BACKEND_PROC.poll() is not None:
                raise RuntimeError("Backend stopped unexpectedly.")
            time.sleep(1)
        return 0
    except KeyboardInterrupt:
        shutdown()
        return 130
    except Exception as e:
        print(f"[launcher][ERROR] {e}")
        shutdown()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())