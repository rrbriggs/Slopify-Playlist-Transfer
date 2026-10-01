"""Local web app: JSON API + single-page UI."""
from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import engine, providers, store
from .providers.base import NotConnected, ProviderError
from .providers.files import export_items, parse_upload, remove_duplicate_imports

STATIC = Path(__file__).parent / "static"
BOOT_ID = uuid.uuid4().hex[:8]  # changes every time the server (re)starts; the page reloads when it does
log = logging.getLogger(__name__)


def recover_interrupted_plans() -> int:
    """Background work dies with the process (e.g. on a code reload). Don't leave reviews stuck."""
    n = 0
    for p in store.list_plans(limit=100000):
        if p["status"] == "analyzing":
            p.update(status="error", error="Interrupted because the app restarted. Click Re-analyze.")
        elif p["status"] == "applying":
            p.update(status="ready", error="Interrupted while applying because the app restarted. Some items may "
                                           "already be added; applying again is safe (it skips what's already there).")
        else:
            continue
        store.save_plan(p)
        n += 1
    return n


@asynccontextmanager
async def lifespan(_: FastAPI):
    dupes = remove_duplicate_imports()
    if dupes:
        print(f"  Removed {dupes} duplicate imported playlist(s).")
    if recover_interrupted_plans():
        print("  Marked reviews interrupted by the restart (re-analyze or apply them again).")
    scheduler = engine.Scheduler()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Playlist Transfer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/dev/version")
def dev_version():
    """Fingerprint of the running server + UI files; the page polls it and reloads itself when it changes."""
    newest = max((p.stat().st_mtime_ns for p in STATIC.iterdir() if p.is_file()), default=0)
    return {"version": f"{BOOT_ID}-{newest}"}


@app.exception_handler(NotConnected)
async def _not_connected(_: Request, exc: NotConnected):
    return JSONResponse({"detail": str(exc), "not_connected": True}, status_code=401)


@app.exception_handler(ProviderError)
async def _provider_error(_: Request, exc: ProviderError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


def _base_url(request: Request) -> str:
    # Spotify only accepts loopback *IP* redirect URIs over http, never "localhost".
    port = request.url.port or 8765
    return f"http://127.0.0.1:{port}"


# ------------------------------------------------------------------ services & auth
@app.get("/api/services")
def services(request: Request):
    out = []
    for name, p in providers.registry().items():
        try:
            acct = p.account()
        except Exception as e:  # noqa: BLE001
            acct = {"connected": False, "error": str(e)}
        out.append({"name": name, "label": p.label, "account": acct, "can_write": p.can_write,
                    "read_kinds": list(p.read_kinds), "write_kinds": list(p.write_kinds)})
    return {"services": out, "demo": bool(os.environ.get("PLT_DEMO")),
            "spotify_redirect_uri": f"{_base_url(request)}/api/auth/spotify/callback"}


@app.post("/api/auth/spotify/start")
def spotify_start(request: Request, body: dict = Body(...)):
    client_id = (body.get("client_id") or store.get_section("spotify").get("client_id") or "").strip()
    if not client_id:
        raise ProviderError("A Spotify Client ID is required")
    url = providers.get("spotify").begin_auth(client_id, f"{_base_url(request)}/api/auth/spotify/callback")
    return {"url": url}


@app.get("/api/auth/spotify/callback")
def spotify_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/#/?error=spotify:{error}")
    try:
        providers.get("spotify").finish_auth(code, state)
    except ProviderError as e:
        return RedirectResponse(f"/#/?error={str(e)[:200]}")
    return RedirectResponse("/#/?connected=spotify")


@app.post("/api/auth/qobuz/token")
def qobuz_token(body: dict = Body(...)):
    providers.get("qobuz").connect_token(body["user_id"], body["token"], body.get("app_id") or None)
    return providers.get("qobuz").account()


@app.post("/api/auth/qobuz/password")
def qobuz_password(body: dict = Body(...)):
    providers.get("qobuz").connect_password(body["email"], body["password"], body.get("app_id") or None)
    return providers.get("qobuz").account()


@app.post("/api/auth/{service}/disconnect")
def disconnect(service: str):
    p = providers.get(service)
    if hasattr(p, "disconnect"):
        p.disconnect()
    return {"ok": True}


# ------------------------------------------------------------------ browsing / import / export
@app.get("/api/services/{service}/collections")
def collections(service: str):
    p = providers.get(service)
    return {"collections": [c.to_dict() for c in p.collections()]}


@app.get("/api/services/{service}/items")
def collection_items(service: str, kind: str, ref: str = ""):
    items = providers.get(service).read_items(kind, ref)
    return {"items": [i.to_dict() for i in items]}


@app.get("/api/services/{service}/export")
def export(service: str, kind: str, ref: str = "", fmt: str = "csv", name: str = "export"):
    items = providers.get(service).read_items(kind, ref)
    content, media = export_items(items, fmt, name)
    safe = "".join(c for c in name if c.isalnum() or c in " -_").strip() or "export"
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{safe}.{fmt}"'})


@app.post("/api/import")
async def import_file(file: UploadFile = File(...)):
    raw = await file.read()
    return {"imported": parse_upload(file.filename or "import.csv", raw)}


@app.delete("/api/import/{ref}")
def delete_import(ref: str):
    providers.get("file").delete(ref)
    return {"ok": True}


@app.post("/api/imports/delete")
def delete_imports(body: dict = Body(...)):
    for ref in body.get("ids") or []:
        providers.get("file").delete(ref)
    return {"deleted": len(body.get("ids") or [])}


@app.post("/api/imports/dedupe")
def dedupe_imports():
    return {"removed": remove_duplicate_imports()}


# ------------------------------------------------------------------ plans
def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "status", "progress", "source", "target", "summary", "created_at", "updated_at",
            "result", "error", "sync_id", "item_type")
    return {k: plan.get(k) for k in keys}


