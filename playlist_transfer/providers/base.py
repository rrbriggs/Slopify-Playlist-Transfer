"""Provider interface. Every music service implements this."""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

import httpx

from ..models import Album, Artist, Collection, PlaylistInfo, Track

log = logging.getLogger(__name__)


class ProviderError(Exception):
    pass


class NotConnected(ProviderError):
    pass


class Provider:
    name = "base"
    label = "Base"
    can_write = True
    # collection kinds this provider can read / write
    read_kinds: tuple[str, ...] = ("playlist", "liked", "albums", "artists")
    write_kinds: tuple[str, ...] = ("playlist", "liked", "albums", "artists")

    # ---- account
    def is_connected(self) -> bool:
        raise NotImplementedError

    def account(self) -> dict[str, Any]:
        """Return {'connected': bool, 'user': str, ...} without raising."""
        raise NotImplementedError

    # ---- reading
    def list_playlists(self) -> list[PlaylistInfo]:
        raise NotImplementedError

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        raise NotImplementedError

    def liked_tracks(self) -> list[Track]:
        raise NotImplementedError

    def saved_albums(self) -> list[Album]:
        raise NotImplementedError

    def followed_artists(self) -> list[Artist]:
        raise NotImplementedError

    def collections(self) -> list[Collection]:
        out = [
            Collection(kind="liked", id="liked", name="Liked / favorite tracks"),
            Collection(kind="albums", id="albums", name="Saved / favorite albums"),
            Collection(kind="artists", id="artists", name="Followed / favorite artists"),
        ]
        for p in self.list_playlists():
            out.append(
                Collection(
                    kind="playlist", id=p.id, name=p.name, count=p.track_count, readable=p.readable,
                    editable=p.editable, owner=p.owner, image=p.image,
                    note="" if p.readable else "Not readable: this service only exposes playlists you own",
                )
            )
        return out

    def read_items(self, kind: str, ref: str) -> list[Any]:
        if kind == "playlist":
            return self.playlist_tracks(ref)
        if kind == "liked":
            return self.liked_tracks()
        if kind == "albums":
            return self.saved_albums()
        if kind == "artists":
            return self.followed_artists()
        raise ProviderError(f"Unknown collection kind {kind}")

    # ---- searching (return candidates; scoring is done by the matcher)
    def search_tracks_by_isrc(self, isrc: str) -> list[Track]:
        return []

    def search_tracks(self, query: str) -> list[Track]:
        raise NotImplementedError

    def search_albums_by_upc(self, upc: str) -> list[Album]:
        return []

    def search_albums(self, query: str) -> list[Album]:
        raise NotImplementedError

    def search_artists(self, query: str) -> list[Artist]:
        raise NotImplementedError

    # ---- writing
    def create_playlist(self, name: str, description: str = "", public: bool = False) -> PlaylistInfo:
        raise NotImplementedError

    def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        raise NotImplementedError

    def remove_from_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        raise NotImplementedError

    def like_tracks(self, ids: list[str]) -> None:
        raise NotImplementedError

    def save_albums(self, ids: list[str]) -> None:
        raise NotImplementedError

    def follow_artists(self, ids: list[str]) -> None:
        raise NotImplementedError

    def unlike_tracks(self, ids: list[str]) -> None:
        raise NotImplementedError

    def unsave_albums(self, ids: list[str]) -> None:
        raise NotImplementedError

    def unfollow_artists(self, ids: list[str]) -> None:
        raise NotImplementedError

    def add_items(self, kind: str, target_ref: str, ids: list[str]) -> None:
        if kind == "playlist":
            self.add_to_playlist(target_ref, ids)
        elif kind == "liked":
            self.like_tracks(ids)
        elif kind == "albums":
            self.save_albums(ids)
        elif kind == "artists":
            self.follow_artists(ids)
        else:
            raise ProviderError(f"Unknown kind {kind}")

    def remove_items(self, kind: str, target_ref: str, items: list[Any]) -> None:
        if kind == "playlist":
            self.remove_from_playlist(target_ref, items)
        elif kind == "liked":
            self.unlike_tracks([i.id for i in items])
        elif kind == "albums":
            self.unsave_albums([i.id for i in items])
        elif kind == "artists":
            self.unfollow_artists([i.id for i in items])


def chunks(seq: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


class HttpMixin:
    """httpx client with retry on 429 / 5xx."""

    _client: httpx.Client | None = None
    max_retries = 5

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30, headers={"User-Agent": "playlist-transfer/1.0"})
        return self._client

    def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self.client.request(method, url, **kw)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise ProviderError(f"Network error: {e}") from e
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.max_retries:
                    return r
                wait = float(r.headers.get("Retry-After") or delay)
                log.info("%s %s -> %s, retrying in %.1fs", method, url, r.status_code, wait)
                time.sleep(min(wait, 60))
                delay *= 2
                continue
            return r
        raise ProviderError("unreachable")
