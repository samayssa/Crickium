(() => {
  "use strict";

  const tg = window.Telegram?.WebApp || null;
  const root = document.getElementById("screen");
  const loader = document.getElementById("loader");
  const initData = tg?.initData || "";
  const imageCache = new Map();
  const state = {
    page: "home",
    drawer: false,
    home: null,
    marketRole: "all",
    marketQuery: "",
    searchQuery: "",
    marketSort: "overall",
    matchTimer: null,
    searchSeq: 0,
    marketSeq: 0,
  };

  const primary = [
    ["home", "Home", "⌂"],
    ["search", "Search", "⌕"],
    ["matches", "Matches", "▤"],
    ["market", "Market", "♙"],
    ["rank", "Rank", "♛"],
  ];
  const secondary = [
    ["profile", "Profile", "◉"],
    ["squad", "Squad management", "♟"],
    ["collection", "Player collection", "▦"],
    ["quests", "Crickium Quest", "✦"],
    ["rewards", "Daily rewards & Kit Bag", "🎁"],
    ["wallet", "Coins & transactions", "◈"],
    ["settings", "Settings", "⚙"],
    ["help", "Help & support", "?"],
    ["about", "About Crickium", "ⓘ"],
  ];

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
  const number = (value) => Number(value || 0).toLocaleString("en-IN");
  const dateText = (value) => {
    if (!value) return "Date unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  };
  const percent = (value) => `${Number(value || 0).toFixed(Number(value) % 1 ? 1 : 0)}%`;
  const initials = (value) => String(value || "C").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
  const ping = (kind = "light") => {
    try { tg?.HapticFeedback?.impactOccurred(kind); } catch (_) {}
  };

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (initData) headers.set("X-Telegram-Init-Data", initData);
    if (options.body && !headers.has("content-type")) headers.set("content-type", "application/json");
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    let payload = {};
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      payload = await response.json().catch(() => ({}));
    }
    if (!response.ok) {
      const error = new Error(payload.detail || payload.message || payload.status || `Request failed (${response.status})`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function hideLoader() {
    if (loader) loader.classList.remove("show");
  }

  function image(url, alt = "", className = "") {
    return url
      ? `<img class="${className}" data-api-image="${esc(url)}" alt="${esc(alt)}" loading="lazy">`
      : "";
  }

  async function hydrateImages(scope = root) {
    if (!scope || !initData) return;
    for (const node of scope.querySelectorAll("img[data-api-image]")) {
      const url = node.dataset.apiImage;
      if (!url || node.dataset.loading === "1") continue;
      node.dataset.loading = "1";
      try {
        let objectUrl = imageCache.get(url);
        if (!objectUrl) {
          const response = await fetch(url, { headers: { "X-Telegram-Init-Data": initData }, cache: "force-cache" });
          if (!response.ok) throw new Error("image unavailable");
          objectUrl = URL.createObjectURL(await response.blob());
          imageCache.set(url, objectUrl);
        }
        node.src = objectUrl;
        node.classList.add("loaded");
      } catch (_) {
        node.remove();
      }
    }
  }

  function topbar() {
    const wallet = state.home?.wallet || {};
    return `<header class="topbar">
      <button class="menu-button" data-action="drawer" aria-label="Open menu"><span></span><span></span><span></span></button>
      <button class="brand-lockup" data-page="home" aria-label="Crickium home"><span class="brand-ball">C</span><span><b>CRICKIUM</b><small>CRICKET CLUB</small></span></button>
      <div class="wallet-pills">
        <span class="wallet-pill"><i class="coin-icon">●</i>${number(wallet.coins)}</span>
        <span class="wallet-pill ruby-pill"><i>◆</i>${number(wallet.rubies)}</span>
      </div>
    </header>`;
  }

  function bottomNav() {
    return `<nav class="bottom-nav" aria-label="Main navigation">${primary.map(([key, label, icon]) => `
      <button class="nav-item ${state.page === key ? "active" : ""}" data-page="${key}">
        <span class="nav-icon">${icon}</span><span>${label}</span>
      </button>`).join("")}</nav>`;
  }

  function drawer() {
    if (!state.drawer) return "";
    const profile = state.home?.profile || {};
    return `<div class="drawer-scrim" data-action="close-drawer">
      <aside class="drawer" aria-label="More sections">
        <div class="drawer-profile">
          <div class="avatar">${image(profile.photo_url, profile.display_name, "avatar-image")}<span>${esc(initials(profile.display_name))}</span></div>
          <div class="drawer-name">${esc(profile.display_name || "Crickium player")}<small>${profile.username ? `@${esc(profile.username)}` : `ID ${esc(profile.id || "")}`}</small></div>
          <button class="close-button" data-action="close-drawer" aria-label="Close menu">×</button>
        </div>
        <div class="drawer-label">YOUR CLUB</div>
        ${secondary.map(([key, label, icon]) => `<button class="drawer-link ${state.page === key ? "active" : ""}" data-page="${key}"><span>${icon}</span>${label}<b>›</b></button>`).join("")}
        <div class="drawer-foot">CRICKIUM · OFFICIAL MINI APP</div>
      </aside>
    </div>`;
  }

  function shell(content, title, eyebrow = "CLUB HOUSE") {
    root.innerHTML = `<div class="app-frame">${topbar()}
      <main class="page-content">
        <div class="page-heading"><div><span class="eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1></div>
          <button class="icon-action" data-action="refresh" aria-label="Refresh">↻</button>
        </div>
        ${content}
      </main>${bottomNav()}${drawer()}<div id="toast" class="toast" role="status"></div></div>`;
    hideLoader();
    hydrateImages();
  }

  function avatarMarkup(profile, size = "") {
    return `<div class="avatar ${size}">${image(profile?.photo_url, profile?.display_name || "", "avatar-image")}<span>${esc(initials(profile?.display_name))}</span></div>`;
  }

  function playerCard(player, compact = false) {
    const rating = Number(player.overall || Math.max(player.bat_level || 0, player.bowl_level || 0));
    const kind = esc(player.kind || "global");
    const id = Number(player.entity_id || player.player_id || 0);
    return `<article class="player-card ${compact ? "compact" : ""}" data-player-kind="${kind}" data-player-id="${id}">
      <div class="player-art">
        ${image(player.card_image_url, player.name, "player-image")}
        <div class="rating">${rating}<small>OVR</small></div>
        <span class="role-mark">${esc(player.role_icon || "🏏")}</span>
      </div>
      <div class="player-copy"><div class="player-name">${esc(player.name)}</div>
        <div class="player-sub">${esc(player.country || "Country not recorded")} · ${esc(player.role || "Player")}</div>
        <div class="player-levels"><span>BAT <b>${number(player.bat_level)}</b></span><span>BOWL <b>${number(player.bowl_level)}</b></span></div>
        ${player.version ? `<div class="version-tag">${esc(player.version)}</div>` : ""}
        ${player.buy_price != null ? `<div class="player-price"><span>${number(player.buy_price)} coins</span>${player.owned ? '<b class="owned-tag">OWNED</b>' : ""}</div>` : ""}
      </div>
    </article>`;
  }

  function matchCard(match, active = false) {
    const won = match.won === true;
    const label = active ? (match.is_live ? "LIVE NOW" : String(match.status || "IN PROGRESS").replaceAll("_", " ")) : (won ? "WON" : match.won === false ? "LOST" : "COMPLETED");
    const engine = esc(match.engine || "MATCH");
    return `<button class="match-card ${active ? "is-active" : ""}" data-match-engine="${engine}" data-match-id="${Number(match.match_id || 0)}">
      <div class="match-top"><span class="match-type">${engine} · ${esc(match.match_type || "Cricket")}</span><span class="match-status ${active && match.is_live ? "live" : won ? "won" : ""}">${esc(label)}</span></div>
      <div class="match-scoreline"><b>${active ? esc(match.challenger_name || "Player 1") : `Match #${number(match.match_id)}`}</b><span>${active ? "vs" : match.runs != null ? `${number(match.runs)} runs · ${number(match.wickets)} wkts` : "Record"}</span></div>
      <div class="match-bottom"><span>${esc(match.pitch || match.target ? [match.pitch, match.target ? `Target ${number(match.target)}` : ""].filter(Boolean).join(" · ") : "Score summary")}</span><time>${esc(dateText(match.played_at || match.created_at))}</time></div>
    </button>`;
  }

  function sectionTitle(title, action = "") {
    return `<div class="section-title"><h2>${esc(title)}</h2>${action}</div>`;
  }

  function emptyState(title, text) {
    return `<div class="empty-state"><span class="empty-ball">◎</span><b>${esc(title)}</b><p>${esc(text)}</p></div>`;
  }

  function errorState(error, retryPage = state.page) {
    const message = initData
      ? (error?.message || "This section could not load right now.")
      : "Open this Mini App from Crickium in Telegram so Telegram can verify your account.";
    return `<div class="error-state"><span>!</span><b>Couldn’t load this section</b><p>${esc(message)}</p><button class="button secondary" data-page="${esc(retryPage)}">Try again</button></div>`;
  }

  async function loadHome() {
    shell(`<div class="skeleton hero-skeleton"></div><div class="skeleton"></div>`, "Home");
    try {
      const [home, quests] = await Promise.all([
        api("/api/home"),
        api("/api/quests").catch(() => ({})),
      ]);
      state.home = home;
      await renderHome(home, quests);
    } catch (error) {
      shell(errorState(error, "home"), "Home");
    }
  }

  async function renderHome(home, questData = {}) {
    const p = home.profile || {};
    const w = home.wallet || {};
    const progress = home.progress || {};
    const stats = home.stats || {};
    const recent = home.recent_matches || [];
    const active = home.active_matches || [];
    const daily = home.daily_reward || {};
    const dailyTasks = questData.daily?.tasks || [];
    const questPreview = dailyTasks.length
      ? dailyTasks.slice(0, 3).map((task) => `<div class="home-quest-row ${task.completed ? "completed" : ""}">
          <span class="quest-check">${task.completed ? "✓" : "·"}</span><span><b>${esc(task.title)}</b><small>${esc(task.description)}</small></span>
          <strong>${number(task.reward?.coins)} 🪙${task.reward?.sigils ? ` · ${number(task.reward.sigils)} sigils` : ""}</strong>
        </div>`).join("")
      : `<div class="quest-empty">${questData.daily?.unavailable ? "Quest service is temporarily unavailable." : "No daily quests are assigned right now."}</div>`;
    const rewardDays = (daily.days || []).map((day) => `<div class="streak-day ${day.status}">
      <small>DAY ${number(day.day)}</small><b>${day.player_range ? `${day.player_range[0]}–${day.player_range[1]} OVR` : `${number(day.coins)} 🪙`}</b>
      <span>${day.rubies ? `${number(day.rubies)} ◆` : day.player_range ? "PLAYER" : "COINS"}</span>
    </div>`).join("");
    const content = `
      <section class="hero-card">
        <div class="hero-glow"></div><div class="profile-line">${avatarMarkup(p, "hero-avatar")}
          <div class="hero-identity"><span class="eyebrow">WELCOME BACK</span><h2>${esc(p.first_name || p.display_name || "Player")}</h2>
            <p>${p.username ? `@${esc(p.username)} · ` : ""}TELEGRAM ID ${number(p.id)}</p>
          </div><div class="level-badge"><small>LEVEL</small><b>${number(progress.level)}</b></div>
        </div>
        <div class="xp-head"><span>XP PROGRESS</span><b>${number(progress.xp)}${progress.xp_to_next != null ? ` / ${number(progress.xp_to_next)}` : " · MAX"}</b></div>
        <div class="progress-track"><i style="width:${Math.max(0, Math.min(100, Number(progress.progress_percent || 0)))}%"></i></div>
        <div class="hero-meta"><span>${esc(home.league?.progress_text || "Level progression")}</span><span>${number(home.squad?.count)}/${number(home.squad?.capacity)} SQUAD</span></div>
      </section>
      <div class="stat-grid">
        <div class="stat-tile"><span>WINS</span><b>${number(stats.matches_won)}</b><small>${percent(stats.win_percentage)} win rate</small></div>
        <div class="stat-tile"><span>MATCHES</span><b>${number(stats.matches_played)}</b><small>career record</small></div>
        <div class="stat-tile"><span>GLOBAL RANK</span><b>${stats.rank ? `#${number(stats.rank)}` : "—"}</b><small>${number(stats.runs)} runs</small></div>
        <div class="stat-tile"><span>WICKETS</span><b>${number(stats.wickets)}</b><small>${number(stats.matches_lost)} losses</small></div>
      </div>
      <section class="panel reward-preview">
        <div class="panel-heading"><div><span class="eyebrow">DAILY STREAK</span><h2>${number(daily.streak)} day${daily.streak === 1 ? "" : "s"} claimed</h2></div>
          <button class="text-button" data-page="rewards">View all ›</button></div>
        <div class="streak-grid">${rewardDays}</div>
        <button class="button primary full" data-action="daily-claim" ${daily.available ? "" : "disabled"}>${daily.available ? "Claim today’s reward" : `Available in ${clock(daily.seconds_until_available)}`}</button>
      </section>
      <section class="panel home-quest-panel">
        <div class="panel-heading"><div><span class="eyebrow">CRICKIUM QUEST</span><h2>Today's challenges</h2></div>
          <button class="text-button" data-page="quests">Quest board ›</button></div>
        ${questPreview}
      </section>
      <section class="kitbag-banner">
        <div><span class="eyebrow">FREE PLAYER DROP</span><h2>Hourly Kit Bag</h2><p>Open for one real catalogued 85 OVR player.</p></div>
        <button class="button gold" data-page="rewards">OPEN <span>→</span></button>
      </section>
      ${sectionTitle("Live matches", `<button class="text-button" data-page="matches">All matches ›</button>`)}
      ${active.length ? `<div class="stack">${active.map((item) => matchCard(item, true)).join("")}</div>` : emptyState("No active matches", "Live and in-progress matches involving you will appear here.")}
      ${sectionTitle("Recent matches", `<button class="text-button" data-page="matches">History ›</button>`)}
      ${recent.length ? `<div class="stack">${recent.slice(0, 3).map((item) => matchCard(item)).join("")}</div>` : emptyState("Your match history starts here", "Completed match summaries will appear after the bot records them.")}`;
    shell(content, "Your club");
    await hydrateImages();
  }

  function clock(seconds) {
    let left = Math.max(0, Number(seconds || 0));
    const hours = Math.floor(left / 3600);
    const minutes = Math.floor((left % 3600) / 60);
    const secs = left % 60;
    return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${secs}s` : `${secs}s`;
  }

  async function loadSearch(query = "") {
    state.searchQuery = query;
    shell(`<label class="search-box"><span>⌕</span><input id="player-search" value="${esc(query)}" placeholder="Search players, country or edition…" autocomplete="off"></label>
      <div id="search-results"><div class="loading-line">Searching the player database…</div></div>`, "Player search", "SCOUTING");
    const input = document.getElementById("player-search");
    input?.focus();
    if (input) {
      input.setSelectionRange(input.value.length, input.value.length);
      input.addEventListener("input", () => {
        clearTimeout(state.searchTimer);
        const value = input.value;
        state.searchTimer = setTimeout(() => updateSearchResults(value), 180);
      });
    }
    await updateSearchResults(query);
  }

  async function updateSearchResults(query) {
    const target = document.getElementById("search-results");
    if (!target) return;
    const seq = ++state.searchSeq;
    try {
      const data = await api(`/api/players/search?q=${encodeURIComponent(query)}&limit=20`);
      if (seq !== state.searchSeq || state.page !== "search" || document.getElementById("search-results") !== target) return;
      target.innerHTML = `${sectionTitle(query ? `${data.count} matches` : "Top rated players")}
        ${data.results?.length ? `<div class="player-grid">${data.results.map((player) => playerCard(player, true)).join("")}</div>` : emptyState("No players found", "Try a different name, country or edition.")}`;
      hydrateImages(target);
    } catch (error) {
      target.innerHTML = errorState(error, "search");
    }
  }

  const marketFilters = () => `<section class="panel filter-panel">
      <div class="market-categories">${["all", "batsman", "bowler", "allrounder", "wicketkeeper"].map((role) =>
        `<button class="filter-chip ${state.marketRole === role ? "selected" : ""}" data-role="${role}">${role === "all" ? "All" : role === "allrounder" ? "All-rounder" : role === "wicketkeeper" ? "Keeper" : role[0].toUpperCase() + role.slice(1)}</button>`).join("")}</div>
      <input id="market-search" class="field" placeholder="Find a player…" value="${esc(state.marketQuery)}">
      <div class="filter-grid">
        <label>OVR min<input id="min-overall" class="field" inputmode="numeric" placeholder="55"></label>
        <label>OVR max<input id="max-overall" class="field" inputmode="numeric" placeholder="99"></label>
        <label>Min coins<input id="min-price" class="field" inputmode="numeric" placeholder="0"></label>
        <label>Max coins<input id="max-price" class="field" inputmode="numeric" placeholder="Any"></label>
      </div>
      <div class="filter-row"><input id="market-country" class="field" placeholder="Country" aria-label="Country">
        <select id="market-sort" class="field"><option value="overall">Highest OVR</option><option value="price_asc">Price: low to high</option><option value="price_desc">Price: high to low</option><option value="name">Name A–Z</option><option value="bat">Batting</option><option value="bowl">Bowling</option></select></div>
    </section>`;

  async function loadMarket() {
    shell(`${marketFilters()}<div id="market-results"><div class="loading-line">Loading real player listings…</div></div>`, "Player market", "TRANSFER DESK");
    const rerun = () => {
      clearTimeout(state.marketTimer);
      state.marketTimer = setTimeout(updateMarketResults, 250);
    };
    document.querySelectorAll("[data-role]").forEach((button) => button.addEventListener("click", () => {
      state.marketRole = button.dataset.role;
      loadMarket();
    }));
    for (const id of ["market-search", "min-overall", "max-overall", "min-price", "max-price", "market-country", "market-sort"]) {
      const input = document.getElementById(id);
      if (!input) continue;
      input.addEventListener("input", rerun);
      input.addEventListener("change", rerun);
      if (id === "market-search") input.addEventListener("input", () => { state.marketQuery = input.value; });
    }
    await updateMarketResults();
  }

  async function updateMarketResults() {
    const target = document.getElementById("market-results");
    if (!target) return;
    const seq = ++state.marketSeq;
    const value = (id) => document.getElementById(id)?.value?.trim() || "";
    const params = new URLSearchParams({ role: state.marketRole, limit: "50", sort: value("market-sort") || "overall" });
    const keys = { q: value("market-search"), min: value("min-overall"), max: value("max-overall"), min_price: value("min-price"), max_price: value("max-price"), country: value("market-country") };
    for (const [key, val] of Object.entries(keys)) if (val) params.set(key, val);
    try {
      const data = await api(`/api/market?${params}`);
      if (seq !== state.marketSeq || !target.isConnected) return;
      target.innerHTML = `${sectionTitle(`${number(data.count)} listings`, `<span class="muted-inline">Actual bot catalog</span>`)}
        ${data.items?.length ? `<div class="player-grid">${data.items.map((item) => `<div class="market-item">${playerCard(item, true)}<button class="button ${item.owned ? "secondary" : "primary"} full" data-action="buy-player" data-kind="${esc(item.kind)}" data-id="${Number(item.entity_id)}" data-name="${esc(item.name)}" data-price="${Number(item.buy_price)}" ${item.owned ? "disabled" : ""}>${item.owned ? "Already in squad" : `Buy · ${number(item.buy_price)} coins`}</button></div>`).join("")}</div>` : emptyState("No listings match", "Adjust the filters to see more players.")}
        ${data.catalog_window_truncated ? `<p class="notice">Showing the first ${number(data.scanned_count)} catalog cards. Add a player name or narrow filters to find more.</p>` : ""}`;
      hydrateImages(target);
    } catch (error) {
      target.innerHTML = errorState(error, "market");
    }
  }

  async function loadMatches() {
    shell(`<div id="matches-body"><div class="loading-line">Loading your match records…</div></div>`, "Matches", "FIXTURES & RESULTS");
    try {
      const data = await api("/api/matches");
      const active = data.active || [];
      const recent = data.recent || [];
      const body = document.getElementById("matches-body");
      if (!body) return;
      body.innerHTML = `${sectionTitle("In progress", `<button class="text-button" data-action="refresh">Refresh ↻</button>`)}
        ${active.length ? `<div class="stack">${active.map((item) => matchCard(item, true)).join("")}</div>` : emptyState("No active games", "Matches involving your account will appear here when they start.")}
        ${sectionTitle("Your recent results")}
        ${recent.length ? `<div class="stack">${recent.map((item) => matchCard(item)).join("")}</div>` : emptyState("No recorded results yet", "Match history is shown when Crickium stores a result for your account.")}
        <p class="notice">${esc(data.note || "")}</p>`;
    } catch (error) {
      const body = document.getElementById("matches-body");
      if (body) body.innerHTML = errorState(error, "matches");
    }
  }

  async function loadRank() {
    shell(`<div id="rank-list"><div class="loading-line">Loading global standings…</div></div>`, "Leaderboard", "GLOBAL RANK");
    try {
      const data = await api("/api/leaderboard?limit=50");
      const entries = data.entries || [];
      document.getElementById("rank-list").innerHTML = `
        <div class="rank-summary"><span>YOUR POSITION</span><b>${data.viewer_rank ? `#${number(data.viewer_rank)}` : "Not ranked"}</b><small>Ranked by level, then XP</small></div>
        ${entries.length ? `<div class="rank-table">${entries.map((item) => `<div class="rank-row ${item.is_viewer ? "you" : ""}">
          <span class="rank-number">${number(item.rank).padStart(2, "0")}</span><span class="rank-avatar">${esc(initials(item.name))}</span>
          <span class="rank-name">${esc(item.name)}${item.is_viewer ? "<small>YOU</small>" : ""}<small>${item.username ? `@${esc(item.username)}` : `${number(item.matches)} matches`}</small></span>
          <span class="rank-score"><b>LV ${number(item.level)}</b><small>${number(item.xp)} XP</small></span>
        </div>`).join("")}</div>` : emptyState("No standings yet", "Players will appear after their accounts are recorded.")}`;
    } catch (error) {
      document.getElementById("rank-list").innerHTML = errorState(error, "rank");
    }
  }

  async function loadPlayer(kind, id) {
    shell(`<div class="loading-line">Loading player profile…</div>`, "Player profile", "SCOUT REPORT");
    try {
      const player = await api(`/api/player?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`);
      state.currentPlayer = player;
      renderPlayerDetail(player);
    } catch (error) {
      shell(errorState(error, "search"), "Player profile", "SCOUT REPORT");
    }
  }

  function statRows(items) {
    return items.map(([label, value]) => `<div class="detail-stat"><span>${esc(label)}</span><b>${value == null ? "—" : esc(value)}</b></div>`).join("");
  }

  function renderPlayerDetail(p) {
    const bat = p.batting_stats || {};
    const bowl = p.bowling_stats || {};
    const h = p.match_history || [];
    const content = `<button class="back-link" data-page="search">‹ Back to players</button>
      <section class="detail-hero">${image(p.card_image_url, p.name, "detail-image")}<div class="rating detail-rating">${number(p.overall)}<small>OVR</small></div>
        <div><span class="eyebrow">${esc(p.rarity || "PLAYER CARD")}</span><h2>${esc(p.name)}</h2><p>${esc(p.description || p.role || "Crickium player")}</p><span class="detail-role">${esc(p.role_icon || "🏏")} ${esc(p.role)}</span></div>
      </section>
      <div class="detail-grid"><section class="panel"><div class="panel-heading"><h2>Batting</h2><b class="rating-mini">${number(p.bat_level)}</b></div>
        <div class="stats-list">${statRows([["Matches", number(bat.matches)], ["Innings", number(bat.innings)], ["Runs", number(bat.runs)], ["Average", bat.average], ["Strike rate", bat.strike_rate], ["Highest recorded match runs", number(bat.highest_recorded_match_runs)], ["Fifties", number(bat.fifties)], ["Centuries", number(bat.centuries)], ["Fours", bat.fours], ["Sixes", bat.sixes]])}</div></section>
        <section class="panel"><div class="panel-heading"><h2>Bowling</h2><b class="rating-mini">${number(p.bowl_level)}</b></div>
        <div class="stats-list">${statRows([["Matches", number(bowl.matches)], ["Innings", number(bowl.innings)], ["Wickets", number(bowl.wickets)], ["Economy", bowl.economy], ["Average", bowl.average], ["Strike rate", bowl.strike_rate], ["Best recorded match wickets", number(bowl.best_recorded_match_wickets)]])}</div></section></div>
      <p class="notice">${esc(p.stats_note || "Only stored statistics are displayed.")}</p>
      ${sectionTitle("Recorded match history")}
      ${h.length ? `<div class="stack">${h.map((match) => `<div class="history-line"><b>Match #${number(match.match_id)}</b><span>${number(match.runs)} runs · ${number(match.wickets)} wickets</span><small>${esc(dateText(match.created_at))}</small></div>`).join("")}</div>` : emptyState("No stored appearances", "There are no recorded player match rows yet.")}`;
    shell(content, "Player report", "SCOUT REPORT");
    hydrateImages();
  }

  async function loadSquad() {
    shell(`<div id="squad-list"><div class="loading-line">Loading squad cards…</div></div>`, "Your squad", "PLAYER ROSTER");
    try {
      const data = await api("/api/squad");
      document.getElementById("squad-list").innerHTML = `<div class="squad-capacity"><span>COLLECTION CAPACITY</span><b>${number(data.count)} / ${number(data.capacity)}</b><i style="width:${Math.min(100, data.count / Math.max(1, data.capacity) * 100)}%"></i></div>
        ${data.players?.length ? `<div class="player-grid">${data.players.map((item) => playerCard(item, true)).join("")}</div>` : emptyState("Your squad is empty", "Player cards you own appear here.")}
        <p class="notice">Squad changes are handled by Crickium's existing bot commands. This view is read-only.</p>`;
      hydrateImages();
    } catch (error) {
      document.getElementById("squad-list").innerHTML = errorState(error, "squad");
    }
  }

  async function loadCollection() {
    shell(`<div id="collection-list"><div class="loading-line">Loading your collection…</div></div>`, "Collection", "YOUR PLAYER CARDS");
    try {
      const data = await api("/api/collection");
      document.getElementById("collection-list").innerHTML = data.players?.length
        ? `<p class="muted-copy">All ${number(data.count)} player card${data.count === 1 ? "" : "s"} currently in your squad.</p><div class="player-grid">${data.players.map((item) => playerCard(item, true)).join("")}</div>`
        : emptyState("No player cards yet", "Your owned cards will be collected here.");
      hydrateImages();
    } catch (error) {
      document.getElementById("collection-list").innerHTML = errorState(error, "collection");
    }
  }

  async function loadQuests() {
    shell(`<div id="quest-list"><div class="loading-line">Loading assigned quests…</div></div>`, "Crickium Quest", "QUEST BOARD");
    try {
      const data = await api("/api/quests");
      const body = document.getElementById("quest-list");
      const content = ["daily", "weekly", "monthly"].map((period) => {
        const group = data[period] || {};
        const tasks = group.tasks || [];
        return `<section class="panel quest-panel"><div class="panel-heading"><div><span class="eyebrow">${period.toUpperCase()}</span><h2>${tasks.length ? `${tasks.filter((task) => task.completed).length}/${tasks.length} complete` : group.unavailable ? "Unavailable" : "No assigned tasks"}</h2></div></div>
          ${tasks.length ? tasks.map((task) => `<div class="quest-row ${task.completed ? "completed" : ""}"><span class="quest-check">${task.completed ? "✓" : "·"}</span><span><b>${esc(task.title)}</b><small>${esc(task.description)}</small></span><strong>${number(task.reward?.coins)} 🪙${task.reward?.rubies ? ` · ${number(task.reward.rubies)} ◆` : ""}${task.reward?.sigils ? ` · ${number(task.reward.sigils)} sigils` : ""}</strong></div>`).join("") : emptyState("No tasks returned", "The bot quest service has no available assignments for this period.")}</section>`;
      }).join("");
      body.innerHTML = content + `<p class="notice">This screen shows the existing quest assignments. A separate persistent achievement-unlock catalog is not present in the uploaded repository.</p>`;
    } catch (error) {
      document.getElementById("quest-list").innerHTML = errorState(error, "quests");
    }
  }

  async function loadRewards() {
    shell(`<div id="rewards-content"><div class="loading-line">Checking reward eligibility…</div></div>`, "Rewards", "CLAIM & COLLECT");
    try {
      const data = await api("/api/rewards");
      const daily = data.daily || {};
      const bag = data.kitbag || {};
      const days = daily.days || [];
      document.getElementById("rewards-content").innerHTML = `
        <section class="kitbag-banner large"><div class="kitbag-emblem">✦</div><div><span class="eyebrow">HOURLY DROP · 85 OVR</span><h2>Free Kit Bag</h2><p>One real, unowned OVR 85 player from the global catalog. Claim cooldown is enforced by the server.</p><small>${bag.available ? "READY TO OPEN" : `AVAILABLE IN ${clock(bag.seconds_until_available)}`}</small></div></section>
        <button class="button gold full" data-action="kitbag-claim" ${bag.available ? "" : "disabled"}>${bag.available ? "Open free Kit Bag" : "Kit Bag cooling down"}</button>
        <section class="panel reward-panel"><div class="panel-heading"><div><span class="eyebrow">7-DAY STREAK</span><h2>${number(daily.streak)} claimed · ${number(daily.total_claimed)} total claims</h2></div><button class="button primary" data-action="daily-claim" ${daily.available ? "" : "disabled"}>${daily.available ? "Claim" : clock(daily.seconds_until_available)}</button></div>
          <div class="reward-list">${days.map((day) => `<div class="reward-row ${day.status}"><span class="day-mark">${number(day.day)}</span><span><b>Day ${number(day.day)}</b><small>${day.player_range ? `Random player · OVR ${day.player_range[0]}–${day.player_range[1]}` : "Coins"}${day.rubies ? ` + ${number(day.rubies)} rubies` : ""}</small></span><strong>${number(day.coins)} 🪙</strong><i>${day.status === "claimed" ? "✓" : day.status === "available" ? "CLAIM" : "LOCKED"}</i></div>`).join("")}</div>
        </section>
        <section class="panel"><span class="eyebrow">MORE WAYS TO EARN</span><h2>Quests & match rewards</h2><p class="muted-copy">Daily, weekly and monthly quests use the bot's existing reward rules.</p><button class="button secondary full" data-page="quests">View quests</button></section>`;
    } catch (error) {
      document.getElementById("rewards-content").innerHTML = errorState(error, "rewards");
    }
  }

  async function loadWallet() {
    shell(`<div id="wallet-content"><div class="loading-line">Loading wallet records…</div></div>`, "Wallet", "BALANCE & HISTORY");
    try {
      const [home, history] = await Promise.all([api("/api/home"), api("/api/wallet/transactions")]);
      state.home = home;
      const coins = home.wallet?.coins || 0;
      const rubies = home.wallet?.rubies || 0;
      document.getElementById("wallet-content").innerHTML = `<div class="currency-grid"><div class="currency-card"><span>COINS</span><b>🪙 ${number(coins)}</b></div><div class="currency-card ruby"><span>RUBIES</span><b>◆ ${number(rubies)}</b></div><div class="currency-card"><span>TOTAL SPENT</span><b>${number(home.wallet?.total_spent)}</b></div><div class="currency-card"><span>SIGILS</span><b>${number(home.wallet?.sigils)}</b></div></div>
        ${sectionTitle("Recorded transactions")}
        ${history.items?.length ? `<div class="stack">${history.items.map((item) => `<div class="transaction-row"><span class="transaction-icon">✦</span><span><b>${esc(item.description)}</b><small>${esc(item.source)} · ${esc(dateText(item.created_at))}</small></span><strong class="positive">+${number(item.coins)} 🪙${item.rubies ? ` +${number(item.rubies)} ◆` : ""}</strong></div>`).join("")}</div>` : emptyState("No reward credits recorded", "Quest reward entries will appear after they are recorded.")}
        <p class="notice">${esc(history.note || "")}</p>`;
    } catch (error) {
      document.getElementById("wallet-content").innerHTML = errorState(error, "wallet");
    }
  }

  async function loadProfile() {
    shell(`<div id="profile-content"><div class="loading-line">Loading player profile…</div></div>`, "Profile", "YOUR CRICKIUM ID");
    try {
      const home = await api("/api/profile");
      state.home = home;
      const p = home.profile || {};
      const stats = home.stats || {};
      const progress = home.progress || {};
      document.getElementById("profile-content").innerHTML = `<section class="profile-card panel">${avatarMarkup(p, "profile-avatar")}<span class="eyebrow">CRICKIUM PLAYER</span><h2>${esc(p.display_name)}</h2><p>${p.username ? `@${esc(p.username)}` : "Telegram username not set"}</p><div class="id-pill">TELEGRAM ID · ${number(p.id)}</div></section>
        <div class="stat-grid"><div class="stat-tile"><span>LEVEL</span><b>${number(progress.level)}</b><small>${number(progress.xp)} XP</small></div><div class="stat-tile"><span>RANK</span><b>${stats.rank ? `#${number(stats.rank)}` : "—"}</b><small>global</small></div><div class="stat-tile"><span>MATCHES</span><b>${number(stats.matches_played)}</b><small>${number(stats.matches_won)} wins</small></div><div class="stat-tile"><span>WIN RATE</span><b>${percent(stats.win_percentage)}</b><small>${number(stats.matches_lost)} losses</small></div></div>
        <div class="shortcut-grid"><button data-page="squad">♟<b>Squad</b><small>${number(home.squad?.count)} players</small></button><button data-page="collection">▦<b>Collection</b><small>Your owned cards</small></button><button data-page="quests">✦<b>Quests</b><small>Current tasks</small></button><button data-page="wallet">◈<b>Wallet</b><small>Coins & rubies</small></button></div>`;
      hydrateImages();
    } catch (error) {
      document.getElementById("profile-content").innerHTML = errorState(error, "profile");
    }
  }

  function staticPage(page) {
    const copy = {
      settings: ["Settings", "Preferences", "Telegram appearance and account security are controlled by Telegram. Crickium's existing bot does not store Mini App settings."],
      help: ["Help & support", "Need a hand?", "Use the Crickium bot’s existing help and support command in Telegram. Never share your login code or account credentials."],
      about: ["About Crickium", "Play. Build. Compete.", "Crickium is your cricket card club. This Mini App displays data from the bot’s existing player, squad, match, quest and reward systems."],
    }[page];
    shell(`<section class="panel info-page"><div class="info-emblem">${page === "settings" ? "⚙" : page === "help" ? "?" : "C"}</div><span class="eyebrow">${esc(copy[1])}</span><h2>${esc(copy[0])}</h2><p>${esc(copy[2])}</p>${page === "about" ? `<p class="notice">Some screens are limited to records the existing bot persists. The Mini App does not run or simulate cricket matches.</p>` : ""}</section>`, copy[0], "CLUB HOUSE");
  }

  async function loadMatchDetail(engine, id) {
    shell(`<div class="loading-line">Loading match record…</div>`, `Match #${id}`, "MATCH REPORT");
    try {
      const data = await api(`/api/matches/${encodeURIComponent(engine)}/${encodeURIComponent(id)}`);
      const scorecard = data.scorecard_available
        ? `<section class="panel"><div class="panel-heading"><h2>Available scorecard</h2></div><pre class="json-view">${esc(JSON.stringify({ batting: data.batting, bowling: data.bowling, over_history: data.over_history }, null, 2))}</pre></section>`
        : `<p class="notice">A full scorecard is not stored for this record. Only the information shown below is available from the source.</p>`;
      const summary = data.runs != null || data.wickets != null ? `${number(data.runs)} runs · ${number(data.wickets)} wickets` : data.status || "Match setup";
      shell(`<button class="back-link" data-page="matches">‹ Back to matches</button><section class="detail-hero match-report"><div class="live-mark">${data.is_live ? "LIVE" : esc(String(data.status || "RESULT").toUpperCase())}</div><div><span class="eyebrow">${esc(data.engine || engine)} · ${esc(data.match_type || "CRICKET")}</span><h2>${esc(data.challenger_name || "Match")} ${data.opponent_name ? `vs ${esc(data.opponent_name)}` : ""}</h2><p>${esc(summary)}</p><span class="detail-role">${esc(data.pitch || "Pitch not recorded")} · ${esc(dateText(data.played_at || data.created_at))}</span></div></section>${scorecard}`, `Match #${id}`, "MATCH REPORT");
    } catch (error) {
      shell(errorState(error, "matches"), `Match #${id}`, "MATCH REPORT");
    }
  }

  function toast(message, bad = false) {
    const node = document.getElementById("toast");
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("bad", bad);
    node.classList.add("show");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => node.classList.remove("show"), 2800);
  }

  async function performClaim(kind) {
    if (!initData) { toast("Open Crickium inside Telegram first.", true); return; }
    const button = document.querySelector(`[data-action="${kind === "daily" ? "daily-claim" : "kitbag-claim"}"]`);
    if (button) { button.disabled = true; button.dataset.oldText = button.textContent; button.textContent = "Claiming…"; }
    try {
      const result = await api(`/api/rewards/${kind === "daily" ? "daily" : "kitbag"}`, { method: "POST", body: "{}" });
      if (result.status !== "success") throw Object.assign(new Error(claimError(result)), { payload: result });
      ping("medium");
      if (kind === "kitbag") {
        const player = result.player;
        shell(`<section class="reveal-card"><div class="reveal-burst">✦</div><span class="eyebrow">KIT BAG OPENED</span><h2>Your new player</h2>${playerCard(player)}<p>Added to your squad. Come back in an hour for your next free Kit Bag.</p><button class="button primary full" data-page="squad">View squad</button></section>`, "Player acquired", "REWARD UNLOCKED");
        hydrateImages();
      } else {
        const reward = result.reward || {};
        toast(`Day ${result.day} claimed · ${number(reward.coins)} coins${reward.rubies ? ` · ${number(reward.rubies)} rubies` : ""}`);
        await loadRewards();
      }
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = button.dataset.oldText || "Try again"; }
    }
  }

  function claimError(payload = {}) {
    const status = typeof payload === "string" ? payload : payload.status || payload.detail || payload.message;
    return ({
      cooldown: "That reward is on cooldown. Check back when it is available.",
      squad_full: "Your squad is full. Make room before claiming this player reward.",
      no_player: "No eligible player is available in the catalog right now.",
      debut_required: "Complete your debut match before claiming the daily streak reward.",
      insufficient_balance: "You don’t have enough coins for this player.",
      already_owned: "You already own this player.",
      user_missing: "Your Crickium account could not be found. Open the bot and try again.",
      squad_full: "Your squad has reached its 25-player capacity.",
    })[status] || String(status || "The reward could not be claimed.");
  }

  function showConfirm(message, confirmText, onConfirm) {
    const modal = document.createElement("div");
    modal.className = "modal-scrim";
    modal.innerHTML = `<section class="confirm-modal"><button class="close-button" data-modal-close>×</button><span class="eyebrow">PLEASE CONFIRM</span><h2>One more step</h2><p>${esc(message)}</p><div class="modal-actions"><button class="button secondary" data-modal-close>Cancel</button><button class="button primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;
    document.body.append(modal);
    modal.addEventListener("click", (event) => {
      if (event.target === modal || event.target.closest("[data-modal-close]")) modal.remove();
      if (event.target.closest("[data-modal-confirm]")) { modal.remove(); onConfirm(); }
    });
  }

  async function buyPlayer(kind, id, button) {
    button.disabled = true;
    button.textContent = "Processing…";
    try {
      const result = await api("/api/market/purchase", { method: "POST", body: JSON.stringify({ kind, id: Number(id) }) });
      if (result.status !== "success") throw Object.assign(new Error(result.status), { payload: result });
      ping("medium");
      toast(`${result.player?.name || "Player"} added to your squad.`);
      await loadMarket();
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      button.disabled = false;
      button.textContent = "Try again";
    }
  }

  async function navigate(page) {
    state.page = page;
    state.drawer = false;
    clearInterval(state.matchTimer);
    ping();
    switch (page) {
      case "home": return loadHome();
      case "search": return loadSearch("");
      case "matches":
        await loadMatches();
        state.matchTimer = setInterval(() => { if (state.page === "matches") loadMatches(); }, 30000);
        return;
      case "market": return loadMarket();
      case "rank": return loadRank();
      case "profile": return loadProfile();
      case "squad": return loadSquad();
      case "collection": return loadCollection();
      case "quests": return loadQuests();
      case "rewards": return loadRewards();
      case "wallet": return loadWallet();
      default: return staticPage(page);
    }
  }

  document.addEventListener("click", (event) => {
    const pageButton = event.target.closest("[data-page]");
    if (pageButton) { navigate(pageButton.dataset.page); return; }
    const actionButton = event.target.closest("[data-action]");
    if (actionButton) {
      const action = actionButton.dataset.action;
      if (action === "drawer") { state.drawer = true; document.querySelector(".app-frame")?.insertAdjacentHTML("beforeend", drawer()); hydrateImages(); }
      if (action === "close-drawer") { state.drawer = false; document.querySelector(".drawer-scrim")?.remove(); }
      if (action === "refresh") navigate(state.page);
      if (action === "daily-claim") performClaim("daily");
      if (action === "kitbag-claim") performClaim("kitbag");
      if (action === "buy-player") {
        const { kind, id } = actionButton.dataset;
        showConfirm(`Buy ${actionButton.dataset.name} for ${number(actionButton.dataset.price)} coins? The server will verify your balance and add the card to your squad.`, "Buy player", () => buyPlayer(kind, id, actionButton));
      }
    }
    const card = event.target.closest("[data-player-kind]");
    if (card && !event.target.closest("button")) loadPlayer(card.dataset.playerKind, card.dataset.playerId);
    const match = event.target.closest("[data-match-engine]");
    if (match) loadMatchDetail(match.dataset.matchEngine, match.dataset.matchId);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.drawer) { state.drawer = false; document.querySelector(".drawer-scrim")?.remove(); }
  });

  if (tg) {
    try { tg.ready(); tg.expand(); tg.setHeaderColor?.("#071310"); tg.setBackgroundColor?.("#071310"); } catch (_) {}
  }
  loadHome();
})();
