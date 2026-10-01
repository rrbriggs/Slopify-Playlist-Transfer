from playlist_transfer import engine, store


def make(source, target, options=None):
    plan = engine.create_plan(source, target, options or {})
    return engine.analyze(plan["id"])


def by_title(plan):
    out = {}
    for i in plan["items"]:
        out.setdefault(i["source"]["title"], i)  # first occurrence
    return out


def test_playlist_merges_into_same_named_playlist_and_dedupes(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp1"], "names": ["Road Trip"]},
                {"service": "qobuz"})
    assert plan["status"] == "ready", plan.get("error")
    # existing Qobuz "Road Trip" is reused instead of creating a second playlist
    assert plan["target"]["mode"] == "existing" and plan["target"]["playlist_id"] == "qp1"
    items = by_title(plan)
    assert items["Smells Like Teen Spirit"]["status"] == "exists"
    assert items["Mr. Brightside"]["status"] == "exists"
    dups = [i for i in plan["items"] if i["status"] == "duplicate"]
    assert len(dups) == 1 and dups[0]["source"]["title"] == "Smells Like Teen Spirit"
    assert items["Some Obscure Demo"]["status"] == "not_found"
    assert items["Bohemian Rhapsody - Remastered 2011"]["match"]["item"]["id"] == "q1"
    assert items["Get Lucky (feat. Pharrell Williams & Nile Rodgers)"]["match"]["item"]["id"] == "q3"
    assert items["Dancing With Myself"]["status"] == "add"
    # Wonderwall is only in the target
    assert [e["item"]["id"] for e in plan["target_only"]] == ["q15"]
    assert plan["target_only"][0]["remove"] is False


def test_apply_adds_only_new_items_in_order(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp1"], "names": ["Road Trip"]},
                {"service": "qobuz"})
    plan = engine.apply(plan["id"])
    assert plan["status"] == "applied", plan.get("error")
    ids = fresh["qobuz"].playlists["qp1"][1]
    assert ids[:3] == ["q2", "q10", "q15"]
    assert ids[3:] == ["q1", "q3", "q4", "q5", "q11", "q12", "q13"]
    assert len(ids) == len(set(ids))
    # applying again is a no-op: nothing is duplicated
    plan = engine.apply(plan["id"])
    assert fresh["qobuz"].playlists["qp1"][1] == ids
    assert plan["summary"]["to_add"] == 0


def test_new_playlist_and_low_confidence_review(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp2"], "names": ["Chill Evenings"]},
                {"service": "qobuz"})
    assert plan["target"]["mode"] == "new"
    items = by_title(plan)
    assert items["Jóga"]["match"]["item"]["id"] == "q9"
    halle = items["Hallelujah"]
    assert halle["status"] in ("review", "not_found")
    assert halle["include"] is False or halle["match"]["confidence"] >= 0.7
    assert items["Heroes - 2017 Remaster"]["match"]["item"]["id"] == "q14"
    plan = engine.apply(plan["id"])
    created = [c for c in fresh["qobuz"].calls if c[0] == "create_playlist"]
    assert created == [("create_playlist", "Chill Evenings")]


def test_manual_override_is_cached(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp2"], "names": ["Chill"]},
                {"service": "qobuz"})
    idx = by_title(plan)["Hallelujah"]["idx"]
    cands = engine.manual_search(plan["id"], idx, "hallelujah buckley")
    live = next(c for c in cands if c["item"]["id"] == "q6")
    plan = engine.edit_plan(plan["id"], {"op": "set_match", "idx": idx, "candidate": live["item"]})
    item = by_title(plan)["Hallelujah"]
    assert item["status"] == "add" and item["include"] and item["match"]["method"] == "manual"
    cached = store.cache_get("spotify", "sp6", "qobuz", "track")
    assert cached["manual"] and cached["target"]["id"] == "q6"


def test_mirror_removes_target_only(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp1"], "names": ["Road Trip"]},
                {"service": "qobuz"}, {"mirror": True})
    assert plan["target_only"][0]["remove"] is True
    engine.apply(plan["id"])
    assert "q15" not in fresh["qobuz"].playlists["qp1"][1]


def test_liked_albums_artists(fresh):
    liked = make({"service": "spotify", "kind": "liked", "refs": ["liked"]}, {"service": "qobuz"})
    assert by_title(liked)["Mr. Brightside"]["status"] == "exists"
    engine.apply(liked["id"])
    assert set(fresh["qobuz"].liked) >= {"q1", "q2", "q10", "q11", "q12"}

    albums = make({"service": "spotify", "kind": "albums", "refs": ["albums"]}, {"service": "qobuz"})
    st = {i["source"]["title"]: i["status"] for i in albums["items"]}
    assert st == {"Nevermind (Remastered)": "exists", "Hot Fuss": "add", "Random Access Memories": "add",
                  "Basement Tapes": "not_found"}

    artists = make({"service": "spotify", "kind": "artists", "refs": ["artists"]}, {"service": "qobuz"})
    st = {i["source"]["name"]: i["status"] for i in artists["items"]}
    assert st["Queen"] == "exists" and st["Daft Punk"] == "add" and st["Unknown Garage Band"] == "not_found"


