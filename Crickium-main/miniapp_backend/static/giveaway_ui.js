/* Crickium Giveaways page: live prize cards with countdown, requirements,
   win-chance meter and past winners. Presentation only - the server decides
   who may enter and who wins. */
(function () {
  "use strict";
  const app = () => window.CrickiumApp;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  let data = null;
  let skew = 0;           // server clock - device clock (ms)
  let ticker = null;
  let busy = new Set();

  const nowMs = () => Date.now() + skew;
  function stop() { if (ticker) { clearInterval(ticker); ticker = null; } }

  function chanceText(c) {
    if (c == null) return "—";
    return c < 0.1 ? "<0.1%" : `${c.toFixed(1)}%`;
  }

  function clock(sec) {
    sec = Math.max(0, Math.floor(sec));
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const box = (v, l) => `<div class="gv-t"><b>${String(v).padStart(2, "0")}</b><small>${l}</small></div>`;
    return (d ? box(d, "DAYS") : "") + box(h, "HRS") + box(m, "MIN") + box(s, "SEC");
  }

  function check(ok, label, hint) {
    const cls = ok === true ? "ok" : ok === false ? "bad" : "warn";
    const icon = ok === true ? "✓" : ok === false ? "✕" : "?";
    return `<li class="${cls}"><i>${icon}</i><span>${esc(label)}${ok !== true && hint ? `<small>${esc(hint)}</small>` : ""}</span></li>`;
  }

  function prizeBlock(p) {
    return `<div class="gv-prize">
      <div class="gv-art"><div class="gv-shine"></div>${app().image(p.card_image_url, p.name, "gv-img")}
        <div class="gv-ovr">${num(p.overall)}<small>OVR</small></div></div>
      <div class="gv-info">
        <span class="gv-rarity r-${esc(String(p.rarity || "").toLowerCase())}">${esc(p.rarity || "Card")}</span>
        <h3>${esc(p.name)}</h3>
        <p>${esc(p.country || "")}${p.role ? ` · ${esc(p.role)}` : ""}${p.version ? ` · ${esc(p.version)}` : ""}</p>
        <div class="gv-chips"><span>🏏 BAT <b>${num(p.bat_level)}</b></span><span>🎯 BOWL <b>${num(p.bowl_level)}</b></span><span>💰 <b>${num(p.buy_price)}</b></span></div>
      </div></div>`;
  }

  function card(g) {
    const live = g.status === "active";
    const r = g.requirements || {};
    const total = Math.max(1, g.total_seconds);
    const pct = live ? Math.min(100, Math.max(0, (1 - g.seconds_left / total) * 100)) : 0;
    const btn = busy.has(g.id) ? `<button class="gv-btn join" disabled>Joining…</button>`
      : !live ? `<button class="gv-btn soon" disabled>Starts ${esc(g.start_text)}</button>`
      : g.joined ? `<button class="gv-btn done" disabled>✓ You're in the draw</button>`
      : `<button class="gv-btn join" data-gv="join" data-id="${g.id}">🎉 Participate</button>`;
    const group = data.main_group || {};
    const joinGroup = (r.member === false && group.link) ? `<button class="gv-btn group" data-gv="group">👥 Join Main Group</button>` : "";
    const note = (!g.joined && g.reason_text) ? `<p class="gv-note">${esc(g.reason_text)}</p>` : "";
    const meter = (g.chance != null) ? `<div class="gv-meter"><div><span>YOUR ACTIVITY</span><b>${num(g.score)}</b></div>
        <div><span>WIN CHANCE</span><b class="hot">${chanceText(g.chance)}</b></div>
        <div><span>ENTRIES</span><b>${num(g.participants)}</b></div></div>` : "";
    return `<article class="gv-card ${live ? "live" : "soon"}" data-gv-id="${g.id}">
      <header><span class="gv-pill ${live ? "on" : ""}">${live ? '<i></i>LIVE' : "UPCOMING"}</span><span class="gv-no">GIVEAWAY #${g.id}</span></header>
      ${prizeBlock(g.prize)}
      <div class="gv-timer" data-gv-end="${esc(live ? g.end_at : g.start_at)}" data-gv-mode="${live ? "end" : "start"}">
        <small>${live ? "ENDS IN" : "STARTS IN"}</small><div class="gv-clock">${clock(live ? g.seconds_left : g.seconds_to_start)}</div></div>
      ${live ? `<div class="gv-bar"><i style="width:${pct.toFixed(1)}%" data-gv-bar data-total="${g.total_seconds}" data-end="${esc(g.end_at)}"></i></div>` : ""}
      <div class="gv-when"><span>🟢 ${esc(g.start_text)}</span><span>🏁 ${esc(g.end_text)}</span></div>
      ${meter}
      ${live && !g.joined ? `<ul class="gv-checks">${check(r.member, "In the main group", "Join the main group first")}
        ${check(r.account, "Team created", "Send /debut to the bot")}
        ${check(!r.owns, "Doesn't own this card", "You already own it")}
        ${check(r.squad_free, "Free squad slot", "Squad is full (25/25)")}</ul>` : ""}
      ${note}
      <div class="gv-actions">${btn}${joinGroup}</div>
      ${live ? `<small class="gv-foot">👥 ${num(g.participants)} participants · more activity = higher chance</small>` : ""}
    </article>`;
  }

  function historyRow(h) {
    const w = h.winner;
    const who = w ? `<b>${esc(w.name)}</b>${w.username ? ` <small>@${esc(w.username)}</small>` : ""}` : `<b class="none">No winner</b>`;
    return `<div class="gv-hist ${h.mine ? "mine" : ""}"><div class="gv-hist-av">${w ? esc(String(w.name || "?").trim().charAt(0).toUpperCase()) : "–"}</div>
      <div><span>${esc(h.prize.name)} · ${num(h.prize.overall)} OVR</span>${who}<small>${h.mine ? "🎉 That's you! · " : ""}${num(h.participants)} participants · ${esc(h.ended_text)}</small></div>
      <span class="gv-trophy">${w ? "🏆" : "🏁"}</span></div>`;
  }

  function render() {
    const items = data.items || [];
    const live = items.filter((g) => g.status === "active").length;
    const hero = `<section class="gv-hero"><div class="gv-glow"></div><span class="eyebrow">CRICKIUM REWARDS</span>
      <h2>Giveaways</h2><p>Win real player cards. Stay active, stay in the main group, and the draw favours you.</p>
      <div class="gv-hero-stats"><div><b>${live}</b><small>LIVE NOW</small></div><div><b>${num(items.reduce((a, g) => a + g.participants, 0))}</b><small>ENTRIES</small></div>
      <div><b>${data.history.length}</b><small>RECENT WINNERS</small></div></div></section>`;
    const body = items.length ? items.map(card).join("")
      : `<div class="empty-state"><span class="empty-ball">🎁</span><b>No live giveaway right now</b><p>Check back soon - new player-card giveaways are announced in the bot and the main group.</p></div>`;
    const hist = data.history.length ? `<div class="section-title"><h2>Past winners</h2></div><div class="gv-hists">${data.history.map(historyRow).join("")}</div>` : "";
    const rules = `<section class="gv-rules"><b>How the draw works</b><ul>${(data.how || []).map((x) => `<li>${esc(x)}</li>`).join("")}
      <li>The winner's card is added to their squad automatically</li></ul></section>`;
    document.getElementById("gv-root").innerHTML = hero + body + hist + rules;
    app().hydrateImages(document.getElementById("gv-root"));
    startTicker();
  }

  function startTicker() {
    stop();
    ticker = setInterval(() => {
      const root = document.getElementById("gv-root");
      if (!root) { stop(); return; }
      let expired = false;
      root.querySelectorAll(".gv-timer").forEach((el) => {
        const left = (Date.parse(el.dataset.gvEnd) - nowMs()) / 1000;
        el.querySelector(".gv-clock").innerHTML = clock(left);
        if (left <= 0) expired = true;
      });
      root.querySelectorAll("[data-gv-bar]").forEach((el) => {
        const total = Number(el.dataset.total) || 1, left = (Date.parse(el.dataset.end) - nowMs()) / 1000;
        el.style.width = `${Math.min(100, Math.max(0, (1 - left / total) * 100)).toFixed(1)}%`;
      });
      if (expired) { stop(); setTimeout(() => refresh(true), 2500); }
    }, 1000);
  }

  async function refresh(silent) {
    try {
      data = await app().api("/api/giveaways");
      skew = Date.parse(data.now) - Date.now();
      if (document.getElementById("gv-root")) render();
    } catch (error) {
      if (!silent && document.getElementById("gv-root")) {
        document.getElementById("gv-root").innerHTML = `<div class="error-state"><span>!</span><b>Couldn’t load giveaways</b><p>${esc(error.message)}</p><button class="button secondary" data-page="giveaways">Try again</button></div>`;
      }
    }
  }

  async function load() {
    stop();
    app().shell(`<div id="gv-root"><div class="loading-line">Loading giveaways…</div></div>`, "Giveaways", "WIN PLAYER CARDS");
    await refresh(false);
  }

  async function join(id, button) {
    busy.add(id);
    if (button) { button.disabled = true; button.textContent = "Joining…"; }
    try {
      const r = await app().api("/api/giveaways/join", { method: "POST", body: JSON.stringify({ id }) });
      app().ping("medium");
      app().toast(r.status === "already" ? "You're already in the draw." : `You're in! Win chance ~${chanceText(r.chance)}`);
    } catch (error) {
      app().toast((error.payload && (error.payload.message || error.payload.detail)) || error.message, true);
    } finally {
      busy.delete(id);
      await refresh(true);
    }
  }

  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-gv]");
    if (!el) return;
    if (el.dataset.gv === "join") join(Number(el.dataset.id), el);
    if (el.dataset.gv === "group") {
      const link = data?.main_group?.link;
      if (!link) return;
      const tg = app().tg;
      try { if (tg?.openTelegramLink && /^https:\/\/t\.me\//i.test(link)) tg.openTelegramLink(link); else window.open(link, "_blank"); }
      catch (_) { window.open(link, "_blank"); }
    }
  });

  window.CrickiumGiveaway = { load, stop };
})();
