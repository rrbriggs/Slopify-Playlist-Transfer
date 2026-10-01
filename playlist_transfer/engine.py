"""Transfer engine: build a plan (the diff), let the user edit it, apply it, sync it.

A plan goes through:  analyzing -> ready -> applying -> applied   (or error)

Each plan item holds the source item, the chosen target match, alternative
candidates, and a *status* that is recomputed by `classify()` whenever the
user changes something:

  add        new on the target, confident match
  review     match found but not confident; user should check it
  exists     already present in the target (no duplicate will be created)
  duplicate  duplicate of an earlier source item, or maps to the same target item
  not_found  nothing good enough on the target
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import providers, store
from .matching import (
    base_title, is_liked_name, norm, playlist_name_score, score_album, score_artist, score_track, search_queries,
    split_artists, track_key,
)
from .models import Album, Artist, Track, item_type_for

log = logging.getLogger(__name__)

ADD_THRESHOLD = 0.85  # >= this: confident match
MIN_THRESHOLD = 0.55  # < this: treated as not found (candidates still shown)
REVIEW_INCLUDE = 0.70  # review items at/above this are included by default
WORKERS = 4

CLS = {"track": Track, "album": Album, "artist": Artist}
_plan_locks: dict[str, threading.Lock] = {}


def _lock_for(plan_id: str) -> threading.Lock:
    return _plan_locks.setdefault(plan_id, threading.Lock())


def to_obj(item_type: str, d: dict[str, Any] | None):
    return CLS[item_type].from_dict(d) if d else None


# ====================================================================== matching
def _score(item_type: str, src, cand) -> tuple[float, str]:
    if item_type == "track":
        return score_track(src, cand)
    if item_type == "album":
        return score_album(src, cand)
    return score_artist(src, cand)


def _rank(item_type: str, src, cands: dict[str, Any]) -> list[dict[str, Any]]:
    scored = []
    for c in cands.values():
        conf, method = _score(item_type, src, c)
        scored.append({"item": c.to_dict(), "confidence": round(conf, 3), "method": method})
    scored.sort(key=lambda x: (-x["confidence"], not x["item"].get("available", True)))
    return scored[:6]


def find_candidates(target, item_type: str, src) -> list[dict[str, Any]]:
    cands: dict[str, Any] = {}

    def add(results):
        for r in results:
            cands.setdefault(r.id, r)

    if item_type == "track":
        if src.isrc:
            add(target.search_tracks_by_isrc(src.isrc))
            ranked = _rank(item_type, src, cands)
            if ranked and ranked[0]["method"] == "isrc":
                return ranked
        for q in search_queries(src):
            add(target.search_tracks(q))
            ranked = _rank(item_type, src, cands)
            if ranked and ranked[0]["confidence"] >= ADD_THRESHOLD:
                return ranked
        return _rank(item_type, src, cands)

    if item_type == "album":
        if src.upc:
            add(target.search_albums_by_upc(src.upc))
            ranked = _rank(item_type, src, cands)
            if ranked and ranked[0]["method"] == "upc":
                return ranked
        artist = src.artists[0] if src.artists else ""
        for q in (f"{base_title(src.title)} {norm(artist)}", base_title(src.title)):
            add(target.search_albums(q))
            ranked = _rank(item_type, src, cands)
            if ranked and ranked[0]["confidence"] >= ADD_THRESHOLD:
                return ranked
        return _rank(item_type, src, cands)

    add(target.search_artists(src.name))
    return _rank(item_type, src, cands)


def match_item(src_service: str, target, item_type: str, src) -> dict[str, Any]:
    cached = store.cache_get(src_service, src.id, target.name, item_type)
    if cached and cached["target"] and (cached["manual"] or cached["confidence"] >= ADD_THRESHOLD):
        cand = {"item": cached["target"], "confidence": cached["confidence"], "method": cached["method"]}
        return {"match": cand, "candidates": [cand], "manual": cached["manual"], "cached": True}
    cands = find_candidates(target, item_type, src)
    best = cands[0] if cands and cands[0]["confidence"] >= MIN_THRESHOLD else None
    if best and best["confidence"] >= ADD_THRESHOLD:
        store.cache_put(src_service, src.id, target.name, item_type, best["item"], best["confidence"], best["method"])
    return {"match": best, "candidates": cands, "manual": False, "cached": False}


# ====================================================================== classify (dedupe)
def _keys(item_type: str, d: dict[str, Any], fuzzy: bool) -> list[str]:
    o = to_obj(item_type, d)
    keys = [f"id:{o.id}"]
    if item_type == "track":
        if o.isrc:
            keys.append(f"isrc:{o.isrc.upper()}")
        if fuzzy and o.title:
            keys.append(f"key:{track_key(o)}")
    elif item_type == "album":
        if o.upc:
            keys.append(f"upc:{o.upc.lstrip('0')}")
        if fuzzy:
            a = split_artists(o.artists)
            keys.append(f"key:{base_title(o.title)}|{a[0] if a else ''}")
    else:
        keys.append(f"key:{norm(o.name)}")
    return keys


def default_include(item: dict[str, Any]) -> bool:
    if item["status"] == "add":
        return True
    if item["status"] == "review":
        return (item.get("match") or {}).get("confidence", 0) >= REVIEW_INCLUDE
    return False


def classify(plan: dict[str, Any]) -> None:
    it = plan["item_type"]
    fuzzy = plan["options"].get("fuzzy_dupes", True)
    existing = plan.get("target_existing") or []
    existing_keys: dict[str, int] = {}
    canon: dict[int, int] = {}  # entries duplicated inside the target map to their first occurrence
    for i, e in enumerate(existing):
        ks = _keys(it, e, fuzzy)
        canon[i] = next((existing_keys[k] for k in ks if k in existing_keys), i)
        for k in ks:
            existing_keys.setdefault(k, i)

    seen_src: dict[str, int] = {}
    claimed: dict[str, int] = {}
    used_existing: set[int] = set()
    counts = {s: 0 for s in ("add", "review", "exists", "duplicate", "not_found")}

    for item in plan["items"]:
        src_keys = _keys(it, item["source"], fuzzy)
        dup_of = next((seen_src[k] for k in src_keys if k in seen_src), None)
        for k in src_keys:
            seen_src.setdefault(k, item["idx"])
        m = item.get("match")
        item["reason"] = ""
        if dup_of is not None:
            item["status"], item["reason"] = "duplicate", f"Duplicate of #{dup_of + 1} in the source"
        elif not m:
            item["status"] = "not_found"
            item["reason"] = "No good match found" + (" (closest candidates listed)" if item.get("candidates") else "")
        else:
            mkeys = _keys(it, m["item"], fuzzy)
            hit = next((existing_keys[k] for k in mkeys if k in existing_keys), None)
            if hit is not None:
                used_existing.add(hit)
                item["status"] = "exists"
                item["reason"] = "Already in the target" + ("" if mkeys[0] in existing_keys else " (same recording)")
            elif m["item"]["id"] in claimed:
                item["status"] = "duplicate"
                item["reason"] = f"Matches the same target item as #{claimed[m['item']['id']] + 1}"
            else:
                claimed[m["item"]["id"]] = item["idx"]
                manual = item.get("manual")
                item["status"] = "add" if manual or m["confidence"] >= ADD_THRESHOLD else "review"
                if item["status"] == "review":
                    item["reason"] = "Low-confidence match — please check"
                if not m["item"].get("available", True):
                    item["status"] = "review"
                    item["reason"] = "Match is not streamable on the target"
        override = item.get("include_override")
        item["include"] = default_include(item) if override is None else bool(override)
        counts[item["status"]] += 1

    # Items that are only in the target (candidates for removal in mirror mode)
    mirror = plan["options"].get("mirror", False)
    prev = {e["item"]["id"]: e.get("remove_override") for e in plan.get("target_only") or []}
    target_only = []
    if plan["target"].get("mode") == "existing":
        for i, e in enumerate(existing):
            if canon[i] in used_existing:
                continue
            if canon[i] != i:
                continue  # repeated entry inside the target; shown once
            ov = prev.get(e["id"])
            target_only.append({"item": e, "remove_override": ov, "remove": mirror if ov is None else bool(ov)})
    plan["target_only"] = target_only
    counts["target_only"] = len(target_only)
    counts["to_add"] = sum(1 for i in plan["items"] if i["include"] and i.get("match")
                           and i["status"] in ("add", "review", "duplicate"))
    counts["to_remove"] = sum(1 for e in target_only if e["remove"])
    counts["total"] = len(plan["items"])
    plan["summary"] = counts


# ====================================================================== plan lifecycle
def _target_choices(target, kind: str) -> list[dict[str, Any]]:
    if kind != "playlist":
        return []
    return [{"id": p.id, "name": p.name, "count": p.track_count}
            for p in target.list_playlists() if p.editable]


def refresh_target(plan: dict[str, Any]) -> None:
    """(Re)load what is currently in the target collection."""
    target = providers.get(plan["target"]["service"])
    t = plan["target"]
    if t["kind"] == "playlist":
        if t.get("mode") == "existing" and t.get("playlist_id"):
            items = target.playlist_tracks(t["playlist_id"])
        else:
            items = []
    else:
        items = target.read_items(t["kind"], "")
    plan["target_existing"] = [i.to_dict() for i in items]


SIMILAR_NAME = 0.75  # suggest an existing target playlist at/above this name similarity


def suggest_mappings(source_service: str, target_service: str) -> dict[str, Any]:
    """For every track list on the source, guess where it was transferred to before.

    Returns {choices: [target playlists], suggestions: {"kind|id": {target, score, matched, alternatives}}}
    where target is {"kind": "liked"} | {"kind": "playlist", "mode": "existing", "playlist_id", "name"}
    | {"kind": "playlist", "mode": "new", "name"}.
    """
    src_p, tgt_p = providers.get(source_service), providers.get(target_service)
    choices = _target_choices(tgt_p, "playlist")
    suggestions: dict[str, Any] = {}
    for c in src_p.collections():
        if c.kind not in ("playlist", "liked") or not c.readable:
            continue
        name = c.name if c.kind == "playlist" else "Liked Songs"
        ranked = sorted(({"id": t["id"], "name": t["name"], "count": t["count"],
                          "score": round(playlist_name_score(name, t["name"]), 3)} for t in choices),
                        key=lambda x: -x["score"])
        alternatives = [r for r in ranked if r["score"] >= 0.5][:5]
        best = alternatives[0] if alternatives and alternatives[0]["score"] >= SIMILAR_NAME else None
        liked = c.kind == "liked" or is_liked_name(c.name)
        if best:
            target = {"kind": "playlist", "mode": "existing", "playlist_id": best["id"], "name": best["name"]}
            matched, score = True, best["score"]
        elif liked:
            target, matched, score = {"kind": "liked"}, True, 1.0
        else:
            target, matched, score = {"kind": "playlist", "mode": "new", "name": c.name}, False, 0.0
        suggestions[f"{c.kind}|{c.id}"] = {"target": target, "score": score, "matched": matched,
                                           "liked": liked, "alternatives": alternatives}
    return {"choices": choices, "suggestions": suggestions}


def create_plan(source: dict[str, Any], target: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Create the plan record. `source` = {service, kind, refs, names}; `target` = {service, kind, ...}."""
    src_kind = source["kind"]
    tgt_kind = target.get("kind") or src_kind
    item_type = item_type_for(src_kind)
    if item_type_for(tgt_kind) != item_type:
        raise providers.ProviderError(f"Can't transfer {src_kind} into {tgt_kind}")
    names = source.get("names") or source["refs"]
    if tgt_kind == "playlist":
        default_name = " + ".join(names) if src_kind == "playlist" else (
            "Liked tracks" if src_kind == "liked" else names[0])
        target.setdefault("name", options.get("name") or default_name)
        target.setdefault("mode", "auto")
    plan = {
        "id": store.new_id(),
        "status": "analyzing",
        "progress": {"phase": "Queued", "done": 0, "total": 0},
        "source": {**source, "names": names},
        "target": {**target, "kind": tgt_kind},
        "options": {"mirror": False, "fuzzy_dupes": True, "public": False, **options},
        "item_type": item_type,
        "items": [],
        "target_only": [],
        "target_existing": [],
        "target_choices": [],
        "summary": {},
        "log": [],
    }
    store.save_plan(plan)
    return plan


