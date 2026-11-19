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
    if (tab === "myteam") return `<div id="tn-myteam"><div class="loading-line">Loading your team…</div></div>`;
    return `<div id="tn-allsquads"><div class="loading-line">Loading squads…</div></div>`;
  }

  /* ---------------- my team: bought players (+ price) during the auction, squad by role + XI builder after it ---------------- */
  let mt = null, mtXi = new Set(), mtMsg = "";
  const ROLE_ORDER = [["Batsman", "🏏 Batters"], ["Wicketkeeper", "🧤 Wicket-keepers"], ["AllRounder", "🔄 All-rounders"], ["Bowler", "⚡ Bowlers"]];
  function xiProblem() {
    if (!mt) return "";
    const ps = mt.players.filter((p) => mtXi.has(p.pkey)), c = (r) => ps.filter((p) => p.role === r).length;
    if (ps.length !== 11) return `Pick exactly 11 players (${ps.length}/11).`;
    if (c("Batsman") < 1) return "You need at least 1 batter.";
    if (c("Wicketkeeper") < 1) return "You need at least 1 wicket-keeper.";
    if (c("AllRounder") < 2 || c("AllRounder") > 4) return `You need 2–4 all-rounders (you have ${c("AllRounder")}).`;
    if (c("Bowler") < 3 || c("Bowler") > 4) return `You need 3–4 bowlers (you have ${c("Bowler")}).`;
    const om = Number(mt.xi_overseas_max || 0);
    if (om && ps.filter((p) => p.overseas).length > om) return `At most ${om} overseas players in the XI.`;
    return "";
  }
  function myTeamHtml() {
    if (!mt.has_team) return '<p class="tn-empty">You do not have a team in this tournament.</p>';
    const tm = mt.team, auctionPhase = mt.phase === "auction", total = mt.squad_max;
    const logo = tm.logo ? app().image(tm.logo, tm.name, "tn-logo") : `<span class="tn-logo ph">${esc((tm.short || "T").slice(0, 2))}</span>`;
    const pct = Math.min(100, Math.round(100 * mt.players.length / Math.max(1, total)));
    const rl = mt.rules && mt.rules.roles ? mt.rules.roles : [];
    const rolebar = `<div class="tn-rules">${rl.map((r) => { const n = (mt.counts || {})[r.role] || 0; return `<span class="${n < r.min ? "need" : n >= r.max ? "full" : ""}" title="${esc(r.label)}">${esc(r.icon)} ${n} · ${r.min}–${r.max}</span>`; }).join("")}${mt.rules.overseas ? `<span>✈️ ${mt.players.filter((p) => p.overseas).length} · ${mt.rules.overseas[0]}–${mt.rules.overseas[1] || "any"}</span>` : ""}</div>`;
    const head = `<section class="tn-myhead">${logo}<div class="tn-myhead-t"><h3>${esc(tm.name)}</h3><small>${esc(tm.owner || "")}${mt.cobidder ? " · 🤝 Co-bidder " + esc(mt.cobidder.name || "") : ""}</small></div></section>
      <div class="tn-mystats"><div><small>PURSE LEFT</small><b>${esc(tm.purse_text)}</b><i>of ${esc(mt.purse_total_text)}</i></div><div><small>SPENT</small><b>${esc(tm.spent_text)}</b></div>
      <div><small>SQUAD</small><b>${mt.players.length}/${total}</b><i>min ${mt.squad_min}</i></div></div>
      <div class="tn-progress"><span style="width:${pct}%"></span></div>${rolebar}${mt.need ? `<div class="tn-howto">⚠️ Still must sign: ${esc(mt.need)}</div>` : ""}`;
    const canEdit = mt.can_edit_xi;
    let body = "";
    if (!mt.players.length) body = '<p class="tn-empty">No player bought yet — win a lot in the auction and it shows up here.</p>';
    for (const [role, label] of ROLE_ORDER) {
      const rows = mt.players.filter((p) => p.role === role);
      if (!rows.length) continue;
      body += `<div class="tn-sec">${label} · ${rows.length}</div>` + rows.map((p) => {
        const on = mtXi.has(p.pkey);
        const right = auctionPhase ? `<b>${esc(p.price_text || "")}</b>` : `<span class="tn-ovr-chip">🏏 ${p.bat} · ⚡ ${p.bowl}</span>`;
        return `<div class="tn-sale tn-pl ${canEdit ? "pick" : ""} ${canEdit && on ? "on" : ""}" ${canEdit ? `data-tn-xi="${esc(p.pkey)}"` : ""}><span>${canEdit ? (on ? "✅ " : "⬜ ") : ""}${esc(p.name)}${p.overseas ? " ✈️" : ""}<small>${esc(p.country || "")} · OVR ${p.ovr}</small></span>${right}</div>`;
      }).join("");
    }
    let xi = "";
    if (!auctionPhase) {
      if (canEdit) {
        const why = xiProblem();
        xi = `<div class="tn-xibar ${why ? "bad" : "ok"}"><b>Playing XI ${mtXi.size}/11</b><span>${why ? esc(why) : "✅ Valid XI — ready to save"}</span></div>
          <div class="tn-xibtns"><button class="tn-btn gold" data-tn-xisave ${why ? "disabled" : ""}>💾 Save Playing XI</button><button class="tn-btn" data-tn-xiauto>✨ Auto-pick best XI</button></div>
          <small class="tn-prize-note">Rules: 11 players · 1+ batter · 1+ keeper · 2–4 all-rounders · 3–4 bowlers${mt.xi_overseas_max ? " · max " + mt.xi_overseas_max + " overseas" : ""}. Tap players to add or remove them.</small>`;
      } else if (mt.xi_picked_by_captain) {
        xi = '<div class="tn-howto">🏏 In this tournament you choose your Playing XI in the match itself.</div>';
      }
      if (mtMsg) xi += `<div class="tn-howto">${esc(mtMsg)}</div>`;
    }
    return head + xi + body;
  }
  async function loadMyTeam() {
    try {
      mt = await app().api(`/api/tournaments/${tid}/myteam`);
      mtXi = new Set((mt.players || []).filter((p) => p.xi).map((p) => p.pkey));
      paintMyTeam();
    } catch (e) { const el = document.getElementById("tn-myteam"); if (el) el.innerHTML = `<p class="tn-empty">${esc(e.message)}</p>`; }
  }
  function paintMyTeam() {
    const el = document.getElementById("tn-myteam"); if (!el || !mt) return;
    el.innerHTML = myTeamHtml();
    try { app().hydrateImages(el); } catch (_) {}
  }
  async function saveXi(auto) {
    try {
      const r = await app().api(`/api/tournaments/${tid}/xi`, { method: "POST", body: JSON.stringify(auto ? { auto: true } : { pkeys: [...mtXi] }) });
      mtMsg = r.message || "Saved";
      if (auto && r.pkeys) mtXi = new Set(r.pkeys);
      app().toast(mtMsg);
    } catch (e) { mtMsg = (e.payload && (e.payload.message || e.payload.detail)) || e.message; app().toast(mtMsg, true); }
    paintMyTeam();
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
    const tabs = [["fixtures", "Matches"], ["table", "Table"], ["stats", "Stats"]].concat(hub.my_team ? [["myteam", "My Team"]] : [], [["teams", "Squads"]]);
    const champ = hub.champion ? `<div class="tn-champ">🏆 Champions <b>${esc(hub.champion.short)}</b> <small>${esc(hub.champion.owner || "")}</small>${hub.runner_up ? ` · 🥈 ${esc(hub.runner_up.short)}` : ""}</div>` : "";
    root().innerHTML = `<section class="tn-hero"><div class="tn-glow"></div><span class="eyebrow">${esc(c.icon)} ${esc(c.title)}</span><h2>${esc(c.name)}</h2>
      <div class="tn-hero-row"><span class="tn-pill ${cls}">${cls === "hot" || cls === "on" ? "<i></i>" : ""}${label}</span><span>🕔 ${esc(c.window)}</span><span>👥 ${hub.teams.length}/${c.max_teams}</span></div>${champ}
      ${prizeBlock(c.prize)}
      ${hub.my_team ? `<div class="tn-me">🎽 Your team <b>${esc(hub.my_team.short)}</b> · 💰 ${esc(hub.my_team.purse_text)} · ${hub.my_team.squad} players</div>` : ""}
      ${c.status === "auction" ? `<button class="tn-btn gold" data-tn-room="${c.id}">🏛 Enter Auction Room</button>` : ""}</section>
      <div class="tn-tabs">${tabs.map(([k, l]) => `<button class="${tab === k ? "on" : ""}" data-tn-tab="${k}">${l}</button>`).join("")}</div><div id="tn-body">${tabBody()}</div>`;
    if (tab === "stats") loadBoard();
    if (tab === "teams") loadSquads();
    if (tab === "myteam") loadMyTeam();
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
  const bidSecs = () => Number(snap?.bid_seconds) || 18;
  function ring(sec) {
    if (sec == null) return "";
    const pct = Math.min(1, sec / bidSecs()), cls = sec <= 5 ? "bad" : sec <= 10 ? "warn" : "";
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
      const sec = l.status === "live" ? leftNow() : null;
      const lead = l.leader_short ? `<div class="tn-lead-team"><small>HIGHEST BIDDER</small><b>${esc(l.leader_short)}</b><span>${esc(l.leader_owner || "")}</span></div>` : `<div class="tn-lead-team none"><small>NO BIDS YET</small><b>Base ${esc(l.base_text)}</b></div>`;
      const sold = l.status !== "live";
      const banner = l.status === "sold"
        ? `<div class="tn-sold"><b>🔨 SOLD</b><span>to <b>${esc(l.sold_team || "")}</b>${l.sold_owner ? " · " + esc(l.sold_owner) : ""}</span><strong>${esc(l.sold_text || l.cur_text)}</strong><small>Base ${esc(l.base_text)} · next player in a moment…</small></div>`
        : l.status === "unsold" ? `<div class="tn-sold unsold"><b>❌ UNSOLD</b><span>No bids · base ${esc(l.base_text)}</span><small>Next player in a moment…</small></div>` : "";
      const bidBtn = banner || `<div class="tn-howto">💬 ${me ? `Send <b>/bid</b> in the group to bid ${esc(l.next_text)}` : "Team owners bid with <b>/bid</b> in the group"}</div>`;
      stage = `<section class="tn-lot ${l.round > 1 ? "accel" : ""}"><div class="tn-lot-top"><span>${esc(l.set || "")} · ${l.set_pos}/${l.set_size}</span><span>Lot ${l.ord}/${s.total_lots}${l.round > 1 ? " · ⚡ Accelerated" : ""}</span></div>
        <div class="tn-lot-body"><div class="tn-player"><div class="tn-ovr">${l.ovr}<small>OVR</small></div><h3>${esc(l.name)}</h3>
          <p>${esc(l.role_icon)} ${esc(l.role_label)} ${l.country ? "· " + esc(l.country) : ""} ${l.overseas ? "· ✈️" : ""}</p>
          <div class="tn-chips2"><span>🏏 ${l.bat}</span><span>⚡ ${l.bowl}</span><span>${esc(l.bat_hand || "")}</span><span>${esc(l.bowl_hand || "")}</span></div></div>${ring(sec)}</div>
        <div class="tn-price"><div><small>${sold && l.status === "sold" ? "SOLD PRICE" : "CURRENT PRICE"}</small><b>${esc(l.status === "sold" && l.sold_text ? l.sold_text : l.cur_text)}</b></div>${lead}</div>${bidBtn}
        <div class="tn-bids">${(l.bids || []).map((b, i) => `<div class="${i === 0 ? "top" : ""}"><b>${esc(b.team)}</b><span>${esc(b.amount_text)}</span></div>`).join("")}</div></section>`;
    } else {
      stage = `<section class="tn-lot idle"><div class="tn-empty">${s.status === "done" ? "🏁 Auction finished" : "⏳ Next player coming up…"}</div>${s.status === "done" ? `<button class="tn-btn gold" data-tn-squads="${c.id}">👥 View all squads &amp; prices</button>` : ""}</section>`;
    }
    const mine = me ? `<div class="tn-me">🎽 <b>${esc(me.short)}</b> · 💰 Purse <b>${esc(me.purse_text)}</b> · 👥 ${me.squad}/${s.squad_max}</div>` : '<div class="tn-me">👀 You are watching — only team owners can bid.</div>';
    const rl = s.rules && s.rules.roles ? s.rules.roles : [];
    const mr = s.my_roles || {};
    const roleBar = rl.length ? `<div class="tn-rules">${rl.map((r) => { const n = mr[r.role] || 0; const cls = me && n < r.min ? "need" : (me && n >= r.max ? "full" : ""); return `<span class="${cls}" title="${esc(r.label)}">${esc(r.icon)} ${me ? n + " · " : ""}${r.min}–${r.max}</span>`; }).join("")}${s.rules.overseas ? `<span title="Overseas">✈️ ${me ? (me.overseas || 0) + " · " : ""}${s.rules.overseas[0]}–${s.rules.overseas[1] || "any"}</span>` : ""}</div>` : "";
    const note = s.bid_note ? `<div class="tn-howto">${esc(s.bid_note)}</div>` : "";
    const purses = `<div class="tn-sec">Teams</div><div class="tn-purses">${s.teams.map((t) => `<div class="${me && t.no === me.no ? "me" : ""} ${l && l.leader_team_no === t.no ? "lead" : ""}"><b>${esc(t.short)}</b><span>${esc(t.purse_text)}</span><small>${t.squad}/${s.squad_max}</small></div>`).join("")}</div>`;
    const recent = (s.recent || []).length ? `<div class="tn-sec">Recent</div>${s.recent.map((r) => `<div class="tn-sale ${r.status}"><span>${r.status === "sold" ? "✅" : "❌"} ${esc(r.name)}</span><b>${esc(r.status === "sold" ? r.team + " · " + r.price_text : "Unsold")}</b></div>`).join("")}` : "";
    const upc = (s.upcoming || []).length ? `<div class="tn-sec">Up next</div>${s.upcoming.map((r) => `<div class="tn-sale"><span>${esc(r.name)}</span><b>${esc(r.base_text)}</b></div>`).join("")}` : "";
    const cnt = s.counts ? `<div class="tn-counts"><span>✅ ${s.counts.sold} sold</span><span>❌ ${s.counts.unsold} unsold</span><span>⏳ ${s.counts.pending + s.counts.live} left</span></div>` : "";
    return head + host + stage + note + mine + roleBar + cnt + purses + recent + upc;
  }
  function paintRoom() { if (mode === "room" && root() && snap) { root().innerHTML = roomHtml(); } }
  async function pollRoom() {
    if (mode !== "room") return;
    try {
      const r = await app().api(`/api/tournaments/${tid}/auction`);
      snap = r; snapAt = Date.now();
      if (r.tournament && r.tournament.status === "live" && r.status === "done") { /* auction over */ }
      const key = JSON.stringify([r.version, r.status, r.lot && r.lot.cur_lakh, r.lot && r.lot.status, r.my_team && r.my_team.purse, r.can_control, confirmEnd, r.lot && r.lot.leader_team_no]);
      if (key !== lastVer) { lastVer = key; paintRoom(); }
    } catch (e) { if (!snap) fail(e); }
    if (mode === "room") poll = setTimeout(pollRoom, document.hidden ? 4000 : 1000);
  }
  function tickRoom() {
    if (mode !== "room" || !snap || !snap.lot) return;
    const r = document.querySelector(".tn-ring"); if (!r) return;
    const sec = leftNow(); if (sec == null) return;
    r.style.setProperty("--p", (Math.min(1, sec / bidSecs()) * 100).toFixed(1));
    r.querySelector("b").textContent = Math.ceil(sec);
    r.classList.toggle("bad", sec <= 5); r.classList.toggle("warn", sec > 5 && sec <= 10);
  }
  async function openRoom(id) {
    stop(); mode = "room"; tid = id; snap = null; lastVer = -1; pending = false; confirmEnd = false;
    app().shell(`<div id="tn-root"><div class="loading-line">Entering the auction room…</div></div>`, "Auction Room", "LIVE BIDDING");
    clock = setInterval(tickRoom, 250);
    await pollRoom();
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
    if ((el = g("[data-tn-xi]"))) { const k = el.dataset.tnXi; if (mtXi.has(k)) mtXi.delete(k); else if (mtXi.size < 11) mtXi.add(k); else app().toast("Your XI already has 11 players — remove one first.", true); mtMsg = ""; paintMyTeam(); return; }
    if (g("[data-tn-xisave]")) { if (!xiProblem()) saveXi(false); return; }
    if (g("[data-tn-xiauto]")) { saveXi(true); return; }
    if ((el = g("[data-tn-tab]"))) { tab = el.dataset.tnTab; renderHub(); return; }
    if ((el = g("[data-tn-board]"))) { board = el.dataset.tnBoard; renderHub(); return; }
    if ((el = g("[data-tn-watch]"))) { const [e, m] = el.dataset.tnWatch.split(":"); app().openMatch && app().openMatch(e, Number(m)); return; }
    if (false) {
      const r = await app().api(`/api/tournaments/${tid}/squad?team=${el.dataset.tnSquad}`);
      const box = document.getElementById("tn-squad");
      if (box) box.innerHTML = `<div class="tn-sec">Squad</div>` + (r.players.length ? r.players.map((p) => `<div class="tn-sale"><span>${p.xi ? "⭐ " : ""}${esc(p.role_icon)} ${esc(p.name)}</span><b>${esc(p.price_text)}</b></div>`).join("") : '<p class="tn-empty">No players yet.</p>');
      return;
    }
    if ((el = g("[data-tn-ctl]"))) {
      const a = el.dataset.tnCtl;
      if (a === "ask") { confirmEnd = true; lastVer = -1; paintRoom(); }
      else if (a === "no") { confirmEnd = false; lastVer = -1; paintRoom(); }
      else control(a);
    }
  });

  window.CrickiumTournament = { load: loadList, stop, openHub, openRoom };
})();
