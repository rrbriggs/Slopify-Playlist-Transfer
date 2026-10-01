# Playlist Transfer

A local, self-hosted alternative to Soundiiz for **Spotify ⇄ Qobuz**. It finds which songs from your Spotify
playlists and Liked Songs are missing on Qobuz, shows you the diff in your browser, and only adds what you approve.
Nothing is ever added twice.

---

## Quick start (the short version)

Open PowerShell or a terminal and run:

```
cd path\to\Slopify-Playlist-Transfer
python -m playlist_transfer
```

Your browser opens at **http://127.0.0.1:8765**. If it doesn't, open that address yourself.
Leave the terminal window open while you use the app, and press **Ctrl+C** in it when you're done.
When the app's code is updated, it restarts itself and the browser page refreshes automatically. There's no need to
restart it by hand.

> Want to look around without touching your real accounts? Run `python -m playlist_transfer serve --demo`
> to use fake sample data.

---

## First-time setup (only once per computer)

1. **Python 3.10+** must be installed. Check with `python --version`.
2. Install the app and its dependencies from the project folder:
   ```
   cd path\to\Slopify-Playlist-Transfer
   pip install -e .
   ```
3. Start it (`python -m playlist_transfer`) and go to **Connections**:
   - **Qobuz** (required): log in at [play.qobuz.com](https://play.qobuz.com), press **F12** → **Application** tab →
     **Local Storage** → `https://play.qobuz.com` → the `localuser` entry. Copy its `id` into **User ID** and
     its `token` into **User auth token**, then click **Connect Qobuz**.
     (Email & password login exists too, but Qobuz usually blocks it with a captcha.)
   - **Spotify**: you **don't need to connect it** without Spotify Premium. Use an export file instead (next section).
     With Premium you can connect directly; see [Connecting Spotify directly](#connecting-spotify-directly-premium-only).

Your logins and data are saved in `%USERPROFILE%\.playlist-transfer`, so you only connect once.

---

## How to use it

### Get your Spotify data (no Premium needed)

Pick one:

- **[Exportify](https://exportify.app)** (recommended): sign in with Spotify and click **Export All**. You get a
  `.zip` with one CSV per playlist. These include ISRC codes, so matching is very accurate. If Liked Songs isn't
  in the zip, export it separately from Exportify's list, or use the Spotify data download below.
- **Spotify "Download your data"**: at [spotify.com/account/privacy](https://www.spotify.com/account/privacy/)
  request **Account data**. It arrives by email in a few days as `my_spotify_data.zip`. It includes playlists, Liked
  Songs, saved albums and followed artists, but no ISRCs, so a few more tracks may need review.

### Find what's missing on Qobuz (the main use)

1. Start the app (`python -m playlist_transfer`).
2. Go to **Transfer** → **From: File import**, and drag the `.zip` onto the drop area.
   Each playlist from the zip appears in the list.
   *(Dropping a zip again, or a newer export, **replaces** the earlier copy of each playlist, so you never get
   duplicates. To clear things out, use **Remove duplicates** or tick items and click **Delete selected**.)*
3. **To: Qobuz** should be selected.
4. Click **Select previously transferred**. This ticks every Spotify playlist that has a playlist with the same or
   a similar name on Qobuz, plus Liked Songs. Matches show in green next to each one, e.g.
   `↔ Qobuz "Road Trip"`.
5. Check the **Compare against** table. Each Spotify playlist has a dropdown with the Qobuz playlist it will be
   compared with:
   - The best name match is pre-selected, and the % shows how close the names are.
   - **Liked Songs** compares against your **Qobuz favorites** by default. If you put them into a Qobuz *playlist*
     last time, pick that playlist instead.
   - If something is matched wrong, or says **new playlist** but you know it exists on Qobuz under another name,
     choose the right one from the dropdown.
6. Click **Analyze & preview diff**. Nothing is changed yet. You land on **Reviews**, with one card per playlist
   showing counts like "12 to add · 3 not found".
7. Open a review. It starts on **Missing on Qobuz**: the songs in Spotify that aren't in that Qobuz playlist.
   - Ticked rows will be added. Untick any you don't want.
   - **Needs review** rows (orange) are uncertain matches, e.g. only a live version was found. Check them and click
     **Change** to pick a different version or search Qobuz yourself.
   - **Not found** rows aren't available on Qobuz (or are too different to match). Click **Find** to search manually.
   - Songs that are only on Qobuz are left alone.
8. Click **Apply to Qobuz** to add the ticked songs. Applying again later never creates duplicates.

**Just want a list, not changes?** In any review, use **Download diff… → Missing on target (CSV)**.

### Other things you can do

| Task | How |
|---|---|
| Copy a playlist to a new Qobuz playlist | Transfer → tick it → set its dropdown to **+ Create a new playlist** → Analyze → Apply |
| Combine several playlists into one | Tick them → **Merge the N playlists into one playlist** → name it |
| Saved albums / followed artists | Tick them under **Library** (they go to Qobuz favorites) |
| Remove songs from Qobuz that aren't in Spotify | In a review, open **Only in Qobuz** and tick **Remove** (or enable **Mirror**) |
| Export a playlist to a file | Transfer → **Export…** next to any playlist → CSV / JSON / Text |
| Check again later | Re-export from Exportify, import the new zip, repeat the steps. Already-transferred songs show as **Already there** |
| Qobuz → Spotify | Requires Spotify connected directly (Premium) |

### Understanding a review

| Label | Meaning |
|---|---|
| **Missing on Qobuz** | Everything below that isn't on Qobuz yet: New + Needs review + Not found |
| **New** | Found on Qobuz with a confident match; will be added |
| **Needs review** | Probably the right song but not certain; pre-ticked only if ≥ 70% |
| **Already there** | Already in the Qobuz playlist or favorites; skipped |
| **Duplicates** | Appears more than once in the Spotify playlist; only added once |
| **Not found** | No good match on Qobuz |
| **Only in Qobuz** | On Qobuz but not in Spotify; kept unless you tick Remove |

The match column shows a confidence % and how it matched: **ISRC** (exact recording code, most reliable),
**metadata** (title/artist/duration/album), or **manual** (your choice, remembered for next time).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Install Python from python.org or the Microsoft Store, then redo first-time setup |
| `No module named playlist_transfer` | Run the command from the project folder, or redo `pip install -e .` |
| Browser shows "can't connect" | The app isn't running. Start it again; keep the terminal open |
| "Address already in use" | It's already running in another window. Use that, or run `python -m playlist_transfer serve --port 8766` |
| Odd errors after the code was updated | The app reloads itself on code changes. If it says *"The app stopped"*, the new code has an error; it starts again on the next change. If the page looks stale, refresh it |
| A review says "Interrupted because the app restarted" | The app reloaded while it was working. Click **Re-analyze**, or **Apply** again (safe, it skips what's already there) |
| "Qobuz session rejected, please reconnect" | Your Qobuz token expired (e.g. you logged out on the web). Connections → Disconnect → reconnect with a fresh `localuser` token |
| Spotify says Premium is required | Expected without Premium; use the Exportify / file import route instead |
| A Spotify playlist shows "Not readable" | Spotify only exposes playlists you own. Use an export file instead |
| Imported playlists show up twice | Transfer → File import → **Remove duplicates** (exact copies are also removed automatically each time the app starts) |
| Too many old reviews | Reviews → **Remove duplicate reviews** or **Delete unapplied…** (applied and synced reviews are kept) |
| Start completely fresh | Stop the app and delete `%USERPROFILE%\.playlist-transfer` (removes logins, imports and reviews) |

Commands at a glance:

```
python -m playlist_transfer                     # start the app (same as "serve")
python -m playlist_transfer serve --port 8766   # use a different port
python -m playlist_transfer serve --demo        # fake data, safe to play with
python -m playlist_transfer serve --no-browser  # don't auto-open the browser
python -m playlist_transfer serve --no-reload   # don't restart automatically on code changes
python -m playlist_transfer syncs               # list saved syncs
python -m playlist_transfer sync                # run syncs that are due
```

---

## Reference

### Connecting Spotify directly (Premium only)
Since February 2026 Spotify only lets Premium accounts own API apps. With Premium:
create a free app at <https://developer.spotify.com/dashboard>, add the redirect URI shown on the Connections page
(`http://127.0.0.1:8765/api/auth/spotify/callback`), tick *Web API*, then paste the Client ID on **Connections**.
This enables reading Spotify directly, syncs from Spotify, and Qobuz → Spotify transfers.
Even then, Spotify only lets you read playlists you own or collaborate on.

### Syncs
When applying a review you can tick **Keep in sync** (daily/weekly/…). Syncs run while the app is open.
Manage them on the **Syncs** page. To run them without the app open, schedule `python -m playlist_transfer sync`,
e.g. on Windows:
`schtasks /Create /SC DAILY /TN PlaylistTransferSync /TR "python -m playlist_transfer sync"`.
Unattended runs only add confident matches; uncertain ones wait in a review.
Syncs need a directly connected source, since file imports don't update themselves.

### How duplicates are prevented
1. **Playlists:** a same/similar-named playlist on the target is reused instead of creating a second one.
2. **Within the source:** repeated tracks, the same ISRC on different releases, and (with *strict duplicate
   detection*) remasters/re-releases of the same song are only transferred once.
3. **Against the target:** anything already there is marked *Already there* and skipped.
4. **At write time:** the target is re-read right before writing, so applying twice is safe.

### How matching works
1. **ISRC** (tracks) or **UPC** (albums) lookup: exact recording / release.
2. Otherwise a Qobuz search scored on title, artists, duration and album. Remaster/"feat." noise is ignored;
   *Live / Remix / Acoustic / Radio edit…* mismatches are penalised.
3. ≥ 85% → added; 55–85% → **Needs review**; below → **Not found** (closest candidates still shown).
4. Manual choices are remembered for future transfers.

### Supported import files
Exportify CSVs or zip, Spotify "Download your data" zip (`Playlist*.json`, `YourLibrary.json`), Soundiiz /
TuneMyMusic CSV exports, any CSV with Title / Artist (and optionally Album / ISRC / Duration) columns, or a text
file with one `Artist - Title` per line.

### Where data is stored
`%USERPROFILE%\.playlist-transfer` (override with the `PLT_DATA_DIR` environment variable):
`settings.json` (logins/tokens, keep private), `data.db` (reviews, syncs, history, remembered matches),
`imports\` (imported files). Demo mode uses `.playlist-transfer-demo` instead.

### Limitations
- Qobuz has no official public API. This uses the same web-player API as streamrip / qobuz-dl, so it could change.
- Spotify Development Mode limits search to 10 results per query, which slows large direct transfers.
- Not included: Soundiiz's SmartLinks, AI playlists, and its 40+ other services.

### Development
```
pip install -e .[dev]
pytest
```
`providers/demo.py` holds fake services with tricky cases (remasters, Qobuz `version` fields, missing ISRCs,
accents, live-only alternatives, duplicates, renamed playlists); tests and `--demo` use it.
Layout: `matching.py` (scoring), `engine.py` (diff, dedupe, apply, sync, playlist mapping), `providers/`
(Spotify, Qobuz, files, demo), `server.py` (FastAPI), `static/` (UI, no build step).
