/* Playlist Transfer — single page UI (no build step). */
"use strict";

const $app = document.getElementById("app");
const $modal = document.getElementById("modal-root");

const S = {
  services: [], demo: false, redirect: "",
  collCache: {}, mapCache: {},
  tr: {
    source: null, target: null, selected: new Set(), filter: "",
    mapping: {}, merge: false, name: "", public: false, mirror: false, fuzzy: true,
  },
  planFilter: "all", planSearch: "", plan: null,
};
let pollTimer = null;

// ------------------------------------------------------------------ helpers
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtDur = (ms) => { if (!ms) return ""; const s = Math.round(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "—";
const svc = (name) => S.services.find((s) => s.name === name) || { name, label: name, account: {} };
const svcLabel = (name) => svc(name).label.replace(" (demo)", "");
const logo = (name) => `<span class="svc-logo svc-${esc(name)}">${{ spotify: "S", qobuz: "Q", file: "F" }[name] || "?"}</span>`;
const KIND_LABEL = { playlist: "Playlist", liked: "Liked tracks", albums: "Albums", artists: "Artists" };
const STATUS_LABEL = { add: "To add", review: "Review", exists: "Already there", duplicate: "Duplicate", not_found: "Not found", target_only: "Only in target" };
const METHOD_LABEL = { isrc: "ISRC", upc: "UPC", text: "metadata", manual: "manual", name: "name" };

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data;
}

function toast(msg, err = false) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), err ? 7000 : 3500);
}
const fail = (e) => toast(e.message || String(e), true);

function closeModal() { $modal.innerHTML = ""; }
function openModal(html) {
  $modal.innerHTML = `<div class="modal-back"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
  $modal.querySelector(".modal-back").addEventListener("click", (e) => { if (e.target.classList.contains("modal-back")) closeModal(); });
  return $modal.querySelector(".modal");
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

async function loadServices() {
  const d = await api("/api/services");
  S.services = d.services; S.demo = d.demo; S.redirect = d.spotify_redirect_uri;
  document.getElementById("demo-badge").innerHTML = S.demo ? `<span class="badge st-review">DEMO DATA</span>` : "";
  document.getElementById("conn-dots").innerHTML = S.services.filter((s) => s.name !== "file")
    .map((s) => `<span><span class="dot ${s.account.connected ? "on" : ""}"></span>${esc(svcLabel(s.name))}${s.account.connected && s.account.user ? ` · ${esc(s.account.user)}` : ""}</span>`).join("");
}

// ------------------------------------------------------------------ router
function route() {
  clearTimeout(pollTimer);
  closeModal();
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query || "");
  if (params.get("connected")) toast(`${svcLabel(params.get("connected"))} connected`);
  if (params.get("error")) toast(params.get("error"), true);
  if (query) history.replaceState(null, "", `#${path || "/"}`);
  const parts = (path || "/").split("/").filter(Boolean);
  const nav = parts[0] === "plan" ? "plans" : (parts[0] || "transfer");
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  if (!parts.length) return viewTransfer();
  if (parts[0] === "plans") return viewPlans();
  if (parts[0] === "plan") return viewPlan(parts[1]);
  if (parts[0] === "syncs") return viewSyncs();
  if (parts[0] === "history") return viewHistory();
  if (parts[0] === "connections") return viewConnections();
  $app.innerHTML = `<div class="empty">Not found</div>`;
}
window.addEventListener("hashchange", route);

// ================================================================== CONNECTIONS
function viewConnections() {
  const sp = svc("spotify"), qb = svc("qobuz");
  $app.innerHTML = `
    <h1>Connections</h1>
    <p class="muted">Credentials are stored only on this computer (in <code>~/.playlist-transfer</code>).</p>
    ${S.demo ? `<div class="notice warn">Demo mode: both services are simulated with sample data. Run without <code>--demo</code> to use your real accounts.</div>` : ""}
    <div class="grid2">
      <section class="panel" id="sp-panel">
        <div class="row between"><h2>${logo("spotify")} Spotify</h2>${connBadge(sp)}</div>
        ${sp.account.connected ? `
          <div class="kv"><span class="muted">Account</span><span>${esc(sp.account.user)}</span></div>
          <div class="row" style="margin-top:14px"><button class="danger" data-disc="spotify">Disconnect</button></div>` : `
          ${sp.account.error ? `<div class="notice err">${esc(sp.account.error)}</div>` : ""}
          <p class="muted small">Spotify requires each user to register a (free) developer app. It takes about a minute:</p>
          <ol class="help small">
            <li>Open <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener">developer.spotify.com/dashboard</a> and click <b>Create app</b>.</li>
            <li>Add this Redirect URI exactly: <br><code id="redir">${esc(S.redirect)}</code> <button class="sm ghost" id="copy-redir">Copy</button></li>
            <li>Tick <b>Web API</b>, save, then copy the app's <b>Client ID</b> below.</li>
          </ol>
          <div class="notice warn small"><b>No Spotify Premium?</b> Spotify only lets Premium accounts own API apps (since Feb 2026). You don't need this connection to move music <i>from</i> Spotify:
            export with <a href="https://exportify.app" target="_blank" rel="noopener">Exportify</a> or Spotify's
            <a href="https://www.spotify.com/account/privacy/" target="_blank" rel="noopener">Download your data</a>, then use
            <a href="#/" id="go-file">Transfer → File import</a>.</div>
          <div class="row"><input type="text" id="sp-client" class="grow" placeholder="Client ID" value="${esc(sp.account.client_id || "")}">
            <button class="primary" id="sp-connect">Connect Spotify</button></div>`}
      </section>
      <section class="panel" id="qb-panel">
        <div class="row between"><h2>${logo("qobuz")} Qobuz</h2>${connBadge(qb)}</div>
        ${qb.account.connected ? `
          <div class="kv"><span class="muted">Account</span><span>${esc(qb.account.user || qb.account.user_id)}</span>
          ${qb.account.subscription ? `<span class="muted">Plan</span><span>${esc(qb.account.subscription)}</span>` : ""}
          <span class="muted">App ID</span><span class="mono">${esc(qb.account.app_id || "")}</span></div>
          <div class="row" style="margin-top:14px"><button class="danger" data-disc="qobuz">Disconnect</button></div>` : `
          <div class="tabs"><button class="active" data-qtab="token">Auth token (recommended)</button><button data-qtab="pw">Email &amp; password</button></div>
          <div data-qpane="token">
            <ol class="help small">
              <li>Log in at <a href="https://play.qobuz.com" target="_blank" rel="noopener">play.qobuz.com</a>.</li>
              <li>Press <b>F12</b> → <b>Application</b> → <b>Local Storage</b> → <code>https://play.qobuz.com</code> → key <code>localuser</code>.</li>
              <li>Copy its <code>id</code> (User ID) and <code>token</code> values below.<br>
                <span class="muted">(Alternatively: Network tab → the <code>user/login</code> request → response <code>user.id</code> and <code>user_auth_token</code>.)</span></li>
            </ol>
            <div class="col">
              <input type="text" id="qb-uid" placeholder="User ID (numbers)">
              <input type="password" id="qb-token" placeholder="User auth token">
            </div>
          </div>
          <div data-qpane="pw" hidden>
            <p class="muted small">Qobuz often blocks scripted password logins with a captcha. If this fails, use the token method. Your password is never stored, only the session token.</p>
            <div class="col"><input type="text" id="qb-email" placeholder="Email or username"><input type="password" id="qb-pw" placeholder="Password"></div>
          </div>
          <details style="margin-top:10px"><summary class="small muted">Advanced: App ID</summary>
            <p class="small muted">Detected automatically from the Qobuz web player. Override only if detection fails.</p>
            <input type="text" id="qb-app" placeholder="9-digit app_id (optional)" value="${esc(qb.account.app_id || "")}">
          </details>
          <div class="row" style="margin-top:12px"><button class="primary" id="qb-connect">Connect Qobuz</button></div>`}
      </section>
    </div>`;

  $app.querySelectorAll("[data-disc]").forEach((b) => b.onclick = async () => {
    await api(`/api/auth/${b.dataset.disc}/disconnect`, { body: {} }).catch(fail);
    S.collCache = {}; await loadServices(); viewConnections();
  });
  const goFile = $app.querySelector("#go-file");
  if (goFile) goFile.onclick = () => { S.tr.source = "file"; S.tr.selected.clear(); };
  const copy = $app.querySelector("#copy-redir");
  if (copy) copy.onclick = () => navigator.clipboard.writeText(S.redirect).then(() => toast("Copied"));
  const spc = $app.querySelector("#sp-connect");
  if (spc) spc.onclick = async () => {
    try { const d = await api("/api/auth/spotify/start", { body: { client_id: $app.querySelector("#sp-client").value } }); location.href = d.url; } catch (e) { fail(e); }
  };
  let qtab = "token";
  $app.querySelectorAll("[data-qtab]").forEach((b) => b.onclick = () => {
    qtab = b.dataset.qtab;
    $app.querySelectorAll("[data-qtab]").forEach((x) => x.classList.toggle("active", x === b));
    $app.querySelectorAll("[data-qpane]").forEach((p) => p.hidden = p.dataset.qpane !== qtab);
  });
  const qbc = $app.querySelector("#qb-connect");
  if (qbc) qbc.onclick = async () => {
    const v = (id) => ($app.querySelector(id)?.value || "").trim();
    qbc.disabled = true; qbc.textContent = "Connecting…";
    try {
      if (qtab === "token") await api("/api/auth/qobuz/token", { body: { user_id: v("#qb-uid"), token: v("#qb-token"), app_id: v("#qb-app") } });
      else await api("/api/auth/qobuz/password", { body: { email: v("#qb-email"), password: v("#qb-pw"), app_id: v("#qb-app") } });
      toast("Qobuz connected"); S.collCache = {}; await loadServices(); viewConnections();
    } catch (e) { fail(e); qbc.disabled = false; qbc.textContent = "Connect Qobuz"; }
  };
}
const connBadge = (s) => s.account.connected ? `<span class="badge st-add">Connected</span>` : `<span class="badge st-not_found">Not connected</span>`;

