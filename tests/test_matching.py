from playlist_transfer.matching import base_title, score_album, score_track, split_artists, track_key, version_tags
from playlist_transfer.models import Album, Track


def T(title, artists, album="", secs=200, isrc="", version=""):
    return Track(id=title, title=title, artists=artists, album=album, duration_ms=secs * 1000, isrc=isrc, version=version)


def test_base_title_strips_noise_but_keeps_words():
    assert base_title("Bohemian Rhapsody - Remastered 2011") == "bohemian rhapsody"
    assert base_title("Get Lucky (feat. Pharrell Williams & Nile Rodgers)") == "get lucky"
    assert base_title("Dancing With Myself") == "dancing with myself"
    assert base_title("Song feat. Someone") == "song"


def test_version_tags():
    assert version_tags("Hallelujah (Live)") == {"live"}
    assert version_tags("Song - Radio Edit") == {"radio"}
    assert version_tags("Bohemian Rhapsody - Remastered 2011") == set()


def test_isrc_wins():
    conf, method = score_track(T("A", ["X"], isrc="US123"), T("Totally different", ["Y"], isrc="us123"))
    assert method == "isrc" and conf > 0.99


def test_remaster_vs_qobuz_version_field():
    src = T("Bohemian Rhapsody - Remastered 2011", ["Queen"], "A Night At The Opera (2011 Remaster)", 354)
    cand = T("Bohemian Rhapsody", ["Queen"], "A Night At The Opera", 355, version="Remastered 2011")
    assert score_track(src, cand)[0] >= 0.85


def test_live_version_is_penalised():
    src = T("Hallelujah", ["Jeff Buckley"], "Grace", 413)
    live = T("Hallelujah", ["Jeff Buckley"], "Live at Sin-é", 425, version="Live")
    other_artist = T("Hallelujah", ["Leonard Cohen"], "Various Positions", 279)
    assert score_track(src, live)[0] < 0.85
    assert score_track(src, other_artist)[0] < 0.55


def test_accents_and_case():
    assert score_track(T("Jóga", ["Björk"], "Homogenic", 305), T("Joga", ["Bjork"], "Homogenic", 305))[0] >= 0.95


def test_featuring_credit_in_artist_list():
    src = T("Get Lucky (feat. Pharrell Williams & Nile Rodgers)", ["Daft Punk", "Pharrell Williams"], "RAM", 369)
    cand = T("Get Lucky", ["Daft Punk"], "Random Access Memories", 368)
    assert score_track(src, cand)[0] >= 0.8


def test_track_key_ignores_remaster_noise():
    assert track_key(T("Heroes - 2017 Remaster", ["David Bowie"])) == track_key(T("Heroes", ["David Bowie"]))
    assert track_key(T("Song (Live)", ["A"])) != track_key(T("Song", ["A"]))


def test_split_artists():
    assert split_artists(["Simon & Garfunkel"]) == ["simon", "garfunkel"]
    assert split_artists(["The Killers"]) == ["killers"]


def test_album_upc_with_leading_zero():
    a = Album("1", "Nevermind", ["Nirvana"], upc="0720642442524")
    b = Album("2", "Nevermind (Remastered)", ["Nirvana"], upc="720642442524")
    assert score_album(a, b)[1] == "upc"
