/* Crickium "Play Game" - user vs computer match mode.
   Presentation + input only. Every result, probability and AI decision is
   produced by the server (/api/game/*); this file never decides an outcome. */
(function () {
  "use strict";
  const app = () => window.CrickiumApp;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const ping = (k) => { try { window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(k || "light"); } catch (_) {} };
  const buzz = (k) => { try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred(k || "success"); } catch (_) {} };

  const G = {
    cfg: null, hub: null, view: null, busy: false, tab: "live", card: 0, sel: null, skip: false,
    setup: { difficulty: "medium", overs: 5, sim: "ball", pitch: "random" },
    toss: null, tossShow: false, alive: false, revealing: false,
  };

  /* ------------------------------------------------------------------ io */
  async function post(route, body) {
    return app().api(`/api/game/${route}`, { method: "POST", body: JSON.stringify(body || {}) });
  }
  function toast(msg, bad) { app().toast(msg, bad); }
  function shell(html, title, eyebrow) { app().shell(html, title || "Play Game", eyebrow || "VS COMPUTER"); }
  function img(url, alt, cls) { return url ? app().image(url, alt, cls || "gm-avimg") : ""; }
  function hydrate() { try { app().hydrateImages(); } catch (_) {} }
  const initials = (n) => String(n || "?").trim().split(/\s+/).slice(0, 2).map((p) => p[0]).join("").toUpperCase();

  function avatar(p, cls) {
    return `<span class="gm-av ${cls || ""}"><b>${esc(initials(p.name))}</b>${img(p.img, p.name)}</span>`;
  }

  /* --------------------------------------------------------------- entry */
  async function load() {
    G.alive = true;
    shell(`<div class="skeleton hero-skeleton"></div><div class="skeleton"></div>`);
    try {
      const [cfg, st] = await Promise.all([G.cfg ? Promise.resolve(G.cfg) : app().api("/api/game/config"), app().api("/api/game/state")]);
      G.cfg = cfg;
      G.hub = st;
      G.view = st.game || null;
      G.sel = null;
      if (G.view && G.view.stage !== "done") { G.tab = "live"; G.tossShow = false; render(); }
      else { G.view = null; renderHub(); }
    } catch (e) {
      shell(`<div class="panel empty-state"><b>Play Game is unavailable</b><p>${esc(e.message || "Please try again.")}</p><button class="button primary" data-page="game">Retry</button></div>`);
    }
  }
  function stop() { G.alive = false; }

  function render() {
    const v = G.view;
    if (!v) return renderHub();
    if (G.toss && G.toss.phase === "flip") return renderTossFlip();
    if (G.tossShow) return renderTossResult();
    if (v.stage === "xi") return renderXI();
    if (v.stage === "toss") return renderToss();
    if (v.stage === "decision") return renderTossResult();
    return renderMatch();
  }

  /* ------------------------------------------------------------ hub/setup */
  const STARS = (n) => "★".repeat(n) + `<i>${"★".repeat(Math.max(0, 4 - n))}</i>`;
  const DIFF_ICON = { easy: "🌱", medium: "⚖️", hard: "🔥", expert: "💀" };

  function careerStrip(s) {
    if (!s || !s.games) return `<div class="gm-career empty"><b>No matches yet</b><small>Your career record starts with your first game.</small></div>`;
    const cells = [["PLAYED", s.games], ["WON", s.wins], ["WIN %", `${Math.round(s.win_pct || 0)}%`], ["BEST", s.high_score], ["STREAK", s.best_streak]];
    return `<div class="gm-career">${cells.map(([l, v]) => `<div><b>${esc(v)}</b><small>${l}</small></div>`).join("")}</div>`;
  }

  function recentList(rows) {
    if (!rows || !rows.length) return "";
    return `<section class="gm-sec"><div class="gm-sec-head"><span>RECENT MATCHES</span></div>
      ${rows.map((r) => {
        const o = r.outcome === "win" ? "W" : r.outcome === "loss" ? "L" : "T";
        const you = r.forfeit ? "—" : `${r.user_runs}/${r.user_wkts}`;
        const ai = r.forfeit ? "—" : `${r.ai_runs}/${r.ai_wkts}`;
        return `<div class="gm-recent ${esc(r.outcome)}"><span class="gm-res">${o}</span>
          <div><b>vs ${esc(r.opponent || "Computer")}</b><small>${esc(r.summary?.text || (r.forfeit ? "Forfeited" : ""))}</small></div>
          <div class="gm-recent-score"><b>${you}</b><small>${ai} · ${r.overs} ov · ${esc(r.difficulty)}</small></div></div>`;
      }).join("")}</section>`;
  }

  function setupForm() {
    const c = G.cfg, s = G.setup;
    const diffs = c.difficulties.map((d) => `<button class="gm-diff ${s.difficulty === d.key ? "on" : ""} d-${d.key}" data-g="setup" data-k="difficulty" data-v="${d.key}">
        <span class="gm-diff-ico">${DIFF_ICON[d.key] || "🏏"}</span><b>${esc(d.label)}</b><span class="gm-stars">${STARS(d.stars)}</span><small>${esc(d.tagline)}</small></button>`).join("");
    const perks = (c.difficulties.find((d) => d.key === s.difficulty)?.perks || []).map((p) => `<li>${esc(p)}</li>`).join("");
    const overs = c.overs.map((o) => `<button class="gm-chip ${s.overs === o.value ? "on" : ""}" data-g="setup" data-k="overs" data-v="${o.value}"><b>${o.value}</b><small>OVERS</small></button>`).join("");
    const sims = c.sims.map((m) => `<button class="gm-opt ${s.sim === m.key ? "on" : ""}" data-g="setup" data-k="sim" data-v="${m.key}"><b>${m.key === "ball" ? "🎯" : "⏩"} ${esc(m.label)}</b><small>${esc(m.hint)}</small></button>`).join("");
    const pitches = [{ key: "random", label: "Random", emoji: "🎲", desc: "Surprise me" }, ...c.pitches].map((p) => `<button class="gm-pitch ${s.pitch === p.key ? "on" : ""}" data-g="setup" data-k="pitch" data-v="${p.key}"><span>${p.emoji}</span><b>${esc(p.label)}</b></button>`).join("");
    const pit = c.pitches.find((p) => p.key === s.pitch);
    const q = c.overs.find((o) => o.value === s.overs)?.quota;
    return `<section class="gm-sec"><div class="gm-sec-head"><span>1 · DIFFICULTY</span></div><div class="gm-diffs">${diffs}</div><ul class="gm-perks">${perks}</ul></section>
      <section class="gm-sec"><div class="gm-sec-head"><span>2 · MATCH LENGTH</span><em>${q} over${q === 1 ? "" : "s"} max per bowler</em></div><div class="gm-chips">${overs}</div></section>
      <section class="gm-sec"><div class="gm-sec-head"><span>3 · SIMULATION</span></div><div class="gm-opts">${sims}</div></section>
      <section class="gm-sec"><div class="gm-sec-head"><span>4 · PITCH</span></div><div class="gm-pitches">${pitches}</div>
        <p class="gm-note">${pit ? `${esc(pit.desc)}. ${esc(pit.tip)}` : "A random pitch is picked for you and shown before the toss."}</p></section>
      <button class="gm-cta" data-g="start">PLAY GAME <span>›</span></button>`;
  }

  function resumeCard(sum) {
    if (!sum) return "";
    const live = sum.score ? `${esc(sum.score)} (${esc(sum.overs_text)} ov)` : "Match set up";
    const stage = { xi: "Choose your Playing XI", toss: "Toss time", decision: "Toss won – choose bat or bowl", play: "In progress", break: "Innings break" }[sum.stage] || "In progress";
    return `<section class="gm-resume"><div><span class="eyebrow">MATCH IN PROGRESS</span><h3>${esc(sum.user)} vs ${esc(sum.ai)}</h3>
      <p>${esc(sum.difficulty.toUpperCase())} · ${sum.overs} ov · ${live}</p><small>${stage}</small></div>
      <div class="gm-resume-actions"><button class="gm-cta small" data-g="resume">RESUME ›</button><button class="gm-ghost" data-g="abandon">Abandon</button></div></section>`;
  }

  function renderHub() {
    const st = G.hub || {};
    const has = !!(G.view && G.view.stage !== "done");
    shell(`<section class="gm-hero"><div class="gm-hero-glow"></div><div class="gm-hero-ball"></div>
        <span class="eyebrow">CRICKIUM ARENA</span><h2>Play Game</h2><p>Your Playing XI against the computer. Choose the difficulty, the format and the pitch – then outsmart it.</p></section>
      ${careerStrip(st.stats)}
      ${has ? resumeCard(st.summary || {}) : ""}
      ${setupForm()}
      ${recentList(st.recent)}`);
    hydrate();
  }

  async function startGame(replace) {
    if (G.busy) return;
    G.busy = true;
    try {
      const r = await post("start", { ...G.setup, replace: !!replace });
      G.view = r.game; G.tab = "live"; G.sel = null; G.toss = null; G.tossShow = false;
      render();
    } catch (e) {
      if (e.status === 409 && e.payload?.status === "active_game") {
        confirmBox("You already have a match in progress. Starting a new one will forfeit it.", "Forfeit & start new", () => startGame(true));
      } else toast(e.message, true);
    } finally { G.busy = false; }
  }

  /* ------------------------------------------------------------- dialogs */
  function confirmBox(text, okLabel, fn) {
    const host = document.createElement("div");
    host.className = "gm-modal";
    host.innerHTML = `<div class="gm-modal-card"><p>${esc(text)}</p><div><button class="gm-ghost" data-m="no">Cancel</button><button class="gm-cta small" data-m="yes">${esc(okLabel)}</button></div></div>`;
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-m]");
      if (!b && e.target !== host) return;
      host.remove();
      if (b && b.dataset.m === "yes") fn();
    });
    document.body.appendChild(host);
  }

  /* ------------------------------------------------------------- XI stage */
  function xiRow(p, side) {
    const sel = G.sel && G.sel.pid === p.pid;
    const bowl = p.can_bowl ? `<span class="gm-tag">🎯 ${esc(p.bw_label || "Bowls")}</span>` : "";
    const tools = side === "xi" ? `<span class="gm-mv"><button data-g="mv" data-pid="${p.pid}" data-dir="-1" aria-label="Move up">▲</button><button data-g="mv" data-pid="${p.pid}" data-dir="1" aria-label="Move down">▼</button></span>` : "";
    return `<div class="gm-xi-row ${sel ? "sel" : ""} r-${esc(String(p.rarity || "").toLowerCase())}" data-g="pick" data-side="${side}" data-pid="${p.pid}">
      <span class="gm-pos">${side === "xi" ? p.pos : "SUB"}</span>${avatar(p)}
      <div class="gm-xi-main"><b>${esc(p.name)}</b><small>${p.role_icon} ${esc(p.role_label)}${p.bh ? ` · ${esc(p.bh)}` : ""}</small>${bowl}</div>
      <div class="gm-xi-rate"><span title="Batting"><i>BAT</i><b>${p.bat}</b></span><span title="Bowling"><i>BWL</i><b>${p.bowl}</b></span></div>${tools}</div>`;
  }

  function renderXI() {
    const x = G.view.xi, s = x.summary;
    const warns = s.warnings.map((w) => `<div class="gm-warn">⚠ ${esc(w)}</div>`).join("");
    const hint = G.sel ? (G.sel.side === "xi" ? "Now tap a substitute to bring in." : "Now tap a Playing XI player to replace.") : "Tap a player, then a player from the other list to swap. Use ▲▼ to change the batting order.";
    shell(`<section class="gm-brief"><div><span class="eyebrow">${esc(G.view.cfg.difficulty_label.toUpperCase())} · ${G.view.cfg.overs} OVERS · ${esc(G.view.cfg.sim === "ball" ? "BALL BY BALL" : "OVER BY OVER")}</span>
        <h3>${esc(G.view.teams.user.name)} <em>vs</em> ${esc(G.view.teams.ai.name)}</h3><small>${G.view.cfg.pitch_emoji} ${esc(G.view.cfg.pitch_label)} pitch · ${esc(G.view.cfg.pitch_tip)}</small></div></section>
      <div class="gm-xi-stats"><div><b>${s.bat_avg}</b><small>BAT AVG</small></div><div><b>${s.bowl_avg}</b><small>BOWL AVG</small></div><div><b>${s.bowlers}</b><small>BOWLERS</small></div><div><b>${s.keepers}</b><small>KEEPERS</small></div><div><b>${s.overall}</b><small>OVERALL</small></div></div>
      ${warns}
      <p class="gm-note">${hint}<br><em>Changes here apply to this match only – your saved lineup is not touched.</em></p>
      <div class="gm-sec-head"><span>PLAYING XI · BATTING ORDER</span><em>${x.changes} change${x.changes === 1 ? "" : "s"}</em></div>
      <div class="gm-xi-list">${x.players.map((p) => xiRow(p, "xi")).join("")}</div>
      <div class="gm-sec-head"><span>SUBSTITUTES</span><em>${x.bench.length}</em></div>
      <div class="gm-xi-list">${x.bench.length ? x.bench.map((p) => xiRow(p, "bench")).join("") : `<div class="gm-note">No substitutes in your squad.</div>`}</div>
      <div class="gm-sticky"><button class="gm-cta" data-g="xi-confirm">CONFIRM PLAYING XI <span>›</span></button></div>`, "Playing XI", "TEAM SELECTION");
    hydrate();
  }

  async function act(route, payload, after) {
    if (G.busy || !G.view) return null;
    G.busy = true;
    try {
      const r = await post(route, { rev: G.view.rev, ...(payload || {}) });
      G.view = r.game;
      if (r.new_achievements) setTimeout(() => window.CrickiumAch?.celebrate(r.new_achievements), 600);
      if (after) after(r);
      return r;
    } catch (e) {
      if (e.payload && e.payload.game) { G.view = e.payload.game; toast(e.message || "Updated", true); render(); }
      else toast(e.message || "Something went wrong", true);
      return null;
    } finally { G.busy = false; }
  }

  async function xiPick(side, pid) {
    pid = Number(pid);
    if (!G.sel) { G.sel = { side, pid }; ping(); return renderXI(); }
    if (G.sel.pid === pid) { G.sel = null; return renderXI(); }
    if (G.sel.side === side) { G.sel = { side, pid }; ping(); return renderXI(); }
    const out = side === "xi" ? pid : G.sel.pid, inn = side === "xi" ? G.sel.pid : pid;
    G.sel = null;
    const r = await act("xi/swap", { out_pid: out, in_pid: inn });
    ping("medium");
    if (r) renderXI(); else renderXI();
  }

  /* ---------------------------------------------------------------- toss */
  function coinFace(face) {
    return `<div class="gm-coin-face ${face}"><span>${face === "heads" ? "H" : "T"}</span><small>${face.toUpperCase()}</small></div>`;
  }
  function tossHead(sub) {
    const t = G.view.teams;
    return `<section class="gm-toss-head"><div class="tm you"><b>${esc(t.user.code)}</b><small>${esc(t.user.name)}</small></div><span class="vs">VS</span><div class="tm ai"><b>${esc(t.ai.code)}</b><small>${esc(t.ai.name)}</small></div></section>
      <div class="gm-toss-meta"><span>${G.view.cfg.pitch_emoji} ${esc(G.view.cfg.pitch_label)}</span><span>${G.view.cfg.overs} ov</span><span>${esc(G.view.cfg.difficulty_label)}</span></div>${sub || ""}`;
  }
  function renderToss() {
    shell(`${tossHead()}<div class="gm-stage"><div class="gm-coin idle"><div class="gm-coin-in">${coinFace("heads")}${coinFace("tails")}</div></div></div>
      <h3 class="gm-call-title">Choose your call</h3><p class="gm-note center">The winner decides whether to bat or bowl first.</p>
      <div class="gm-calls"><button data-g="call" data-call="heads"><span>H</span><b>HEADS</b></button><button data-g="call" data-call="tails"><span>T</span><b>TAILS</b></button></div>`, "The Toss", "MATCH DAY");
  }
  function renderTossFlip() {
    const tt = G.toss;
    shell(`${tossHead()}<div class="gm-stage"><div class="gm-coin flipping" style="--end:${tt.coin === "tails" ? "1980deg" : "1800deg"}"><div class="gm-coin-in">${coinFace("heads")}${coinFace("tails")}</div></div></div>
      <div class="gm-countdown" id="gm-count">3</div><p class="gm-note center">You called <b>${esc(tt.call.toUpperCase())}</b>… the coin is in the air</p>`, "The Toss", "MATCH DAY");
    let n = 3;
    const el = () => document.getElementById("gm-count");
    const iv = setInterval(() => { n -= 1; if (el() && n > 0) { el().textContent = n; ping("light"); } if (n <= 0) clearInterval(iv); }, 1000);
  }
  async function doCall(call) {
    if (G.busy || !G.view) return;
    const r = await act("toss", { call });
    if (!r) return render();
    const t = r.game.toss || {};
    G.toss = { call, phase: "flip", coin: t.coin || call };
    renderTossFlip();
    await sleep(3150);
    G.toss = null; G.tossShow = true; buzz(t.winner === "user" ? "success" : "warning");
    render();
  }
  function renderTossResult() {
    const v = G.view, t = v.toss || {};
    const youWon = t.winner === "user";
    const decided = !!t.decision;
    const body = youWon
      ? (decided ? `<p class="gm-toss-line">You chose to <b>${t.decision === "bat" ? "BAT" : "BOWL"}</b> first.</p>`
        : `<h3 class="gm-call-title">You won the toss!</h3><p class="gm-note center">What do you want to do?</p>
           <div class="gm-calls two"><button data-g="decide" data-d="bat"><span>🏏</span><b>BAT FIRST</b></button><button data-g="decide" data-d="bowl"><span>🎯</span><b>BOWL FIRST</b></button></div>`)
      : `<p class="gm-toss-line"><b>${esc(v.teams.ai.name)}</b> won the toss and chose to <b>${t.decision === "bat" ? "BAT" : "BOWL"}</b> first.</p>`;
    const cta = decided ? `<button class="gm-cta" data-g="start-match">${(youWon ? t.decision === "bat" : t.decision === "bowl") ? "GO OUT TO BAT" : "TAKE THE FIELD"} <span>›</span></button>` : "";
    shell(`${tossHead()}<div class="gm-stage"><div class="gm-coin landed" style="--end:${t.coin === "tails" ? "180deg" : "0deg"}"><div class="gm-coin-in">${coinFace("heads")}${coinFace("tails")}</div></div></div>
      <div class="gm-toss-result ${youWon ? "win" : "lose"}"><small>COIN: ${esc(String(t.coin || "").toUpperCase())} · YOU CALLED ${esc(String(t.call || "").toUpperCase())}</small><h3>${youWon ? "🎉 YOU WON THE TOSS" : `${esc(v.teams.ai.code)} WON THE TOSS`}</h3></div>${body}${cta}`, "The Toss", "MATCH DAY");
  }
  async function doDecide(d) {
    const r = await act("toss/decide", { decision: d });
    if (r) { G.tossShow = true; render(); }
  }

  window.CrickiumGameCore = { G, esc, num, sleep, ping, buzz, act, post, shell, toast, confirmBox, avatar, img, hydrate, render };

  /* --------------------------------------------------------- match centre */
  const chipCls = (s) => ({ "0": "dot", "4": "four", "6": "six", W: "wkt", wd: "ext", nb: "ext", b: "bye", lb: "bye" }[s] || "run");
  const chipTxt = (s) => (s === "0" ? "•" : s === "W" ? "W" : s);
  const chips = (arr) => (arr && arr.length ? arr.map((s) => `<span class="gm-ball ${chipCls(s)}">${esc(chipTxt(s))}</span>`).join("") : `<span class="gm-muted">New over</span>`);

  function wpBar(v) {
    const w = v.wp, t = v.teams;
    const lead = w.leader === "user" ? t.user.name : w.leader === "ai" ? t.ai.name : null;
    return `<div class="gm-wp"><div class="gm-wp-top"><span>WIN PROBABILITY</span><em>${lead ? `▲ ${esc(lead)} favoured` : "Evenly poised"}</em></div>
      <div class="gm-wp-names"><b class="${w.leader === "user" ? "lead" : ""}">${esc(t.user.code)} <i>${w.user}%</i></b><b class="${w.leader === "ai" ? "lead" : ""}"><i>${w.ai}%</i> ${esc(t.ai.code)}</b></div>
      <div class="gm-wp-bar"><span class="u" style="width:${w.user}%"></span><span class="a" style="width:${w.ai}%"></span><s></s></div>
      <div class="gm-wp-full"><small>${esc(t.user.name)}</small><small>${esc(t.ai.name)}</small></div></div>`;
  }

  function scoreboard(v) {
    const m = v.match, c = v.cfg;
    const chase = m.target != null;
    const first = v.first_innings && chase ? `<div class="gm-first">${esc(v.first_innings.team)} ${v.first_innings.runs}/${v.first_innings.wkts} (${v.first_innings.overs} ov)</div>` : "";
    const need = chase ? `<div class="gm-need"><b>${m.need > 0 ? `Need ${m.need} off ${m.balls_left}` : "Target reached"}</b><small>Target ${m.target} · RRR ${m.rrr ?? "—"}</small></div>`
      : `<div class="gm-need"><b>${m.proj != null ? `Projected ${m.proj}` : `Par ${m.par} RPO`}</b><small>${m.wkts_left} wickets left</small></div>`;
    return `<section class="gm-score ${m.bat === "user" ? "batting" : "bowling"}">
      <div class="gm-score-top"><span>${c.pitch_emoji} ${esc(c.pitch_label)}</span><span>${esc(c.difficulty_label)}</span><span>${c.overs} ov</span><span class="ph">${esc(m.phase)}</span>${m.free_hit ? `<span class="fh">FREE HIT</span>` : ""}</div>
      <div class="gm-score-main"><div><small>INNINGS ${m.no} · ${m.bat === "user" ? "YOU BAT" : "YOU BOWL"}</small><h2>${esc(m.bat_name)}</h2></div>
        <div class="gm-runs"><b>${m.runs}/${m.wkts}</b><small>(${m.overs}/${m.max_overs}) · CRR ${m.crr}</small></div></div>
      ${first}${need}${wpBar(v)}</section>`;
  }

  function batterRow(b, striker) {
    if (!b) return "";
    return `<div class="gm-bat ${striker ? "on" : ""}">${avatar(b)}<div class="gm-bat-main"><b>${esc(b.name)}${striker ? " 🏏" : ""}</b><small>${esc(b.zone_label)} · SR ${b.sr} · ${b.fours}×4 ${b.sixes}×6</small>
      <div class="gm-conf"><i class="z-${esc(b.zone)}" style="width:${Math.max(6, Math.min(100, b.conf))}%"></i></div></div><div class="gm-bat-r"><b>${b.runs}</b><small>(${b.balls})</small></div></div>`;
  }

  function liveTab(v) {
    const m = v.match, n = v.need || {};
    const bw = m.bowler;
    const bowl = bw ? `<div class="gm-bowl">${avatar(bw)}<div class="gm-bat-main"><b>${esc(bw.name)}${bw.part_time ? " · part-timer" : ""}</b><small>${esc(bw.bw_label || "")}</small></div>
      <div class="gm-fig"><b>${bw.wkts}-${bw.runs}</b><small>${bw.overs} ov · M${bw.maidens} · ${bw.econ}</small></div></div>` : "";
    const done = v.stage === "done";
    return `<section class="gm-panel"><div class="gm-sec-head"><span>AT THE CREASE</span><em>P'ship ${m.partnership.runs} (${m.partnership.balls})</em></div>
        ${batterRow(m.striker, true)}${batterRow(m.non, false)}
        <div class="gm-sec-head"><span>BOWLING</span></div>${bowl || `<div class="gm-muted">Bowler to be selected</div>`}
        <div class="gm-over"><div class="gm-over-h"><span>THIS OVER · ${m.over_no}</span><b>${m.over_runs} runs</b></div><div class="gm-over-balls">${chips(m.this_over)}</div></div>
        ${m.last_over ? `<div class="gm-lastline">Last over: ${m.last_over.runs} runs${m.last_over.wkts ? `, ${m.last_over.wkts} wkt` : ""}${m.last_wicket ? ` · Last wkt: ${esc(m.last_wicket)}` : ""}</div>` : (m.last_wicket ? `<div class="gm-lastline">Last wkt: ${esc(m.last_wicket)}</div>` : "")}
      </section>
      ${done ? resultPanel(v) : actionPanel(v, n)}
      <section class="gm-panel"><div class="gm-sec-head"><span>COMMENTARY</span></div><div class="gm-feed">${v.feed.map((f) => `<div class="gm-feed-i ${esc(f.k)} ${f.wicket ? "w" : ""}">${f.k === "ball" ? `<span class="gm-ball ${chipCls(f.sym)}">${esc(chipTxt(f.sym))}</span><div><small>${esc(f.ov)} · ${esc(f.score || "")}</small><p>${esc(f.text)}</p></div>` : `<div><p>${esc(f.text)}</p></div>`}</div>`).join("")}</div></section>`;
  }

  function actionPanel(v, n) {
    const over = n.scope === "over";
    if (n.kind === "continue") {
      const f = v.first_innings;
      return `<section class="gm-action"><div class="gm-break"><span class="eyebrow">INNINGS BREAK</span><h3>${f ? `${esc(f.team)} ${f.runs}/${f.wkts}` : "Innings complete"}</h3>
        <p>${f ? `${esc(v.teams[v.match.bat === "user" ? "user" : "ai"].name)} need <b>${f.target}</b> to win from ${v.match.max_overs} overs.` : ""}</p>
        <button class="gm-cta" data-g="continue">START THE CHASE <span>›</span></button></div></section>`;
    }
    if (n.kind === "mindset") {
      return `<section class="gm-action"><div class="gm-sec-head"><span>${over ? "YOUR APPROACH FOR THE OVER" : "YOUR BATTING APPROACH"}</span>${n.free_hit ? `<em class="fh">FREE HIT</em>` : ""}</div>
        <div class="gm-opts-col">${n.options.map((o) => `<button class="gm-act bat a-${esc(o.key)}" data-g="play" data-c="${esc(o.key)}"><span>${o.emoji}</span><div><b>${esc(o.label)}</b><small>${esc(o.hint)}</small></div></button>`).join("")}</div>
        ${over ? `<p class="gm-note center">The whole over is simulated from this one call.</p>` : ""}</section>`;
    }
    if (n.kind === "bowler") {
      return `<section class="gm-action"><div class="gm-sec-head"><span>CHOOSE YOUR BOWLER · OVER ${v.match.over_no}</span></div>
        <div class="gm-opts-col">${n.options.map((o) => `<button class="gm-act bowler" data-g="bowler" data-slot="${o.slot}"><span>🎯</span><div><b>${esc(o.name)}${o.part ? " (part-timer)" : ""}</b><small>${esc(o.bw || "")} · BWL ${o.bowl} · ${o.overs} ov, ${o.wkts}-${o.runs}${o.econ != null ? ` · econ ${o.econ}` : ""}</small></div><em>${o.left} left</em></button>`).join("")}</div></section>`;
    }
    if (n.kind === "tactic") {
      return `<section class="gm-action"><div class="gm-sec-head"><span>${over ? "BOWLING PLAN FOR THE OVER" : "BOWLING TACTIC"} · ${esc(n.bowler?.name || "")}</span></div>
        <div class="gm-opts-col">${n.options.map((o) => `<button class="gm-act tac" data-g="play" data-c="${esc(o.key)}"><span>${o.emoji}</span><div><b>${esc(o.label)}</b><small>${esc(o.hint)}</small></div></button>`).join("")}</div></section>`;
    }
    return "";
  }

  function resultPanel(v) {
    const r = v.result || {};
    const won = r.winner === "user";
    const pom = r.pom;
    return `<section class="gm-result ${r.forfeit ? "lose" : won ? "win" : r.type === "tie" ? "tie" : "lose"}"><span class="eyebrow">${r.type === "tie" ? "MATCH TIED" : won ? "VICTORY" : "DEFEAT"}</span>
      <h3>${esc(r.text || "Match complete")}</h3>
      ${pom ? `<div class="gm-pom"><small>PLAYER OF THE MATCH</small><b>${esc(pom.name)}</b><span>${esc(pom.line)}</span></div>` : ""}
      <div class="gm-res-btns"><button class="gm-cta small" data-g="rematch">REMATCH</button><button class="gm-ghost" data-g="new">New game</button><button class="gm-ghost" data-page="home">Home</button></div></section>`;
  }

  /* scorecard tab */
  function scoreTab(v) {
    const cards = v.scorecards;
    const sc = cards[Math.min(G.card, cards.length - 1)];
    const tabs = cards.map((c, i) => `<button class="${i === G.card ? "on" : ""}" data-g="card" data-i="${i}">${esc(c.code)} ${c.runs}/${c.wkts}</button>`).join("");
    const bat = sc.batters.filter((b) => b.state !== "yet").map((b) => `<tr class="${b.striker ? "on" : ""}"><td><b>${esc(b.name)}${b.striker ? " *" : ""}</b><small>${b.state === "out" ? esc(b.how || "out") : "not out"}</small></td><td>${b.runs}</td><td>${b.balls}</td><td>${b.fours}</td><td>${b.sixes}</td><td>${b.sr}</td></tr>`).join("");
    const bowl = sc.bowlers.map((b) => `<tr class="${b.current ? "on" : ""}"><td><b>${esc(b.name)}</b></td><td>${b.overs}</td><td>${b.maidens}</td><td>${b.runs}</td><td>${b.wkts}</td><td>${b.econ}</td></tr>`).join("");
    const fow = sc.fow.map((f) => `<span>${f.runs}-${f.w} <small>${esc(f.name)} (${f.ov} ov)</small></span>`).join("");
    const ps = sc.partnerships.map((p) => `<div class="gm-ps"><b>${p.runs}</b><small>(${p.balls})</small><span>${esc(p.a)} &amp; ${esc(p.b)}</span></div>`).join("");
    const maxO = Math.max(1, ...sc.overs_log.map((o) => o.runs));
    const ov = sc.overs_log.map((o) => `<div class="gm-ov-bar" title="Over ${o.no}"><i class="${o.wkts ? "w" : ""}" style="height:${Math.max(6, (o.runs / maxO) * 100)}%"></i><b>${o.runs}</b><small>${o.no}</small></div>`).join("");
    return `<div class="gm-seg">${tabs}</div>
      <section class="gm-panel"><div class="gm-sc-head"><b>${esc(sc.team)}</b><span>${sc.runs}/${sc.wkts} (${sc.overs} ov) · RR ${sc.rr}</span></div>
        <div class="gm-table"><table><thead><tr><th>BATTER</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead><tbody>${bat}
        <tr class="ex"><td><b>Extras</b><small>${esc(sc.extras_text || "none")}</small></td><td colspan="5">${sc.extras}</td></tr>
        <tr class="tot"><td><b>TOTAL</b></td><td colspan="5">${sc.runs}/${sc.wkts} (${sc.overs} ov)</td></tr></tbody></table></div>
        ${sc.did_not_bat.length ? `<p class="gm-dnb"><b>Yet to bat:</b> ${sc.did_not_bat.map(esc).join(", ")}</p>` : ""}
        <div class="gm-sec-head"><span>BOWLING · ${esc(sc.bowling_team)}</span></div>
        <div class="gm-table"><table><thead><tr><th>BOWLER</th><th>O</th><th>M</th><th>R</th><th>W</th><th>ECO</th></tr></thead><tbody>${bowl}</tbody></table></div>
        ${fow ? `<div class="gm-sec-head"><span>FALL OF WICKETS</span></div><div class="gm-fow">${fow}</div>` : ""}
        ${ps ? `<div class="gm-sec-head"><span>PARTNERSHIPS</span></div>${ps}` : ""}
        ${ov ? `<div class="gm-sec-head"><span>RUNS PER OVER</span></div><div class="gm-ov-chart">${ov}</div>` : ""}</section>`;
  }

  /* charts */
  function worm(v) {
    const inns = v.stats.innings;
    const W = 320, H = 130, P = 22;
    const maxX = Math.max(1, v.match.max_overs), maxY = Math.max(10, ...inns.flatMap((i) => i.worm.map((p) => p[1])));
    const X = (x) => P + (x / maxX) * (W - P - 6), Y = (y) => H - 18 - (y / maxY) * (H - 30);
    const line = (i, col) => `<polyline fill="none" stroke="${col}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" points="${i.worm.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")}"/>`;
    const cols = ["#c5ef61", "#ff9f6e"];
    return `<svg viewBox="0 0 ${W} ${H}" class="gm-chart" role="img" aria-label="Run progression">
      <line x1="${P}" y1="${H - 18}" x2="${W - 6}" y2="${H - 18}" stroke="#26382f"/><line x1="${P}" y1="8" x2="${P}" y2="${H - 18}" stroke="#26382f"/>
      <text x="${P}" y="${H - 4}" fill="#95a69b" font-size="9">0</text><text x="${W - 14}" y="${H - 4}" fill="#95a69b" font-size="9">${maxX}</text><text x="2" y="14" fill="#95a69b" font-size="9">${maxY}</text>
      ${inns.map((i, k) => line(i, cols[k % 2])).join("")}</svg>
      <div class="gm-legend">${inns.map((i, k) => `<span><i style="background:${cols[k % 2]}"></i>${esc(i.team)}</span>`).join("")}</div>`;
  }
  function wpChart(v) {
    const s = v.wp.series || [];
    if (s.length < 2) return `<div class="gm-muted">The probability graph appears once the match is under way.</div>`;
    const W = 320, H = 110, maxX = Math.max(1, s[s.length - 1][0]);
    const pts = s.map((p) => `${(6 + (p[0] / maxX) * (W - 12)).toFixed(1)},${(H - 6 - (p[1] / 100) * (H - 12)).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${W} ${H}" class="gm-chart"><line x1="6" y1="${H / 2}" x2="${W - 6}" y2="${H / 2}" stroke="#26382f" stroke-dasharray="3 4"/>
      <polyline fill="none" stroke="#c5ef61" stroke-width="2.4" stroke-linejoin="round" points="${pts}"/></svg>
      <div class="gm-legend"><span>▲ ${esc(v.teams.user.code)} ${v.wp.user}%</span><span>▼ ${esc(v.teams.ai.code)} ${v.wp.ai}%</span></div>`;
  }

  function statsTab(v) {
    const st = v.stats;
    const cmp = st.compare.length ? `<div class="gm-sec-head"><span>HEAD TO HEAD</span></div>${st.compare.map((r) => `<div class="gm-cmp"><b class="${r.lead === "user" ? "lead" : ""}">${esc(r.user)}</b><span>${esc(r.label)}</span><b class="${r.lead === "ai" ? "lead" : ""}">${esc(r.ai)}</b></div>`).join("")}` : "";
    const per = st.innings.map((i) => `<div class="gm-inn-stat"><div class="gm-sc-head"><b>${esc(i.team)}</b><span>${i.runs}/${i.wkts} (${i.overs})</span></div>
      <div class="gm-kv"><div><b>${i.rr}</b><small>RUN RATE</small></div><div><b>${i.boundary_pct}%</b><small>BOUNDARY</small></div><div><b>${i.dot_pct}%</b><small>DOT BALLS</small></div><div><b>${i.boundary_runs}</b><small>IN BOUNDARIES</small></div></div>
      ${i.phases.map((p) => `<div class="gm-phase"><span>${esc(p.label)}</span><div class="gm-bar"><i style="width:${Math.min(100, p.rr * 7)}%"></i></div><b>${p.runs}/${p.wkts}</b><small>${p.rr} rpo</small></div>`).join("")}
      ${i.best_partnership ? `<p class="gm-note">Best partnership: <b>${i.best_partnership.runs}</b> (${i.best_partnership.balls}) – ${esc(i.best_partnership.a)} &amp; ${esc(i.best_partnership.b)}</p>` : ""}
      ${i.top_bat ? `<p class="gm-note">Top scorer: <b>${esc(i.top_bat.name)}</b> ${i.top_bat.runs} (${i.top_bat.balls})${i.top_bowl ? ` · Best bowler: <b>${esc(i.top_bowl.name)}</b> ${i.top_bowl.wkts}/${i.top_bowl.runs}` : ""}</p>` : ""}</div>`).join("");
    const mo = st.momentum ? `<div class="gm-sec-head"><span>MOMENTUM · LAST ${st.momentum.balls} BALLS</span></div><div class="gm-over-balls">${chips(st.momentum.tokens)}</div><p class="gm-note">${st.momentum.runs} runs, ${st.momentum.wkts} wkts · ${st.momentum.rr} rpo</p>` : "";
    const choice = (rows, title) => rows.length ? `<div class="gm-sec-head"><span>${title}</span></div>${rows.map((r) => `<div class="gm-choice"><span>${r.emoji} ${esc(r.label)}</span><small>${r.balls} balls</small><b>${r.runs} runs · ${r.wkts} wkt</b></div>`).join("")}` : "";
    return `<section class="gm-panel"><div class="gm-sec-head"><span>RUN PROGRESSION</span></div>${worm(v)}${cmp}${per}${mo}${choice(st.choices.bat, "YOUR BATTING APPROACHES")}${choice(st.choices.bowl, "YOUR BOWLING TACTICS")}</section>
      <section class="gm-panel"><div class="gm-sec-head"><span>WIN PROBABILITY TIMELINE</span></div>${wpChart(v)}</section>`;
  }

  function infoTab(v) {
    const c = v.cfg;
    const lineup = (side) => `<div class="gm-lineup"><div class="gm-sc-head"><b>${esc(v.teams[side].name)}</b><span>${side === "user" ? "YOU" : "COMPUTER"}</span></div>${(v.lineups?.[side] || []).map((p) => `<div class="gm-lu"><span>${p.pos}</span><b>${p.role_icon} ${esc(p.name)}</b><small>${p.bat}/${p.bowl}</small></div>`).join("")}</div>`;
    return `<section class="gm-panel"><div class="gm-sec-head"><span>MATCH INFO</span></div>
      <div class="gm-kv"><div><b>${esc(c.difficulty_label)}</b><small>DIFFICULTY</small></div><div><b>${c.overs}</b><small>OVERS</small></div><div><b>${c.sim === "ball" ? "Ball" : "Over"}</b><small>SIMULATION</small></div><div><b>${c.pitch_emoji} ${esc(c.pitch_label)}</b><small>PITCH</small></div></div>
      <p class="gm-note">${esc(c.pitch_desc)}. ${esc(c.pitch_tip)}</p>
      ${v.toss ? `<p class="gm-note">🪙 ${esc(v.toss.text || "")}</p>` : ""}
      <p class="gm-note">Max ${c.quota} over${c.quota === 1 ? "" : "s"} per bowler.</p></section>
      <section class="gm-panel">${lineup("user")}${lineup("ai")}</section>
      <div class="gm-sticky static"><button class="gm-ghost danger" data-g="forfeit">Forfeit match</button></div>`;
  }

  function renderMatch() {
    const v = G.view;
    const tabs = [["live", "LIVE"], ["card", "SCORECARD"], ["stats", "STATS"], ["info", "INFO"]];
    const body = G.tab === "card" ? scoreTab(v) : G.tab === "stats" ? statsTab(v) : G.tab === "info" ? infoTab(v) : liveTab(v);
    shell(`${scoreboard(v)}<nav class="gm-tabs">${tabs.map(([k, l]) => `<button class="${G.tab === k ? "on" : ""}" data-g="tab" data-t="${k}">${l}</button>`).join("")}</nav>${body}`, v.stage === "done" ? "Full Time" : "Match Centre", `${esc(v.teams.user.code)} VS ${esc(v.teams.ai.code)}`);
    hydrate();
  }

  /* -------------------------------------------------------- ball reveal */
  async function reveal(events) {
    if (!events || !events.length) return;
    G.revealing = true; G.skip = false;
    const host = document.createElement("div");
    host.className = "gm-reveal";
    host.innerHTML = `<div class="gm-reveal-card"><div class="gm-rv-top"><span id="rv-ov"></span><button data-g="skip">SKIP ›</button></div><div class="gm-rv-big" id="rv-big"></div><div class="gm-rv-balls" id="rv-balls"></div><p id="rv-text"></p><small id="rv-score"></small></div>`;
    document.body.appendChild(host);
    const big = host.querySelector("#rv-big"), balls = host.querySelector("#rv-balls");
    for (const e of events) {
      if (G.skip) break;
      const label = e.wicket ? "WICKET!" : e.sym === "6" ? "SIX!" : e.sym === "4" ? "FOUR!" : e.sym === "wd" ? "WIDE" : e.sym === "nb" ? "NO BALL" : e.sym === "0" ? "DOT" : `${e.sym} RUN${e.sym === "1" ? "" : "S"}`;
      big.className = `gm-rv-big ${chipCls(e.sym)} pop`; big.textContent = label;
      host.querySelector("#rv-ov").textContent = `${e.ov} · ${e.bowl} to ${e.bat}`;
      balls.insertAdjacentHTML("beforeend", `<span class="gm-ball ${chipCls(e.sym)}">${esc(chipTxt(e.sym))}</span>`);
      host.querySelector("#rv-text").textContent = e.wicket && e.dis ? `${e.dis}. ${e.text}` : e.text;
      host.querySelector("#rv-score").textContent = e.score;
      if (e.wicket) buzz("error"); else if (e.sym === "6" || e.sym === "4") ping("heavy"); else ping("light");
      await sleep(events.length > 1 ? 780 : 1250);
    }
    host.remove(); G.revealing = false;
  }

  async function doPlay(choice) {
    if (G.busy || G.revealing) return;
    const r = await act("play", { choice });
    if (!r) return;
    await reveal(r.game.events);
    G.tab = "live";
    if (r.game.stage === "done") buzz(r.game.result?.winner === "user" ? "success" : "warning");
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ------------------------------------------------------------- events */
  document.addEventListener("click", async (event) => {
    const el = event.target.closest("[data-g]");
    if (!el || !G.alive) return;
    const a = el.dataset.g;
    if (a === "setup") { G.setup[el.dataset.k] = el.dataset.k === "overs" ? Number(el.dataset.v) : el.dataset.v; ping(); return renderHub(); }
    if (a === "start") return startGame(false);
    if (a === "resume") { G.tab = "live"; return render(); }
    if (a === "abandon") return confirmBox("Abandon this match? It will count as a loss if the innings had started.", "Abandon", async () => { const r = await act("forfeit", {}); G.view = null; G.hub = null; await load(); });
    if (a === "pick") return xiPick(el.dataset.side, el.dataset.pid);
    if (a === "mv") { event.stopPropagation(); const r = await act("xi/move", { pid: Number(el.dataset.pid), dir: Number(el.dataset.dir) }); return renderXI(); }
    if (a === "xi-confirm") { const r = await act("xi/confirm", {}); if (r) { G.toss = null; render(); } return; }
    if (a === "call") return doCall(el.dataset.call);
    if (a === "decide") return doDecide(el.dataset.d);
    if (a === "start-match") { G.tossShow = false; G.tab = "live"; return render(); }
    if (a === "tab") { G.tab = el.dataset.t; return renderMatch(); }
    if (a === "card") { G.card = Number(el.dataset.i); return renderMatch(); }
    if (a === "play") return doPlay(el.dataset.c);
    if (a === "bowler") { const r = await act("bowler", { slot: Number(el.dataset.slot) }); if (r) renderMatch(); return; }
    if (a === "continue") { const r = await act("continue", {}); if (r) { G.tab = "live"; renderMatch(); } return; }
    if (a === "skip") { G.skip = true; return; }
    if (a === "forfeit") return confirmBox("Forfeit this match? It counts as a loss.", "Forfeit", async () => { await act("forfeit", {}); G.tab = "live"; renderMatch(); });
    if (a === "rematch") { const c = G.view.cfg; G.setup = { difficulty: c.difficulty, overs: c.overs, sim: c.sim, pitch: c.random_pitch ? "random" : c.pitch }; G.view = null; return startGame(false); }
    if (a === "new") { G.view = null; G.hub = null; return load(); }
  });

  window.CrickiumGame = { load, stop, mountHome };

  /* home entry card */
  async function mountHome() {
    const slot = document.getElementById("home-game-slot");
    if (!slot) return;
    try {
      const st = await app().api("/api/game/state");
      if (!document.getElementById("home-game-slot")) return;
      const s = st.summary, c = st.stats || {};
      slot.innerHTML = `<section class="gm-home" data-page="game"><div class="gm-home-glow"></div><div><span class="eyebrow">NEW · VS COMPUTER</span><h2>Play Game</h2>
        <p>${s ? `Match in progress · ${esc(s.score || "setting up")}` : c.games ? `${c.wins}W of ${c.games} · best ${c.high_score}` : "4 difficulties · 2–20 overs · your XI"}</p></div>
        <button class="gm-cta small">${s ? "RESUME" : "PLAY"} ›</button></section>`;
    } catch (_) { slot.innerHTML = `<section class="gm-home" data-page="game"><div><span class="eyebrow">NEW</span><h2>Play Game</h2><p>Take on the computer</p></div><button class="gm-cta small">PLAY ›</button></section>`; }
  }
})();