def analyze(plan_id: str, on_progress: Callable[[dict], None] | None = None) -> dict[str, Any]:
    plan = store.get_plan(plan_id)
    assert plan is not None
    src_p = providers.get(plan["source"]["service"])
    tgt_p = providers.get(plan["target"]["service"])
    it = plan["item_type"]
    last_save = [0.0]

    def progress(phase: str, done: int, total: int, force: bool = False) -> None:
        plan["progress"] = {"phase": phase, "done": done, "total": total}
        if force or time.time() - last_save[0] > 0.7:
            last_save[0] = time.time()
            store.save_plan(plan)
        if on_progress:
            on_progress(plan)

    try:
        # 1. read source
        progress("Reading source", 0, 0, True)
        src_items = []
        for n, ref in enumerate(plan["source"]["refs"]):
            try:
                src_items.extend(src_p.read_items(plan["source"]["kind"], ref))
            except providers.ProviderError:
                # An imported file may have been replaced by a re-import of the same playlist: follow it by name.
                name = (plan["source"].get("names") or [None] * (n + 1))[n]
                same = [c for c in src_p.collections() if src_p.name == "file" and name
                        and c.kind == plan["source"]["kind"] and norm(c.name) == norm(name)]
                if not same:
                    raise
                plan["source"]["refs"][n] = same[0].id
                src_items.extend(src_p.read_items(plan["source"]["kind"], same[0].id))

        # 2. target: choose / load
        progress("Reading target", 0, 0, True)
        t = plan["target"]
        plan["target_choices"] = _target_choices(tgt_p, t["kind"])
        if t["kind"] == "playlist" and t.get("mode") == "auto":
            same = [c for c in plan["target_choices"] if norm(c["name"]) == norm(t["name"])]
            if same:
                t.update(mode="existing", playlist_id=same[0]["id"])
                plan["log"].append(f"A playlist named “{same[0]['name']}” already exists on {tgt_p.label}; "
                                   "new items will be merged into it instead of creating a duplicate playlist.")
            else:
                t["mode"] = "new"
        refresh_target(plan)

        # 3. match
        total = len(src_items)
        items: list[dict[str, Any] | None] = [None] * total
        done = [0]
        lock = threading.Lock()

        def work(i_src):
            i, src = i_src
            res = match_item(src_p.name, tgt_p, it, src)
            items[i] = {"idx": i, "source": src.to_dict(), **res, "include_override": None}
            with lock:
                done[0] += 1
                progress("Matching", done[0], total)

        progress("Matching", 0, total, True)
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(work, enumerate(src_items)))
        plan["items"] = items
        classify(plan)
        plan["status"] = "ready"
        plan["progress"] = {"phase": "Ready", "done": total, "total": total}
    except Exception as e:  # noqa: BLE001
        log.exception("analyze failed")
        plan["status"] = "error"
        plan["error"] = str(e)
        plan["trace"] = traceback.format_exc()[-2000:]
    store.save_plan(plan)
    return plan