// ================================================================== TRANSFER
async function viewTransfer() {
  const tr = S.tr;
  const readable = S.services.filter((s) => s.account.connected && s.read_kinds.length);
  const writable = S.services.filter((s) => s.account.connected && s.can_write);
  if (!tr.source || !readable.some((s) => s.name === tr.source)) tr.source = (readable.find((s) => s.name === "spotify") || readable[0] || {}).name;
  if (!tr.target || tr.target === tr.source || !writable.some((s) => s.name === tr.target))
    tr.target = (writable.find((s) => s.name !== tr.source && s.name === "qobuz") || writable.find((s) => s.name !== tr.source) || {}).name;
  const missing = ["spotify", "qobuz"].filter((n) => !svc(n).account.connected);

  $app.innerHTML = `
    <h1>New transfer</h1>
    <p class="muted">Choose what to move. Nothing is written until you've reviewed the diff and pressed <b>Apply</b>.</p>
    ${missing.length ? `<div class="notice warn">${missing.map(svcLabel).join(" and ")} ${missing.length > 1 ? "are" : "is"} not connected. <a href="#/connections">Connect accounts →</a></div>` : ""}
    <section class="panel">
      <div class="step"><span class="n">1</span><h2 style="margin:0">From</h2></div>
      <div class="pills" id="src-pills">${S.services.map((s) => `
        <button class="pill ${s.name === tr.source ? "active" : ""}" data-src="${s.name}" ${s.account.connected ? "" : "disabled"}>${logo(s.name)} ${esc(svcLabel(s.name))}</button>`).join("")}</div>
    </section>
    <section class="panel">
      <div class="row between" style="margin-bottom:12px">
        <div class="step" style="margin:0"><span class="n">2</span><h2 style="margin:0">What</h2></div>
        <div class="row"><input type="search" id="coll-filter" placeholder="Filter…" value="${esc(tr.filter)}">
          <button class="sm" id="sel-prev" title="Select every playlist that has a same or similarly named playlist on the target, plus liked songs">Select previously transferred</button>
          <button class="sm" id="sel-all">Select all playlists</button><button class="sm" id="sel-none">Clear</button>
          ${tr.source === "file" ? `<button class="sm" id="imp-dedupe" title="Delete imports that are exact copies of another import">Remove duplicates</button>
          <button class="sm danger" id="imp-del">Delete selected</button>` : ""}</div>
      </div>
      <div id="coll-area"><div class="empty">Loading…</div></div>
    </section>
    <section class="panel">
      <div class="step"><span class="n">3</span><h2 style="margin:0">To</h2></div>
      <div class="pills" id="tgt-pills">${S.services.filter((s) => s.can_write).map((s) => `
        <button class="pill ${s.name === tr.target ? "active" : ""}" data-tgt="${s.name}" ${s.account.connected && s.name !== tr.source ? "" : "disabled"}>${logo(s.name)} ${esc(svcLabel(s.name))}</button>`).join("")}</div>
      <div id="opts" style="margin-top:16px"></div>
    </section>
    <div class="row" style="justify-content:flex-end">
      <span class="muted small" id="sel-summary"></span>
      <button class="primary" id="analyze">Analyze &amp; preview diff →</button>
    </div>`;

  $app.querySelectorAll("[data-src]").forEach((b) => b.onclick = () => { tr.source = b.dataset.src; tr.selected.clear(); viewTransfer(); });
  $app.querySelectorAll("[data-tgt]").forEach((b) => b.onclick = () => { tr.target = b.dataset.tgt; viewTransfer(); });
  $app.querySelector("#coll-filter").oninput = (e) => { tr.filter = e.target.value; renderCollections(); };
  $app.querySelector("#sel-all").onclick = () => { visibleCollections().filter((c) => c.kind === "playlist" && c.readable).forEach((c) => tr.selected.add(`${c.kind}|${c.id}`)); renderCollections(); };
  $app.querySelector("#sel-none").onclick = () => { tr.selected.clear(); renderCollections(); };
  const dd = $app.querySelector("#imp-dedupe");
  if (dd) dd.onclick = async () => {
    try {
      const r = await api("/api/imports/dedupe", { body: {} });
      toast(r.removed ? `Removed ${r.removed} duplicate import(s)` : "No duplicates found");
      delete S.collCache.file; S.mapCache = {}; tr.selected.clear(); viewTransfer();
    } catch (e) { fail(e); }
  };
  const del = $app.querySelector("#imp-del");
  if (del) del.onclick = async () => {
    const ids = [...tr.selected].map((k) => k.split("|").slice(1).join("|"));
    if (!ids.length) return toast("Tick the imports to delete first (Select all playlists works too)", true);
    if (!confirm(`Delete ${ids.length} imported item(s)? This only removes them from this app, not from Spotify or Qobuz.`)) return;
    try {
      await api("/api/imports/delete", { body: { ids } });
      toast(`Deleted ${ids.length} import(s)`);
      delete S.collCache.file; S.mapCache = {}; tr.selected.clear(); viewTransfer();
    } catch (e) { fail(e); }
  };
  $app.querySelector("#sel-prev").onclick = () => {
    const m = currentMapping();
    if (!m) return toast("Pick a target service first", true);
    let n = 0;
    for (const [key, sug] of Object.entries(m.suggestions)) if (sug.matched) { tr.selected.add(key); n++; }
    toast(n ? `Selected ${n} playlist(s) that look previously transferred` : "No similarly named playlists found on the target");
    renderCollections();
  };
  $app.querySelector("#analyze").onclick = startAnalyze;
  if (!tr.source) { $app.querySelector("#coll-area").innerHTML = `<div class="empty">Connect a service first.</div>`; return; }
  try {
    if (!S.collCache[tr.source]) S.collCache[tr.source] = (await api(`/api/services/${tr.source}/collections`)).collections;
  } catch (e) {
    $app.querySelector("#coll-area").innerHTML = `<div class="notice err">${esc(e.message)}</div>`;
    return;
  }
  renderCollections();
  const mk = `${tr.source}>${tr.target}`;
  if (tr.target && !(mk in S.mapCache)) {
    try { S.mapCache[mk] = await api(`/api/mapping?source=${tr.source}&target=${tr.target}`); }
    catch (e) { S.mapCache[mk] = null; toast(`Couldn't read ${svcLabel(tr.target)} playlists: ${e.message}`, true); }
    renderCollections();
  }
}