@app.get("/api/mapping")
def mapping(source: str, target: str):
    """Suggest which target playlist (or favorites) each source playlist was previously transferred to."""
    return engine.suggest_mappings(source, target)


@app.post("/api/plans")
def create_plans(body: dict = Body(...)):
    options = body.get("options") or {}
    ids = []
    if body.get("mappings"):
        # One review per source collection, each with its own explicit target.
        svc = body["source"]["service"]
        for m in body["mappings"]:
            src = {"service": svc, "kind": m["kind"], "refs": [m["ref"]], "names": [m["name"]]}
            plan = engine.create_plan(src, {"service": body["target"]["service"], **m["target"]}, dict(options))
            ids.append(plan["id"])
    else:
        source, target = body["source"], body["target"]
        refs = source.get("refs") or [source["kind"]]
        names = source.get("names") or refs
        if source["kind"] == "playlist" and not options.get("merge") and len(refs) > 1:
            groups = [([r], [n]) for r, n in zip(refs, names)]
        else:
            groups = [(refs, names)]
        for g_refs, g_names in groups:
            plan = engine.create_plan({**source, "refs": g_refs, "names": g_names}, dict(target), dict(options))
            ids.append(plan["id"])
    # Analyze sequentially in one background thread to stay polite with rate limits.
    threading.Thread(target=lambda: [engine.analyze(i) for i in ids], daemon=True).start()
    return {"plan_ids": ids}


@app.get("/api/plans")
def list_plans():
    return {"plans": [_summary(p) for p in store.list_plans()]}


@app.post("/api/plans/dedupe")
def dedupe_plans():
    return {"removed": engine.remove_duplicate_plans()}


@app.post("/api/plans/delete")
def delete_plans(body: dict = Body(...)):
    for pid in body.get("ids") or []:
        store.delete_plan(pid)
    return {"deleted": len(body.get("ids") or [])}


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    plan = store.get_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    plan.pop("target_existing", None)
    plan.pop("trace", None)
    return plan


