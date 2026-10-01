"""In-memory fake services so the UI can be tried (and tested) without accounts.

Enable with PLT_DEMO=1. Data deliberately contains the hard cases: remaster
suffixes, Qobuz `version` fields, missing ISRCs, featuring credits, accents,
in-source duplicates, tracks already in the target, live-only alternatives,
and a track that doesn't exist on the target at all.
"""
from __future__ import annotations

import copy
import itertools
from typing import Any

from ..matching import norm
from ..models import Album, Artist, PlaylistInfo, Track
from .base import Provider, ProviderError


class InMemoryProvider(Provider):
    def __init__(self, name: str, label: str, tracks: list[Track], albums: list[Album], artists: list[Artist],
                 playlists: list[tuple[PlaylistInfo, list[str]]], liked: list[str], saved_albums: list[str],
                 followed: list[str]) -> None:
        self.name, self.label = name, label
        self.tracks = {t.id: t for t in tracks}
        self.albums = {a.id: a for a in albums}
        self.artists = {a.id: a for a in artists}
        self.playlists = {p.id: (p, list(ids)) for p, ids in playlists}
        self.liked, self.saved, self.followed = list(liked), list(saved_albums), list(followed)
        self._seq = itertools.count(1)
        self._entry = itertools.count(1000)
        self.calls: list[tuple[str, Any]] = []

    def is_connected(self) -> bool:
        return True

    def account(self) -> dict[str, Any]:
        return {"connected": True, "user": f"demo-{self.name}", "demo": True}

    def list_playlists(self) -> list[PlaylistInfo]:
        out = []
        for p, ids in self.playlists.values():
            q = copy.copy(p)
            q.track_count = len(ids)
            out.append(q)
        return out

    def _entries(self, ids: list[str]) -> list[Track]:
        out = []
        for i, tid in enumerate(ids):
            t = copy.copy(self.tracks[tid])
            t.entry_id = f"{tid}#{i}"
            out.append(t)
        return out

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        p, ids = self.playlists[playlist_id]
        if not p.readable:
            raise ProviderError(f"{self.label}: playlist '{p.name}' can't be read (not owned by you)")
        return self._entries(ids)

    def liked_tracks(self) -> list[Track]:
        return [copy.copy(self.tracks[i]) for i in self.liked]

    def saved_albums(self) -> list[Album]:
        return [self.albums[i] for i in self.saved]

    def followed_artists(self) -> list[Artist]:
        return [self.artists[i] for i in self.followed]

    @staticmethod
    def _hit(query: str, *fields: str) -> bool:
        q = set(norm(query).split())
        hay = set(norm(" ".join(fields)).split())
        return bool(q) and len(q & hay) >= max(1, int(len(q) * 0.6))

    def search_tracks_by_isrc(self, isrc: str) -> list[Track]:
        return [t for t in self.tracks.values() if t.isrc and t.isrc == isrc]

    def search_tracks(self, query: str) -> list[Track]:
        return [t for t in self.tracks.values() if self._hit(query, t.full_title, t.artist, t.album)][:10]

    def search_albums_by_upc(self, upc: str) -> list[Album]:
        return [a for a in self.albums.values() if a.upc and a.upc.lstrip("0") == upc.lstrip("0")]

    def search_albums(self, query: str) -> list[Album]:
        return [a for a in self.albums.values() if self._hit(query, a.title, a.artist)][:10]

    def search_artists(self, query: str) -> list[Artist]:
        return [a for a in self.artists.values() if self._hit(query, a.name)][:10]

    def create_playlist(self, name: str, description: str = "", public: bool = False) -> PlaylistInfo:
        pid = f"{self.name}-new-{next(self._seq)}"
        p = PlaylistInfo(id=pid, name=name, description=description, editable=True, public=public)
        self.playlists[pid] = (p, [])
        self.calls.append(("create_playlist", name))
        return p

    def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        self.calls.append(("add", (playlist_id, list(track_ids))))
        self.playlists[playlist_id][1].extend(track_ids)

    def remove_from_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        self.calls.append(("remove", (playlist_id, [t.id for t in tracks])))
        drop = {t.entry_id for t in tracks}
        ids = self.playlists[playlist_id][1]
        self.playlists[playlist_id] = (self.playlists[playlist_id][0],
                                       [tid for i, tid in enumerate(ids) if f"{tid}#{i}" not in drop])

    def like_tracks(self, ids: list[str]) -> None:
        self.liked.extend(i for i in ids if i not in self.liked)

    def save_albums(self, ids: list[str]) -> None:
        self.saved.extend(i for i in ids if i not in self.saved)

    def follow_artists(self, ids: list[str]) -> None:
        self.followed.extend(i for i in ids if i not in self.followed)

    def unlike_tracks(self, ids: list[str]) -> None:
        self.liked = [i for i in self.liked if i not in ids]

    def unsave_albums(self, ids: list[str]) -> None:
        self.saved = [i for i in self.saved if i not in ids]

    def unfollow_artists(self, ids: list[str]) -> None:
        self.followed = [i for i in self.followed if i not in ids]


