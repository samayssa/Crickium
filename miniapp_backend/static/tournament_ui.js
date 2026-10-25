/* Crickium Tournaments: list + career, tournament hub (fixtures, table, caps, squads) and the live Auction Room.
   Presentation only - every rule (bids, permissions, results) is decided by the server. */
(function () {
  "use strict";
  const app = () => window.CrickiumApp;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  const ST = { auction: ["AUCTION LIVE", "hot"], live: ["LIVE", "on"], registration: ["JOINING", "soon"], ready: ["READY", "soon"], completed: ["FINISHED", ""] };
  let mode = null;          // "list" | "hub" | "room"
  let tid = 0, hub = null, tab = "fixtures", board = "orange", list = null;
  let poll = null, clock = null, snap = null, snapAt = 0, lastVer = -1, pending = false, confirmEnd = false, skew = 0;

  function stop() { mode = null; clearTimeout(poll); poll = null; clearInterval(clock); clock = null; }
  const root = () => document.getElementById("tn-root");
  const fail = (e) => { if (root()) root().innerHTML = `<div class="error-state"><span>!</span><b>Couldn’t load</b><p>${esc(e.message)}</p><button class="button secondary" data-page="tournaments">Try again</button></div>`; };

  function prizeLine(p) {
    return [p.coins ? `🪙 ${num(p.coins)}` : "", p.rubies ? `💎 ${num(p.rubies)}` : "", p.card ? `🃏 ${esc(p.card)}` : ""].filter(Boolean).join(" · ");
  }
  function prizeBlock(p) {
    if (!p) return "";
    const rows = p.parts.map((x) => `<div><span>${esc(x.role)}</span><b>${x.pct}%</b><small>${[x.coins ? "🪙 " + num(x.coins) : "", x.rubies ? "💎 " + num(x.rubies) : ""].filter(Boolean).join(" · ")}</small></div>`).join("");
    const paid = p.state === "paid" && p.paid.length ? `<div class="tn-paid"><b>✅ Paid out</b>${p.paid.map((x) => `<div><span>${esc(x.name || "Player")}</span><small>${esc((x.roles || []).join(", "))}</small><b>${[x.coins ? "🪙 " + num(x.coins) : "", x.rubies ? "💎 " + num(x.rubies) : ""].filter(Boolean).join(" · ")}</b></div>`).join("")}</div>` : "";
    return `<section class="tn-prize"><div class="tn-prize-head"><span>🎁 PRIZE POOL</span><b>${prizeLine(p)}</b></div>
      <div class="tn-prize-split">${rows}</div>${p.card ? `<small class="tn-prize-card">🃏 ${esc(p.card)} goes to the Champion</small>` : ""}
      <small class="tn-prize-note">${esc(p.split)} · divided automatically when the final ends${p.by_owner ? " · provided by the bot owner" : " · locked by the host"}</small>${paid}</section>`;
  }

  /* ---------------- list ---------------- */
  function cardHtml(c) {
    const [label, cls] = ST[c.status] || [c.status.toUpperCase(), ""];
    return `<article class="tn-card ${c.status}" data-tn-open="${c.id}">
      <div class="tn-card-top"><span class="tn-icon">${esc(c.icon)}</span><div><h3>${esc(c.name)}${c.kind === "official" ? ' <i class="tn-off">OFFICIAL</i>' : ""}</h3>
      <small>${esc(c.title)} · ${c.mode === "auction" ? "Auction" : "Ready squads"} · ${c.teams}/${c.max_teams} teams</small></div>
      <span class="tn-pill ${cls}">${cls === "hot" || cls === "on" ? "<i></i>" : ""}${label}</span></div>
      ${c.prize ? `<div class="tn-prize-chip">🎁 ${prizeLine(c.prize)}</div>` : ""}
      <div class="tn-card-foot"><span>🕔 ${esc(c.window)}</span>${c.my_team ? `<span class="tn-mine">🎽 You: <b>${esc(c.my_team.short)}</b></span>` : c.mine ? '<span class="tn-mine">👑 Host</span>' : ""}</div>
      ${c.status === "auction" ? `<button class="tn-btn gold" data-tn-room="${c.id}">🏛 Enter Auction Room</button>` : ""}
    </article>`;
  }
  function renderList() {
    const k = list.career || {};
    const hero = `<section class="tn-hero"><div class="tn-glow"></div><span class="eyebrow">AUCTION TOURNAMENTS</span><h2>Your tournament career</h2>
      <div class="tn-stats"><div><b>${num(k.played)}</b><small>PLAYED</small></div><div><b>${num(k.won)}</b><small>TITLES</small></div>
      <div><b>${num(k.match_wins)}</b><small>MATCH WINS</small></div><div><b>${num(k.match_losses)}</b><small>MATCH LOSSES</small></div></div></section>`;
    const items = list.items || [];
    const body = items.length ? items.map(cardHtml).join("")
      : `<div class="empty-state"><span class="empty-ball">🏆</span><b>No tournaments yet</b><p>Send /createtour in your group to start one — or join a friend's tournament.</p></div>`;
    root().innerHTML = hero + body;
  }
  async function loadList() {
    stop(); mode = "list";
    app().shell(`<div id="tn-root"><div class="loading-line">Loading tournaments…</div></div>`, "Tournaments", "PREMIUM LEAGUES");
    try { list = await app().api("/api/tournaments"); if (mode === "list" && root()) renderList(); } catch (e) { fail(e); }
  }

  /* ---------------- hub ---------------- */
  const teamTag = (t) => t ? `<b class="tn-t">${esc(t.short)}</b>` : "—";
  function fixtureRow(f) {
    const done = f.status === "done", live = f.status === "live";
    const mid = done ? `<span class="tn-res">${esc(f.result_text || "")}</span>` : live ? `<span class="tn-livechip"><i></i>LIVE</span>` : `<span class="tn-vs">Day ${f.day}</span>`;
    const sc = (s) => s ? `<small>${esc(s)}</small>` : "";
    const win = (t) => done && f.winner_team_no === t?.no ? " win" : "";
    return `<div class="tn-fx ${f.status}"><div class="tn-fx-side${win(f.home)}">${teamTag(f.home)}${sc(f.home_score)}</div><div class="tn-fx-mid">${mid}
      <small>M${f.fno}${f.stage !== "league" ? " · " + esc(f.stage_label) : ""}</small>${live && f.match_id ? `<button class="tn-mini" data-tn-watch="${esc(f.engine)}:${f.match_id}">📺 Watch</button>` : ""}</div>
      <div class="tn-fx-side right${win(f.away)}">${teamTag(f.away)}${sc(f.away_score)}</div></div>`;
  }
  function tabBody() {
    if (tab === "fixtures") {
      const live = hub.fixtures.filter((f) => f.status === "live"), up = hub.fixtures.filter((f) => f.status === "scheduled"), done = hub.fixtures.filter((f) => f.status === "done").reverse();
      const sec = (t, a) => a.length ? `<div class="tn-sec">${t}</div>${a.map(fixtureRow).join("")}` : "";
      return sec("🔴 Live now", live) + sec("📅 Upcoming", up.slice(0, 30)) + sec("✅ Completed", done.slice(0, 40)) || '<p class="tn-empty">Fixtures appear once the tournament starts.</p>';
    }
    if (tab === "table") {
      if (!hub.table.length) return '<p class="tn-empty">The table appears after the first match.</p>';
      return `<div class="tn-table"><div class="tn-tr head"><span>#</span><span>TEAM</span><span>P</span><span>W</span><span>L</span><span>NRR</span><span>PTS</span></div>` +
        hub.table.map((r) => `<div class="tn-tr ${r.pos <= 4 ? "q" : ""} ${hub.my_team && r.team.no === hub.my_team.no ? "me" : ""}"><span>${r.pos}</span><span>${esc(r.team.short)}<small>${esc(r.team.owner || "")}</small></span>
        <span>${r.played}</span><span>${r.won}</span><span>${r.lost}</span><span>${esc(r.nrr)}</span><b>${r.points}</b></div>`).join("") + "</div>";
    }
    if (tab === "stats") {
      const names = [["orange", "🟠 Orange Cap"], ["purple", "🟣 Purple Cap"], ["mvp", "⭐ MVP"], ["hundreds", "💯 100s"], ["fifties", "5️⃣0️⃣ 50s"], ["fastest100", "⚡ Fastest 100"], ["sixes", "🚀 Sixes"], ["highest", "🏏 High score"], ["bestbowl", "🎳 Best bowling"]];
      return `<div class="tn-chips">${names.map(([k, l]) => `<button class="${board === k ? "on" : ""}" data-tn-board="${k}">${l}</button>`).join("")}</div><div id="tn-board"><div class="loading-line">Loading…</div></div>`;
    }
    return `<div id="tn-allsquads"><div class="loading-line">Loading squads…</div></div>`;
  }
  async function loadSquads() {
    try {
      const r = await app().api(`/api/tournaments/${tid}/squads`);
      const el = document.getElementById("tn-allsquads"); if (!el) return;
      el.innerHTML = r.teams.map((t) => {
        const me = hub.my_team && t.no === hub.my_team.no;
        const rows = t.players.map((p) => `<div class="tn-sale"><span>${p.xi ? "⭐ " : ""}${esc(p.role_icon)} ${esc(p.name)}${p.overseas ? " ✈️" : ""}</span><b>${esc(p.price_text)}</b></div>`).join("");
        return `<details class="tn-squadbox ${me ? "me" : ""}" ${me ? "open" : ""}><summary><b>${esc(t.short)}</b><span>${esc(t.name)}</span>
          <small>${esc(t.owner || "No owner")} · ${t.players.length} players · 💰 ${esc(t.purse_text)} left</small></summary>${rows || '<p class="tn-empty">No players yet.</p>'}</details>`;
      }).join("") || '<p class="tn-empty">No teams yet.</p>';
    } catch (_) {}
  }
  function renderHub() {
    const c = hub.tournament, [label, cls] = ST[c.status] || [c.status, ""];
    const tabs = [["fixtures", "Matches"], ["table", "Table"], ["stats", "Stats"], ["teams", "Teams"]];
    const champ = hub.champion ? `<div class="tn-champ">🏆 Champions <b>${esc(hub.champion.short)}</b> <small>${esc(hub.champion.owner || "")}</small>${hub.runner_up ? ` · 🥈 ${esc(hub.runner_up.short)}` : ""}</div>` : "";
    root().innerHTML = `<section class="tn-hero"><div class="tn-glow"></div><span class="eyebrow">${esc(c.icon)} ${esc(c.title)}</span><h2>${esc(c.name)}</h2>
      <div class="tn-hero-row"><span class="tn-pill ${cls}">${cls === "hot" || cls === "on" ? "<i></i>" : ""}${label}</span><span>🕔 ${esc(c.window)}</span><span>👥 ${hub.teams.length}/${c.max_teams}</span></div>${champ}
      ${prizeBlock(c.prize)}
      ${hub.my_team ? `<div class="tn-me">🎽 Your team <b>${esc(hub.my_team.short)}</b> · 💰 ${esc(hub.my_team.purse_text)} · ${hub.my_team.squad} players</div>` : ""}
      ${c.status === "auction" ? `<button class="tn-btn gold" data-tn-room="${c.id}">🏛 Enter Auction Room</button>` : ""}</section>
      <div class="tn-tabs">${tabs.map(([k, l]) => `<button class="${tab === k ? "on" : ""}" data-tn-tab="${k}">${l}</button>`).join("")}</div><div id="tn-body">${tabBody()}</div>`;
    if (tab === "stats") loadBoard();
    if (tab === "teams") loadSquads();
  }
  async function loadBoard() {
    try {
      const r = await app().api(`/api/tournaments/${tid}/board?name=${board}`);
      const el = document.getElementById("tn-board"); if (!el) return;
      el.innerHTML = r.rows.length ? r.rows.map((x, i) => `<div class="tn-lead"><i>${i + 1}</i><div><b>${esc(x.name)}</b><small>${esc(x.team)}</small></div><strong>${esc(x.value ?? x.runs ?? "")}</strong></div>`).join("")
        : '<p class="tn-empty">Nothing yet — play some matches first.</p>';
    } catch (_) {}
  }
  async function openHub(id) {
    stop(); mode = "hub"; tid = id;
    app().shell(`<div id="tn-root"><div class="loading-line">Loading tournament…</div></div>`, "Tournament", "PREMIUM LEAGUES");
    try { hub = await app().api(`/api/tournaments/${id}`); if (mode === "hub" && root()) renderHub(); } catch (e) { fail(e); }
  }

  /* ---------------- auction room ---------------- */
  const leftNow = () => {
    const l = snap?.lot; if (!l || l.seconds_left == null) return null;
    return snap.paused || l.status !== "live" ? l.seconds_left : Math.max(0, l.seconds_left - (Date.now() - snapAt) / 1000);
  };
  function ring(sec) {
    if (sec == null) return "";
    const pct = Math.min(1, sec / 30), cls = sec <= 5 ? "bad" : sec <= 10 ? "warn" : "";
    return `<div class="tn-ring ${cls}" style="--p:${(pct * 100).toFixed(1)}"><b>${Math.ceil(sec)}</b><small>SEC</small></div>`;
  }
  function roomHtml() {
    const s = snap, c = s.tournament, l = s.lot, me = s.my_team;
    const head = `<div class="tn-room-head"><div><span class="eyebrow">🏛 LIVE AUCTION</span><h2>${esc(c.name)}</h2></div>
      <span class="tn-pill ${s.paused ? "" : "hot"}">${s.paused ? "⏸ PAUSED" : s.status === "running" ? "<i></i>LIVE" : esc(s.status.toUpperCase())}</span></div>`;
    const host = s.can_control && ["running", "paused"].includes(s.status) ? (confirmEnd
      ? `<div class="tn-host"><span>End the auction now?</span><button class="tn-btn danger" data-tn-ctl="end">Yes, end</button><button class="tn-btn ghost" data-tn-ctl="no">No</button></div>`
      : `<div class="tn-host"><span>👑 Host</span>${s.paused ? '<button class="tn-btn green" data-tn-ctl="resume">▶ Resume</button>' : '<button class="tn-btn" data-tn-ctl="pause">⏸ Pause</button>'}<button class="tn-btn danger" data-tn-ctl="ask">🛑 End</button></div>`) : "";
    let stage = "";
    if (l) {
      const sec = leftNow();
      const lead = l.leader_short ? `<div class="tn-lead-team"><small>HIGHEST BIDDER</small><b>${esc(l.leader_short)}</b><span>${esc(l.leader_owner || "")}</span></div>` : `<div class="tn-lead-team none"><small>NO BIDS YET</small><b>Base ${esc(l.base_text)}</b></div>`;
      const sold = l.status !== "live";
      const bidBtn = !me ? `<button class="tn-bid" disabled>👀 Spectating</button>`
        : l.can_bid ? `<button class="tn-bid" data-tn-bid="${l.ord}:${l.next_lakh}" ${pending ? "disabled" : ""}>💰 BID ${esc(l.next_text)}</button>`
        : `<button class="tn-bid" disabled>${s.paused ? "⏸ Paused" : sold ? "Lot closed" : l.leader_team_no === me.no ? "✅ You lead" : l.bid_block ? esc({ purse: "Not enough purse", squad_full: "Squad full", overseas_full: "Overseas limit" }[l.bid_block] || "Cannot bid") : "Waiting…"}</button>`;
      stage = `<section class="tn-lot ${l.round > 1 ? "accel" : ""}"><div class="tn-lot-top"><span>${esc(l.set || "")} · ${l.set_pos}/${l.set_size}</span><span>Lot ${l.ord}/${s.total_lots}${l.round > 1 ? " · ⚡ Accelerated" : ""}</span></div>
        <div class="tn-lot-body"><div class="tn-player"><div class="tn-ovr">${l.ovr}<small>OVR</small></div><h3>${esc(l.name)}</h3>
          <p>${esc(l.role_icon)} ${esc(l.role_label)} ${l.country ? "· " + esc(l.country) : ""} ${l.overseas ? "· ✈️" : ""}</p>
          <div class="tn-chips2"><span>🏏 ${l.bat}</span><span>⚡ ${l.bowl}</span><span>${esc(l.bat_hand || "")}</span><span>${esc(l.bowl_hand || "")}</span></div></div>${ring(sec)}</div>
        <div class="tn-price"><div><small>CURRENT PRICE</small><b>${esc(l.cur_text)}</b></div>${lead}</div>${bidBtn}
        <div class="tn-bids">${(l.bids || []).map((b, i) => `<div class="${i === 0 ? "top" : ""}"><b>${esc(b.team)}</b><span>${esc(b.amount_text)}</span></div>`).join("")}</div></section>`;
    } else {
      stage = `<section class="tn-lot idle"><div class="tn-empty">${s.status === "done" ? "🏁 Auction finished" : "⏳ Next player coming up…"}</div>${s.status === "done" ? `<button class="tn-btn gold" data-tn-squads="${c.id}">👥 View all squads &amp; prices</button>` : ""}</section>`;
    }
    const mine = me ? `<div class="tn-me">🎽 <b>${esc(me.short)}</b> · 💰 Purse <b>${esc(me.purse_text)}</b> · 👥 ${me.squad}/${s.squad_max}</div>` : '<div class="tn-me">👀 You are watching — only team owners can bid.</div>';
    const purses = `<div class="tn-sec">Teams</div><div class="tn-purses">${s.teams.map((t) => `<div class="${me && t.no === me.no ? "me" : ""} ${l && l.leader_team_no === t.no ? "lead" : ""}"><b>${esc(t.short)}</b><span>${esc(t.purse_text)}</span><small>${t.squad}/${s.squad_max}</small></div>`).join("")}</div>`;
    const recent = (s.recent || []).length ? `<div class="tn-sec">Recent</div>${s.recent.map((r) => `<div class="tn-sale ${r.status}"><span>${r.status === "sold" ? "✅" : "❌"} ${esc(r.name)}</span><b>${esc(r.status === "sold" ? r.team + " · " + r.price_text : "Unsold")}</b></div>`).join("")}` : "";
    const upc = (s.upcoming || []).length ? `<div class="tn-sec">Up next</div>${s.upcoming.map((r) => `<div class="tn-sale"><span>${esc(r.name)}</span><b>${esc(r.base_text)}</b></div>`).join("")}` : "";
    const cnt = s.counts ? `<div class="tn-counts"><span>✅ ${s.counts.sold} sold</span><span>❌ ${s.counts.unsold} unsold</span><span>⏳ ${s.counts.pending + s.counts.live} left</span></div>` : "";
    return head + host + stage + mine + cnt + purses + recent + upc;
  }
  function paintRoom() { if (mode === "room" && root() && snap) { root().innerHTML = roomHtml(); } }
  async function pollRoom() {
    if (mode !== "room") return;
    try {
      const r = await app().api(`/api/tournaments/${tid}/auction`);
      snap = r; snapAt = Date.now();
      if (r.tournament && r.tournament.status === "live" && r.status === "done") { /* auction over */ }
      const key = JSON.stringify([r.version, r.status, r.lot && r.lot.cur_lakh, r.lot && r.lot.status, r.my_team && r.my_team.purse, r.can_control, confirmEnd, pending]);
      if (key !== lastVer) { lastVer = key; paintRoom(); }
    } catch (e) { if (!snap) fail(e); }
    if (mode === "room") poll = setTimeout(pollRoom, document.hidden ? 4000 : 1000);
  }
  function tickRoom() {
    if (mode !== "room" || !snap || !snap.lot) return;
    const r = document.querySelector(".tn-ring"); if (!r) return;
    const sec = leftNow(); if (sec == null) return;
    r.style.setProperty("--p", (Math.min(1, sec / 30) * 100).toFixed(1));
    r.querySelector("b").textContent = Math.ceil(sec);
    r.classList.toggle("bad", sec <= 5); r.classList.toggle("warn", sec > 5 && sec <= 10);
  }
  async function openRoom(id) {
    stop(); mode = "room"; tid = id; snap = null; lastVer = -1; pending = false; confirmEnd = false;
    app().shell(`<div id="tn-root"><div class="loading-line">Entering the auction room…</div></div>`, "Auction Room", "LIVE BIDDING");
    clock = setInterval(tickRoom, 250);
    await pollRoom();
  }
  async function bid(ord, price) {
    if (pending) return; pending = true; lastVer = -1; paintRoom();
    try {
      const r = await app().api(`/api/tournaments/${tid}/bid`, { method: "POST", body: JSON.stringify({ ord, price }) });
      app().ping("medium"); app().toast(r.message || "Bid placed!");
    } catch (e) { app().ping("error"); app().toast((e.payload && (e.payload.message || e.payload.detail)) || e.message, true); }
    pending = false; lastVer = -1; clearTimeout(poll); await pollRoom();
  }
  async function control(action) {
    try { const r = await app().api(`/api/tournaments/${tid}/control`, { method: "POST", body: JSON.stringify({ action }) }); app().toast(r.message || "Done"); }
    catch (e) { app().toast((e.payload && (e.payload.message || e.payload.detail)) || e.message, true); }
    confirmEnd = false; lastVer = -1; clearTimeout(poll); await pollRoom();
  }

  /* ---------------- events ---------------- */
  document.addEventListener("click", async (ev) => {
    const g = (sel) => ev.target.closest(sel);
    let el;
    if ((el = g("[data-tn-room]"))) { ev.stopPropagation(); openRoom(Number(el.dataset.tnRoom)); return; }
    if ((el = g("[data-tn-squads]"))) { tab = "teams"; openHub(Number(el.dataset.tnSquads)); return; }
    if ((el = g("[data-tn-open]"))) { openHub(Number(el.dataset.tnOpen)); return; }
    if ((el = g("[data-tn-tab]"))) { tab = el.dataset.tnTab; renderHub(); return; }
    if ((el = g("[data-tn-board]"))) { board = el.dataset.tnBoard; renderHub(); return; }
    if ((el = g("[data-tn-watch]"))) { const [e, m] = el.dataset.tnWatch.split(":"); app().openMatch && app().openMatch(e, Number(m)); return; }
    if (false) {
      const r = await app().api(`/api/tournaments/${tid}/squad?team=${el.dataset.tnSquad}`);
      const box = document.getElementById("tn-squad");
      if (box) box.innerHTML = `<div class="tn-sec">Squad</div>` + (r.players.length ? r.players.map((p) => `<div class="tn-sale"><span>${p.xi ? "⭐ " : ""}${esc(p.role_icon)} ${esc(p.name)}</span><b>${esc(p.price_text)}</b></div>`).join("") : '<p class="tn-empty">No players yet.</p>');
      return;
    }
    if ((el = g("[data-tn-bid]"))) { const [o, p] = el.dataset.tnBid.split(":"); bid(Number(o), Number(p)); return; }
    if ((el = g("[data-tn-ctl]"))) {
      const a = el.dataset.tnCtl;
      if (a === "ask") { confirmEnd = true; lastVer = -1; paintRoom(); }
      else if (a === "no") { confirmEnd = false; lastVer = -1; paintRoom(); }
      else control(a);
    }
  });

  window.CrickiumTournament = { load: loadList, stop, openHub, openRoom };
})();