@app.delete("/api/plans/{plan_id}")
def delete_plan(plan_id: str):
    store.delete_plan(plan_id)
    return {"ok": True}


@app.post("/api/plans/{plan_id}/edit")
def edit_plan(plan_id: str, body: dict = Body(...)):
    plan = engine.edit_plan(plan_id, body)
    plan.pop("target_existing", None)
    return plan


@app.post("/api/plans/{plan_id}/search")
def search(plan_id: str, body: dict = Body(...)):
    return {"candidates": engine.manual_search(plan_id, int(body["idx"]), body["query"])}


@app.post("/api/plans/{plan_id}/reanalyze")
def reanalyze(plan_id: str):
    plan = store.get_plan(plan_id)
    plan.update(status="analyzing", error=None, result=None)
    store.save_plan(plan)
    engine.analyze_async(plan_id)
    return {"ok": True}


@app.post("/api/plans/{plan_id}/apply")
def apply(plan_id: str, body: dict = Body(default={})):
    keep = body.get("keep_synced")
    plan = store.get_plan(plan_id)
    if not plan or plan["status"] not in ("ready", "applied"):
        raise ProviderError("Plan is not ready to apply")
    if not providers.get(plan["target"]["service"]).can_write:
        raise ProviderError("This service can't be written to")

    def run():
        p = engine.apply(plan_id)
        if keep and p["status"] == "applied" and not p.get("sync_id"):
            engine.sync_from_plan(p, keep.get("schedule", "daily"), bool(keep.get("auto_apply", True)),
                                  bool(keep.get("mirror", False)))

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/plans/{plan_id}/report")
def report(plan_id: str, which: str = "all"):
    import csv
    import io

    plan = store.get_plan(plan_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "Status", "Included", "Source title", "Source artist", "Source album", "Source ISRC/UPC",
                "Target title", "Target artist", "Target album", "Confidence", "Method", "Target URL", "Reason"])
    for i in plan["items"]:
        wanted = {"missing": ("add", "review", "not_found")}.get(which, (which,))
        if which != "all" and i["status"] not in wanted:
            continue
        s, m = i["source"], (i.get("match") or {}).get("item") or {}
        def label(d):
            return d.get("full_title") or d.get("title") or d.get("name") or ""
        def artist(d):
            return ", ".join(d.get("artists") or [])
        w.writerow([i["idx"] + 1, i["status"], "yes" if i["include"] else "no", label(s), artist(s), s.get("album", ""),
                    s.get("isrc") or s.get("upc") or "", label(m), artist(m), m.get("album", ""),
                    (i.get("match") or {}).get("confidence", ""), (i.get("match") or {}).get("method", ""),
                    m.get("url", ""), i.get("reason", "")])
    name = f"diff-{plan_id}-{which}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ syncs & history
@app.get("/api/syncs")
def list_syncs():
    return {"syncs": store.list_syncs(), "schedules": list(engine.SCHEDULES)}


@app.post("/api/syncs/{sync_id}/run")
def run_sync(sync_id: str):
    threading.Thread(target=engine.run_sync, args=(sync_id,), daemon=True).start()
    return {"ok": True}


@app.patch("/api/syncs/{sync_id}")
def update_sync(sync_id: str, body: dict = Body(...)):
    import time

    s = store.get_sync(sync_id)
    if not s:
        raise HTTPException(404)
    for k in ("enabled", "auto_apply", "schedule", "name"):
        if k in body:
            s[k] = body[k]
    if "mirror" in body:
        s["options"]["mirror"] = bool(body["mirror"])
    secs = engine.SCHEDULES.get(s.get("schedule") or "manual")
    if "schedule" in body:
        s["next_run"] = time.time() + secs if secs else None
    store.save_sync(s)
    return s


@app.delete("/api/syncs/{sync_id}")
def delete_sync(sync_id: str):
    store.delete_sync(sync_id)
    return {"ok": True}


@app.get("/api/history")
def history():
    return {"history": store.list_history()}