def remove_duplicate_plans() -> int:
    """Delete reviews that repeat a newer one (same source playlist names → same target). Never touches reviews
    that were applied, are running, or belong to a sync. Returns how many were deleted."""
    seen: set[str] = set()
    removed = 0
    for p in store.list_plans(limit=100000):  # newest first
        t = p["target"]
        key = "|".join([p["source"]["service"], p["source"]["kind"], "+".join(norm(n) for n in p["source"]["names"]),
                        t["service"], t["kind"], str(t.get("playlist_id") or norm(t.get("name") or ""))])
        if p["status"] in ("applied", "analyzing", "applying") or p.get("sync_id"):
            seen.add(key)
            continue
        if key in seen:
            store.delete_plan(p["id"])
            removed += 1
        else:
            seen.add(key)
    return removed


def analyze_async(plan_id: str) -> None:
    threading.Thread(target=analyze, args=(plan_id,), daemon=True).start()


# ====================================================================== edits from the UI
def edit_plan(plan_id: str, op: dict[str, Any]) -> dict[str, Any]:
    with _lock_for(plan_id):
        plan = store.get_plan(plan_id)
        if not plan:
            raise KeyError(plan_id)
        if plan["status"] not in ("ready", "applied", "error"):
            raise providers.ProviderError(f"Plan is {plan['status']}; wait for it to finish")
        kind = op["op"]
        by_idx = {i["idx"]: i for i in plan["items"]}
        it = plan["item_type"]
        if kind == "include":
            for idx in op["idx"]:
                by_idx[idx]["include_override"] = bool(op["value"])
        elif kind == "include_status":
            for i in plan["items"]:
                if i["status"] == op["status"]:
                    i["include_override"] = bool(op["value"])
        elif kind == "reset_includes":
            for i in plan["items"]:
                i["include_override"] = None
        elif kind == "remove":
            ids = set(op["ids"])
            for e in plan["target_only"]:
                if e["item"]["id"] in ids:
                    e["remove_override"] = bool(op["value"])
        elif kind == "set_match":
            item = by_idx[op["idx"]]
            cand = op.get("candidate")
            if cand:
                src = to_obj(it, item["source"])
                conf, method = _score(it, src, to_obj(it, cand))
                item["match"] = {"item": cand, "confidence": round(conf, 3), "method": "manual"}
                item["manual"] = True
                item["include_override"] = True
                if not any(c["item"]["id"] == cand["id"] for c in item.get("candidates") or []):
                    item.setdefault("candidates", []).insert(0, {"item": cand, "confidence": round(conf, 3),
                                                                 "method": method})
                store.cache_put(plan["source"]["service"], src.id, plan["target"]["service"], it, cand,
                                conf, "manual", manual=True)
            else:  # explicitly "no match"
                item["match"] = None
                item["manual"] = True
                item["include_override"] = None
        elif kind == "target":
            t = plan["target"]
            if "name" in op:
                t["name"] = op["name"]
            if op.get("mode") == "new":
                t.update(mode="new", playlist_id=None)
            elif op.get("mode") == "existing":
                choice = next((c for c in plan["target_choices"] if c["id"] == op["playlist_id"]), None)
                t.update(mode="existing", playlist_id=op["playlist_id"], name=choice["name"] if choice else t["name"])
            refresh_target(plan)
        elif kind == "options":
            for k in ("mirror", "fuzzy_dupes", "public", "description"):
                if k in op:
                    plan["options"][k] = op[k]
            if "mirror" in op:
                for e in plan["target_only"]:
                    e["remove_override"] = None
        elif kind == "refresh_target":
            refresh_target(plan)
        else:
            raise providers.ProviderError(f"Unknown op {kind}")
        if plan["status"] == "applied" and kind != "options":
            plan["status"] = "ready"  # editing an applied plan re-opens it (e.g. to add more)
        classify(plan)
        store.save_plan(plan)
        return plan