def test_merge_multiple_playlists_dedupes_across_them(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp1", "spp2"], "names": ["Road Trip", "Chill"]},
                {"service": "qobuz", "kind": "playlist"}, {"name": "Everything"})
    blinding = [i for i in plan["items"] if i["source"]["title"] == "Blinding Lights"]
    assert [i["status"] for i in blinding] == ["add", "duplicate"]


def test_reverse_direction_qobuz_to_spotify(fresh):
    plan = make({"service": "qobuz", "kind": "playlist", "refs": ["qp1"], "names": ["Road Trip"]},
                {"service": "spotify"})
    assert plan["target"]["playlist_id"] == "spp1"
    st = {i["source"]["title"]: i["status"] for i in plan["items"]}
    assert st["Smells Like Teen Spirit"] == "exists"
    assert st["Wonderwall"] == "add"


def test_unreadable_source_reports_error(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp3"], "names": ["Discover Weekly"]},
                {"service": "qobuz"})
    assert plan["status"] == "error" and "not owned" in plan["error"]


def test_sync_auto_apply_only_new(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp1"], "names": ["Road Trip"]},
                {"service": "qobuz"})
    plan = engine.apply(plan["id"])
    sync = engine.sync_from_plan(plan, "daily", auto_apply=True, mirror=False)
    before = list(fresh["qobuz"].playlists["qp1"][1])
    # a new track shows up in the source playlist
    fresh["spotify"].playlists["spp1"][1].append("sp8")
    out = engine.run_sync(sync["id"])
    assert out["result"]["added"] == 1
    assert fresh["qobuz"].playlists["qp1"][1] == before + ["q8"]


def test_file_import(fresh, tmp_path):
    from playlist_transfer.providers.files import parse_upload

    csv_text = ('Track URI,Track Name,Artist Name(s),Album Name,ISRC,Duration (ms)\n'
                'spotify:track:sp13,"Africa","TOTO","Toto IV",USSM19801546,295000\n')
    [rec] = parse_upload("my.csv", csv_text.encode())
    plan = make({"service": "file", "kind": "playlist", "refs": [rec["id"]], "names": ["my"]}, {"service": "qobuz"})
    assert plan["items"][0]["source"]["id"] == "sp13"
    assert plan["items"][0]["match"]["item"]["id"] == "q13"
    [rec2] = parse_upload("list.txt", b"1. Oasis - Wonderwall\nM83 - Midnight City\n")
    assert rec2["count"] == 2


