"""Service-agnostic data model shared by every provider."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Track:
    id: str
    title: str
    artists: list[str]
    album: str = ""
    duration_ms: int = 0
    isrc: str = ""
    version: str = ""  # Qobuz splits "(Live)", "Remastered" etc. into a separate field
    explicit: bool = False
    available: bool = True
    hires: bool = False
    url: str = ""
    entry_id: str = ""  # per-playlist entry handle (Qobuz playlist_track_id / Spotify uri)

    @property
    def full_title(self) -> str:
        if self.version and self.version.lower() not in self.title.lower():
            return f"{self.title} ({self.version})"
        return self.title

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["full_title"] = self.full_title
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Track":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Album:
    id: str
    title: str
    artists: list[str]
    upc: str = ""
    track_count: int = 0
    year: str = ""
    available: bool = True
    hires: bool = False
    url: str = ""

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Album":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Artist:
    id: str
    name: str
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Artist":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PlaylistInfo:
    id: str
    name: str
    description: str = ""
    track_count: int = 0
    owner: str = ""
    editable: bool = False  # can we write to it?
    readable: bool = True  # can we read its items? (Spotify dev-mode restriction)
    public: bool = False
    url: str = ""
    image: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Collection:
    """Something a user can pick as a transfer source."""

    kind: str  # playlist | liked | albums | artists
    id: str
    name: str
    count: int = 0
    readable: bool = True
    editable: bool = False
    owner: str = ""
    note: str = ""
    image: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ITEM_CLASSES = {"track": Track, "album": Album, "artist": Artist}


def item_type_for(kind: str) -> str:
    """Map a collection kind to the type of item it contains."""
    return {"albums": "album", "artists": "artist"}.get(kind, "track")
