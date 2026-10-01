"""Spotify Web API provider (Authorization Code + PKCE, no client secret needed).

Targets the post-February-2026 Web API:
  * playlist contents live at /playlists/{id}/items and entries use `item`
  * library writes go through PUT/DELETE /me/library with URIs (max 40)
  * /search returns at most 10 results per page
  * playlist contents are only readable for playlists you own or collaborate on
Older field names are still accepted when reading, for Extended Quota apps.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from .. import store
from ..models import Album, Artist, PlaylistInfo, Track
from .base import HttpMixin, NotConnected, Provider, ProviderError, chunks

API = "https://api.spotify.com/v1"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
    "user-follow-read",
    "user-follow-modify",
]


class SpotifyProvider(HttpMixin, Provider):
    name = "spotify"
    label = "Spotify"

    def __init__(self) -> None:
        self._me: dict[str, Any] | None = None

    # ------------------------------------------------------------ auth
    @property
    def cfg(self) -> dict[str, Any]:
        return store.get_section("spotify")

    def begin_auth(self, client_id: str, redirect_uri: str) -> str:
        verifier = secrets.token_urlsafe(64)[:100]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        store.update_section(
            "spotify", client_id=client_id.strip(), redirect_uri=redirect_uri, pkce_verifier=verifier, pkce_state=state
        )
        return AUTH_URL + "?" + urlencode(
            {
                "client_id": client_id.strip(),
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": state,
                "scope": " ".join(SCOPES),
            }
        )

    def finish_auth(self, code: str, state: str) -> None:
        cfg = self.cfg
        if not state or state != cfg.get("pkce_state"):
            raise ProviderError("OAuth state mismatch, please try connecting again")
        r = self._send(
            "POST",
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": cfg["redirect_uri"],
                "client_id": cfg["client_id"],
                "code_verifier": cfg["pkce_verifier"],
            },
        )
        if r.status_code != 200:
            raise ProviderError(f"Spotify token exchange failed: {r.text}")
        self._store_token(r.json())
        store.update_section("spotify", pkce_verifier=None, pkce_state=None)
        self._me = None

    def _store_token(self, tok: dict[str, Any]) -> None:
        store.update_section(
            "spotify",
            access_token=tok["access_token"],
            refresh_token=tok.get("refresh_token") or self.cfg.get("refresh_token"),
            expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60,
        )

    def _token(self) -> str:
        cfg = self.cfg
        if not cfg.get("access_token"):
            raise NotConnected("Spotify is not connected")
        if time.time() < cfg.get("expires_at", 0):
            return cfg["access_token"]
        r = self._send(
            "POST",
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": cfg.get("refresh_token", ""),
                  "client_id": cfg.get("client_id", "")},
        )
        if r.status_code != 200:
            store.update_section("spotify", access_token=None)
            raise NotConnected(f"Spotify session expired, please reconnect ({r.text[:200]})")
        self._store_token(r.json())
        return self.cfg["access_token"]

    def disconnect(self) -> None:
        store.clear_section("spotify", keep=("client_id",))
        self._me = None

    # ------------------------------------------------------------ http
    def _req(self, method: str, path: str, **kw: Any) -> Any:
        url = path if path.startswith("http") else API + path
        headers = {"Authorization": f"Bearer {self._token()}"}
        r = self._send(method, url, headers=headers, **kw)
        if r.status_code == 401:
            store.update_section("spotify", expires_at=0)
            headers = {"Authorization": f"Bearer {self._token()}"}
            r = self._send(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            raise ProviderError(f"Spotify {method} {path.split('?')[0]} -> {r.status_code}: {msg}")
        if not r.content:
            return None
        return r.json()

    def _paged(self, path: str, key: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        url: str | None = path
        while url:
            data = self._req("GET", url)
            page = data[key] if key else data
            out.extend(page.get("items") or [])
            url = page.get("next")
        return out

    # ------------------------------------------------------------ account
    def is_connected(self) -> bool:
        return bool(self.cfg.get("access_token") or self.cfg.get("refresh_token"))

    def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = self._req("GET", "/me")
        return self._me

    def account(self) -> dict[str, Any]:
        cfg = self.cfg
        info: dict[str, Any] = {"connected": False, "client_id": cfg.get("client_id", "")}
        if not self.is_connected():
            return info
        try:
            me = self.me()
            info.update(connected=True, user=me.get("display_name") or me.get("id"), user_id=me.get("id"))
        except ProviderError as e:
            info["error"] = str(e)
        return info

    # ------------------------------------------------------------ parsing
    @staticmethod
    def parse_track(t: dict[str, Any]) -> Track:
        album = t.get("album") or {}
        uri = t.get("uri", "")
        return Track(
            id=t.get("id") or uri,  # local files have no id
            title=t.get("name", ""),
            artists=[a.get("name", "") for a in t.get("artists") or [] if a.get("name")],
            album=album.get("name", ""),
            duration_ms=t.get("duration_ms") or 0,
            isrc=(t.get("external_ids") or {}).get("isrc", ""),
            explicit=bool(t.get("explicit")),
            available=t.get("is_playable", True) is not False and not t.get("is_local"),
            url=(t.get("external_urls") or {}).get("spotify", ""),
            entry_id=uri,
        )

    @staticmethod
    def parse_album(a: dict[str, Any]) -> Album:
        return Album(
            id=a["id"],
            title=a.get("name", ""),
            artists=[x.get("name", "") for x in a.get("artists") or []],
            upc=(a.get("external_ids") or {}).get("upc", ""),
            track_count=a.get("total_tracks") or 0,
            year=(a.get("release_date") or "")[:4],
            url=(a.get("external_urls") or {}).get("spotify", ""),
        )

    @staticmethod
    def parse_artist(a: dict[str, Any]) -> Artist:
        return Artist(id=a["id"], name=a.get("name", ""), url=(a.get("external_urls") or {}).get("spotify", ""))

    # ------------------------------------------------------------ reading
    def list_playlists(self) -> list[PlaylistInfo]:
        me_id = self.me().get("id")
        out = []
        for p in self._paged("/me/playlists?limit=50"):
            if not p:
                continue
            owner = p.get("owner") or {}
            mine = owner.get("id") == me_id or bool(p.get("collaborative"))
            counter = p.get("items") if isinstance(p.get("items"), dict) else p.get("tracks") or {}
            images = p.get("images") or []
            out.append(
                PlaylistInfo(
                    id=p["id"], name=p.get("name", ""), description=p.get("description") or "",
                    track_count=(counter or {}).get("total", 0), owner=owner.get("display_name") or owner.get("id", ""),
                    editable=mine, readable=mine, public=bool(p.get("public")),
                    url=(p.get("external_urls") or {}).get("spotify", ""),
                    image=images[-1]["url"] if images else "",
                )
            )
        return out

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        out = []
        for entry in self._paged(f"/playlists/{playlist_id}/items?limit=50&additional_types=track"):
            t = entry.get("item") or entry.get("track")
            if not t or t.get("type", "track") != "track":
                continue
            out.append(self.parse_track(t))
        return out

    def liked_tracks(self) -> list[Track]:
        out = []
        for entry in self._paged("/me/tracks?limit=50"):
            t = entry.get("track") or entry.get("item")
            if t:
                out.append(self.parse_track(t))
        return out

    def saved_albums(self) -> list[Album]:
        return [self.parse_album(e["album"]) for e in self._paged("/me/albums?limit=50") if e.get("album")]

    def followed_artists(self) -> list[Artist]:
        return [self.parse_artist(a) for a in self._paged("/me/following?type=artist&limit=50", key="artists")]

    # ------------------------------------------------------------ searching
    def _search(self, q: str, typ: str) -> list[dict[str, Any]]:
        data = self._req("GET", "/search?" + urlencode({"q": q, "type": typ, "limit": 10}))
        return [x for x in (data.get(typ + "s") or {}).get("items") or [] if x]

    def search_tracks_by_isrc(self, isrc: str) -> list[Track]:
        return [self.parse_track(t) for t in self._search(f"isrc:{isrc}", "track")]

    def search_tracks(self, query: str) -> list[Track]:
        return [self.parse_track(t) for t in self._search(query, "track")]

    def search_albums_by_upc(self, upc: str) -> list[Album]:
        return [self.parse_album(a) for a in self._search(f"upc:{upc}", "album")]

    def search_albums(self, query: str) -> list[Album]:
        return [self.parse_album(a) for a in self._search(query, "album")]

    def search_artists(self, query: str) -> list[Artist]:
        return [self.parse_artist(a) for a in self._search(query, "artist")]

    # ------------------------------------------------------------ writing
    def create_playlist(self, name: str, description: str = "", public: bool = False) -> PlaylistInfo:
        p = self._req("POST", "/me/playlists", json={"name": name, "description": description, "public": public})
        return PlaylistInfo(id=p["id"], name=p["name"], editable=True, public=public,
                            url=(p.get("external_urls") or {}).get("spotify", ""))

    def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        for batch in chunks(track_ids, 100):
            self._req("POST", f"/playlists/{playlist_id}/items", json={"uris": [f"spotify:track:{i}" for i in batch]})

    def remove_from_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        uris = [t.entry_id or f"spotify:track:{t.id}" for t in tracks]
        for batch in chunks(uris, 100):
            self._req("DELETE", f"/playlists/{playlist_id}/items", json={"items": [{"uri": u} for u in batch]})

    def _library(self, method: str, typ: str, ids: list[str]) -> None:
        legacy = {"track": "/me/tracks", "album": "/me/albums", "artist": "/me/following?type=artist"}[typ]
        for batch in chunks(ids, 40):
            uris = ",".join(f"spotify:{typ}:{i}" for i in batch)
            try:
                self._req(method, "/me/library?" + urlencode({"uris": uris}))
            except ProviderError:
                # Extended-quota apps may still be on the legacy endpoints.
                sep = "&" if "?" in legacy else "?"
                self._req(method, f"{legacy}{sep}ids={','.join(batch[:20])}")
                if len(batch) > 20:
                    self._req(method, f"{legacy}{sep}ids={','.join(batch[20:])}")

    def like_tracks(self, ids: list[str]) -> None:
        self._library("PUT", "track", ids)

    def save_albums(self, ids: list[str]) -> None:
        self._library("PUT", "album", ids)

    def follow_artists(self, ids: list[str]) -> None:
        self._library("PUT", "artist", ids)

    def unlike_tracks(self, ids: list[str]) -> None:
        self._library("DELETE", "track", ids)

    def unsave_albums(self, ids: list[str]) -> None:
        self._library("DELETE", "album", ids)

    def unfollow_artists(self, ids: list[str]) -> None:
        self._library("DELETE", "artist", ids)