def test_spotify_data_download_zip(fresh):
    import io
    import json
    import zipfile

    from playlist_transfer.providers.files import parse_upload

    playlists = {"playlists": [{"name": "Road Trip", "items": [
        {"track": {"trackName": "Mr. Brightside", "artistName": "The Killers", "albumName": "Hot Fuss",
                   "trackUri": "spotify:track:sp10"}, "episode": None, "localTrack": None},
        {"track": None, "episode": None, "localTrack": {"uri": "spotify:local:Oasis:Morning+Glory:Wonderwall:258"}},
    ]}]}
    library = {"tracks": [{"artist": "Queen", "album": "A Night At The Opera", "track": "Bohemian Rhapsody",
                           "uri": "spotify:track:sp1"}],
               "albums": [{"artist": "The Killers", "album": "Hot Fuss", "uri": "spotify:album:spa2"}],
               "artists": [{"name": "Daft Punk", "uri": "spotify:artist:spr3"}], "shows": [], "episodes": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Spotify Account Data/Playlist1.json", json.dumps(playlists))
        z.writestr("Spotify Account Data/YourLibrary.json", json.dumps(library))
        z.writestr("Spotify Account Data/StreamingHistory_music_0.json", json.dumps([{"trackName": "x"}]))
    recs = {r["name"]: r for r in parse_upload("my_spotify_data.zip", buf.getvalue())}
    assert set(recs) == {"Road Trip", "Liked Songs", "Saved albums", "Followed artists"}
    assert recs["Road Trip"]["count"] == 2 and recs["Saved albums"]["kind"] == "albums"

    plan = make({"service": "file", "kind": "playlist", "refs": [recs["Road Trip"]["id"]], "names": ["Road Trip"]},
                {"service": "qobuz"})
    st = {i["source"]["title"]: (i["status"], (i["match"] or {}).get("item", {}).get("id")) for i in plan["items"]}
    assert st["Mr. Brightside"] == ("exists", "q10")      # merged into existing Qobuz "Road Trip"
    assert st["Wonderwall"] == ("exists", "q15")          # local file matched by text
    albums = make({"service": "file", "kind": "albums", "refs": [recs["Saved albums"]["id"]]}, {"service": "qobuz"})
    assert albums["items"][0]["match"]["item"]["id"] == "qa2"
    artists = make({"service": "file", "kind": "artists", "refs": [recs["Followed artists"]["id"]]}, {"service": "qobuz"})
    assert artists["items"][0]["status"] == "add"


def test_suggest_mappings_finds_previous_transfers(fresh):
    m = engine.suggest_mappings("spotify", "qobuz")
    sug = m["suggestions"]
    assert sug["playlist|spp1"]["target"] == {"kind": "playlist", "mode": "existing", "playlist_id": "qp1",
                                               "name": "Road Trip"}
    assert sug["playlist|spp2"]["target"]["playlist_id"] == "qp3"  # "Chill Evenings (Spotify)"
    assert sug["playlist|spp2"]["matched"]
    assert sug["liked|liked"]["target"] == {"kind": "liked"}
    assert "playlist|spp3" not in sug  # unreadable


def test_mapped_plan_shows_only_whats_missing(fresh):
    plan = make({"service": "spotify", "kind": "playlist", "refs": ["spp2"], "names": ["Chill Evenings"]},
                {"service": "qobuz", "kind": "playlist", "mode": "existing", "playlist_id": "qp3",
                 "name": "Chill Evenings (Spotify)"})
    st = {i["source"]["title"]: i["status"] for i in plan["items"]}
    assert st["Midnight City"] == "exists" and st["Jóga"] == "exists"
    missing = {t for t, s in st.items() if s in ("add", "review", "not_found")}
    assert missing == {"Hallelujah", "Heroes - 2017 Remaster", "Blinding Lights"}
    # the Qobuz-only song is listed but never removed by default
    assert [e["item"]["id"] for e in plan["target_only"]] == ["q16"] and not plan["target_only"][0]["remove"]
    engine.apply(plan["id"])
    assert "q16" in fresh["qobuz"].playlists["qp3"][1]


def test_liked_file_import_to_favorites(fresh):
    from playlist_transfer.providers.files import parse_upload

    csv_text = "Track Name,Artist Name(s)\nMr. Brightside,The Killers\nBlinding Lights,The Weeknd\n"
    [rec] = parse_upload("liked_songs.csv", csv_text.encode())
    sug = engine.suggest_mappings("file", "qobuz")["suggestions"][f"playlist|{rec['id']}"]
    assert sug["liked"] and sug["target"] == {"kind": "liked"}
    plan = make({"service": "file", "kind": "playlist", "refs": [rec["id"]], "names": [rec["name"]]},
                {"service": "qobuz", "kind": "liked"})
    st = {i["source"]["title"]: i["status"] for i in plan["items"]}
    assert st == {"Mr. Brightside": "exists", "Blinding Lights": "add"}


def _exportify_zip():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("road_trip.csv", "Track Name,Artist Name(s)\nAfrica,TOTO\n")
        z.writestr("liked_songs.csv", "Track Name,Artist Name(s)\nMr. Brightside,The Killers\n")
    return buf.getvalue()


def test_dropping_same_zip_twice_replaces_instead_of_duplicating(fresh):
    from playlist_transfer import providers
    from playlist_transfer.providers.files import parse_upload

    first = parse_upload("export.zip", _exportify_zip())
    second = parse_upload("export.zip", _exportify_zip())
    colls = providers.get("file").collections()
    assert len(colls) == 2
    assert {c.id for c in colls} == {r["id"] for r in second}
    assert all(r["replaced"] == 1 for r in second) and all(r["replaced"] == 0 for r in first)


def test_remove_duplicate_imports_cleans_up_existing_copies(fresh):
    import json

    from playlist_transfer import providers
    from playlist_transfer.providers import files

    recs = files.parse_upload("export.zip", _exportify_zip())
    # simulate copies made by the old version (no batch / replace logic)
    for r in recs:
        for n in range(3):
            path = files._imports_dir() / f"{r['id']}.json"
            data = json.loads(path.read_text("utf-8"))
            data.update(id=f"{r['id']}copy{n}", batch=None)
            (files._imports_dir() / f"{data['id']}.json").write_text(json.dumps(data), "utf-8")
    assert len(providers.get("file").collections()) == 8
    assert files.remove_duplicate_imports() == 6
    assert len(providers.get("file").collections()) == 2
    assert files.remove_duplicate_imports() == 0


def test_review_follows_replaced_import_and_duplicate_reviews_are_removed(fresh):
    from playlist_transfer.providers.files import parse_upload

    recs = {r["name"]: r for r in parse_upload("export.zip", _exportify_zip())}
    src = {"service": "file", "kind": "playlist", "refs": [recs["road trip"]["id"]], "names": ["road trip"]}
    old = make(dict(src, refs=list(src["refs"])), {"service": "qobuz"})
    newer = make(dict(src, refs=list(src["refs"])), {"service": "qobuz"})
    applied = make(dict(src, refs=list(src["refs"])), {"service": "qobuz"})
    applied = engine.apply(applied["id"])
    assert engine.remove_duplicate_plans() == 2  # applied one kept, both older unapplied copies removed
    assert store.get_plan(applied["id"]) and not store.get_plan(old["id"]) and not store.get_plan(newer["id"])

    parse_upload("export.zip", _exportify_zip())  # replaces the import the review points at
    plan = engine.analyze(applied["id"])
    assert plan["status"] == "ready" and plan["source"]["refs"][0] != recs["road trip"]["id"]
