"""Import (as a transfer source) and export of track lists as CSV / JSON / TXT.

Import understands common column names (Exportify / Soundiiz / TuneMyMusic exports),
Spotify's "Download your data" JSON (Playlist*.json, YourLibrary.json), zips of any of
those, and plain text with one "Artist - Title" per line.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

from .. import store
from ..matching import norm
from ..models import Album, Artist, Collection, Track
from .base import Provider, ProviderError

ALIASES = {
    "title": ["title", "track name", "track", "name", "song", "song name", "track title"],
    "artists": ["artist", "artists", "artist name(s)", "artist name", "artist(s)", "performer"],
    "album": ["album", "album name", "album title", "release"],
    "isrc": ["isrc"],
    "duration_ms": ["duration (ms)", "duration_ms", "duration ms", "track duration (ms)"],
    "duration": ["duration", "length", "time"],
    "upc": ["upc", "ean", "barcode"],
    "uri": ["track uri", "spotify uri", "uri"],
}


def _imports_dir() -> Path:
    d = store.DATA_DIR / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_duration(v: str) -> int:
    v = (v or "").strip()
    if not v:
        return 0
    if ":" in v:
        parts = [int(p) for p in v.split(":") if p.isdigit()]
        secs = 0
        for p in parts:
            secs = secs * 60 + p
        return secs * 1000
    try:
        f = float(v)
    except ValueError:
        return 0
    return int(f if f > 10000 else f * 1000)


def _split_artists(v: str) -> list[str]:
    parts = re.split(r"\s*[;,]\s*", v or "") if (";" in (v or "") or "," in (v or "")) else [v or ""]
    return [p.strip() for p in parts if p.strip()]


def _uri_id(uri: str) -> str:
    """'spotify:track:abc' -> 'abc' (keeps Spotify IDs so the match cache and dedupe work)."""
    parts = (uri or "").split(":")
    return parts[2] if len(parts) == 3 and parts[0] == "spotify" else ""


def _tracks_from_csv(text: str) -> list[Track] | None:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not any(sep in lines[0] for sep in (",", ";", "\t")):
        return None
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    cols = {(c or "").strip().lower(): c for c in reader.fieldnames or []}
    colmap = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in cols:
                colmap[field] = cols[n]
                break
    if "title" not in colmap:
        return None
    tracks = []
    for i, row in enumerate(reader):
        get = lambda f: (row.get(colmap[f]) or "").strip() if f in colmap else ""  # noqa: E731
        if not get("title"):
            continue
        dur = int(get("duration_ms")) if get("duration_ms").isdigit() else _parse_duration(get("duration"))
        tracks.append(Track(id=_uri_id(get("uri")) or f"row{i}", title=get("title"),
                            artists=_split_artists(get("artists")), album=get("album"), isrc=get("isrc"),
                            duration_ms=dur, entry_id=get("uri")))
    return tracks


def _tracks_from_text(text: str) -> list[Track]:
    tracks = []
    for i, ln in enumerate(ln for ln in text.splitlines() if ln.strip()):
        ln = re.sub(r"^\s*\d+[\.\)]\s*", "", ln.strip())
        artist, title = ln.split(" - ", 1) if " - " in ln else ("", ln)
        tracks.append(Track(id=f"row{i}", title=title.strip(), artists=_split_artists(artist)))
    return tracks


def _spotify_local(uri: str) -> Track | None:
    """spotify:local:Artist:Album:Title:seconds (URL-encoded, '+' for spaces)."""
    parts = (uri or "").split(":")
    if len(parts) < 6:
        return None
    artist, album, title, secs = (unquote_plus(p) for p in parts[2:6])
    return Track(id=uri, title=title, artists=_split_artists(artist), album=album,
                 duration_ms=int(secs) * 1000 if secs.isdigit() else 0)


def _from_spotify_json(data: Any) -> list[dict[str, Any]] | None:
    """Spotify 'Download your data' files: Playlist*.json and YourLibrary.json. None if not that format."""
    out: list[dict[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("playlists"), list):
        for pl in data["playlists"]:
            tracks = []
            for it in pl.get("items") or []:
                t = it.get("track")
                if t and t.get("trackName"):
                    uri = t.get("trackUri", "")
                    tracks.append(Track(id=_uri_id(uri) or uri or f"row{len(tracks)}", title=t["trackName"],
                                        artists=_split_artists(t.get("artistName", "")),
                                        album=t.get("albumName", ""), entry_id=uri))
                elif it.get("localTrack"):
                    lt = _spotify_local((it["localTrack"] or {}).get("uri", ""))
                    if lt:
                        tracks.append(lt)
            if tracks:
                out.append({"kind": "playlist", "name": pl.get("name") or "Untitled playlist", "items": tracks})
        return out
    if isinstance(data, dict) and "artists" in data and ("tracks" in data or "albums" in data):
        liked = [Track(id=_uri_id(t.get("uri", "")) or f"row{i}", title=t["track"],
                       artists=_split_artists(t.get("artist", "")), album=t.get("album", ""))
                 for i, t in enumerate(data.get("tracks") or []) if t.get("track")]
        albums = [Album(id=_uri_id(a.get("uri", "")) or f"row{i}", title=a["album"],
                        artists=_split_artists(a.get("artist", "")))
                  for i, a in enumerate(data.get("albums") or []) if a.get("album")]
        artists = [Artist(id=_uri_id(a.get("uri", "")) or f"row{i}", name=a["name"])
                   for i, a in enumerate(data.get("artists") or []) if a.get("name")]
        if liked:
            out.append({"kind": "playlist", "name": "Liked Songs", "items": liked})
        if albums:
            out.append({"kind": "albums", "name": "Saved albums", "items": albums})
        if artists:
            out.append({"kind": "artists", "name": "Followed artists", "items": artists})
        return out
    return None


def _parse_one(filename: str, raw: bytes) -> list[dict[str, Any]]:
    """Return [{kind, name, items}] for one file (a file may hold several playlists)."""
    text = raw.decode("utf-8-sig", errors="replace")
    stem = Path(filename).stem or "Imported playlist"
    if filename.lower().endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderError(f"{filename} is not valid JSON") from e
        spotify = _from_spotify_json(data)
        if spotify is not None:
            return spotify
        name = stem
        if isinstance(data, dict):
            name = data.get("name") or name
            data = data.get("tracks") or data.get("items") or []
        if not isinstance(data, list):
            return []
        tracks = []
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                continue
            src = row.get("source") if isinstance(row.get("source"), dict) else row
            if not src.get("title"):
                continue
            t = Track.from_dict({**src, "id": src.get("id") or f"row{i}"})
            if isinstance(src.get("artists"), str):
                t.artists = _split_artists(src["artists"])
            tracks.append(t)
        return [{"kind": "playlist", "name": name, "items": tracks}] if tracks else []
    tracks = _tracks_from_csv(text)
    if tracks is None:
        tracks = _tracks_from_text(text)
    return [{"kind": "playlist", "name": stem.replace("_", " "), "items": tracks}] if tracks else []


def _wanted_in_zip(name: str) -> bool:
    low = Path(name).name.lower()
    if "__macosx" in name.lower() or low.startswith("."):
        return False
    if low.endswith(".json"):
        # Spotify's data download also has streaming history etc.; only take library + playlists.
        return low.startswith("playlist") or low == "yourlibrary.json"
    return low.endswith((".csv", ".tsv", ".txt"))


def parse_upload(filename: str, raw: bytes) -> list[dict[str, Any]]:
    """Import a CSV / TXT / JSON file, or a .zip of them (Exportify "export all", Spotify data download).

    Returns one entry per imported playlist / library section.
    """
    groups: list[dict[str, Any]] = []
    if filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as e:
            raise ProviderError("That zip file can't be read") from e
        for info in zf.infolist():
            if not info.is_dir() and _wanted_in_zip(info.filename):
                groups.extend(_parse_one(Path(info.filename).name, zf.read(info)))
    else:
        groups = _parse_one(filename, raw)
    if not groups:
        raise ProviderError("No tracks, albums or artists found in the uploaded file")
    batch = store.new_id()
    previous = _records()  # before writing, so this upload's own records are never replaced
    out = []
    for g in groups:
        rec = {"id": store.new_id(), "name": g["name"], "kind": g["kind"], "filename": filename, "batch": batch,
               "imported_at": time.time(), "tracks": [i.to_dict() for i in g["items"]]}
        (_imports_dir() / f"{rec['id']}.json").write_text(json.dumps(rec), "utf-8")
        # Re-importing replaces the earlier copy of the same playlist (e.g. the same zip dropped twice,
        # or a fresh export). Same-named playlists inside one upload are all kept.
        replaced = 0
        for path, old in previous:
            if old.get("batch") != batch and _same_list(old, rec) and path.exists():
                path.unlink()
                replaced += 1
        out.append({"id": rec["id"], "name": rec["name"], "kind": rec["kind"], "count": len(g["items"]),
                    "replaced": replaced})
    return out


def _same_list(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("kind", "playlist") == b.get("kind", "playlist") and norm(a["name"]) == norm(b["name"])


def _records() -> list[tuple[Path, dict[str, Any]]]:
    """All imported records, newest first."""
    out = []
    for p in _imports_dir().glob("*.json"):
        try:
            rec = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append((p, rec))
    out.sort(key=lambda pr: pr[1].get("imported_at") or pr[0].stat().st_mtime, reverse=True)
    return out


def remove_duplicate_imports() -> int:
    """Delete imports that are exact copies (same kind, name and contents) of a newer one. Returns how many."""
    seen: set[str] = set()
    removed = 0
    for path, rec in _records():
        content = hashlib.sha1(json.dumps(
            [(t.get("title") or t.get("name"), t.get("artists"), t.get("album"), t.get("isrc")) for t in rec["tracks"]]
        ).encode()).hexdigest()
        key = f"{rec.get('kind', 'playlist')}|{norm(rec['name'])}|{content}"
        if key in seen:
            path.unlink(missing_ok=True)
            removed += 1
        else:
            seen.add(key)
    return removed


class FileProvider(Provider):
    """Uploaded files as a read-only source."""

    name = "file"
    label = "File import"
    can_write = False
    read_kinds = ("playlist", "albums", "artists")
    write_kinds = ()

    def is_connected(self) -> bool:
        return True

    def account(self) -> dict[str, Any]:
        return {"connected": True, "user": "local files"}

    def _load(self, ref: str) -> dict[str, Any]:
        p = _imports_dir() / f"{Path(ref).name}.json"
        if not p.exists():
            raise ProviderError("Imported file not found")
        return json.loads(p.read_text("utf-8"))

    def collections(self) -> list[Collection]:
        out = []
        for _, rec in _records():
            out.append(Collection(kind=rec.get("kind", "playlist"), id=rec["id"], name=rec["name"],
                                  count=len(rec["tracks"]), owner=rec.get("filename", "")))
        return out

    def list_playlists(self):  # pragma: no cover - collections() overridden
        return []

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        return [Track.from_dict(t) for t in self._load(playlist_id)["tracks"]]

    def read_items(self, kind: str, ref: str) -> list[Any]:
        cls = {"albums": Album, "artists": Artist}.get(kind, Track)
        return [cls.from_dict(r) for r in self._load(ref)["tracks"]]

    def delete(self, ref: str) -> None:
        (_imports_dir() / f"{Path(ref).name}.json").unlink(missing_ok=True)


# ---------------------------------------------------------------- export
def export_items(items: list[Any], fmt: str, name: str = "playlist") -> tuple[str, str]:
    """Return (content, media_type)."""
    if fmt == "json":
        return json.dumps({"name": name, "items": [i.to_dict() for i in items]}, indent=2), "application/json"
    if fmt == "txt":
        lines = []
        for i in items:
            if isinstance(i, Track):
                lines.append(f"{i.artist} - {i.full_title}")
            elif isinstance(i, Album):
                lines.append(f"{i.artist} - {i.title}")
            elif isinstance(i, Artist):
                lines.append(i.name)
        return "\n".join(lines) + "\n", "text/plain"
    buf = io.StringIO()
    w = csv.writer(buf)
    if items and isinstance(items[0], Album):
        w.writerow(["Album", "Artist", "UPC", "Tracks", "Year", "URL"])
        for a in items:
            w.writerow([a.title, a.artist, a.upc, a.track_count, a.year, a.url])
    elif items and isinstance(items[0], Artist):
        w.writerow(["Artist", "URL"])
        for a in items:
            w.writerow([a.name, a.url])
    else:
        w.writerow(["Title", "Artist", "Album", "Duration (ms)", "ISRC", "URL"])
        for t in items:
            w.writerow([t.full_title, ", ".join(t.artists), t.album, t.duration_ms, t.isrc, t.url])
    return buf.getvalue(), "text/csv"
