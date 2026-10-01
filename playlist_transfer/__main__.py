"""Command line entry point.

    python -m playlist_transfer serve            # start the web UI (default)
    python -m playlist_transfer serve --demo     # try it with fake services, no accounts needed
    python -m playlist_transfer sync             # run all syncs that are due (for Task Scheduler / cron)
    python -m playlist_transfer sync --all       # run every enabled sync now
    python -m playlist_transfer syncs            # list saved syncs
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser


def _code_snapshot(pkg: str) -> dict[str, float]:
    out = {}
    for root, dirs, files in os.walk(pkg):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                try:
                    out[p] = os.stat(p).st_mtime
                except OSError:
                    pass
    return out


def _supervise(port: int, demo: bool) -> None:
    """Run the server in a child process and restart it whenever a .py file in the package changes.

    (uvicorn's own reloader stops the child with a console Ctrl+C on Windows, which hangs when there is
    no console; killing the child directly always works. UI files in static/ never need a restart.)
    """
    pkg = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, "-m", "playlist_transfer", "serve", "--no-reload", "--no-browser", "--port", str(port)]
    if demo:
        cmd.append("--demo")
    snap = _code_snapshot(pkg)
    proc = subprocess.Popen(cmd)
    crashed = False
    try:
        while True:
            time.sleep(0.7)
            now = _code_snapshot(pkg)
            changed = now != snap
            if changed:
                snap = now
                print("  Code changed, restarting…", flush=True)
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                proc = subprocess.Popen(cmd)
                crashed = False
            elif proc.poll() is not None and not crashed:
                crashed = True
                print(f"  The app stopped (exit code {proc.returncode}). Fix the error above; it restarts on the next "
                      "code change. Press Ctrl+C to quit.", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="playlist-transfer")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="start the web UI")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--demo", action="store_true", help="use fake in-memory services")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-reload", action="store_true", help="don't restart automatically when the code changes")
    y = sub.add_parser("sync", help="run due syncs")
    y.add_argument("--all", action="store_true", help="run every enabled sync, not just due ones")
    sub.add_parser("syncs", help="list syncs")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cmd = args.cmd or "serve"
    if cmd == "serve":
        port = getattr(args, "port", 8765)
        if getattr(args, "demo", False):
            os.environ["PLT_DEMO"] = "1"
            os.environ.setdefault("PLT_DATA_DIR", os.path.join(os.path.expanduser("~"), ".playlist-transfer-demo"))
        url = f"http://127.0.0.1:{port}/"
        reload = not getattr(args, "no_reload", False)
        if not os.environ.get("PLT_SUPERVISED"):
            print(f"\n  Playlist Transfer running at {url}")
            print("  Code changes reload automatically; the browser page refreshes itself.\n" if reload else "")
        if reload:
            os.environ["PLT_SUPERVISED"] = "1"  # inherited by the server child process
        if not getattr(args, "no_browser", False):
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        if reload:
            _supervise(port, demo=getattr(args, "demo", False))
        else:
            import uvicorn

            from .server import app

            uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    elif cmd == "sync":
        from . import engine, store

        todo = [s for s in store.list_syncs() if s.get("enabled")] if args.all else engine.due_syncs()
        if not todo:
            print("No syncs due.")
        for s in todo:
            print(f"Syncing: {s['name']} ...", flush=True)
            out = engine.run_sync(s["id"])
            res = out.get("result") or {}
            if out.get("error"):
                print(f"  error: {out['error']}")
            elif s.get("auto_apply"):
                print(f"  added {res.get('added', 0)}, removed {res.get('removed', 0)}, "
                      f"needs review {out.get('needs_review', 0)}")
            else:
                print(f"  plan ready for review: {out['plan_id']} ({out.get('summary', {}).get('to_add', 0)} to add)")
    elif cmd == "syncs":
        from . import store

        for s in store.list_syncs():
            print(f"{s['id']}  {'on ' if s.get('enabled') else 'off'}  {s.get('schedule'):8}  {s['name']}")


if __name__ == "__main__":
    main()