function visibleCollections() {
  const f = S.tr.filter.toLowerCase();
  return (S.collCache[S.tr.source] || []).filter((c) => !f || c.name.toLowerCase().includes(f) || (c.owner || "").toLowerCase().includes(f));
}

function renderCollections() {
  const tr = S.tr;
  const area = $app.querySelector("#coll-area");
  if (!area) return;
  const all = visibleCollections();
  const lib = all.filter((c) => c.kind !== "playlist");
  const pls = all.filter((c) => c.kind === "playlist");
  const row = (c) => {
    const key = `${c.kind}|${c.id}`;
    const exp = (fmt) => `/api/services/${tr.source}/export?kind=${c.kind}&ref=${encodeURIComponent(c.id)}&fmt=${fmt}&name=${encodeURIComponent(c.name)}`;
    return `<div class="item ${c.readable ? "" : "disabled"}">
      <input type="checkbox" data-key="${esc(key)}" ${tr.selected.has(key) ? "checked" : ""} ${c.readable ? "" : "disabled"} aria-label="Select ${esc(c.name)}">
      ${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy">` : `<div class="thumb"></div>`}
      <div class="grow"><div class="name">${esc(c.name)}</div>
        <div class="small muted">${c.kind === "playlist" ? `${c.count} tracks${c.owner ? ` · ${esc(c.owner)}` : ""}` : esc(KIND_LABEL[c.kind])}${c.note ? ` · <span style="color:var(--review)">${esc(c.note)}</span>` : ""}${mapHint(key)}</div></div>
      ${c.readable ? `<select class="sm" data-export aria-label="Export">
        <option value="">Export…</option><option value="${esc(exp("csv"))}">CSV</option><option value="${esc(exp("json"))}">JSON</option><option value="${esc(exp("txt"))}">Text</option></select>` : ""}
      ${tr.source === "file" ? `<button class="sm ghost danger" data-del-import="${esc(c.id)}">Delete</button>` : ""}
    </div>`;
  };
  area.innerHTML = `
    ${tr.source === "file" ? `<div class="drop" id="drop">Drop a file here, or <label style="color:var(--accent);cursor:pointer">browse<input type="file" id="file-in" accept=".csv,.txt,.json,.tsv,.zip" hidden></label>
      <div class="small" style="margin-top:8px;text-align:left;max-width:640px;margin-inline:auto">
        <b>No Spotify Premium? Use one of these:</b>
        <ul style="margin:4px 0 0;padding-left:18px">
          <li><a href="https://exportify.app" target="_blank" rel="noopener">Exportify</a>: sign in, click <b>Export All</b>, then drop the <code>.zip</code> (or a single playlist <code>.csv</code>) here. Best option: includes ISRCs and durations.</li>
          <li>Spotify's <a href="https://www.spotify.com/account/privacy/" target="_blank" rel="noopener">Download your data</a> (Account data, arrives by email within a few days): drop the <code>my_spotify_data.zip</code> here. Brings in playlists, Liked Songs, saved albums and followed artists.</li>
          <li>Any CSV with Title / Artist / Album / ISRC columns, or a text file with one “Artist - Title” per line.</li>
        </ul></div></div><div style="height:12px"></div>` : ""}
    ${lib.length ? `<h3>Library</h3><div class="list" style="margin-bottom:14px">${lib.map(row).join("")}</div>` : ""}
    ${pls.length ? `<h3>Playlists <span class="muted small">(${pls.length})</span></h3><div class="list">${pls.map(row).join("")}</div>` :
      (tr.source === "file" ? `<div class="empty">No imported files yet.</div>` : `<div class="empty">No playlists found.</div>`)}`;

  area.querySelectorAll("[data-key]").forEach((cb) => cb.onchange = () => { cb.checked ? tr.selected.add(cb.dataset.key) : tr.selected.delete(cb.dataset.key); renderOptions(); });
  area.querySelectorAll("[data-export]").forEach((s) => s.onchange = () => { if (s.value) { location.href = s.value; s.value = ""; } });
  area.querySelectorAll("[data-del-import]").forEach((b) => b.onclick = async () => {
    await api(`/api/import/${b.dataset.delImport}`, { method: "DELETE" }).catch(fail);
    delete S.collCache.file; S.mapCache = {}; viewTransfer();
  });
  const drop = area.querySelector("#drop");
  if (drop) {
    const upload = async (file) => {
      const fd = new FormData(); fd.append("file", file);
      drop.textContent = "Importing…";
      try {
        const r = await api("/api/import", { body: fd });
        const imported = r.imported || [{ kind: "playlist", ...r }];
        const n = imported.reduce((s, x) => s + x.count, 0);
        const replaced = imported.reduce((s, x) => s + (x.replaced || 0), 0);
        toast((imported.length === 1 ? `Imported ${n} items from “${imported[0].name}”` : `Imported ${imported.length} playlists/sections (${n} items)`)
          + (replaced ? ` — replaced ${replaced} older cop${replaced === 1 ? "y" : "ies"}` : ""));
        delete S.collCache.file; S.mapCache = {};
        tr.selected = new Set(imported.map((x) => `${x.kind}|${x.id}`));
        viewTransfer();
      } catch (e) { fail(e); renderCollections(); }
    };
    area.querySelector("#file-in").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); };
  }
  renderOptions();
}

function selectedByKind() {
  const groups = {};
  const coll = S.collCache[S.tr.source] || [];
  for (const key of S.tr.selected) {
    const [kind, ...rest] = key.split("|");
    const id = rest.join("|");
    const c = coll.find((x) => x.kind === kind && x.id === id);
    if (!c) continue;
    (groups[kind] = groups[kind] || []).push(c);
  }
  return groups;
}

function renderOptions() {
  const tr = S.tr;
  const el = $app.querySelector("#opts");
  if (!el) return;
  const g = selectedByKind();
  const nPl = (g.playlist || []).length;
  const tgt = svc(tr.target);
  const parts = [];
  if (nPl > 1) parts.push(`<label class="check"><input type="checkbox" id="o-merge" ${tr.merge ? "checked" : ""}> Merge the ${nPl} playlists into one playlist</label>
    ${tr.merge ? `<input type="text" id="o-name" placeholder="Merged playlist name" value="${esc(tr.name)}" style="margin-left:24px">` : ""}`);
  const mapRows = mappableSelection();
  if (mapRows.length && tr.target) parts.push(mappingTable(mapRows));
  if (nPl || g.liked) parts.push(`<label class="check"><input type="checkbox" id="o-public" ${tr.public ? "checked" : ""}> Make new playlists public</label>`);
  parts.push(`<label class="check"><input type="checkbox" id="o-fuzzy" ${tr.fuzzy ? "checked" : ""}> Strict duplicate detection <span class="muted small">(also treat the same song from a different release/remaster as a duplicate)</span></label>`);
  parts.push(`<label class="check"><input type="checkbox" id="o-mirror" ${tr.mirror ? "checked" : ""}> Mirror: also propose <b>removing</b> items in the target that aren't in the source <span class="muted small">(you can untick each one in the review)</span></label>`);
  el.innerHTML = tgt && tr.target ? `<div class="col">${parts.join("")}</div>` : `<div class="muted">Connect a second service to transfer into.</div>`;
  const on = (id, fn) => { const x = el.querySelector(id); if (x) x.onchange = fn; };
  on("#o-merge", (e) => { tr.merge = e.target.checked; renderOptions(); });
  const name = el.querySelector("#o-name"); if (name) name.oninput = (e) => tr.name = e.target.value;
  el.querySelectorAll("[data-map]").forEach((x) => x.onchange = () => { tr.mapping[x.dataset.map] = x.value; renderOptions(); });
  on("#o-public", (e) => tr.public = e.target.checked);
  on("#o-fuzzy", (e) => tr.fuzzy = e.target.checked);
  on("#o-mirror", (e) => tr.mirror = e.target.checked);
  const total = Object.values(g).reduce((n, a) => n + a.length, 0);
  const sum = $app.querySelector("#sel-summary");
  if (sum) sum.textContent = total ? `${total} selected` : "Nothing selected";
}