def manual_search(plan_id: str, idx: int, query: str) -> list[dict[str, Any]]:
    plan = store.get_plan(plan_id)
    it = plan["item_type"]
    tgt = providers.get(plan["target"]["service"])
    src = to_obj(it, next(i for i in plan["items"] if i["idx"] == idx)["source"])
    if it == "track":
        results = tgt.search_tracks(query)
    elif it == "album":
        results = tgt.search_albums(query)
    else:
        results = tgt.search_artists(query)
    return _rank(it, src, {r.id: r for r in results}) if results else []


# ====================================================================== apply
def apply(plan_id: str) -> dict[str, Any]:
    with _lock_for(plan_id):
        plan = store.get_plan(plan_id)
        if plan["status"] not in ("ready", "applied"):
            raise providers.ProviderError(f"Plan is {plan['status']}")
        plan["status"] = "applying"
        plan["progress"] = {"phase": "Applying", "done": 0, "total": 0}
        store.save_plan(plan)

    tgt = providers.get(plan["target"]["service"])
    t = plan["target"]
    it = plan["item_type"]
    result: dict[str, Any] = {"added": 0, "skipped_existing": 0, "removed": 0, "errors": []}
    try:
        # Re-read the target right before writing so we never add duplicates even if
        # it changed since the plan was analyzed.
        refresh_target(plan)
        fresh_ids = {e["id"] for e in plan["target_existing"]}
        fresh_isrc = {e.get("isrc", "").upper() for e in plan["target_existing"] if e.get("isrc")}

        to_add: list[str] = []
        seen: set[str] = set()
        for i in plan["items"]:
            if not i["include"] or not i.get("match"):
                continue
            m = i["match"]["item"]
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            if m["id"] in fresh_ids or (m.get("isrc") and m["isrc"].upper() in fresh_isrc):
                result["skipped_existing"] += 1
                continue
            to_add.append(m["id"])

        if t["kind"] == "playlist" and t.get("mode") != "existing":
            created = tgt.create_playlist(t["name"], plan["options"].get("description")
                                          or f"Transferred from {plan['source']['service'].title()} by playlist-transfer",
                                          bool(plan["options"].get("public")))
            t.update(mode="existing", playlist_id=created.id, url=created.url)
            plan["target_choices"].append({"id": created.id, "name": created.name, "count": 0})
            result["created_playlist"] = created.name

        ref = t.get("playlist_id") or ""
        total = len(to_add)
        step = 50
        for n in range(0, total, step):
            batch = to_add[n:n + step]
            try:
                tgt.add_items(t["kind"], ref, batch)
                result["added"] += len(batch)
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"Adding items {n + 1}-{n + len(batch)}: {e}")
            plan["progress"] = {"phase": "Adding", "done": min(n + step, total), "total": total}
            store.save_plan(plan)

        removals = [e["item"] for e in plan["target_only"] if e["remove"]]
        if removals:
            try:
                tgt.remove_items(t["kind"], ref, [to_obj(it, d) for d in removals])
                result["removed"] = len(removals)
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"Removing items: {e}")

        result["not_transferred"] = sum(1 for i in plan["items"] if not i["include"] and i["status"] != "exists")
        result["target_url"] = t.get("url") or ""
        refresh_target(plan)
        classify(plan)
        plan["status"] = "applied"
        plan["result"] = {**result, "at": time.time()}
        plan["progress"] = {"phase": "Done", "done": total, "total": total}
        store.add_history(plan_id, {
            "source": plan["source"], "target": {k: t.get(k) for k in ("service", "kind", "name", "playlist_id")},
            "result": result, "sync_id": plan.get("sync_id"),
        })
    except Exception as e:  # noqa: BLE001
        log.exception("apply failed")
        plan["status"] = "ready"
        plan["error"] = f"Apply failed: {e}"
        result["errors"].append(str(e))
        plan["result"] = result
    store.save_plan(plan)
    return plan


