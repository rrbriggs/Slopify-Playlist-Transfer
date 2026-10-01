"""Text normalisation and match scoring.

Scores are in [0, 1]. ISRC / UPC equality short-circuits to 1.0; otherwise a
weighted blend of title, artist, duration and album similarity is used, with a
penalty when one side is e.g. "Live" or a "Remix" and the other is not.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Album, Artist, Track

# Version markers that change *which recording* a track is.
VERSION_TAGS = {
    "live": r"\blive\b|\bin concert\b|\bunplugged\b",
    "remix": r"\bremix\b|\brmx\b|\bmix\)|\bdub\b|\brework\b|\bedit mix\b",
    "acoustic": r"\bacoustic\b",
    "instrumental": r"\binstrumental\b|\bkaraoke\b",
    "demo": r"\bdemo\b",
    "radio": r"\bradio edit\b|\bradio version\b|\bsingle edit\b|\bsingle version\b",
    "extended": r"\bextended\b|\b12\"|\bclub mix\b",
    "orchestral": r"\borchestral\b|\bsymphonic\b",
    "cover": r"\bcover\b|\btribute\b",
    "sped": r"\bsped up\b|\bslowed\b|\breverb\b|\bnightcore\b",
    "clean": r"\bclean\b|\bcensored\b",
}
_VERSION_RE = {k: re.compile(v) for k, v in VERSION_TAGS.items()}

# Noise that doesn't change the recording.
_NOISE = re.compile(
    r"\b(\d{4} )?(digital(ly)? )?remaster(ed)?( version)?( \d{4})?\b"
    r"|\b(\d{4} )?(stereo|mono)( mix| version)?\b"
    r"|\bdeluxe( edition| version)?\b|\bexpanded( edition)?\b"
    r"|\banniversary( edition)?\b|\bbonus track\b|\bexplicit\b|\balbum version\b"
    r"|\boriginal( mix| version)?\b|\bfrom .*?(soundtrack|motion picture)\b"
    r"|\b\d+(st|nd|rd|th) anniversary\b|\bremastered\b"
)
_FEAT = re.compile(
    r"[\(\[]\s*(feat|ft|featuring|with)\b\.?[^\)\]]*[\)\]]"  # "(feat. X)", "[with Y]"
    r"|\s\b(feat|ft|featuring)\b\.?\s.*$"  # "Song feat. X"
)
_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_SPLIT_ARTISTS = re.compile(r"\s*(?:,|&|\band\b|\bx\b|\bfeat\.?|\bft\.?|\bwith\b|/|;|\+)\s*")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    s = strip_accents(s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s']", " ", s)
    s = s.replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def version_tags(title: str) -> set[str]:
    t = strip_accents(title or "").lower()
    return {k for k, rx in _VERSION_RE.items() if rx.search(t)}


def base_title(title: str) -> str:
    """Title with featuring credits, remaster noise and dash-suffixes removed."""
    t = strip_accents(title or "").lower()
    t = _FEAT.sub(" ", t)
    # "Song - 2011 Remaster" / "Song - Live at X" -> "Song"
    t = re.split(r"\s+-\s+", t, maxsplit=1)[0]
    t = _BRACKETS.sub(" ", t)
    t = _NOISE.sub(" ", t)
    return norm(t)


def split_artists(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        for part in _SPLIT_ARTISTS.split(strip_accents(n or "").lower()):
            p = norm(part)
            if p.startswith("the "):
                p = p[4:]
            if p and p not in out:
                out.append(p)
    return out


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def token_ratio(a: str, b: str) -> float:
    """Order-insensitive similarity, tolerant of one side having extra words."""
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    if ta == tb:
        return 1.0
    inter = ta & tb
    if not inter:
        return ratio(a, b) * 0.8
    containment = len(inter) / min(len(ta), len(tb))
    jaccard = len(inter) / len(ta | tb)
    return max(ratio(a, b), 0.6 * containment + 0.4 * jaccard)


def artist_score(a: list[str], b: list[str]) -> float:
    sa, sb = split_artists(a), split_artists(b)
    if not sa or not sb:
        return 0.5
    if set(sa) & set(sb):
        # primary artist match is the strongest signal
        return 1.0 if sa[0] in sb or sb[0] in sa else 0.9
    best = max(ratio(x, y) for x in sa for y in sb)
    joined = ratio(" ".join(sorted(sa)), " ".join(sorted(sb)))
    return max(best, joined)


def duration_score(a_ms: int, b_ms: int) -> float:
    if not a_ms or not b_ms:
        return 0.6
    d = abs(a_ms - b_ms) / 1000
    if d <= 2:
        return 1.0
    if d <= 5:
        return 0.85
    if d <= 10:
        return 0.55
    if d <= 30:
        return 0.2
    return 0.0


def same_code(a: str, b: str) -> bool:
    """ISRC / UPC comparison (UPC vs EAN-13 differ by a leading zero)."""
    a, b = (a or "").strip().upper(), (b or "").strip().upper()
    if not a or not b:
        return False
    if a.isdigit() and b.isdigit():
        return a.lstrip("0") == b.lstrip("0")
    return a == b


def score_track(src: Track, cand: Track) -> tuple[float, str]:
    """Return (confidence, method)."""
    if same_code(src.isrc, cand.isrc):
        # Same recording. Small nudge so that, among several ISRC hits,
        # the one on the same album / available one wins.
        bonus = 0.0
        if norm(src.album) and norm(src.album) == norm(cand.album):
            bonus += 0.002
        if cand.available:
            bonus += 0.001
        return min(1.0, 0.997 + bonus), "isrc"

    st, ct = src.full_title, cand.full_title
    title = token_ratio(base_title(st), base_title(ct))
    artist = artist_score(src.artists, cand.artists)
    dur = duration_score(src.duration_ms, cand.duration_ms)
    album = token_ratio(base_title(src.album), base_title(cand.album)) if src.album and cand.album else 0.5

    score = 0.45 * title + 0.30 * artist + 0.15 * dur + 0.10 * album

    tags_a, tags_b = version_tags(st), version_tags(ct)
    mismatch = tags_a ^ tags_b
    if mismatch:
        score -= 0.18 * len(mismatch)
    if title < 0.5 or artist < 0.4:
        score = min(score, 0.45)
    if not cand.available:
        score -= 0.05
    return max(0.0, min(score, 0.99)), "text"


def score_album(src: Album, cand: Album) -> tuple[float, str]:
    if same_code(src.upc, cand.upc):
        return 0.999, "upc"
    title = token_ratio(base_title(src.title), base_title(cand.title))
    artist = artist_score(src.artists, cand.artists)
    if src.track_count and cand.track_count:
        diff = abs(src.track_count - cand.track_count)
        count = 1.0 if diff == 0 else 0.7 if diff <= 2 else 0.3
    else:
        count = 0.6
    year = 1.0 if src.year and src.year[:4] == (cand.year or "")[:4] else 0.5
    score = 0.45 * title + 0.35 * artist + 0.12 * count + 0.08 * year
    mismatch = version_tags(src.title) ^ version_tags(cand.title)
    if mismatch:
        score -= 0.15 * len(mismatch)
    if title < 0.5 or artist < 0.4:
        score = min(score, 0.45)
    return max(0.0, min(score, 0.99)), "text"


def score_artist(src: Artist, cand: Artist) -> tuple[float, str]:
    a, b = norm(src.name), norm(cand.name)
    if a.startswith("the "):
        a = a[4:]
    if b.startswith("the "):
        b = b[4:]
    if a == b:
        return 0.98, "name"
    return ratio(a, b) * 0.9, "name"


def track_key(t: Track) -> str:
    """Loose identity used for duplicate detection when IDs/ISRCs differ."""
    tags = ",".join(sorted(version_tags(t.full_title)))
    artists = split_artists(t.artists)
    return f"{base_title(t.full_title)}|{artists[0] if artists else ''}|{tags}"


def search_queries(t: Track) -> list[str]:
    artists = split_artists(t.artists)
    primary = t.artists[0] if t.artists else ""
    bt = base_title(t.full_title)
    tags = " ".join(sorted(version_tags(t.full_title)))
    qs = [f"{bt} {tags} {norm(primary)}".strip()]
    if artists and len(artists) > 1:
        qs.append(f"{bt} {artists[1]}")
    if t.album:
        qs.append(f"{bt} {base_title(t.album)}")
    seen, out = set(), []
    for q in qs:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# ---------------------------------------------------------------- playlist names
_NAME_FILLER = {"spotify", "qobuz", "from", "imported", "import", "copy", "soundiiz", "tunemymusic", "playlist", "my"}
LIKED_NAMES = {"liked songs", "liked tracks", "liked", "favorites", "favourites", "favorite tracks",
               "favourite tracks", "loved tracks", "your liked songs", "my liked songs", "spotify liked songs"}


def _name_core(name: str) -> str:
    words = [w for w in norm(name).split() if w not in _NAME_FILLER]
    return " ".join(words) or norm(name)


def is_liked_name(name: str) -> bool:
    return norm(name) in LIKED_NAMES or _name_core(name) in {"liked songs", "liked", "liked tracks", "favorites"}


def playlist_name_score(a: str, b: str) -> float:
    """How likely two playlist names refer to the same playlist (tolerates 'Road Trip' vs 'Road Trip (Spotify)')."""
    if norm(a) == norm(b):
        return 1.0
    ca, cb = _name_core(a), _name_core(b)
    if ca == cb:
        return 0.97
    if is_liked_name(a) and is_liked_name(b):
        return 0.95
    score = token_ratio(ca, cb) * 0.95
    if set(re.findall(r"\d+", ca)) != set(re.findall(r"\d+", cb)):
        score = min(score, 0.6)  # "Summer 2023" is not "Summer 2024"
    return score
