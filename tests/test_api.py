import time

from fastapi.testclient import TestClient

from playlist_transfer.server import app

client = TestClient(app)


def wait(plan_id, statuses=("ready", "applied", "error")):
    for _ in range(100):
        p = client.get(f"/api/plans/{plan_id}").json()
        if p["status"] in statuses:
            return p
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_end_to_end_via_http(fresh):
    r = client.post("/api/plans", json={
        "source": {"service": "spotify", "kind": "playlist", "refs": ["spp1"], "names": ["Road Trip"]},
        "target": {"service": "qobuz", "kind": "playlist"}, "options": {},
    })
    pid = r.json()["plan_ids"][0]
    p = wait(pid)
    assert p["status"] == "ready"
    assert "target_existing" not in p

    # exclude one item, switch to a brand new playlist
    africa = next(i for i in p["items"] if i["source"]["title"] == "Africa")
    p = client.post(f"/api/plans/{pid}/edit", json={"op": "include", "idx": [africa["idx"]], "value": False}).json()
    p = client.post(f"/api/plans/{pid}/edit", json={"op": "target", "mode": "new", "name": "Road Trip 2"}).json()
    assert p["target"]["mode"] == "new" and p["summary"]["exists"] == 0 and p["target_only"] == []

    assert client.post(f"/api/plans/{pid}/apply", json={}).json()["ok"]
    p = wait(pid, ("applied",))
    created = [pl for pl, ids in fresh["qobuz"].playlists.values() if pl.name == "Road Trip 2"]
    assert created and "q13" not in fresh["qobuz"].playlists[created[0].id][1]

    csv = client.get(f"/api/plans/{pid}/report?which=not_found").text
    assert "Some Obscure Demo" in csv and "Africa" not in csv
    assert len(client.get("/api/history").json()["history"]) == 1


def test_bad_request_is_400(fresh):
    r = client.post("/api/plans", json={
        "source": {"service": "spotify", "kind": "albums", "refs": ["albums"]},
        "target": {"service": "qobuz", "kind": "liked"},
    })
    assert r.status_code == 400


def test_create_plans_with_mappings(fresh):
    r = client.post("/api/plans", json={
        "source": {"service": "spotify"}, "target": {"service": "qobuz"}, "options": {},
        "mappings": [
            {"kind": "liked", "ref": "liked", "name": "Liked Songs", "target": {"kind": "liked"}},
            {"kind": "playlist", "ref": "spp2", "name": "Chill Evenings",
             "target": {"kind": "playlist", "mode": "existing", "playlist_id": "qp3", "name": "Chill Evenings (Spotify)"}},
        ]})
    ids = r.json()["plan_ids"]
    plans = [wait(i) for i in ids]
    assert plans[0]["target"]["kind"] == "liked" and plans[0]["status"] == "ready"
    assert plans[1]["target"]["playlist_id"] == "qp3" and plans[1]["summary"]["exists"] == 2
    m = client.get("/api/mapping?source=spotify&target=qobuz").json()
    assert m["suggestions"]["playlist|spp2"]["matched"]
    csv = client.get(f"/api/plans/{ids[1]}/report?which=missing").text
    assert "Blinding Lights" in csv and "Midnight City" not in csv


def test_restart_recovers_interrupted_reviews(fresh):
    from playlist_transfer import engine, store
    from playlist_transfer.server import recover_interrupted_plans

    a = engine.create_plan({"service": "spotify", "kind": "liked", "refs": ["liked"]}, {"service": "qobuz"}, {})
    b = engine.analyze(engine.create_plan({"service": "spotify", "kind": "liked", "refs": ["liked"]},
                                          {"service": "qobuz"}, {})["id"])
    b["status"] = "applying"
    store.save_plan(b)
    assert recover_interrupted_plans() == 2
    assert store.get_plan(a["id"])["status"] == "error"
    assert store.get_plan(b["id"])["status"] == "ready"
    assert client.get("/api/dev/version").json()["version"]