def apply_async(plan_id: str) -> None:
    threading.Thread(target=apply, args=(plan_id,), daemon=True).start()


# ====================================================================== sync
SCHEDULES = {"manual": None, "hourly": 3600, "daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}


def sync_from_plan(plan: dict[str, Any], schedule: str, auto_apply: bool, mirror: bool) -> dict[str, Any]:
    t = plan["target"]
    sync = {
        "id": store.new_id(),
        "name": f"{' + '.join(plan['source']['names'])} → {t.get('name') or t['kind']}",
        "source": plan["source"],
        "target": {"service": t["service"], "kind": t["kind"], "playlist_id": t.get("playlist_id"),
                   "name": t.get("name"), "mode": "existing" if t.get("playlist_id") else t.get("mode")},
        "options": {**plan["options"], "mirror": mirror},
        "schedule": schedule,
        "auto_apply": auto_apply,
        "enabled": True,
        "last_run": None,
        "next_run": time.time() + SCHEDULES[schedule] if SCHEDULES.get(schedule) else None,
        "last_plan_id": plan["id"],
        "last_result": None,
    }
    store.save_sync(sync)
    plan["sync_id"] = sync["id"]
    store.save_plan(plan)
    return sync


def run_sync(sync_id: str) -> dict[str, Any]:
    sync = store.get_sync(sync_id)
    plan = create_plan(dict(sync["source"]), dict(sync["target"]), dict(sync["options"]))
    plan["sync_id"] = sync_id
    store.save_plan(plan)
    plan = analyze(plan["id"])
    outcome: dict[str, Any] = {"plan_id": plan["id"], "status": plan["status"]}
    if plan["status"] == "ready":
        outcome["summary"] = plan["summary"]
        if sync.get("auto_apply"):
            # Only confident matches are applied unattended; anything needing review stays in the plan.
            for i in plan["items"]:
                if i["status"] == "review" and not i.get("manual"):
                    i["include_override"] = False
            classify(plan)
            store.save_plan(plan)
            plan = apply(plan["id"])
            outcome["result"] = plan.get("result")
            outcome["needs_review"] = plan["summary"].get("review", 0)
    else:
        outcome["error"] = plan.get("error")
    sync = store.get_sync(sync_id) or sync
    if plan["target"].get("playlist_id"):
        sync["target"].update(playlist_id=plan["target"]["playlist_id"], mode="existing")
    sync["last_run"] = time.time()
    secs = SCHEDULES.get(sync.get("schedule") or "manual")
    sync["next_run"] = time.time() + secs if secs else None
    sync["last_plan_id"] = plan["id"]
    sync["last_result"] = outcome
    store.save_sync(sync)
    return outcome


def due_syncs() -> list[dict[str, Any]]:
    now = time.time()
    return [s for s in store.list_syncs() if s.get("enabled") and s.get("next_run") and s["next_run"] <= now]


class Scheduler(threading.Thread):
    def __init__(self, interval: int = 60) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            for s in due_syncs():
                try:
                    log.info("Running scheduled sync %s", s["name"])
                    run_sync(s["id"])
                except Exception:  # noqa: BLE001
                    log.exception("scheduled sync failed")

    def stop(self) -> None:
        self._stop_event.set()