// --- mapping source playlists to target playlists (where were they transferred before?)
function currentMapping() { return S.mapCache[`${S.tr.source}>${S.tr.target}`] || null; }

function mapValue(key) {
  if (S.tr.mapping[key]) return S.tr.mapping[key];
  const sug = currentMapping()?.suggestions[key];
  if (!sug) return key.startsWith("liked|") ? "liked" : "auto";
  const t = sug.target;
  return t.kind === "liked" ? "liked" : t.mode === "existing" ? `pl:${t.playlist_id}` : "new";
}

function mapHint(key) {
  const sug = currentMapping()?.suggestions[key];
  if (!sug || !sug.matched) return "";
  const t = sug.target;
  const label = t.kind === "liked" ? `${svcLabel(S.tr.target)} favorites` : `${svcLabel(S.tr.target)} “${t.name}”`;
  return ` · <span style="color:var(--add)" title="Looks previously transferred">↔ ${esc(label)}</span>`;
}

// Track lists that get their own target (everything except a merge of several playlists).
function mappableSelection() {
  const g = selectedByKind();
  const pls = g.playlist || [];
  const merging = S.tr.merge && pls.length > 1;
  return [...(g.liked || []), ...(merging ? [] : pls)];
}

function targetSpec(c) {
  const v = mapValue(`${c.kind}|${c.id}`);
  if (v === "liked") return { kind: "liked" };
  if (v === "new") return { kind: "playlist", mode: "new", name: c.name };
  if (v.startsWith("pl:")) {
    const id = v.slice(3);
    const ch = (currentMapping()?.choices || []).find((x) => x.id === id);
    return { kind: "playlist", mode: "existing", playlist_id: id, name: ch ? ch.name : c.name };
  }
  return { kind: "playlist", name: c.name }; // "auto": exact-name merge decided during analysis
}

function mappingTable(rows) {
  const m = currentMapping();
  const tgt = svcLabel(S.tr.target);
  const choices = (m?.choices || []).slice().sort((a, b) => a.name.localeCompare(b.name));
  const body = rows.map((c) => {
    const key = `${c.kind}|${c.id}`;
    const v = mapValue(key);
    const sug = m?.suggestions[key];
    const alts = sug?.alternatives || [];
    const altIds = new Set(alts.map((a) => a.id));
    const opt = (val, label) => `<option value="${esc(val)}" ${v === val ? "selected" : ""}>${esc(label)}</option>`;
    const sel = `<select data-map="${esc(key)}" style="max-width:100%" aria-label="Target for ${esc(c.name)}">
      ${opt("liked", `★ ${tgt} favorites (liked tracks)`)}
      ${alts.length ? `<optgroup label="Similar names">${alts.map((a) => opt(`pl:${a.id}`, `${a.name} (${a.count}) — ${Math.round(a.score * 100)}% name match`)).join("")}</optgroup>` : ""}
      ${choices.length ? `<optgroup label="All ${esc(tgt)} playlists">${choices.filter((x) => !altIds.has(x.id)).map((x) => opt(`pl:${x.id}`, `${x.name} (${x.count})`)).join("")}</optgroup>` : ""}
      ${opt("new", `+ Create a new playlist “${c.name}”`)}
      ${m ? "" : opt("auto", "Same-named playlist if it exists, else new")}
    </select>`;
    const state = v === "new" ? `<span class="badge st-review">new playlist</span>`
      : v === "liked" ? `<span class="badge st-exists">favorites</span>`
      : v.startsWith("pl:") ? `<span class="badge st-add">${sug && sug.target.playlist_id === v.slice(3) ? "matched by name" : "chosen"}</span>` : "";
    return `<tr><td><b>${esc(c.name)}</b><div class="sub">${c.count ? `${c.count} tracks` : esc(KIND_LABEL[c.kind])}</div></td>
      <td class="arrow">→</td><td style="width:55%">${sel}</td><td>${state}</td></tr>`;
  }).join("");
  return `<div style="margin-bottom:8px"><h3 style="margin-bottom:2px">Compare against</h3>
    <div class="small muted">Each one is checked against the ${esc(tgt)} playlist it was transferred to before (matched by name). The review shows what's <b>missing on ${esc(tgt)}</b>; songs only on ${esc(tgt)} are left alone.</div></div>
    <table class="diff" style="margin-bottom:12px"><tbody>${body}</tbody></table>`;
}

async function startAnalyze() {
  const tr = S.tr;
  const g = selectedByKind();
  if (!Object.keys(g).length) return toast("Select at least one playlist or library item", true);
  if (!tr.target) return toast("Choose a target service", true);
  const btn = $app.querySelector("#analyze"); btn.disabled = true; btn.textContent = "Starting…";
  const options = { public: tr.public, mirror: tr.mirror, fuzzy_dupes: tr.fuzzy };
  const ids = [];
  try {
    const mapRows = mappableSelection();
    if (mapRows.length) {
      const mappings = mapRows.map((c) => ({ kind: c.kind, ref: c.id, name: c.name, target: targetSpec(c) }));
      const r = await api("/api/plans", { body: { source: { service: tr.source }, target: { service: tr.target }, mappings, options } });
      ids.push(...r.plan_ids);
    }
    for (const [kind, colls] of Object.entries(g)) {
      if (mapRows.some((c) => c.kind === kind)) continue;
      const target = { service: tr.target, kind };
      const opt = { ...options };
      if (kind === "playlist" && tr.merge && colls.length > 1) { opt.merge = true; if (tr.name) opt.name = tr.name; }
      const r = await api("/api/plans", { body: { source: { service: tr.source, kind, refs: colls.map((c) => c.id), names: colls.map((c) => c.name) }, target, options: opt } });
      ids.push(...r.plan_ids);
    }
  } catch (e) { fail(e); btn.disabled = false; btn.textContent = "Analyze & preview diff →"; return; }
  tr.selected.clear();
  location.hash = ids.length === 1 ? `#/plan/${ids[0]}` : "#/plans";
}

