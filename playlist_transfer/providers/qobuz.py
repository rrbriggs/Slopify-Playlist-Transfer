"""Qobuz provider using the (unofficial) JSON API used by play.qobuz.com.

Only the public app_id is needed for library/playlist management. It is scraped
from the web player's bundle (the same approach used by streamrip / qobuz-dl)
or can be entered manually. Authentication is either:
  * user_id + user_auth_token copied from the web player (recommended: email/
    password login is behind a captcha for many accounts), or
  * email + password (tried as a convenience; we only keep the token).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .. import store
from ..models import Album, Artist, PlaylistInfo, Track
from .base import HttpMixin, NotConnected, Provider, ProviderError, chunks

API = "https://www.qobuz.com/api.json/0.2/"
PLAY = "https://play.qobuz.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def fetch_app_id(client) -> str:
    login = client.get(f"{PLAY}/login", headers={"User-Agent": UA}, follow_redirects=True)
    m = re.search(r'<script src="(/resources/[^"]+/bundle\.js)"', login.text)
    if not m:
        raise ProviderError("Could not locate the Qobuz web player bundle; enter the app_id manually")
    bundle = client.get(PLAY + m.group(1), headers={"User-Agent": UA}).text
    m = re.search(r'production:\{api:\{appId:"(\d{9})"', bundle) or re.search(r'appId:"(\d{9})"', bundle)
    if not m:
        raise ProviderError("Could not find app_id in the Qobuz bundle; enter it manually")
    return m.group(1)


def _parse_performers(s: str) -> list[str]:
    """'A, MainArtist - B, FeaturedArtist, Composer - C, Producer' -> ['A', 'B']"""
    names = []
    for part in (s or "").split(" - "):
        bits = [b.strip() for b in part.split(",")]
        if len(bits) >= 2 and any(r.lower() in ("mainartist", "main artist", "featuredartist", "featured artist")
                                  for r in bits[1:]):
            if bits[0] and bits[0] not in names:
                names.append(bits[0])
    return names


class QobuzProvider(HttpMixin, Provider):
    name = "qobuz"
    label = "Qobuz"

    @property
    def cfg(self) -> dict[str, Any]:
        return store.get_section("qobuz")

    # ------------------------------------------------------------ auth
    def ensure_app_id(self, app_id: str | None = None) -> str:
        if app_id:
            store.update_section("qobuz", app_id=app_id.strip())
            return app_id.strip()
        if self.cfg.get("app_id"):
            return self.cfg["app_id"]
        aid = fetch_app_id(self.client)
        store.update_section("qobuz", app_id=aid)
        return aid

    def _login(self, params: dict[str, Any], app_id: str) -> dict[str, Any]:
        r = self._send("GET", API + "user/login", params={**params, "app_id": app_id},
                       headers={"X-App-Id": app_id, "User-Agent": UA})
        if r.status_code != 200:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            raise ProviderError(f"Qobuz login failed ({r.status_code}): {msg}")
        return r.json()

    def connect_password(self, email: str, password: str, app_id: str | None = None) -> None:
        aid = self.ensure_app_id(app_id)
        pw = hashlib.md5(password.encode()).hexdigest()
        data = self._login({"email": email, "password": pw}, aid)
        self._save_login(data)

    def connect_token(self, user_id: str, token: str, app_id: str | None = None) -> None:
        aid = self.ensure_app_id(app_id)
        try:
            data = self._login({"user_id": user_id.strip(), "user_auth_token": token.strip()}, aid)
            if "user_auth_token" not in data:
                data["user_auth_token"] = token.strip()
        except ProviderError:
            # Fall back to validating the token directly against an authenticated endpoint.
            store.update_section("qobuz", user_id=user_id.strip(), user_auth_token=token.strip())
            self._req("GET", "favorite/getUserFavorites", params={"type": "tracks", "limit": 1})
            data = {"user_auth_token": token.strip(), "user": {"id": user_id.strip()}}
        self._save_login(data)

    def _save_login(self, data: dict[str, Any]) -> None:
        user = data.get("user") or {}
        store.update_section(
            "qobuz",
            user_auth_token=data["user_auth_token"],
            user_id=str(user.get("id", "")),
            user_name=user.get("display_name") or user.get("login") or user.get("email") or str(user.get("id", "")),
            subscription=((user.get("credential") or {}).get("label") or ""),
        )

    def disconnect(self) -> None:
        store.clear_section("qobuz", keep=("app_id",))

    def is_connected(self) -> bool:
        return bool(self.cfg.get("user_auth_token") and self.cfg.get("app_id"))

    def account(self) -> dict[str, Any]:
        cfg = self.cfg
        return {
            "connected": self.is_connected(),
            "user": cfg.get("user_name", ""),
            "user_id": cfg.get("user_id", ""),
            "app_id": cfg.get("app_id", ""),
            "subscription": cfg.get("subscription", ""),
        }

    # ------------------------------------------------------------ http
    def _req(self, method: str, endpoint: str, params: dict[str, Any] | None = None,
             data: dict[str, Any] | None = None) -> Any:
        cfg = self.cfg
        if not cfg.get("user_auth_token"):
            raise NotConnected("Qobuz is not connected")
        headers = {"X-App-Id": cfg["app_id"], "X-User-Auth-Token": cfg["user_auth_token"], "User-Agent": UA}
        params = {**(params or {}), "app_id": cfg["app_id"]}
        r = self._send(method, API + endpoint, params=params, data=data, headers=headers)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            if r.status_code == 401:
                raise NotConnected(f"Qobuz session rejected ({msg}); please reconnect")
            raise ProviderError(f"Qobuz {endpoint} -> {r.status_code}: {msg}")
        return r.json() if r.content else None

    def _paged(self, endpoint: str, key: str, params: dict[str, Any], page: int = 500) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._req("GET", endpoint, params={**params, "limit": page, "offset": offset})
            block = data.get(key) or {}
            items = block.get("items") or []
            out.extend(items)
            total = block.get("total", 0)
            offset += len(items)
            if not items or offset >= total:
                return out

    # ------------------------------------------------------------ parsing
    @staticmethod
    def parse_track(t: dict[str, Any]) -> Track:
        album = t.get("album") or {}
        artists = _parse_performers(t.get("performers", ""))
        main = (t.get("performer") or {}).get("name") or (album.get("artist") or {}).get("name") or ""
        if main and main not in artists:
            artists.insert(0, main)
        elif main:
            artists.remove(main)
            artists.insert(0, main)
        return Track(
            id=str(t["id"]),
            title=(t.get("title") or "").strip(),
            version=(t.get("version") or "").strip(),
            artists=artists,
            album=album.get("title", ""),
            duration_ms=int(t.get("duration") or 0) * 1000,
            isrc=t.get("isrc") or "",
            explicit=bool(t.get("parental_warning")),
            available=t.get("streamable", True) is not False,
            hires=bool(t.get("hires_streamable") or t.get("hires")),
            url=f"https://open.qobuz.com/track/{t['id']}",
            entry_id=str(t.get("playlist_track_id") or ""),
        )

    @staticmethod
    def parse_album(a: dict[str, Any]) -> Album:
        title = a.get("title", "")
        if a.get("version") and a["version"].lower() not in title.lower():
            title = f"{title} ({a['version']})"
        return Album(
            id=str(a["id"]),
            title=title,
            artists=[(a.get("artist") or {}).get("name", "")],
            upc=a.get("upc") or "",
            track_count=a.get("tracks_count") or 0,
            year=(a.get("release_date_original") or "")[:4],
            available=a.get("streamable", True) is not False,
            hires=bool(a.get("hires_streamable") or a.get("hires")),
            url=f"https://open.qobuz.com/album/{a['id']}",
        )

    @staticmethod
    def parse_artist(a: dict[str, Any]) -> Artist:
        return Artist(id=str(a["id"]), name=a.get("name", ""), url=f"https://open.qobuz.com/artist/{a['id']}")

    # ------------------------------------------------------------ reading
    def list_playlists(self) -> list[PlaylistInfo]:
        uid = str(self.cfg.get("user_id", ""))
        out = []
        for p in self._paged("playlist/getUserPlaylists", "playlists", {}):
            owner = p.get("owner") or {}
            mine = str(owner.get("id", "")) == uid or bool(p.get("is_collaborative"))
            imgs = p.get("images300") or p.get("images") or []
            out.append(
                PlaylistInfo(
                    id=str(p["id"]), name=p.get("name", ""), description=p.get("description") or "",
                    track_count=p.get("tracks_count") or 0, owner=owner.get("name", ""), editable=mine,
                    readable=True, public=bool(p.get("is_public")), url=f"https://open.qobuz.com/playlist/{p['id']}",
                    image=imgs[0] if imgs else "",
                )
            )
        return out

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        items = self._paged("playlist/get", "tracks", {"playlist_id": playlist_id, "extra": "tracks"})
        return [self.parse_track(t) for t in items]

    def liked_tracks(self) -> list[Track]:
        return [self.parse_track(t) for t in self._paged("favorite/getUserFavorites", "tracks", {"type": "tracks"})]

    def saved_albums(self) -> list[Album]:
        return [self.parse_album(a) for a in self._paged("favorite/getUserFavorites", "albums", {"type": "albums"})]

    def followed_artists(self) -> list[Artist]:
        return [self.parse_artist(a) for a in self._paged("favorite/getUserFavorites", "artists", {"type": "artists"})]

    # ------------------------------------------------------------ searching
    def _search(self, kind: str, query: str) -> list[dict[str, Any]]:
        data = self._req("GET", f"{kind}/search", params={"query": query, "limit": 10})
        return (data.get(kind + "s") or {}).get("items") or []

    def search_tracks_by_isrc(self, isrc: str) -> list[Track]:
        return [self.parse_track(t) for t in self._search("track", isrc)]

    def search_tracks(self, query: str) -> list[Track]:
        return [self.parse_track(t) for t in self._search("track", query)]

    def search_albums_by_upc(self, upc: str) -> list[Album]:
        return [self.parse_album(a) for a in self._search("album", upc)]

    def search_albums(self, query: str) -> list[Album]:
        return [self.parse_album(a) for a in self._search("album", query)]

    def search_artists(self, query: str) -> list[Artist]:
        return [self.parse_artist(a) for a in self._search("artist", query)]

    # ------------------------------------------------------------ writing
    def create_playlist(self, name: str, description: str = "", public: bool = False) -> PlaylistInfo:
        p = self._req("POST", "playlist/create", data={
            "name": name, "description": description, "is_public": "true" if public else "false",
            "is_collaborative": "false",
        })
        return PlaylistInfo(id=str(p["id"]), name=p.get("name", name), editable=True, public=public,
                            url=f"https://open.qobuz.com/playlist/{p['id']}")

    def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        for batch in chunks(track_ids, 50):
            self._req("POST", "playlist/addTracks", data={
                "playlist_id": playlist_id, "track_ids": ",".join(batch), "no_duplicate": "true",
            })

    def remove_from_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        ids = [t.entry_id for t in tracks if t.entry_id]
        for batch in chunks(ids, 50):
            self._req("POST", "playlist/deleteTracks", data={
                "playlist_id": playlist_id, "playlist_track_ids": ",".join(batch),
            })

    def _fav(self, action: str, field: str, ids: list[str]) -> None:
        for batch in chunks(ids, 50):
            self._req("POST", f"favorite/{action}", data={field: ",".join(batch)})

    def like_tracks(self, ids: list[str]) -> None:
        self._fav("create", "track_ids", ids)

    def save_albums(self, ids: list[str]) -> None:
        self._fav("create", "album_ids", ids)

    def follow_artists(self, ids: list[str]) -> None:
        self._fav("create", "artist_ids", ids)

    def unlike_tracks(self, ids: list[str]) -> None:
        self._fav("delete", "track_ids", ids)

    def unsave_albums(self, ids: list[str]) -> None:
        self._fav("delete", "album_ids", ids)

    def unfollow_artists(self, ids: list[str]) -> None:
        self._fav("delete", "artist_ids", ids)
