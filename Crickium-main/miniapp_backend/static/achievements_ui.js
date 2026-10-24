/* Crickium achievements: vertical journey timeline, home/profile summaries and
   unlock celebrations. Presentation only - the server decides every unlock. */
(function () {
  "use strict";
  const app = () => window.CrickiumApp;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  let cache = { at: 0, data: null };
  let filter = "all";

  async function fetchData(force) {
    if (!force && cache.data && Date.now() - cache.at < 20000) return cache.data;
    const data = await app().api("/api/achievements");
    cache = { at: Date.now(), data };
    return data;
  }

  function ring(pct, label, sub) {
    const r = 44, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(100, pct)) / 100);
    return `<div class="ach-ring"><svg viewBox="0 0 110 110" aria-hidden="true"><circle cx="55" cy="55" r="${r}" class="ach-ring-bg"/><circle cx="55" cy="55" r="${r}" class="ach-ring-fg" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/></svg><div><b>${esc(label)}</b><small>${esc(sub)}</small></div></div>`;
  }

  function hero(d) {
    const nextRank = d.next_rank ? `${num(d.points_to_next)} pts to ${esc(d.next_rank)}` : "Top rank reached";
    return `<section class="ach-hero">
      <div class="ach-hero-glow"></div>
      ${ring(d.pct, `${d.done}/${d.total}`, "UNLOCKED")}
      <div class="ach-hero-info">
        <span class="eyebrow">YOUR RANK</span>
        <h2>${esc(d.rank)}</h2>
        <p>${num(d.points)} / ${num(d.total_points)} points</p>
        <small>${nextRank}</small>
      </div>
    </section>
    ${d.next ? `<section class="ach-next"><span class="ach-next-icon">${esc(d.next.icon)}</span>
      <div><span class="eyebrow">NEXT UP</span><b>${esc(d.next.title)}</b>
      <div class="ach-bar"><i style="width:${d.next.pct}%"></i></div><small>${num(d.next.current)} / ${num(d.next.target)}</small></div></section>` : `<section class="ach-next done"><span class="ach-next-icon">🌌</span><div><span class="eyebrow">COMPLETE</span><b>Every achievement unlocked</b></div></section>`}`;
  }

  function item(it, isCurrent) {
    const cls = it.done ? "done" : isCurrent ? "current" : "locked";
    const progress = it.done ? `<span class="ach-badge">+${it.points} pts</span>`
      : `<div class="ach-bar"><i style="width:${it.pct}%"></i></div><small>${num(it.current)} / ${num(it.target)} · +${it.points} pts</small>`;
    return `<li class="ach-item ${cls}"><span class="ach-node">${it.done ? "✓" : isCurrent ? "●" : "🔒"}</span>
      <div class="ach-card"><span class="ach-icon">${esc(it.icon)}</span>
        <div class="ach-body"><b>${esc(it.title)}</b><p>${esc(it.desc)}</p>${progress}</div></div></li>`;
  }

  function timeline(d) {
    const firstLocked = d.chapters.flatMap((c) => c.items).find((i) => !i.done);
    return d.chapters.map((ch) => {
      const rows = ch.items.filter((i) => filter === "all" || (filter === "done" ? i.done : !i.done));
      if (!rows.length) return "";
      return `<section class="ach-chapter"><div class="ach-chapter-head"><span>${esc(ch.icon)}</span>
        <div><b>${esc(ch.name)}</b><small>${esc(ch.tagline)}</small></div><em>${ch.done}/${ch.total}</em></div>
        <ol class="ach-line">${rows.map((i) => item(i, firstLocked && i.key === firstLocked.key)).join("")}</ol></section>`;
    }).join("") || `<div class="panel empty-state"><b>Nothing here yet</b><p>Play, collect and win to unlock achievements.</p></div>`;
  }

  async function load() {
    const a = app();
    a.shell(`<div class="loading-line">Loading achievements…</div>`, "Achievements", "HALL OF FAME");
    try {
      const d = await fetchData(true);
      draw(d);
    } catch (e) {
      a.shell(`<div class="panel empty-state"><b>Could not load achievements</b><p>${esc(e.message || "Try again")}</p><button class="button primary" data-page="achievements">Retry</button></div>`, "Achievements", "HALL OF FAME");
    }
  }

  function draw(d) {
    app().shell(`${hero(d)}
      <div class="ach-filters">${[["all", "All"], ["done", "Unlocked"], ["todo", "Locked"]].map(([k, l]) => `<button class="${filter === k ? "on" : ""}" data-ach-filter="${k}">${l}</button>`).join("")}</div>
      <div id="ach-timeline">${timeline(d)}</div>`, "Achievements", "HALL OF FAME");
    const cur = document.querySelector(".ach-item.current");
    if (cur && filter === "all") setTimeout(() => cur.scrollIntoView({ block: "center", behavior: "smooth" }), 250);
  }

  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-ach-filter]");
    if (!btn) return;
    filter = btn.dataset.achFilter;
    if (cache.data) draw(cache.data);
  });

  /* compact cards ---------------------------------------------------------- */
  async function mount(slotId, kind) {
    const slot = document.getElementById(slotId);
    if (!slot) return;
    try {
      const d = await fetchData(false);
      if (!document.getElementById(slotId)) return;
      if (kind === "profile") {
        slot.innerHTML = `<button class="ach-mini panel" data-page="achievements"><span class="ach-mini-ico">🏅</span>
          <span><span class="eyebrow">ACHIEVEMENTS</span><b>${d.done}/${d.total} · ${esc(d.rank)}</b><div class="ach-bar"><i style="width:${d.pct}%"></i></div></span><strong>›</strong></button>`;
      } else {
        slot.innerHTML = `<section class="panel ach-home"><div class="panel-heading"><div><span class="eyebrow">ACHIEVEMENTS</span><h2>${esc(d.rank)} · ${d.done}/${d.total}</h2></div>
          <button class="text-button" data-page="achievements">Journey ›</button></div>
          <div class="ach-bar"><i style="width:${d.pct}%"></i></div>
          ${d.next ? `<div class="ach-home-next"><span>${esc(d.next.icon)}</span><div><b>${esc(d.next.title)}</b><small>${num(d.next.current)} / ${num(d.next.target)}</small></div></div>` : ""}</section>`;
      }
    } catch (_) { slot.innerHTML = ""; }
  }

  function celebrate(list) {
    if (!Array.isArray(list) || !list.length) return;
    cache.at = 0;
    const host = document.createElement("div");
    host.className = "ach-pop-host";
    host.innerHTML = list.slice(0, 3).map((a, i) => `<div class="ach-pop" style="animation-delay:${i * 0.35}s"><span>${esc(a.icon)}</span><div><small>ACHIEVEMENT UNLOCKED</small><b>${esc(a.title)}</b></div><em>+${num(a.points)}</em></div>`).join("");
    document.body.appendChild(host);
    try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("success"); } catch (_) {}
    setTimeout(() => host.remove(), 5200);
  }

  window.CrickiumAch = { load, mountHome: () => mount("home-ach-slot", "home"), mountProfile: () => mount("profile-ach-slot", "profile"), celebrate, refresh: () => { cache.at = 0; } };
})();