// ================================================================== PLANS LIST
async function viewPlans() {
  let plans;
  try { plans = (await api("/api/plans")).plans; } catch (e) { $app.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  const busy = plans.some((p) => p.status === "analyzing" || p.status === "applying");
  $app.innerHTML = `
    <div class="row between"><div><h1>Reviews</h1><p class="muted">Each transfer is analyzed into a diff you can inspect and edit before applying.</p></div>
      <div class="row">${plans.length > 1 ? `<button id="plans-dedupe" title="Delete reviews that repeat a newer one. Applied and synced reviews are kept.">Remove duplicate reviews</button>` : ""}
        ${plans.some((p) => !["applied", "analyzing", "applying"].includes(p.status) && !p.sync_id) ? `<button class="danger" id="plans-clear">Delete unapplied…</button>` : ""}
        ${plans.some((p) => p.status === "ready") ? `<button id="apply-all">Apply all ready…</button>` : ""}<a class="btn primary" href="#/">New transfer</a></div></div>
    ${plans.length ? plans.map(planCard).join("") : `<div class="panel empty">No transfers yet. <a href="#/">Start one →</a></div>`}`;
  $app.querySelectorAll("[data-del]").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); if (!confirm("Delete this review?")) return;
    await api(`/api/plans/${b.dataset.del}`, { method: "DELETE" }).catch(fail); viewPlans();
  });
  const pd = $app.querySelector("#plans-dedupe");
  if (pd) pd.onclick = async () => {
    try { const r = await api("/api/plans/dedupe", { body: {} }); toast(r.removed ? `Removed ${r.removed} duplicate review(s)` : "No duplicate reviews"); viewPlans(); } catch (e) { fail(e); }
  };
  const pc = $app.querySelector("#plans-clear");
  if (pc) pc.onclick = async () => {
    const ids = plans.filter((p) => !["applied", "analyzing", "applying"].includes(p.status) && !p.sync_id).map((p) => p.id);
    if (!confirm(`Delete ${ids.length} review(s) that haven't been applied? Applied and synced ones are kept. Nothing on Spotify or Qobuz changes.`)) return;
    try { await api("/api/plans/delete", { body: { ids } }); toast(`Deleted ${ids.length} review(s)`); viewPlans(); } catch (e) { fail(e); }
  };
  const aa = $app.querySelector("#apply-all");
  if (aa) aa.onclick = async () => {
    const ready = plans.filter((p) => p.status === "ready");
    const n = ready.reduce((s, p) => s + ((p.summary || {}).to_add || 0), 0);
    if (!confirm(`Apply ${ready.length} reviewed transfer(s), adding ${n} item(s) in total?`)) return;
    for (const p of ready) await api(`/api/plans/${p.id}/apply`, { body: {} }).catch(fail);
    viewPlans();
  };
  if (busy) pollTimer = setTimeout(viewPlans, 1500);
}

function planTitle(p) {
  const names = p.source.names || [];
  return names.length > 2 ? `${names.slice(0, 2).join(" + ")} +${names.length - 2}` : names.join(" + ") || KIND_LABEL[p.source.kind];
}

function planCard(p) {
  const s = p.summary || {};
  const prog = p.progress || {};
  const pct = prog.total ? Math.round((100 * prog.done) / prog.total) : 5;
  return `<a class="panel" href="#/plan/${p.id}" style="display:block;color:inherit;text-decoration:none">
    <div class="row between">
      <div class="row">${logo(p.source.service)} <b>${esc(planTitle(p))}</b> <span class="muted">→</span> ${logo(p.target.service)}
        <span>${esc(p.target.kind === "playlist" ? (p.target.name || "") : KIND_LABEL[p.target.kind])}</span>
        <span class="badge st-${p.status}">${p.status}</span>${p.sync_id ? `<span class="badge st-exists">synced</span>` : ""}</div>
      <div class="row"><span class="small muted">${fmtTime(p.created_at)}</span><button class="sm ghost danger" data-del="${p.id}">Delete</button></div>
    </div>
    ${p.status === "analyzing" || p.status === "applying" ? `<div style="margin-top:10px"><div class="small muted">${esc(prog.phase || "")} ${prog.total ? `${prog.done}/${prog.total}` : ""}</div><div class="progress"><div style="width:${pct}%"></div></div></div>` : ""}
    ${p.status === "error" ? `<div class="notice err" style="margin:10px 0 0">${esc(p.error)}</div>` : ""}
    ${s.total !== undefined && p.status !== "analyzing" ? `<div class="row small" style="margin-top:8px;gap:12px">
      <span class="badge st-add">${s.to_add} to add</span><span class="badge st-review">${s.review} review</span>
      <span class="badge st-exists">${s.exists} already there</span><span class="badge st-duplicate">${s.duplicate} duplicates</span>
      <span class="badge st-not_found">${s.not_found} not found</span>${s.to_remove ? `<span class="badge st-target_only">${s.to_remove} to remove</span>` : ""}</div>` : ""}
    ${p.result && p.status === "applied" ? `<div class="small muted" style="margin-top:6px">Applied: ${p.result.added} added, ${p.result.removed || 0} removed, ${p.result.skipped_existing} skipped as already present.</div>` : ""}
  </a>`;
}