def _t(i, title, artists, album, secs, isrc="", version="", hires=False, available=True):
    return Track(id=i, title=title, artists=artists, album=album, duration_ms=secs * 1000, isrc=isrc,
                 version=version, hires=hires, available=available)


def demo_providers() -> dict[str, Provider]:
    sp_tracks = [
        _t("sp1", "Bohemian Rhapsody - Remastered 2011", ["Queen"], "A Night At The Opera (2011 Remaster)", 354, "GBUM71029604"),
        _t("sp2", "Smells Like Teen Spirit", ["Nirvana"], "Nevermind (Remastered)", 301, "USGF19942501"),
        _t("sp3", "Get Lucky (feat. Pharrell Williams & Nile Rodgers)", ["Daft Punk", "Pharrell Williams", "Nile Rodgers"], "Random Access Memories", 369, "USQX91300108"),
        _t("sp4", "Hey Ya!", ["OutKast"], "Speakerboxxx/The Love Below", 235, "USAR10300984"),
        _t("sp5", "Dancing With Myself", ["Billy Idol"], "Billy Idol", 199, "USCH38100046"),
        _t("sp6", "Hallelujah", ["Jeff Buckley"], "Grace", 413, "USSM19400397"),
        _t("sp7", "Some Obscure Demo", ["Unknown Garage Band"], "Basement Tapes", 187),
        _t("sp8", "Midnight City", ["M83"], "Hurry Up, We're Dreaming", 244, "FR6V81141061"),
        _t("sp9", "Jóga", ["Björk"], "Homogenic", 305, "GBAAN9700012"),
        _t("sp10", "Mr. Brightside", ["The Killers"], "Hot Fuss", 222, "USIR20400274"),
        _t("sp11", "Blinding Lights", ["The Weeknd"], "After Hours", 200, "USUG11904206"),
        _t("sp12", "Take On Me", ["a-ha"], "Hunting High and Low", 225, "GBAYE8500009"),
        _t("sp13", "Africa", ["TOTO"], "Toto IV", 295, "USSM19801546"),
        _t("sp14", "Heroes - 2017 Remaster", ["David Bowie"], "\"Heroes\" (2017 Remaster)", 371, "USJT11700001"),
        _t("sp15", "Wonderwall", ["Oasis"], "(What's The Story) Morning Glory?", 258, "GBBQY9500001"),
    ]
    sp_albums = [
        Album("spa1", "Nevermind (Remastered)", ["Nirvana"], "0720642442524", 13, "1991"),
        Album("spa2", "Hot Fuss", ["The Killers"], "602498620355", 11, "2004"),
        Album("spa3", "Random Access Memories", ["Daft Punk"], "886443927087", 13, "2013"),
        Album("spa4", "Basement Tapes", ["Unknown Garage Band"], "", 6, "2019"),
    ]
    sp_artists = [Artist("spr1", "Queen"), Artist("spr2", "Nirvana"), Artist("spr3", "Daft Punk"),
                  Artist("spr4", "Unknown Garage Band"), Artist("spr5", "The Killers")]
    spotify = InMemoryProvider(
        "spotify", "Spotify (demo)", sp_tracks, sp_albums, sp_artists,
        playlists=[
            (PlaylistInfo("spp1", "Road Trip", owner="you", editable=True),
             ["sp1", "sp2", "sp3", "sp4", "sp5", "sp10", "sp11", "sp2", "sp12", "sp13", "sp7"]),
            (PlaylistInfo("spp2", "Chill Evenings", owner="you", editable=True), ["sp8", "sp9", "sp6", "sp14", "sp11"]),
            (PlaylistInfo("spp3", "Discover Weekly", owner="Spotify", editable=False, readable=False), ["sp15"]),
        ],
        liked=["sp1", "sp2", "sp10", "sp11", "sp12"], saved_albums=["spa1", "spa2", "spa3", "spa4"],
        followed=["spr1", "spr2", "spr3", "spr4", "spr5"],
    )

    qb_tracks = [
        _t("q1", "Bohemian Rhapsody", ["Queen"], "A Night At The Opera", 355, "GBUM71029604", "Remastered 2011", hires=True),
        _t("q1b", "Bohemian Rhapsody", ["Queen"], "Live Aid", 360, "GBUM70000001", "Live Aid"),
        _t("q2", "Smells Like Teen Spirit", ["Nirvana"], "Nevermind", 301, "USGF19942501", hires=True),
        _t("q3", "Get Lucky", ["Daft Punk", "Pharrell Williams", "Nile Rodgers"], "Random Access Memories", 368, hires=True),
        _t("q3b", "Get Lucky", ["Daft Punk", "Pharrell Williams"], "Get Lucky", 248, version="Radio Edit"),
        _t("q4", "Hey Ya!", ["Outkast"], "Speakerboxxx / The Love Below", 235, "USAR10300984"),
        _t("q5", "Dancing with Myself", ["Billy Idol"], "Billy Idol", 200, "USCH38100099"),
        _t("q6", "Hallelujah", ["Jeff Buckley"], "Live at Sin-é", 425, "USSM19300001", "Live"),
        _t("q6b", "Hallelujah", ["Leonard Cohen"], "Various Positions", 279, "USSM18400001"),
        _t("q8", "Midnight City", ["M83"], "Hurry Up, We're Dreaming", 243, "FR6V81141061", hires=True),
        _t("q9", "Joga", ["Bjork"], "Homogenic", 305),
        _t("q10", "Mr. Brightside", ["The Killers"], "Hot Fuss", 222, "USIR20400274"),
        _t("q11", "Blinding Lights", ["The Weeknd"], "After Hours", 200, "USUG11904206", hires=True),
        _t("q12", "Take On Me", ["a-ha"], "Hunting High and Low", 226, "GBAYE8500009"),
        _t("q13", "Africa", ["Toto"], "Toto IV", 295, "USSM19801546"),
        _t("q14", "\"Heroes\"", ["David Bowie"], "\"Heroes\"", 372, "USJT11700099", "2017 Remaster", hires=True),
        _t("q15", "Wonderwall", ["Oasis"], "(What's The Story) Morning Glory?", 258, "GBBQY9500001", "Remastered"),
        _t("q16", "So What", ["Miles Davis"], "Kind of Blue", 562, "USSM15900113", hires=True),
    ]
    qb_albums = [
        Album("qa1", "Nevermind", ["Nirvana"], "720642442524", 13, "1991", hires=True),
        Album("qa2", "Hot Fuss", ["The Killers"], "0602498620355", 11, "2004"),
        Album("qa3", "Random Access Memories", ["Daft Punk"], "", 13, "2013", hires=True),
        Album("qa4", "Kind of Blue", ["Miles Davis"], "", 5, "1959"),
    ]
    qb_artists = [Artist("qr1", "Queen"), Artist("qr2", "Nirvana"), Artist("qr3", "Daft Punk"),
                  Artist("qr5", "The Killers"), Artist("qr6", "Miles Davis")]
    qobuz = InMemoryProvider(
        "qobuz", "Qobuz (demo)", qb_tracks, qb_albums, qb_artists,
        playlists=[
            (PlaylistInfo("qp1", "Road Trip", owner="you", editable=True), ["q2", "q10", "q15"]),
            (PlaylistInfo("qp2", "Jazz Classics", owner="you", editable=True), ["q16"]),
            # an earlier transfer that kept a suffix on the name, and missed some songs
            (PlaylistInfo("qp3", "Chill Evenings (Spotify)", owner="you", editable=True), ["q8", "q9", "q16"]),
        ],
        liked=["q10"], saved_albums=["qa1"], followed=["qr1"],
    )
    return {"spotify": spotify, "qobuz": qobuz}