// ================================================================== PLAN (the diff)
async function viewPlan(id, keepScroll = false) {
  let plan;
  try { plan = await api(`/api/plans/${id}`); } catch (e) { $app.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  if (S.plan?.id !== plan.id) S.planFilter = "missing";
  S.plan = plan;
  renderPlan(keepScroll);
  if (plan.status === "analyzing" || plan.status === "applying") pollTimer = setTimeout(() => viewPlan(id, true), 1000);
}

async function planEdit(op) {
  try { S.plan = await api(`/api/plans/${S.plan.id}/edit`, { body: op }); renderPlan(true); } catch (e) { fail(e); }
}

function itemLabel(d) { return d ? (d.full_title || d.title || d.name || "") : ""; }

function sideHtml(d, type, other) {
  if (!d) return `<span class="muted">—</span>`;
  const link = d.url ? ` <a href="${esc(d.url)}" target="_blank" rel="noopener" title="Open">↗</a>` : "";
  const hires = d.hires ? ` <span class="hires" title="Hi-Res available">HI-RES</span>` : "";
  const unavailable = d.available === false ? ` <span class="badge st-not_found">unavailable</span>` : "";
  if (type === "artist") return `<div class="t">${esc(d.name)}${link}</div>`;
  if (type === "album") {
    return `<div class="t">${esc(d.title)}${hires}${link}${unavailable}</div>
      <div class="sub">${esc((d.artists || []).join(", "))}${d.year ? ` · ${esc(d.year)}` : ""}${d.track_count ? ` · ${d.track_count} tracks` : ""}${d.upc ? ` · <span class="mono">UPC ${esc(d.upc)}</span>` : ""}</div>`;
  }
  let dur = fmtDur(d.duration_ms);
  if (other && other.duration_ms && d.duration_ms && Math.abs(other.duration_ms - d.duration_ms) > 5000) dur = `<span style="color:var(--review)" title="Duration differs">${dur}</span>`;
  return `<div class="t">${esc(itemLabel(d))}${d.explicit ? ` <span class="badge st-only" title="Explicit" style="background:var(--only-bg)">E</span>` : ""}${hires}${link}${unavailable}</div>
    <div class="sub">${esc((d.artists || []).join(", "))}${d.album ? ` · ${esc(d.album)}` : ""}${dur ? ` · ${dur}` : ""}</div>
    ${d.isrc ? `<div class="sub mono">${esc(d.isrc)}</div>` : ""}`;
}

function confHtml(m, status) {
  if (!m) return "";
  const pct = Math.round(m.confidence * 100);
  const color = { add: "var(--add)", review: "var(--review)", exists: "var(--exists)", duplicate: "var(--dup)" }[status] || "var(--muted)";
  return `<span class="conf" title="Match confidence"><span class="bar"><div style="width:${pct}%;background:${color}"></div></span>${pct}% · ${esc(METHOD_LABEL[m.method] || m.method)}</span>`;
}

function filteredItems(plan) {
  const q = S.planSearch.toLowerCase();
  return plan.items.filter((i) => {
    if (S.planFilter === "missing") { if (!["add", "review", "not_found"].includes(i.status)) return false; }
    else if (S.planFilter !== "all" && S.planFilter !== "target_only" && i.status !== S.planFilter) return false;
    if (S.planFilter === "target_only") return false;
    if (!q) return true;
    const hay = [itemLabel(i.source), ...(i.source.artists || []), i.source.album, itemLabel(i.match && i.match.item)].join(" ").toLowerCase();
    return hay.includes(q);
  });
}

function renderPlan(keepScroll) {
  const plan = S.plan;
  const y = window.scrollY;
  const s = plan.summary || {};
  const t = plan.target;
  const busy = plan.status === "analyzing" || plan.status === "applying";
  const prog = plan.progress || {};
  const pct = prog.total ? Math.round((100 * prog.done) / prog.total) : 5;
  const type = plan.item_type;
  const tgtLabel = svcLabel(t.service);
  const where = t.kind === "playlist" ? (t.mode === "existing" ? `the existing ${tgtLabel} playlist “${t.name}”` : `a new ${tgtLabel} playlist “${t.name}”`) : `your ${tgtLabel} ${KIND_LABEL[t.kind].toLowerCase()}`;
  const items = busy ? [] : filteredItems(plan);
  const noun = type === "track" ? "tracks" : type + "s";

  $app.innerHTML = `
    <div class="row between" style="margin-bottom:14px">
      <div>
        <div class="small muted"><a href="#/plans">← Reviews</a></div>
        <h1 class="row">${logo(plan.source.service)} ${esc(planTitle(plan))} <span class="muted">→</span> ${logo(t.service)} ${esc(t.kind === "playlist" ? t.name || "" : KIND_LABEL[t.kind])}
          <span class="badge st-${plan.status}">${plan.status}</span></h1>
        <div class="muted small">${esc(svcLabel(plan.source.service))} ${esc(KIND_LABEL[plan.source.kind].toLowerCase())} → ${esc(where)} · created ${fmtTime(plan.created_at)}</div>
      </div>
      <div class="row">
        ${busy ? "" : `<button id="reanalyze" title="Re-read source and target and re-match">↻ Re-analyze</button>
        <select id="dl"><option value="">Download diff…</option><option value="missing">Missing on target (CSV)</option><option value="all">Full diff (CSV)</option><option value="not_found">Not found (CSV)</option><option value="review">Needs review (CSV)</option></select>`}
      </div>
    </div>
    ${busy ? `<section class="panel"><div class="muted">${esc(prog.phase || "Working")}… ${prog.total ? `${prog.done} / ${prog.total}` : ""}</div><div class="progress" style="margin-top:8px"><div style="width:${pct}%"></div></div></section>` : ""}
    ${plan.error ? `<div class="notice err">${esc(plan.error)}</div>` : ""}
    ${(plan.log || []).map((l) => `<div class="notice">${esc(l)}</div>`).join("")}
    ${plan.status === "applied" && plan.result ? `<div class="notice ok"><b>Done.</b> ${plan.result.created_playlist ? `Created playlist “${esc(plan.result.created_playlist)}”. ` : ""}Added ${plan.result.added}, removed ${plan.result.removed || 0}, skipped ${plan.result.skipped_existing} already present.
      ${(plan.result.errors || []).length ? `<br><span style="color:var(--nf)">${plan.result.errors.map(esc).join("<br>")}</span>` : ""}
      ${t.url ? ` <a href="${esc(t.url)}" target="_blank" rel="noopener">Open on ${esc(tgtLabel)} ↗</a>` : ""}
      <div class="small muted">Edit anything below and apply again to send more; items already there are never duplicated.</div></div>` : ""}
    ${!busy && plan.status !== "error" ? `
    ${t.kind === "playlist" ? `<section class="panel"><h3>Destination</h3>
      <div class="row">
        <label class="check"><input type="radio" name="tmode" value="new" ${t.mode !== "existing" ? "checked" : ""}> New playlist</label>
        <input type="text" id="tname" value="${esc(t.name || "")}" ${t.mode === "existing" ? "disabled" : ""} style="min-width:260px">
        <label class="check" style="margin-left:16px"><input type="radio" name="tmode" value="existing" ${t.mode === "existing" ? "checked" : ""} ${plan.target_choices.length ? "" : "disabled"}> Add to existing</label>
        <select id="texisting" ${t.mode === "existing" ? "" : "disabled"}>${plan.target_choices.map((c) => `<option value="${esc(c.id)}" ${c.id === t.playlist_id ? "selected" : ""}>${esc(c.name)} (${c.count})</option>`).join("")}</select>
      </div></section>` : ""}
    <div class="chips" style="margin-bottom:14px">
      ${[["missing", (s.add || 0) + (s.review || 0) + (s.not_found || 0), `Missing on ${tgtLabel}`], ["all", s.total, "All"], ["add", s.add, "New"], ["review", s.review, "Needs review"], ["exists", s.exists, "Already there"], ["duplicate", s.duplicate, "Duplicates"], ["not_found", s.not_found, "Not found"], ...(t.mode === "existing" || t.kind !== "playlist" ? [["target_only", s.target_only, `Only in ${tgtLabel}`]] : [])]
        .map(([k, n, l]) => `<button class="chip c-${k} ${S.planFilter === k ? "active" : ""}" data-filter="${k}"><span class="num">${n ?? 0}</span><span class="lbl">${l}</span></button>`).join("")}
    </div>
    <section class="panel" style="padding:0">
      <div class="row between" style="padding:12px 14px;border-bottom:1px solid var(--border)">
        <input type="search" id="psearch" placeholder="Search ${noun}…" value="${esc(S.planSearch)}" style="min-width:240px">
        <div class="row">${S.planFilter === "target_only" ? "" : `
          <button class="sm" id="inc-view">Include all shown</button><button class="sm" id="exc-view">Exclude all shown</button>`}
          <button class="sm ghost" id="reset-inc">Reset to suggestions</button></div>
      </div>
      ${S.planFilter === "target_only" ? targetOnlyHtml(plan, type) : items.length ? `
      <table class="diff"><thead><tr><th style="width:28px"></th><th>#</th><th>${esc(svcLabel(plan.source.service))}</th><th></th><th>${esc(tgtLabel)} match</th><th>Status</th><th></th></tr></thead>
      <tbody>${items.map((i) => `
        <tr class="${i.include ? "" : "excluded"}">
          <td><input type="checkbox" data-inc="${i.idx}" ${i.include ? "checked" : ""} ${i.match ? "" : "disabled"} aria-label="Include"></td>
          <td class="idx">${i.idx + 1}</td>
          <td class="side">${sideHtml(i.source, type)}</td>
          <td class="arrow">→</td>
          <td class="side">${sideHtml(i.match && i.match.item, type, i.source)}<div style="margin-top:4px">${confHtml(i.match, i.status)}</div></td>
          <td><span class="badge st-${i.status}">${STATUS_LABEL[i.status]}</span>${i.reason ? `<div class="sub small muted" style="max-width:220px">${esc(i.reason)}</div>` : ""}</td>
          <td><button class="sm" data-change="${i.idx}">${i.match ? "Change" : "Find"}</button></td>
        </tr>`).join("")}</tbody></table>` : `<div class="empty">Nothing here.</div>`}
      ${S.planFilter === "all" && (plan.target_only || []).length ? `<div style="border-top:1px solid var(--border)">${targetOnlyHtml(plan, type)}</div>` : ""}
    </section>
    <div class="footer-bar">
      <div class="grow"><b>${s.to_add} ${noun}</b> will be added to ${esc(where)}${s.to_remove ? `, <b style="color:var(--nf)">${s.to_remove} removed</b>` : ""}.
        <span class="muted small">${s.exists} already there and ${s.duplicate} duplicates are skipped.</span></div>
      ${plan.sync_id ? `<span class="badge st-exists">Kept in sync · <a href="#/syncs">manage</a></span>` : `
      <label class="check small"><input type="checkbox" id="keep"> Keep in sync</label>
      <select id="keep-sched" class="sm"><option value="daily">daily</option><option value="weekly">weekly</option><option value="hourly">hourly</option><option value="monthly">monthly</option><option value="manual">manually</option></select>`}
      <button class="primary" id="apply" ${s.to_add || s.to_remove ? "" : "disabled"}>Apply to ${esc(tgtLabel)}</button>
    </div>` : ""}`;

  if (keepScroll) window.scrollTo(0, y);
  wirePlan(plan);
}

function targetOnlyHtml(plan, type) {
  const rows = plan.target_only || [];
  if (!rows.length) return `<div class="empty">Nothing is only in the target.</div>`;
  return `<div style="padding:12px 14px"><div class="row between"><h3 style="margin:0">Only in ${esc(svcLabel(plan.target.service))} <span class="muted small">(${rows.length})</span></h3>
      <label class="check small"><input type="checkbox" id="mirror" ${plan.options.mirror ? "checked" : ""}> Mirror mode: remove these by default</label></div>
    <p class="small muted">These are already in the target but not in the source. They are kept unless you tick <b>Remove</b>.</p></div>
    <table class="diff"><thead><tr><th style="width:80px">Remove</th><th>${esc(svcLabel(plan.target.service))}</th></tr></thead><tbody>
    ${rows.map((e) => `<tr><td><input type="checkbox" data-rm="${esc(e.item.id)}" ${e.remove ? "checked" : ""} aria-label="Remove"></td><td class="side">${sideHtml(e.item, type)}</td></tr>`).join("")}
    </tbody></table>`;
}

function wirePlan(plan) {
  const q = (sel) => $app.querySelector(sel);
  $app.querySelectorAll("[data-filter]").forEach((b) => b.onclick = () => { S.planFilter = b.dataset.filter; renderPlan(); });
  const ps = q("#psearch");
  if (ps) ps.oninput = (e) => { S.planSearch = e.target.value; const pos = e.target.selectionStart; renderPlan(true); const n = q("#psearch"); n.focus(); n.setSelectionRange(pos, pos); };
  $app.querySelectorAll("[data-inc]").forEach((cb) => cb.onchange = () => planEdit({ op: "include", idx: [Number(cb.dataset.inc)], value: cb.checked }));
  $app.querySelectorAll("[data-rm]").forEach((cb) => cb.onchange = () => planEdit({ op: "remove", ids: [cb.dataset.rm], value: cb.checked }));
  $app.querySelectorAll("[data-change]").forEach((b) => b.onclick = () => openChange(Number(b.dataset.change)));
  const vis = () => filteredItems(plan).filter((i) => i.match).map((i) => i.idx);
  if (q("#inc-view")) q("#inc-view").onclick = () => planEdit({ op: "include", idx: vis(), value: true });
  if (q("#exc-view")) q("#exc-view").onclick = () => planEdit({ op: "include", idx: vis(), value: false });
  if (q("#reset-inc")) q("#reset-inc").onclick = () => planEdit({ op: "reset_includes" });
  if (q("#mirror")) q("#mirror").onchange = (e) => planEdit({ op: "options", mirror: e.target.checked });
  $app.querySelectorAll("[name=tmode]").forEach((r) => r.onchange = () => {
    if (r.value === "new") planEdit({ op: "target", mode: "new", name: q("#tname").value });
    else planEdit({ op: "target", mode: "existing", playlist_id: q("#texisting").value });
  });
  if (q("#tname")) q("#tname").onchange = (e) => planEdit({ op: "target", mode: "new", name: e.target.value });
  if (q("#texisting")) q("#texisting").onchange = (e) => planEdit({ op: "target", mode: "existing", playlist_id: e.target.value });
  if (q("#reanalyze")) q("#reanalyze").onclick = async () => { await api(`/api/plans/${plan.id}/reanalyze`, { body: {} }).catch(fail); viewPlan(plan.id); };
  if (q("#dl")) q("#dl").onchange = (e) => { if (e.target.value) location.href = `/api/plans/${plan.id}/report?which=${e.target.value}`; e.target.value = ""; };
  if (q("#apply")) q("#apply").onclick = async () => {
    const s = plan.summary;
    const msg = `Add ${s.to_add} item(s)${s.to_remove ? ` and REMOVE ${s.to_remove}` : ""} on ${svcLabel(plan.target.service)}?`;
    if (!confirm(msg)) return;
    const keep = q("#keep") && q("#keep").checked ? { schedule: q("#keep-sched").value, auto_apply: true, mirror: !!plan.options.mirror } : null;
    try { await api(`/api/plans/${plan.id}/apply`, { body: { keep_synced: keep } }); viewPlan(plan.id); } catch (e) { fail(e); }
  };
}

function openChange(idx) {
  const plan = S.plan;
  const item = plan.items.find((i) => i.idx === idx);
  const type = plan.item_type;
  const src = item.source;
  const defaultQ = type === "artist" ? src.name : `${(src.artists || [])[0] || ""} ${type === "album" ? src.title : (src.full_title || src.title)}`.trim();
  const candList = (cands) => cands.length ? cands.map((c, n) => `
    <div class="cand ${item.match && item.match.item.id === c.item.id ? "current" : ""}">
      <div class="grow">${sideHtml(c.item, type, src)}<div style="margin-top:4px">${confHtml(c, c.confidence >= 0.85 ? "add" : "review")}</div></div>
      <button class="sm ${item.match && item.match.item.id === c.item.id ? "" : "primary"}" data-pick="${n}">${item.match && item.match.item.id === c.item.id ? "Current" : "Use this"}</button>
    </div>`).join("") : `<div class="empty">No candidates.</div>`;
  let current = item.candidates || [];
  const m = openModal(`
    <div class="row between"><h2 style="margin:0">Choose the ${esc(svcLabel(plan.target.service))} match</h2><button class="ghost" id="mclose" aria-label="Close">✕</button></div>
    <div class="panel" style="margin:12px 0;background:var(--panel-2)"><div class="small muted">Source #${idx + 1}</div>${sideHtml(src, type)}</div>
    <div class="row" style="margin-bottom:12px"><input type="search" id="mq" class="grow" value="${esc(defaultQ)}" placeholder="Search ${esc(svcLabel(plan.target.service))}…"><button id="mgo">Search</button></div>
    <div id="mres">${candList(current)}</div>
    <div class="row between" style="margin-top:12px"><span class="small muted">Your choice is remembered for future transfers and syncs.</span><button class="danger" id="mnone">No match — skip this</button></div>`);
  const wire = () => m.querySelectorAll("[data-pick]").forEach((b) => b.onclick = async () => {
    closeModal(); await planEdit({ op: "set_match", idx, candidate: current[Number(b.dataset.pick)].item });
  });
  wire();
  const search = async () => {
    m.querySelector("#mres").innerHTML = `<div class="empty">Searching…</div>`;
    try { current = (await api(`/api/plans/${plan.id}/search`, { body: { idx, query: m.querySelector("#mq").value } })).candidates; m.querySelector("#mres").innerHTML = candList(current); wire(); }
    catch (e) { m.querySelector("#mres").innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
  };
  m.querySelector("#mgo").onclick = search;
  m.querySelector("#mq").onkeydown = (e) => { if (e.key === "Enter") search(); };
  m.querySelector("#mclose").onclick = closeModal;
  m.querySelector("#mnone").onclick = async () => { closeModal(); await planEdit({ op: "set_match", idx, candidate: null }); };
  m.querySelector("#mq").focus();
}

// ================================================================== SYNCS
async function viewSyncs() {
  let d;
  try { d = await api("/api/syncs"); } catch (e) { $app.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  const cmd = "python -m playlist_transfer sync";
  $app.innerHTML = `
    <h1>Syncs</h1>
    <p class="muted">Keep a target up to date with its source. Create one by ticking <b>Keep in sync</b> when applying a review.
      Unattended runs only add confident matches; anything uncertain waits in a review for you.</p>
    <div class="notice small">Scheduled syncs run while this app is open. To run them in the background, schedule <code>${esc(cmd)}</code> (it runs whatever is due), e.g.
      <code>schtasks /Create /SC DAILY /TN PlaylistTransferSync /TR "${esc(cmd)}"</code> (start it from the project folder, or use the full path to python).</div>
    ${d.syncs.length ? `<section class="panel" style="padding:0"><table class="diff"><thead><tr>
      <th>Sync</th><th>Schedule</th><th>Auto-apply</th><th>Mirror</th><th>Enabled</th><th>Last run</th><th></th></tr></thead><tbody>
      ${d.syncs.map((s) => {
        const r = s.last_result || {};
        const res = r.error ? `<span style="color:var(--nf)">${esc(r.error)}</span>` :
          r.result ? `+${r.result.added}${r.result.removed ? ` −${r.result.removed}` : ""}${r.needs_review ? `, ${r.needs_review} need review` : ""}` :
          r.summary ? `${r.summary.to_add} to add (awaiting review)` : "";
        return `<tr>
          <td>${logo(s.source.service)} → ${logo(s.target.service)} <b>${esc(s.name)}</b><div class="sub small muted">next: ${s.next_run ? fmtTime(s.next_run) : "manual"}</div></td>
          <td><select data-sched="${s.id}">${d.schedules.map((x) => `<option ${x === s.schedule ? "selected" : ""}>${x}</option>`).join("")}</select></td>
          <td><input type="checkbox" data-auto="${s.id}" ${s.auto_apply ? "checked" : ""} aria-label="Auto-apply"></td>
          <td><input type="checkbox" data-mirror="${s.id}" ${s.options.mirror ? "checked" : ""} aria-label="Mirror"></td>
          <td><input type="checkbox" data-en="${s.id}" ${s.enabled ? "checked" : ""} aria-label="Enabled"></td>
          <td class="small">${fmtTime(s.last_run)}<div class="muted">${res}</div>${s.last_plan_id ? `<a href="#/plan/${s.last_plan_id}">open review</a>` : ""}</td>
          <td class="row"><button class="sm" data-run="${s.id}">Run now</button><button class="sm ghost danger" data-sdel="${s.id}">Delete</button></td></tr>`;
      }).join("")}</tbody></table></section>` : `<div class="panel empty">No syncs yet.</div>`}`;
  const patch = (id, body) => api(`/api/syncs/${id}`, { method: "PATCH", body }).then(viewSyncs).catch(fail);
  $app.querySelectorAll("[data-sched]").forEach((x) => x.onchange = () => patch(x.dataset.sched, { schedule: x.value }));
  $app.querySelectorAll("[data-auto]").forEach((x) => x.onchange = () => patch(x.dataset.auto, { auto_apply: x.checked }));
  $app.querySelectorAll("[data-mirror]").forEach((x) => x.onchange = () => patch(x.dataset.mirror, { mirror: x.checked }));
  $app.querySelectorAll("[data-en]").forEach((x) => x.onchange = () => patch(x.dataset.en, { enabled: x.checked }));
  $app.querySelectorAll("[data-run]").forEach((x) => x.onclick = async () => { await api(`/api/syncs/${x.dataset.run}/run`, { body: {} }).catch(fail); toast("Sync started — it will show up under Reviews"); setTimeout(viewSyncs, 2500); });
  $app.querySelectorAll("[data-sdel]").forEach((x) => x.onclick = async () => { if (confirm("Delete this sync?")) { await api(`/api/syncs/${x.dataset.sdel}`, { method: "DELETE" }).catch(fail); viewSyncs(); } });
}

// ================================================================== HISTORY
async function viewHistory() {
  let h;
  try { h = (await api("/api/history")).history; } catch (e) { $app.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  $app.innerHTML = `<h1>History</h1><p class="muted">Every change written to a service.</p>
    ${h.length ? `<section class="panel" style="padding:0"><table class="diff"><thead><tr><th>When</th><th>Transfer</th><th>Added</th><th>Removed</th><th>Skipped (already there)</th><th></th></tr></thead><tbody>
    ${h.map((x) => `<tr><td class="small">${fmtTime(x.at)}</td>
      <td>${logo(x.source.service)} ${esc((x.source.names || []).join(" + ") || KIND_LABEL[x.source.kind])} → ${logo(x.target.service)} ${esc(x.target.name || KIND_LABEL[x.target.kind])}${x.sync_id ? ` <span class="badge st-exists">sync</span>` : ""}
        ${(x.result.errors || []).length ? `<div class="small" style="color:var(--nf)">${x.result.errors.map(esc).join("<br>")}</div>` : ""}</td>
      <td>${x.result.added}</td><td>${x.result.removed || 0}</td><td>${x.result.skipped_existing}</td>
      <td><a href="#/plan/${x.plan_id}">review</a></td></tr>`).join("")}</tbody></table></section>` : `<div class="panel empty">Nothing applied yet.</div>`}`;
}

// ------------------------------------------------------------------ live reload
// The server restarts itself when its code changes; refresh the page when it (or a UI file) changed.
// Waits while you're typing or have a dialog open, so nothing in progress is lost.
(function liveReload() {
  let current = null;
  let pending = false;
  const busy = () => {
    const a = document.activeElement;
    const typing = a && (a.tagName === "TEXTAREA" || a.tagName === "SELECT" || (a.tagName === "INPUT" && !["checkbox", "radio", "file"].includes(a.type)));
    return Boolean($modal.innerHTML) || typing;
  };
  setInterval(async () => {
    try {
      const r = await fetch("/api/dev/version", { cache: "no-store" });
      const { version } = await r.json();
      if (current === null) current = version;
      else if (version !== current) pending = true;
    } catch { return; } // server is mid-restart
    if (pending && !busy()) location.reload();
  }, 1500);
})();

// ------------------------------------------------------------------ boot
(async () => {
  try { await loadServices(); } catch (e) { fail(e); }
  const anyConnected = S.services.some((s) => s.name !== "file" && s.account.connected);
  if (!anyConnected && (location.hash === "" || location.hash === "#/")) location.hash = "#/connections";
  route();
})();
