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
    matchesSeq: 0,
    matchRequestSeq: 0,
    matchRequestBusy: false,
    currentMatch: null,
    currentMatchData: null,
    matchTab: "live",
    matchInnings: null,
    matchesFilter: "ongoing",
    packData: null,
    packHistory: [],
    packOpening: null,
    squadData: null,
    currentPlayerRef: null,
    playerReturnPage: "search",
    lineupSwap: null,
    pendingKitbag: null,
    pendingKitbagClaimId: null,
    currentNewsId: null,
    internalBackStack: [],
    playingOrderDraft: [],
    bulkSellSelected: [],
    tgBackBound: false,
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
    ["packs", "Pack Shop", "▣"],
    ["help", "Help & support", "?"],
    ["news", "News Update", "▤"],
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
        let cached = imageCache.get(url);
        if (!cached) {
          const response = await fetch(url, { headers: { "X-Telegram-Init-Data": initData }, cache: "force-cache" });
          if (!response.ok) throw new Error("image unavailable");
          const kind = response.headers.get("x-crickium-card-type") || "custom";
          cached = { url: URL.createObjectURL(await response.blob()), kind };
          imageCache.set(url, cached);
        }
        node.src = cached.url || cached;
        node.classList.add("loaded");
        const art = node.closest(".player-art");
        if (art && cached.kind === "template-fallback") art.classList.add("has-default-template");
        else if (art && cached.kind === "rendered-template") art.classList.add("has-rendered-template");
        else if (art) art.classList.add("has-custom-image");
      } catch (_) {
        const art = node.closest(".player-art");
        if (art) art.classList.add("has-default-template");
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
      <button class="nav-item ${state.page === key || (key === "matches" && state.page === "match-detail") ? "active" : ""}" data-page="${key}">
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
    // Crickium owns page-level navigation. Keep it inside the content header
    // instead of coupling this control to browser history or Telegram chrome.
    const hasBackRoute = state.page !== "home" || state.internalBackStack.length > 0;
    const back = hasBackRoute
      ? `<button class="back-page-button" data-action="back-page" aria-label="Go to previous Crickium page">‹</button>`
      : "";
    root.innerHTML = `<div class="app-frame">${topbar()}
      <main class="page-content">
        <div class="page-heading"><div class="page-heading-main">${back}<div><span class="eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1></div></div>
          <button class="icon-action" data-action="refresh" aria-label="Refresh">↻</button>
        </div>
        ${content}
      </main>${bottomNav()}${drawer()}<div id="toast" class="toast" role="status"></div></div>`;
    hideLoader();
    hydrateImages();
    syncTelegramBackButton();
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
        <div class="default-card-overlay"><b>${esc(player.name || "Player")}</b><span>${esc(player.country || "")}${player.role ? ` · ${esc(player.role)}` : ""}</span><small>BAT ${number(player.bat_level)} · BOWL ${number(player.bowl_level)}</small></div>
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
    const statusRaw = String(match.status || (active ? "in_progress" : "completed")).toLowerCase();
    const isLive = Boolean(match.is_live) || ["live", "playing", "innings_break", "chase", "in_progress"].includes(statusRaw);
    const label = active ? (isLive ? "LIVE NOW" : statusRaw.replaceAll("_", " ").toUpperCase()) : (won ? "WON" : match.won === false ? "LOST" : statusRaw.toUpperCase());
    const engine = esc(match.engine || "MATCH");
    const teamA = esc(match.team_a_name || match.challenger_team_name || match.challenger_name || "Your match");
    const teamB = esc(match.team_b_name || match.opponent_team_name || match.opponent_name || "Opponent");
    const score = match.current_score || (match.runs != null ? `${number(match.runs)} runs · ${number(match.wickets)} wickets` : "Score not stored");
    return `<button class="match-card ${active ? "is-active" : ""}" data-match-engine="${engine}" data-match-id="${Number(match.match_id || 0)}" aria-label="Open ${teamA} versus ${teamB}">
      <div class="match-top"><span class="match-type">${engine} · ${esc(match.match_type || "Cricket")}</span><span class="match-status ${isLive ? "live" : won ? "won" : ""}">${esc(label)}</span></div>
      <div class="match-versus"><div class="match-team"><b>${teamA}</b><small>${esc(match.challenger_username ? `@${match.challenger_username}` : "")}</small></div><span class="match-vs">VS</span><div class="match-team right"><b>${teamB}</b><small>${esc(match.opponent_username ? `@${match.opponent_username}` : "")}</small></div></div>
      <div class="match-card-score"><b>${esc(score)}</b>${match.over_text ? `<span>${esc(match.over_text)} overs</span>` : ""}</div>
      <div class="match-bottom"><span>${esc(match.batting_team ? `${match.batting_team} batting` : match.pitch || (match.target ? `Target ${number(match.target)}` : "Open match"))}</span><time>${esc(dateText(match.played_at || match.created_at))} ›</time></div>
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

  async function loadMatches(options = {}) {
    state.page = "matches";
    const seq = ++state.matchesSeq;
    const previousScroll = Number(window.scrollY || 0);
    if (!options.silent) shell(`<div id="matches-body"><div class="loading-line">Loading your match records…</div></div>`, "Matches", "FIXTURES & RESULTS");
    try {
      const data = await api("/api/matches");
      if (seq !== state.matchesSeq || state.page !== "matches") return;
      const active = data.active || [];
      const recent = data.recent || [];
      const body = document.getElementById("matches-body");
      if (!body) return;
      const inProgress = active.filter((item) => !item.is_completed);
      const filteredActive = state.matchesFilter === "recent" ? [] : inProgress;
      const filteredRecent = state.matchesFilter === "ongoing" ? [] : recent;
      body.innerHTML = `<div class="match-filter-row" role="tablist" aria-label="Match filters">
          <button class="filter-chip ${state.matchesFilter === "ongoing" ? "selected" : ""}" data-action="match-filter" data-filter="ongoing">In progress · ${number(inProgress.length)}</button>
          <button class="filter-chip ${state.matchesFilter === "recent" ? "selected" : ""}" data-action="match-filter" data-filter="recent">Recent results</button>
          <button class="filter-chip ${state.matchesFilter === "all" ? "selected" : ""}" data-action="match-filter" data-filter="all">All matches</button>
        </div>
        ${filteredActive.length ? `${sectionTitle("Live for you", `<button class="text-button" data-action="refresh">Refresh ↻</button>`)}<div class="stack">${filteredActive.map((item) => matchCard(item, true)).join("")}</div>` : state.matchesFilter !== "recent" ? emptyState("No active games", "Matches involving your account appear when Crickium has a current match record.") : ""}
        ${filteredRecent.length ? `${sectionTitle("Recent results", `<span class="muted-inline">${number(filteredRecent.length)} stored</span>`)}<div class="stack">${filteredRecent.map((item) => matchCard(item, false)).join("")}</div>` : state.matchesFilter !== "ongoing" ? emptyState("No recorded results yet", "Completed match summaries appear after the bot stores a result for your account.") : ""}
        <p class="notice">${esc(data.note || "Only matches and statistics stored by Crickium are shown. Missing scores are not invented.")}</p>`;
      if (options.silent && previousScroll) window.scrollTo({ top: previousScroll, behavior: "auto" });
    } catch (error) {
      if (seq !== state.matchesSeq || state.page !== "matches") return;
      if (options.silent) return;
      const body = document.getElementById("matches-body");
      if (body) body.innerHTML = errorState(error, "matches");
    }
  }

  function newRequestId() {
    try { if (window.crypto?.randomUUID) return window.crypto.randomUUID(); } catch (_) {}
    return `crickium-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function persistedRequestId(kind, packKey) {
    const key = `crickium:${kind}:${String(packKey || "").toLowerCase()}`;
    try {
      const existing = window.sessionStorage?.getItem(key);
      if (existing) return existing;
      const created = newRequestId();
      window.sessionStorage?.setItem(key, created);
      return created;
    } catch (_) { return newRequestId(); }
  }

  function clearPersistedRequestId(kind, packKey) {
    try { window.sessionStorage?.removeItem(`crickium:${kind}:${String(packKey || "").toLowerCase()}`); } catch (_) {}
  }

  function renderPackOpening(opening) {
    if (!opening) return "";
    const cards = opening.cards || [];
    return `<section class="panel pack-reveal-panel"><div class="panel-heading"><div><span class="eyebrow">PACK OPENED</span><h2>${esc(String(opening.pack_key || "pack").toUpperCase())} REVEAL</h2></div><button class="close-button" data-action="close-pack-reveal" aria-label="Close reveal">×</button></div>
      <div class="pack-reward-strip"><span>🪙 ${number(opening.coins)} coins awarded</span><span>◆ ${number(opening.rubies)} rubies awarded</span></div>
      ${cards.length ? `<div class="player-grid">${cards.map((card) => `<div class="pack-reveal-card"><span class="pack-card-kind">${esc(String(card.kind || "card").toUpperCase())}</span>${card.player ? playerCard(card.player, true) : `<div class="empty-state"><b>Player details unavailable</b><p>The saved reveal could not provide card details.</p></div>`}<small class="muted-copy">${number(card.ovr)} OVR · ${esc(card.rarity || "Rarity unavailable")}</small></div>`).join("")}</div>` : emptyState("No cards returned", "The server did not return persisted cards for this opening.")}
      <p class="notice">This reveal is loaded from the saved opening record. Reopening history shows the same cards and rewards.</p></section>`;
  }

  function renderPackShop(data = state.packData) {
    if (!data) return;
    state.packData = data;
    const wallet = data.wallet || {};
    const packs = data.packs || [];
    const history = state.packHistory || [];
    const packMarkup = packs.map((pack) => {
      const enough = Number(wallet.sigils || 0) >= Number(pack.price_sigils || 0);
      const owned = Number(pack.quantity || 0);
      return `<article class="pack-store-card panel"><div class="pack-store-head"><span class="pack-emblem ${esc(pack.key)}">${esc(pack.key.slice(0, 1).toUpperCase())}</span><div><h2>${esc(pack.name)}</h2><p>${esc(pack.description || "Pack details provided by Crickium catalogue.")}</p></div><span class="pack-price">${number(pack.price_sigils)} SG</span></div>
        <div class="pack-spec-grid"><div><small>CARDS</small><b>${number(pack.cards)}</b></div><div><small>CORE OVR</small><b>${number(pack.core_ovr?.[0])}–${number(pack.core_ovr?.[1])}</b></div><div><small>BONUS OVR</small><b>${number(pack.bonus_ovr?.[0])}–${number(pack.bonus_ovr?.[1])}</b></div></div>
        <div class="pack-rewards-line"><span>🪙 ${number(pack.coins_reward)} coins</span><span>◆ ${number(pack.rubies_reward)} rubies</span><b>Owned · ${number(owned)}</b></div>
        <div class="pack-actions"><button class="button primary" data-action="buy-pack" data-pack-key="${esc(pack.key)}" ${!pack.enabled || !enough ? "disabled" : ""}>${!pack.enabled ? "Unavailable" : !enough ? "Insufficient Sigils" : "Buy pack"}</button><button class="button secondary" data-action="open-pack" data-pack-key="${esc(pack.key)}" ${owned < 1 ? "disabled" : ""}>Open${owned ? ` · ${number(owned)}` : ""}</button></div>
        ${!enough ? `<small class="pack-help">Need ${number(Number(pack.price_sigils || 0) - Number(wallet.sigils || 0))} more Sigils to buy this pack.</small>` : ""}
      </article>`;
    }).join("");
    const historyMarkup = history.length ? `<div class="stack">${history.map((item) => `<button class="pack-history-row" data-action="open-history" data-opening-id="${esc(item.opening_id)}"><span class="pack-history-badge">▣</span><span><b>${esc(String(item.pack_key || "pack").toUpperCase())} Pack</b><small>${esc(dateText(item.opened_at))} · ${(item.cards || []).length} cards</small></span><strong>🪙 ${number(item.coins)} <small>◆ ${number(item.rubies)}</small></strong><b>›</b></button>`).join("")}</div>` : emptyState("No pack openings yet", "Your saved pack reveals will appear here after your first successful opening.");
    shell(`<section class="pack-wallet-panel panel"><div><span class="eyebrow">YOUR PACK WALLET</span><h2>${number(wallet.sigils)} <small>Sigils</small></h2><p>Coins ${number(wallet.coins)} · Rubies ${number(wallet.rubies)}</p></div><button class="button secondary" data-action="refresh">Refresh</button></section>
      ${sectionTitle("Pack catalogue", `<span class="muted-inline">Server prices · SG</span>`)}
      <div class="pack-shop-list">${packMarkup || emptyState("No packs available", "The server catalogue has no enabled packs right now.")}</div>
      ${sectionTitle("Recent openings", `<button class="text-button" data-action="refresh">Refresh ↻</button>`)}${historyMarkup}
      ${renderPackOpening(state.packOpening)}
      <p class="notice">Purchases, inventory, card selection and rewards are confirmed by Crickium's server-side pack service. A shown price or card reveal is never controlled by the client.</p>`, "Pack Shop", "CARD PACKS");
    hydrateImages();
  }

  async function loadPacks(options = {}) {
    if (options.opening !== undefined) state.packOpening = options.opening;
    shell(`<div class="loading-line">Loading the server pack catalogue and inventory…</div>`, "Pack Shop", "CARD PACKS");
    try {
      const [data, history] = await Promise.all([
        api("/api/packs"),
        api("/api/packs/openings?limit=10").catch(() => ({ openings: [] })),
      ]);
      state.packData = data;
      state.packHistory = history.openings || [];
      renderPackShop(data);
    } catch (error) {
      shell(errorState(error, "packs"), "Pack Shop", "CARD PACKS");
    }
  }

  async function buyPack(packKey) {
    if (state.packBusy) return;
    state.packBusy = true;
    state.purchaseRequestIds = state.purchaseRequestIds || {};
    const requestKey = state.purchaseRequestIds[packKey] || (state.purchaseRequestIds[packKey] = persistedRequestId("pack-purchase", packKey));
    let requestId = null;
    try {
      const request = await api("/api/packs/purchase-request", { method: "POST", body: JSON.stringify({ pack_key: packKey, client_request_id: requestKey }) });
      requestId = request.request_id;
      const confirmed = await api("/api/packs/purchase-confirm", { method: "POST", body: JSON.stringify({ request_id: requestId }) });
      if (confirmed.status !== "success") throw Object.assign(new Error(confirmed.detail || confirmed.status || "Purchase failed"), { payload: confirmed, status: 409 });
      delete state.purchaseRequestIds[packKey];
      clearPersistedRequestId("pack-purchase", packKey);
      ping("medium");
      toast(`${confirmed.pack_key || packKey} pack purchased successfully.`);
      await loadPacks({ opening: null });
    } catch (error) {
      if (requestId && [400, 403, 404, 409].includes(Number(error.status))) {
        try { await api("/api/packs/purchase-cancel", { method: "POST", body: JSON.stringify({ request_id: requestId }) }); } catch (_) {}
        delete state.purchaseRequestIds[packKey];
        clearPersistedRequestId("pack-purchase", packKey);
      }
      toast(error.message || "Purchase could not be confirmed. Refresh before trying again.", true);
      await loadPacks();
    } finally { state.packBusy = false; }
  }

  async function openPack(packKey) {
    if (state.packBusy) return;
    state.packBusy = true;
    state.openingRequestIds = state.openingRequestIds || {};
    const requestKey = state.openingRequestIds[packKey] || (state.openingRequestIds[packKey] = persistedRequestId("pack-opening", packKey));
    try {
      const opening = await api("/api/packs/open", { method: "POST", body: JSON.stringify({ pack_key: packKey, client_request_id: requestKey }) });
      if (opening.status !== "success") throw Object.assign(new Error(opening.detail || opening.status || "Pack opening failed"), { payload: opening, status: 409 });
      delete state.openingRequestIds[packKey];
      clearPersistedRequestId("pack-opening", packKey);
      state.packOpening = opening;
      ping("heavy");
      toast(opening.replayed ? "Loaded your saved pack reveal." : "Pack opened. Your cards and rewards are saved.");
      await loadPacks({ opening });
    } catch (error) {
      if ([400, 401, 403, 404, 409].includes(Number(error.status))) {
        delete state.openingRequestIds[packKey];
        clearPersistedRequestId("pack-opening", packKey);
      }
      toast(error.message || "Opening result uncertain. Retry to recover the same saved reveal.", true);
      await loadPacks();
    } finally { state.packBusy = false; }
  }

  async function openPackHistory(openingId) {
    if (!openingId) return;
    try {
      const opening = await api(`/api/packs/openings/${encodeURIComponent(openingId)}`);
      state.packOpening = opening;
      renderPackShop(state.packData);
    } catch (error) { toast(error.message || "Could not load this saved opening.", true); }
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

  async function loadPlayer(kind, id, options = {}) {
    const playerRef = { kind: String(kind || "global"), id: Number(id) };
    if (!options.skipRoute) {
      state.playerReturnPage = state.page === "player-detail" ? (state.playerReturnPage || "search") : state.page;
      pushRoute({ page: "player-detail", currentPlayer: playerRef, returnPage: state.playerReturnPage });
    } else if (options.returnPage) {
      state.playerReturnPage = options.returnPage;
    }
    state.currentPlayerRef = playerRef;
    state.page = "player-detail";
    shell(`<div class="loading-line">Loading player profile…</div>`, "Player profile", "SCOUT REPORT");
    try {
      const player = await api(`/api/player?kind=${encodeURIComponent(playerRef.kind)}&id=${encodeURIComponent(playerRef.id)}`);
      state.currentPlayer = player;
      renderPlayerDetail(player);
    } catch (error) {
      shell(errorState(error, state.playerReturnPage || "search"), "Player profile", "SCOUT REPORT");
    }
  }

  function statRows(items) {
    return items.map(([label, value]) => `<div class="detail-stat"><span>${esc(label)}</span><b>${value == null ? "—" : esc(value)}</b></div>`).join("");
  }

  function renderPlayerDetail(p) {
    const bat = p.batting_stats || {};
    const bowl = p.bowling_stats || {};
    const h = p.match_history || [];
    const content = `<section class="detail-hero">${image(p.card_image_url, p.name, "detail-image")}<div class="rating detail-rating">${number(p.overall)}<small>OVR</small></div>
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

  function managedPlayerCard(player, squadCount) {
    const squadPlayerId = Number(player.squad_player_id ?? player.player_id ?? 0);
    const inXi = Boolean(player.in_playing_xi);
    const disabledXI = Number(squadCount) < 12;
    const disabledSell = Number(squadCount) <= 11;
    return `<div class="managed-player-card">
      ${playerCard(player, true)}
      <div class="managed-player-state ${inXi ? "active-xi" : "bench-player"}">${inXi ? "✓ PLAYING XI" : "SUBSTITUTE"}</div>
      <div class="managed-actions">
        <button class="button secondary full" data-action="lineup-swap-start" data-player-id="${squadPlayerId}" data-mode="${inXi ? "remove" : "add"}" ${disabledXI ? "disabled" : ""}>${inXi ? "Move to substitutes" : "Add to Playing XI"}</button>
        <button class="button sell-button full" data-action="sell-squad-player" data-player-id="${squadPlayerId}" data-name="${esc(player.name || "player")}" data-sell-price="${Number(player.sell_price || 0)}" ${disabledSell ? "disabled" : ""}>Sell · ${number(player.sell_price)} coins</button>
      </div>
    </div>`;
  }

  function renderSquadList(data, hostId = "squad-list") {
    state.squadData = data;
    const host = document.getElementById(hostId);
    if (!host) return;
    const players = data.players || [];
    const lineupOrder = new Map((data.lineup_player_ids || []).map((id, index) => [Number(id), index]));
    const xi = players.filter((player) => player.in_playing_xi).sort((a, b) => (lineupOrder.get(Number(a.squad_player_id ?? a.player_id)) ?? 99) - (lineupOrder.get(Number(b.squad_player_id ?? b.player_id)) ?? 99));
    const bench = players.filter((player) => !player.in_playing_xi);
    host.innerHTML = `<div class="squad-capacity"><span>SQUAD CAPACITY</span><b>${number(data.count)} / ${number(data.capacity)}</b><i style="width:${Math.min(100, data.count / Math.max(1, data.capacity) * 100)}%"></i></div>
      <section class="managed-roster-section">${sectionTitle(`Playing XI · ${number(xi.length)}/11`, `<button class="button secondary roster-header-action" data-action="set-playing-order">Set Playing Order</button>`)}
        ${xi.length ? `<div class="player-grid">${xi.map((item) => managedPlayerCard(item, data.count)).join("")}</div>` : emptyState("Playing XI not set", "Add at least eleven players to manage your team lineup.")}</section>
      <section class="managed-roster-section">${sectionTitle(`Substitutes · ${number(bench.length)}`, `<button class="button secondary roster-header-action" data-action="open-sell-multiple">Sell Multiple</button>`)}
        ${bench.length ? `<div class="player-grid">${bench.map((item) => managedPlayerCard(item, data.count)).join("")}</div>` : emptyState("No substitutes yet", "Own more than eleven players to add substitutes to your Playing XI.")}</section>
      <p class="notice">Lineup changes are saved to Crickium’s existing Playing XI record, so bot match flows use the same selected players. Squad sales use the bot price chart and require at least eleven players to remain.</p>`;
    hydrateImages(host);
  }

  async function loadSquad() {
    shell(`<div id="squad-list"><div class="loading-line">Loading squad cards…</div></div>`, "Squad management", "PLAYER ROSTER");
    try {
      const data = await api("/api/squad");
      renderSquadList(data, "squad-list");
    } catch (error) {
      document.getElementById("squad-list").innerHTML = errorState(error, "squad");
    }
  }

  function renderPlayingOrder() {
    const host = document.getElementById("playing-order-content");
    if (!host) return;
    const players = state.squadData?.players || [];
    const byId = new Map(players.map((item) => [Number(item.squad_player_id ?? item.player_id), item]));
    const ordered = (state.playingOrderDraft || []).map((id) => byId.get(Number(id))).filter(Boolean);
    const rows = ordered.map((player, index) => {
      const playerId = Number(player.squad_player_id ?? player.player_id);
      const overall = Number(player.overall || Math.max(player.bat_level || 0, player.bowl_level || 0));
      return `<div class="order-row">
        <span class="order-position">${String(index + 1).padStart(2, "0")}</span>
        <div class="order-player-copy"><b>${esc(player.name || "Player")}</b><small>${esc(player.role || "Player")}${player.country ? ` · ${esc(player.country)}` : ""}</small></div>
        <span class="order-rating">${overall}<small>OVR</small></span>
        <div class="order-controls"><button class="order-move" data-action="move-playing-order" data-player-id="${playerId}" data-direction="up" aria-label="Move ${esc(player.name)} forward" ${index === 0 ? "disabled" : ""}>↑</button><button class="order-move" data-action="move-playing-order" data-player-id="${playerId}" data-direction="down" aria-label="Move ${esc(player.name)} backward" ${index === ordered.length - 1 ? "disabled" : ""}>↓</button></div>
      </div>`;
    }).join("");
    const changed = (state.playingOrderDraft || []).join(",") !== (state.squadData?.lineup_player_ids || []).join(",");
    host.innerHTML = `<section class="panel order-intro"><span class="eyebrow">PLAYING XI · BATTING ORDER</span><h2>Set Playing Order</h2><p>Move each player forward or backward to choose the order used by Crickium match flows.</p><div class="order-legend"><span>POSITION</span><span>PLAYER · ROLE</span><span>RATING</span></div></section>
      <div class="order-list">${rows || emptyState("Playing XI unavailable", "Set up eleven players before changing their order.")}</div>
      <div class="order-save-wrap"><button class="button primary full" data-action="save-playing-order" ${!changed || ordered.length !== 11 ? "disabled" : ""}>Save Playing Order</button><p class="muted-copy">Your changes are saved to the same lineup record used by bot match commands.</p></div>`;
  }

  async function loadPlayingOrder() {
    state.page = "lineup-order";
    state.playingOrderDraft = [];
    shell(`<div id="playing-order-content"><div class="loading-line">Loading your Playing XI…</div></div>`, "Set Playing Order", "SQUAD MANAGEMENT");
    try {
      const data = await api("/api/squad");
      state.squadData = data;
      const ids = (data.lineup_player_ids || []).map(Number);
      const valid = new Set((data.players || []).map((item) => Number(item.squad_player_id ?? item.player_id)));
      state.playingOrderDraft = ids.filter((id) => valid.has(id)).slice(0, 11);
      renderPlayingOrder();
      hydrateImages(document.getElementById("playing-order-content"));
    } catch (error) {
      document.getElementById("playing-order-content").innerHTML = errorState(error, "squad");
    }
  }

  function movePlayingOrder(playerId, direction) {
    const draft = [...(state.playingOrderDraft || [])];
    const index = draft.indexOf(Number(playerId));
    const nextIndex = index + (direction === "up" ? -1 : 1);
    if (index < 0 || nextIndex < 0 || nextIndex >= draft.length) return;
    [draft[index], draft[nextIndex]] = [draft[nextIndex], draft[index]];
    state.playingOrderDraft = draft;
    renderPlayingOrder();
    ping("light");
  }

  async function savePlayingOrder(button) {
    if (button) { button.disabled = true; button.textContent = "Saving order…"; }
    try {
      const result = await api("/api/squad/lineup/order", {
        method: "POST",
        body: JSON.stringify({ player_ids: state.playingOrderDraft.map(Number) }),
      });
      if (result.status !== "success") throw Object.assign(new Error(result.detail || result.status || "Could not save order"), { payload: result });
      toast("Playing order saved for Crickium matches.");
      ping("medium");
      state.playingOrderDraft = [];
      backInApp();
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = "Try again"; }
    }
  }

  function renderBulkSellPage() {
    const host = document.getElementById("bulk-sell-content");
    if (!host) return;
    const players = (state.squadData?.players || []).filter((item) => !item.in_playing_xi);
    const available = new Set(players.map((item) => Number(item.squad_player_id ?? item.player_id)));
    state.bulkSellSelected = (state.bulkSellSelected || []).filter((id) => available.has(Number(id)));
    const selectedSet = new Set(state.bulkSellSelected.map(Number));
    const chosen = players.filter((item) => selectedSet.has(Number(item.squad_player_id ?? item.player_id)));
    const total = chosen.reduce((sum, item) => sum + Number(item.sell_price || 0), 0);
    const cards = players.map((player) => {
      const id = Number(player.squad_player_id ?? player.player_id);
      const isSelected = selectedSet.has(id);
      return `<div class="bulk-sale-player ${isSelected ? "selected" : ""}" data-action="toggle-bulk-player" data-player-id="${id}" role="checkbox" aria-checked="${isSelected}" tabindex="0">
        <span class="bulk-select-mark">${isSelected ? "✓" : "＋"}</span>${playerCard({ ...player, buy_price: null }, true)}<div class="bulk-sale-value">Resale value <b>${number(player.sell_price)} coins</b></div>
      </div>`;
    }).join("");
    host.innerHTML = `<section class="bulk-selection-summary"><div><span>SELECTED PLAYERS</span><b>${number(chosen.length)}</b></div><div><span>TOTAL SELL VALUE</span><b>${number(total)} <small>coins</small></b></div><p>Choose one or more substitutes. The Playing XI is protected and at least eleven squad players must remain.</p></section>
      ${sectionTitle("Select substitutes", `<span class="muted-inline">${number(players.length)} available</span>`)}
      ${cards ? `<div class="player-grid bulk-sale-grid">${cards}</div>` : emptyState("No substitutes available", "You need reserve players before using Sell Multiple.")}
      <div class="bulk-sale-footer"><button class="button sell-button full" data-action="confirm-sell-multiple" data-count="${chosen.length}" data-amount="${total}" ${!chosen.length ? "disabled" : ""}>Sell ${number(chosen.length)} players · ${number(total)} coins</button></div>`;
    hydrateImages(host);
  }

  async function loadSellMultiple() {
    state.page = "sell-multiple";
    state.bulkSellSelected = [];
    shell(`<div id="bulk-sell-content"><div class="loading-line">Loading substitute players…</div></div>`, "Sell Multiple", "SQUAD MANAGEMENT");
    try {
      state.squadData = await api("/api/squad");
      renderBulkSellPage();
    } catch (error) {
      document.getElementById("bulk-sell-content").innerHTML = errorState(error, "squad");
    }
  }

  function toggleBulkSellPlayer(playerId) {
    const id = Number(playerId);
    const selected = new Set((state.bulkSellSelected || []).map(Number));
    if (selected.has(id)) selected.delete(id); else selected.add(id);
    state.bulkSellSelected = Array.from(selected);
    renderBulkSellPage();
    ping("light");
  }

  async function sellMultiplePlayers(button) {
    const playerIds = (state.bulkSellSelected || []).map(Number);
    if (!playerIds.length) return;
    if (button) { button.disabled = true; button.textContent = "Selling selected players…"; }
    try {
      const result = await api("/api/squad/sell-multiple", {
        method: "POST",
        body: JSON.stringify({ player_ids: playerIds }),
      });
      if (result.status !== "success") throw Object.assign(new Error(result.detail || result.status || "Bulk sale failed"), { payload: result });
      if (state.home?.wallet) state.home.wallet.coins = result.balance;
      state.bulkSellSelected = [];
      toast(`${number(result.sold_count)} players sold for ${number(result.total_sell_price)} coins.`);
      ping("medium");
      backInApp();
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = "Try again"; }
    }
  }

  async function loadCollection() {
    shell(`<div id="collection-list"><div class="loading-line">Loading your collection…</div></div>`, "Player collection", "YOUR PLAYER CARDS");
    try {
      const data = await api("/api/collection");
      state.squadData = data;
      const squadPlayers = data.players || [];
      const packCards = data.pack_cards || [];
      const lineupOrder = new Map((data.lineup_player_ids || []).map((id, index) => [Number(id), index]));
      const xiPlayers = squadPlayers.filter((item) => item.in_playing_xi).sort((a, b) => (lineupOrder.get(Number(a.squad_player_id ?? a.player_id)) ?? 99) - (lineupOrder.get(Number(b.squad_player_id ?? b.player_id)) ?? 99));
      const benchPlayers = squadPlayers.filter((item) => !item.in_playing_xi);
      const squadSection = squadPlayers.length
        ? `<section class="collection-group">${sectionTitle(`Squad cards · ${number(data.count)}`)}<p class="muted-copy">Manage Playing XI selection or sell a player without leaving the collection.</p>
            ${sectionTitle(`Playing XI · ${number(xiPlayers.length)}/11`)}<div class="player-grid">${xiPlayers.map((item) => managedPlayerCard(item, data.count)).join("")}</div>
            ${benchPlayers.length ? `${sectionTitle(`Substitutes · ${number(benchPlayers.length)}`)}<div class="player-grid">${benchPlayers.map((item) => managedPlayerCard(item, data.count)).join("")}</div>` : ""}</section>`
        : `<section class="collection-group">${emptyState("Your squad is empty", "Player cards currently assigned to your active squad appear here.")}</section>`;
      const packSection = packCards.length
        ? `<section class="collection-group">${sectionTitle(`Pack collection · ${number(packCards.length)}`)}<p class="muted-copy">Cards saved from pack openings are separate from your active squad. Sell a card here to return its listed resale value to your coin balance.</p><div class="player-grid">${packCards.map((item) => `<div class="collection-pack-card">${playerCard(item, true)}<small>${esc(String(item.pack_key || "pack").toUpperCase())} · ${esc(String(item.pack_slot || "bonus").toUpperCase())} · ${number(item.card_ovr)} OVR</small><button class="button sell-button full" data-action="sell-pack-card" data-card-id="${Number(item.card_id)}" data-name="${esc(item.name || "player")}" data-sell-price="${Number(item.sell_price || 0)}">Sell · ${number(item.sell_price)} coins</button></div>`).join("")}</div></section>`
        : `<section class="collection-group">${emptyState("No pack cards yet", "Cards you open from packs will be saved here without being added to your active squad automatically.")}</section>`;
      document.getElementById("collection-list").innerHTML = squadSection + packSection + `<p class="notice">Selling a squad card updates the same squad used by bot commands. Pack-owned cards are sold from their separate collection inventory.</p>`;
      hydrateImages();
    } catch (error) {
      document.getElementById("collection-list").innerHTML = errorState(error, "collection");
    }
  }


  async function startLineupSwap(playerId, mode) {
    state.lineupSwap = { player_id: Number(playerId), mode: mode === "add" ? "add" : "remove" };
    pushRoute({ page: "lineup-swap", lineupSwap: state.lineupSwap });
    await loadLineupSwap({ skipRoute: true });
  }

  async function loadLineupSwap(options = {}) {
    if (!state.lineupSwap?.player_id) {
      await navigate("squad", { history: false });
      return;
    }
    state.page = "lineup-swap";
    const choice = state.lineupSwap;
    const mode = choice.mode === "add" ? "add" : "remove";
    shell(`<div id="lineup-swap-content"><div class="loading-line">Loading your Playing XI…</div></div>`, mode === "add" ? "Add to Playing XI" : "Change Playing XI", "SQUAD MANAGEMENT");
    try {
      const data = await api("/api/squad");
      state.squadData = data;
      const players = data.players || [];
      const target = players.find((item) => Number(item.squad_player_id ?? item.player_id) === Number(choice.player_id));
      if (!target || players.length < 12) {
        document.getElementById("lineup-swap-content").innerHTML = `${emptyState("No valid substitute available", "You need at least twelve squad players to swap one into or out of the Playing XI.")}<button class="button secondary full" data-page="squad">Back to squad</button>`;
        return;
      }
      const candidates = mode === "add" ? players.filter((item) => item.in_playing_xi) : players.filter((item) => !item.in_playing_xi);
      const heading = mode === "add"
        ? `Choose the Playing XI player to move out for ${esc(target.name)}.`
        : `Choose a substitute to enter the XI in place of ${esc(target.name)}.`;
      const choices = candidates.map((item) => {
        const playingId = mode === "add" ? Number(item.squad_player_id ?? item.player_id) : Number(target.squad_player_id ?? target.player_id);
        const substituteId = mode === "add" ? Number(target.squad_player_id ?? target.player_id) : Number(item.squad_player_id ?? item.player_id);
        return `<div class="lineup-choice-card">${playerCard(item, true)}<button class="button secondary full" data-action="confirm-lineup-swap" data-playing-player-id="${playingId}" data-substitute-player-id="${substituteId}" data-name="${esc(item.name)}">${mode === "add" ? "Swap this player out" : "Bring this substitute in"}</button></div>`;
      }).join("");
      document.getElementById("lineup-swap-content").innerHTML = `<section class="panel lineup-swap-intro"><span class="eyebrow">YOUR SELECTED PLAYER</span><h2>${esc(target.name)}</h2><p>${heading}</p></section>${sectionTitle(mode === "add" ? "Current Playing XI" : "Available substitutes", `<span class="muted-inline">${number(candidates.length)} players</span>`)}<div class="player-grid">${choices}</div><p class="notice">This swaps the two players in the saved Playing XI. It never changes the 25-card squad size.</p>`;
      hydrateImages(document.getElementById("lineup-swap-content"));
    } catch (error) {
      document.getElementById("lineup-swap-content").innerHTML = errorState(error, "squad");
    }
  }

  async function confirmLineupSwap(playingPlayerId, substitutePlayerId, button) {
    if (button) { button.disabled = true; button.textContent = "Saving XI…"; }
    try {
      const result = await api("/api/squad/lineup/swap", {
        method: "POST",
        body: JSON.stringify({ playing_player_id: Number(playingPlayerId), substitute_player_id: Number(substitutePlayerId) }),
      });
      if (result.status !== "success") throw Object.assign(new Error(result.status), { payload: result });
      state.lineupSwap = null;
      state.page = "squad";
      replaceRoute({ page: "squad", lineupSwap: null, currentPlayer: null, kitbagClaimId: state.pendingKitbagClaimId });
      await navigate("squad", { history: false });
      toast("Playing XI updated. The bot will use this saved lineup.");
      ping("medium");
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = "Try again"; }
    }
  }

  async function sellSquadPlayer(playerId, button) {
    if (button) { button.disabled = true; button.textContent = "Selling…"; }
    try {
      const result = await api("/api/squad/sell", { method: "POST", body: JSON.stringify({ player_id: Number(playerId) }) });
      if (result.status !== "success") throw Object.assign(new Error(result.status), { payload: result });
      if (state.home?.wallet) state.home.wallet.coins = result.balance;
      const soldName = result.player?.name || "Player";
      const page = state.page === "collection" ? "collection" : "squad";
      await (page === "collection" ? loadCollection() : loadSquad());
      toast(`${soldName} sold for ${number(result.sell_price)} coins.`);
      ping("medium");
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = "Sell failed · Retry"; }
    }
  }

  async function sellPackCard(cardId, button) {
    if (button) { button.disabled = true; button.textContent = "Selling…"; }
    try {
      const result = await api("/api/collection/sell-pack-card", { method: "POST", body: JSON.stringify({ card_id: Number(cardId) }) });
      if (result.status !== "success") throw Object.assign(new Error(result.status), { payload: result });
      if (state.home?.wallet) state.home.wallet.coins = result.balance;
      await loadCollection();
      toast(`${result.player?.name || "Player"} sold for ${number(result.sell_price)} coins.`);
      ping("medium");
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = "Sell failed · Retry"; }
    }
  }

  async function startKitbagReplacement(claimId) {
    state.pendingKitbagClaimId = String(claimId || state.pendingKitbag?.claim_id || "");
    pushRoute({ page: "kitbag-replace", kitbagClaimId: state.pendingKitbagClaimId });
    await loadKitbagReplacement(state.pendingKitbagClaimId, { skipRoute: true });
  }

  async function loadKitbagReplacement(claimId, options = {}) {
    state.page = "kitbag-replace";
    state.pendingKitbagClaimId = String(claimId || state.pendingKitbagClaimId || "");
    shell(`<div id="kitbag-replace-content"><div class="loading-line">Loading saved Kit Bag reward…</div></div>`, "Replace a player", "KIT BAG REWARD");
    try {
      if (!state.pendingKitbag || state.pendingKitbag.claim_id !== state.pendingKitbagClaimId) {
        const rewards = await api("/api/rewards");
        state.pendingKitbag = rewards.pending_kitbag || null;
      }
      const pending = state.pendingKitbag;
      if (!pending || !pending.player || pending.claim_id !== state.pendingKitbagClaimId) {
        document.getElementById("kitbag-replace-content").innerHTML = `${emptyState("No pending replacement", "This Kit Bag reward has already been added or resolved.")}<button class="button primary full" data-page="rewards">Return to rewards</button>`;
        return;
      }
      const squad = await api("/api/squad");
      state.squadData = squad;
      const choices = (squad.players || []).map((item) => `<div class="replace-choice-card">${playerCard(item, true)}<button class="button secondary full" data-action="replace-kitbag-player" data-claim-id="${esc(pending.claim_id)}" data-replaced-player-id="${Number(item.squad_player_id ?? item.player_id)}" data-name="${esc(item.name)}">Replace this player</button></div>`).join("");
      document.getElementById("kitbag-replace-content").innerHTML = `<section class="panel kitbag-replacement-preview"><span class="eyebrow">NEW CARD · 85 OVR</span><h2>${esc(pending.player.name)}</h2><p class="muted-copy">This card is saved safely. Choose one current squad player below to swap out for it.</p><div class="pending-reward-card">${playerCard(pending.player, true)}</div></section>${sectionTitle(`Choose a player to replace · ${number(squad.count)} in squad`)}${choices ? `<div class="player-grid">${choices}</div>` : emptyState("No squad players available", "A squad player is required to complete this replacement.")}<p class="notice">The new card takes the selected player's squad slot. If that player is in the Playing XI, the new card takes their XI position too.</p>`;
      hydrateImages(document.getElementById("kitbag-replace-content"));
    } catch (error) {
      document.getElementById("kitbag-replace-content").innerHTML = errorState(error, "rewards");
    }
  }

  async function confirmKitbagReplacement(claimId, replacedPlayerId, button) {
    if (button) { button.disabled = true; button.textContent = "Replacing…"; }
    try {
      const result = await api("/api/rewards/kitbag/replace", {
        method: "POST",
        body: JSON.stringify({ claim_id: claimId, replaced_player_id: Number(replacedPlayerId) }),
      });
      if (result.status !== "success") throw Object.assign(new Error(result.status), { payload: result });
      const newName = result.player?.name || "New player";
      const oldName = result.replaced_player?.name || "selected player";
      state.pendingKitbag = null;
      state.pendingKitbagClaimId = null;
      state.page = "squad";
      replaceRoute({ page: "squad", kitbagClaimId: null, currentPlayer: null, lineupSwap: null });
      await navigate("squad", { history: false });
      toast(result.added_without_replacement
        ? `${newName} added to your squad. The open slot was used.`
        : `${newName} replaced ${oldName}. Squad updated.`);
      ping("heavy");
    } catch (error) {
      toast(claimError(error.payload || { status: error.message }), true);
      if (button) { button.disabled = false; button.textContent = "Replace failed · Retry"; }
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
      const pending = data.pending_kitbag || null;
      state.pendingKitbag = pending;
      state.pendingKitbagClaimId = pending?.claim_id || null;
      const days = daily.days || [];
      const pendingMarkup = pending ? `<section class="panel pending-reward-panel"><span class="eyebrow">REPLACEMENT REQUIRED</span><h2>Your Kit Bag player is waiting</h2><p class="muted-copy">Your squad was full when this card was claimed. Choose one squad player to replace it. The reward is safely saved until you finish.</p><div class="pending-reward-card">${playerCard(pending.player, true)}</div><button class="button primary full" data-action="open-kitbag-replace" data-claim-id="${esc(pending.claim_id)}">Choose player to replace</button></section>` : "";
      document.getElementById("rewards-content").innerHTML = `${pendingMarkup}
        <section class="kitbag-banner large"><div class="kitbag-emblem">✦</div><div><span class="eyebrow">HOURLY DROP · 85 OVR</span><h2>Free Kit Bag</h2><p>One real, unowned OVR 85 player from the global catalog. Claim cooldown is enforced by the server.</p><small>${pending ? "FINISH YOUR PENDING REPLACEMENT" : bag.available ? "READY TO OPEN" : `AVAILABLE IN ${clock(bag.seconds_until_available)}`}</small></div></section>
        <button class="button gold full" data-action="kitbag-claim" ${bag.available ? "" : "disabled"}>${pending ? "Replace pending player first" : bag.available ? "Open free Kit Bag" : "Kit Bag cooling down"}</button>
        <section class="panel reward-panel"><div class="panel-heading"><div><span class="eyebrow">7-DAY STREAK</span><h2>${number(daily.streak)} claimed · ${number(daily.total_claimed)} total claims</h2></div><button class="button primary" data-action="daily-claim" ${daily.available ? "" : "disabled"}>${daily.available ? "Claim" : clock(daily.seconds_until_available)}</button></div>
          <div class="reward-list">${days.map((day) => `<div class="reward-row ${day.status}"><span class="day-mark">${number(day.day)}</span><span><b>Day ${number(day.day)}</b><small>${day.player_range ? `Random player · OVR ${day.player_range[0]}–${day.player_range[1]}` : "Coins"}${day.rubies ? ` + ${number(day.rubies)} rubies` : ""}</small></span><strong>${number(day.coins)} 🪙</strong><i>${day.status === "claimed" ? "✓" : day.status === "available" ? "CLAIM" : "LOCKED"}</i></div>`).join("")}</div>
        </section>
        <section class="panel"><span class="eyebrow">MORE WAYS TO EARN</span><h2>Quests & match rewards</h2><p class="muted-copy">Daily, weekly and monthly quests use the bot's existing reward rules.</p><button class="button secondary full" data-page="quests">View quests</button></section>`;
      hydrateImages(document.getElementById("rewards-content"));
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

  async function loadNews() {
    shell(`<div id="news-list"><div class="loading-line">Loading latest Crickium news…</div></div>`, "News Update", "CLUB BULLETIN");
    try {
      const data = await api("/api/news?limit=50");
      const host = document.getElementById("news-list");
      const articles = data.articles || [];
      host.innerHTML = articles.length
        ? `<p class="muted-copy news-list-intro">Club announcements, updates and stories from Crickium.</p><div class="news-list">${articles.map((item) => `<button class="news-teaser" data-action="open-news" data-news-id="${Number(item.article_id)}"><div class="news-teaser-image">${image(item.image_url, item.title, "news-list-image")}</div><div class="news-teaser-copy"><span class="eyebrow">CRICKIUM NEWS · ${esc(dateText(item.created_at))}</span><h2>${esc(item.title)}</h2><p>${esc(item.excerpt || "Open this article to read the full update.")}</p><span class="news-read-more">Read article <b>›</b></span></div></button>`).join("")}</div>`
        : emptyState("No news published yet", "New Crickium announcements will appear here.");
      hydrateImages(host);
    } catch (error) {
      document.getElementById("news-list").innerHTML = errorState(error, "news");
    }
  }

  async function openNewsArticle(articleId) {
    const id = Number(articleId || 0);
    if (!id) return;
    pushRoute({ page: "news-detail", currentNewsId: id });
    state.currentNewsId = id;
    state.page = "news-detail";
    await loadNewsDetail(id, { skipRoute: true });
  }

  async function loadNewsDetail(articleId, options = {}) {
    const id = Number(articleId || state.currentNewsId || 0);
    if (!id) { await navigate("news", { history: false }); return; }
    if (!options.skipRoute && id !== Number(state.currentNewsId || 0)) pushRoute({ page: "news-detail", currentNewsId: id });
    state.currentNewsId = id;
    state.page = "news-detail";
    shell(`<div id="news-article"><div class="loading-line">Opening article…</div></div>`, "News article", "CRICKIUM NEWS");
    try {
      const article = await api(`/api/news/${id}`);
      const host = document.getElementById("news-article");
      const paragraphs = String(article.description || "").split(/\r?\n/).map((line) => line.trim());
      host.innerHTML = `<article class="news-article"><div class="news-article-meta"><span class="eyebrow">CLUB BULLETIN</span><time>${esc(dateText(article.created_at))}</time></div><h2>${esc(article.title)}</h2><div class="news-article-art">${image(article.image_url, article.title, "news-article-image")}</div><div class="news-article-body">${paragraphs.map((line) => line ? `<p>${esc(line)}</p>` : `<div class="news-paragraph-space"></div>`).join("")}</div><div class="news-article-end"><span></span> END OF ARTICLE <span></span></div></article>`;
      hydrateImages(host);
    } catch (error) {
      document.getElementById("news-article").innerHTML = errorState(error, "news");
    }
  }

  function staticPage(page) {
    if (page !== "help") { navigate("home", { history: false }); return; }
    shell(`<section class="panel info-page"><div class="info-emblem">?</div><span class="eyebrow">NEED A HAND?</span><h2>Help &amp; support</h2><p>Join the official Crickium community for help, discussion and announcements. Never share your login code or account credentials.</p><div class="support-link-list"><a class="support-link-card" href="https://t.me/CrickiumHub" target="_blank" rel="noopener noreferrer"><span class="support-link-icon">♟</span><span><b>Crickium Main Group</b><small>Join the player community</small></span><strong>↗</strong></a><a class="support-link-card" href="https://t.me/CrickiumUpdates" target="_blank" rel="noopener noreferrer"><span class="support-link-icon">◉</span><span><b>Bot Update Channel</b><small>Official updates and announcements</small></span><strong>↗</strong></a></div></section>`, "Help & support", "CLUB HOUSE");
  }

  function matchRouteSnapshot(page = state.page, match = state.currentMatch, tab = state.matchTab, innings = state.matchInnings) {
    return {
      crickium: true, page, currentMatch: match ? { engine: match.engine, id: match.id } : null,
      currentPlayer: state.currentPlayerRef, returnPage: state.playerReturnPage || "search",
      lineupSwap: state.lineupSwap, kitbagClaimId: state.pendingKitbagClaimId,
      currentNewsId: state.currentNewsId,
      matchTab: tab || "live", matchInnings: innings == null ? null : Number(innings),
      index: Number(window.history?.state?.index || 0),
    };
  }

  function pushRoute(next) {
    const current = matchRouteSnapshot();
    state.internalBackStack.push({ ...current });
    if (state.internalBackStack.length > 60) state.internalBackStack.shift();
    try {
      if (!window.history || !window.history.pushState) return;
      const existing = window.history.state;
      if (!existing || !existing.crickium) window.history.replaceState({ ...current, index: 0 }, "", window.location.href);
      const index = Number(window.history.state?.index || 0) + 1;
      const currentMatch = Object.prototype.hasOwnProperty.call(next, "currentMatch") ? next.currentMatch : state.currentMatch;
      const matchTab = Object.prototype.hasOwnProperty.call(next, "matchTab") ? next.matchTab : state.matchTab;
      const matchInnings = Object.prototype.hasOwnProperty.call(next, "matchInnings") ? next.matchInnings : state.matchInnings;
      window.history.pushState({ ...matchRouteSnapshot(next.page, currentMatch, matchTab, matchInnings), ...next, crickium: true, index }, "", window.location.href);
    } catch (_) {}
  }

  function replaceRoute(next) {
    try {
      if (!window.history?.replaceState) return;
      const existing = window.history.state || {};
      window.history.replaceState({ ...existing, ...matchRouteSnapshot(next.page || state.page), ...next, crickium: true, index: Number(existing.index || 0) }, "", window.location.href);
    } catch (_) {}
  }

  function syncTelegramBackButton() {
    // The visible back arrow is part of the Crickium page heading. Leave
    // Telegram/browser/OS navigation controls in their normal platform state.
    try { tg?.BackButton?.hide(); } catch (_) {}
  }

  async function backInApp() {
    if (state.drawer) {
      state.drawer = false;
      document.querySelector(".drawer-scrim")?.remove();
      return;
    }
    const previous = state.internalBackStack.pop();
    if (previous) {
      await restoreRoute(previous).catch(() => navigate("home", { history: false }));
      return;
    }
    state.currentMatch = null;
    state.currentMatchData = null;
    const fallback = state.page === "match-detail" ? "matches"
      : state.page === "player-detail" ? (state.playerReturnPage || "search")
      : state.page === "lineup-swap" || state.page === "lineup-order" || state.page === "sell-multiple" ? "squad"
      : state.page === "news-detail" ? "news"
      : state.page === "kitbag-replace" || state.page === "kitbag-reveal" ? "rewards"
      : "home";
    await navigate(fallback, { history: false });
  }

  function matchOverText(innings = {}) {
    if (innings.over_text != null && String(innings.over_text) !== "") return String(innings.over_text);
    const balls = Number(innings.legal_balls || 0);
    return `${Math.floor(balls / 6)}.${balls % 6}`;
  }

  function cricketBallLabel(ball) {
    if (typeof ball === "string" || typeof ball === "number") return String(ball || "•");
    if (!ball || typeof ball !== "object") return "•";
    if (ball.wicket || ball.outcome === "wicket") return "W";
    const kind = String(ball.extra_type || ball.outcome || "").toLowerCase();
    if (kind.includes("wide")) return "WD";
    if (kind.includes("no_ball") || kind === "noball") return "NB";
    const runs = Number(ball.runs || 0);
    if (runs) return String(runs);
    return "•";
  }

  function scoreValue(value) { return value == null || value === "" ? "—" : esc(value); }

  function renderBatterTable(innings) {
    const batters = Array.isArray(innings.batters) ? innings.batters : [];
    if (!batters.length) return emptyState("Batting details unavailable", "The source has not stored a batting lineup for this innings.");
    const currentNames = (state.currentMatchData?.current_batting_pair || []).map((item) => item.name);
    const rows = batters.map((batter) => {
      const status = currentNames.includes(batter.name) ? (state.currentMatchData?.current_batting_pair?.find((x) => x.name === batter.name)?.status || "at crease") : (batter.dismissed ? "out" : batter.status || (Number(batter.balls || 0) || Number(batter.runs || 0) ? "not out" : "yet to bat"));
      const statusText = batter.dismissal_text || status;
      return `<tr class="${currentNames.includes(batter.name) ? "current-player" : ""}"><td><b>${esc(batter.name || "Player")}</b><small>${esc(statusText)}</small></td><td>${number(batter.runs)}</td><td>${number(batter.balls)}</td><td>${number(batter.fours)}</td><td>${number(batter.sixes)}</td><td>${batter.strike_rate != null ? Number(batter.strike_rate).toFixed(1) : "—"}</td></tr>`;
    }).join("");
    const extras = innings.extras && typeof innings.extras === "object" ? Object.values(innings.extras).reduce((sum, value) => sum + (Number(value) || 0), 0) : null;
    return `<div class="score-table-wrap"><table class="score-table"><thead><tr><th>Batter</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead><tbody>${rows}</tbody></table><div class="score-table-total"><span>Extras ${extras == null ? "—" : number(extras)}</span><b>Total ${number(innings.runs)}/${number(innings.wickets)} · ${esc(matchOverText(innings))} ov</b></div></div>`;
  }

  function renderBowlerTable(innings) {
    const bowlers = Array.isArray(innings.bowlers) ? innings.bowlers : [];
    if (!bowlers.length) return emptyState("Bowling details unavailable", "No bowling figures were stored for this innings.");
    const currentName = state.currentMatchData?.current_bowler?.name;
    return `<div class="score-table-wrap"><table class="score-table bowling-table"><thead><tr><th>Bowler</th><th>O</th><th>M</th><th>R</th><th>W</th><th>Econ</th></tr></thead><tbody>${bowlers.map((bowler) => {
      const balls = Number(bowler.balls || 0); const overs = bowler.overs || `${Math.floor(balls / 6)}.${balls % 6}`;
      const econ = bowler.economy == null ? (balls ? (Number(bowler.runs || 0) * 6 / balls).toFixed(2) : "—") : Number(bowler.economy).toFixed(2);
      const isCurrent = Boolean(bowler.is_current || (currentName && currentName === bowler.name));
      return `<tr class="${isCurrent ? "current-player" : ""}"><td><b>${esc(bowler.name || "Bowler")}</b>${isCurrent ? "<small>currently bowling</small>" : ""}</td><td>${esc(overs)}</td><td>${number(bowler.maidens)}</td><td>${number(bowler.runs)}</td><td>${number(bowler.wickets)}</td><td>${esc(econ)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  function inningsForDetail(data) {
    const innings = Array.isArray(data.innings) ? data.innings : [];
    if (!innings.length) return null;
    const preferred = Number(state.matchInnings || data.current_innings_number || innings[innings.length - 1].innings_number || 1);
    return innings.find((item) => Number(item.innings_number || 1) === preferred) || innings[innings.length - 1];
  }

  function renderMatchDetail(data) {
    state.currentMatchData = data;
    const innings = Array.isArray(data.innings) ? data.innings : [];
    const current = inningsForDetail(data) || {};
    const currentNo = Number(current.innings_number || data.current_innings_number || 1);
    const battingPair = data.current_batting_pair || current.current_batting_pair || [];
    const bowler = data.current_bowler || null;
    const partnership = data.partnership || current.partnership || null;
    const live = Boolean(data.is_live) && !data.is_completed;
    const completed = Boolean(data.is_completed) || ["completed", "ended", "finished", "complete", "abandoned", "cancelled"].includes(String(data.status || "").toLowerCase());
    const title = completed ? "Match summary" : "Match Centre";
    const matchStatus = live ? "LIVE" : completed ? "COMPLETED" : String(data.status || "MATCH DETAILS").replaceAll("_", " ").toUpperCase();
    const teamA = data.team_a_name || data.challenger_name || "Team A";
    const teamB = data.team_b_name || data.opponent_name || "Team B";
    const score = current.runs == null && current.wickets == null ? null : `${number(current.runs)}/${number(current.wickets)}`;
    const scoreHeader = data.scorecard_available && score ? `<section class="match-score-hero panel"><div class="match-meta-line"><span class="match-status ${live ? "live" : ""}">${esc(matchStatus)}</span><span>${esc(data.match_type || data.engine || "Cricket")} · ${esc(matchOverText(current))} overs</span></div><div class="match-hero-teams"><div><b>${esc(current.batting_team_display || teamA)}</b><small>Batting</small></div><div class="match-hero-score"><strong>${esc(score)}</strong><span>${esc(matchOverText(current))} overs</span><small>${currentNo === 1 ? "1st" : currentNo === 2 ? "2nd" : `${currentNo}th`} Innings</small></div><div class="team-away"><b>${esc(current.bowling_team_display || teamB)}</b><small>Bowling</small></div></div><div class="score-context"><span>CRR <b>${Number(current.legal_balls || 0) ? (Number(current.runs || 0) * 6 / Number(current.legal_balls)).toFixed(2) : "—"}</b></span><span>Target <b>${current.target == null ? "—" : number(current.target)}</b></span><span>Status <b>${esc(matchStatus)}</b></span></div></section>` : `<section class="match-score-hero panel"><div class="match-meta-line"><span class="match-status ${live ? "live" : ""}">${esc(matchStatus)}</span><span>${esc(data.match_type || data.engine || "Cricket")}</span></div><div class="match-hero-teams"><div><b>${esc(teamA)}</b></div><div class="match-hero-score"><strong>—</strong><span>Score not stored</span></div><div class="team-away"><b>${esc(teamB)}</b></div></div><p class="muted-copy">${esc(data.note || "Crickium has not stored enough data to display a live scorecard for this record.")}</p></section>`;

    const tabNames = [["live", "Live"], ["scorecard", "Scorecard"], ["commentary", "Commentary"], ["stats", "Stats"]];
    const tabs = `<div class="match-tabs" role="tablist">${tabNames.map(([key,label]) => `<button class="match-tab ${state.matchTab === key ? "selected" : ""}" role="tab" aria-selected="${state.matchTab === key}" data-action="match-tab" data-tab="${key}">${label}</button>`).join("")}</div>`;
    const innSwitch = innings.length > 1 ? `<div class="innings-switch">${innings.map((item) => `<button class="filter-chip ${Number(item.innings_number) === currentNo ? "selected" : ""}" data-action="match-innings" data-innings="${Number(item.innings_number || 1)}">${Number(item.innings_number) === 1 ? "1st" : Number(item.innings_number) === 2 ? "2nd" : `${Number(item.innings_number)}th`} Innings · ${esc(item.batting_team_display || "Team")} ${number(item.runs)}/${number(item.wickets)}</button>`).join("")}</div>` : "";

    const pairHtml = battingPair.length ? `<section class="panel"><div class="panel-heading"><h2>Current batting pair</h2><span class="muted-inline">${currentNo === 1 ? "1ST INNINGS" : `${currentNo}ND INNINGS`}</span></div><div class="batting-pair-grid">${battingPair.slice(0,2).map((batter, index) => `<div class="batting-pair-player"><span>${index === 0 ? "On strike" : "Non-striker"}</span><b>${esc(batter.name || "Batter")}</b><strong>${number(batter.runs)} <small>(${number(batter.balls)})</small></strong><small>4s ${number(batter.fours)} · 6s ${number(batter.sixes)} · SR ${batter.strike_rate == null ? "—" : Number(batter.strike_rate).toFixed(1)}</small></div>`).join("")}${partnership ? `<div class="partnership-tile"><span>Partnership</span><b>${number(partnership.runs)} <small>(${number(partnership.balls)} balls)</small></b></div>` : ""}</div></section>` : "";
    const currentOver = data.this_over || current.this_over || [];
    const bowlerHtml = bowler ? `<section class="panel"><div class="panel-heading"><h2>Current bowler</h2><span class="muted-inline">CURRENT</span></div><div class="current-bowler-line"><div><b>${esc(bowler.name || "Bowler")}</b><small>${esc(bowler.overs || (Number(bowler.balls || 0) ? `${Math.floor(Number(bowler.balls) / 6)}.${Number(bowler.balls) % 6}` : "0.0"))} overs · ${number(bowler.runs)} runs · ${number(bowler.wickets)} wickets</small></div><strong>${esc(bowler.overs || "0.0")} · ${number(bowler.runs)} · ${number(bowler.wickets)}</strong></div>${currentOver.length ? `<div class="current-over-line"><b>This over</b><div class="ball-strip">${currentOver.slice(-6).map((ball) => `<span class="ball-token ${cricketBallLabel(ball) === "W" ? "wicket" : ["4","6"].includes(cricketBallLabel(ball)) ? "boundary" : ""}">${esc(cricketBallLabel(ball))}</span>`).join("")}</div></div>` : ""}</section>` : "";

    const overRows = current.analysis_overs || [];
    const recentOvers = overRows.slice(-6).reverse();
    const overHtml = recentOvers.length ? `<section class="panel"><div class="panel-heading"><h2>Over-by-over</h2><button class="text-button" data-action="match-tab" data-tab="commentary">Full commentary ›</button></div><div class="recent-over-list">${recentOvers.map((row) => `<div class="recent-over-row"><b>${Number(row.over) || "—"}</b><div class="ball-strip">${(row.balls || []).map((ball) => `<span class="ball-token ${ball.wicket ? "wicket" : [4,6].includes(Number(ball.runs)) ? "boundary" : ""}">${esc(cricketBallLabel(ball))}</span>`).join("")}</div><strong>${number(row.runs)} runs${row.wickets ? ` · ${number(row.wickets)} W` : ""}</strong></div>`).join("")}</div></section>` : `<section class="panel"><div class="panel-heading"><h2>Over-by-over</h2></div><p class="muted-copy">No over-by-over delivery log is stored for this match record.</p></section>`;

    const balls = (Array.isArray(current.analysis_balls) ? current.analysis_balls : []).slice().sort((a,b) => Number(a.over || 0) - Number(b.over || 0) || Number(a.ball || 0) - Number(b.ball || 0));
    const commentary = balls.length ? `<div class="commentary-list">${balls.map((ball) => {
      const ballLabel = cricketBallLabel(ball);
      const comment = ball.commentary || ball.text || ball.description || `${ball.bowler || "Bowler"} to ${ball.batter || "batter"}, ${ballLabel === "W" ? "wicket" : `${number(ball.runs)} run${Number(ball.runs || 0) === 1 ? "" : "s"}`}.`;
      return `<div class="commentary-row"><span class="commentary-over">${Number(ball.over) || "—"}.${Number(ball.ball) || "—"}</span><span class="ball-token ${ball.wicket ? "wicket" : ["4","6"].includes(ballLabel) ? "boundary" : ""}">${esc(ballLabel)}</span><div><b>${esc(ball.bowler || "Bowler")} to ${esc(ball.batter || "Batter")}</b><p>${esc(comment)}</p>${ball.score_after ? `<small>${esc(ball.score_after)}</small>` : ""}</div></div>`;
    }).join("")}</div>` : `<p class="muted-copy">Ball-by-ball commentary is not stored for this match record. No commentary has been fabricated.</p>`;

    const resultSummary = `<section class="panel match-result-panel"><div class="panel-heading"><h2>${completed ? "Match summary" : "Match details"}</h2><span class="match-status ${live ? "live" : ""}">${esc(matchStatus)}</span></div>${data.result_text ? `<h3>${esc(data.result_text)}</h3>` : completed ? `<p class="muted-copy">${esc(data.note || "The final result was not recorded in a displayable format.")}</p>` : `<p class="muted-copy">${esc(data.note || "Match details are derived from the stored game session.")}</p>`}${data.potm?.name && data.potm.name !== "—" && completed ? `<div class="potm-row"><span>Player of the Match</span><b>${esc(data.potm.name)}</b><small>${number(data.potm.runs)} runs · ${number(data.potm.wickets)} wickets</small></div>` : ""}${innings.length ? `<div class="innings-summary-list">${innings.map((item) => `<div><span>${Number(item.innings_number) === 1 ? "1st" : Number(item.innings_number) === 2 ? "2nd" : `${Number(item.innings_number)}th`} innings · ${esc(item.batting_team_display || "Team")}</span><b>${number(item.runs)}/${number(item.wickets)}</b><small>${esc(matchOverText(item))} overs${item.target ? ` · target ${number(item.target)}` : ""}</small></div>`).join("")}</div>` : ""}</section>`;

    let tabContent = "";
    if (state.matchTab === "live") {
      tabContent = `${resultSummary}${live ? pairHtml : ""}${live ? bowlerHtml : ""}${overHtml}<section class="panel"><div class="panel-heading"><h2>Latest commentary</h2><button class="text-button" data-action="match-tab" data-tab="commentary">View all ›</button></div>${commentary}</section>`;
    } else if (state.matchTab === "scorecard") {
      tabContent = `${innSwitch}<section class="panel"><div class="panel-heading"><h2>Batting · ${esc(current.batting_team_display || teamA)}</h2><b class="rating-mini">${number(current.runs)}/${number(current.wickets)}</b></div>${renderBatterTable(current)}</section><section class="panel"><div class="panel-heading"><h2>Bowling · ${esc(current.bowling_team_display || teamB)}</h2></div>${renderBowlerTable(current)}</section>`;
    } else if (state.matchTab === "commentary") {
      tabContent = `${innSwitch}<section class="panel"><div class="panel-heading"><h2>Ball-by-ball commentary</h2><span class="muted-inline">${number(balls.length)} stored deliveries</span></div>${commentary}</section>`;
    } else {
      const crr = Number(current.legal_balls || 0) ? Number(current.runs || 0) * 6 / Number(current.legal_balls) : null;
      const target = current.target == null ? null : Number(current.target);
      const need = target == null ? null : Math.max(0, target - Number(current.runs || 0));
      const remaining = Math.max(0, Number(data.overs_limit || 20) * 6 - Number(current.legal_balls || 0));
      const rrr = need != null && remaining ? need * 6 / remaining : null;
      const allBatters = innings.flatMap((item) => item.batters || []);
      const allBowlers = innings.flatMap((item) => item.bowlers || []);
      const topBatter = allBatters.slice().sort((a,b) => Number(b.runs||0)-Number(a.runs||0))[0];
      const topBowler = allBowlers.slice().sort((a,b) => Number(b.wickets||0)-Number(a.wickets||0))[0];
      tabContent = `<div class="match-stat-grid"><div><span>Runs</span><b>${number(current.runs)}</b></div><div><span>Wickets</span><b>${number(current.wickets)}</b></div><div><span>Overs</span><b>${esc(matchOverText(current))}</b></div><div><span>Current run rate</span><b>${crr == null ? "—" : crr.toFixed(2)}</b></div><div><span>Target</span><b>${target == null ? "—" : number(target)}</b></div><div><span>Required run rate</span><b>${rrr == null ? "—" : rrr.toFixed(2)}</b></div></div><section class="panel"><div class="panel-heading"><h2>Key performances</h2><span class="muted-inline">Stored match data</span></div>${topBatter ? `<div class="performance-row"><span><b>${esc(topBatter.name)}</b><small>Top recorded batter</small></span><strong>${number(topBatter.runs)} (${number(topBatter.balls)})</strong></div>` : ""}${topBowler ? `<div class="performance-row"><span><b>${esc(topBowler.name)}</b><small>Top recorded bowler</small></span><strong>${number(topBowler.wickets)}/${number(topBowler.runs)}</strong></div>` : ""}${!topBatter && !topBowler ? `<p class="muted-copy">No individual player statistics are stored.</p>` : ""}</section>${resultSummary}`;
    }

    shell(`${scoreHeader}${tabs}<div class="match-detail-body">${tabContent}</div>${!data.scorecard_available ? `<p class="notice">This record does not contain a full persisted scorecard. The page shows the available match metadata rather than invented scores.</p>` : ""}`, title, `${esc(data.engine || "MATCH")} · ${esc(data.match_type || "CRICKET")}`);
  }

  async function loadMatchDetail(engine, id, options = {}) {
    if (options.silent && state.matchRequestBusy) return;
    if (!options.skipRoute && (!state.currentMatch || state.currentMatch.engine !== engine || String(state.currentMatch.id) !== String(id))) {
      const nextMatch = { engine: String(engine || "MATCH").toUpperCase(), id: Number(id) };
      pushRoute({ page: "match-detail", currentMatch: nextMatch, matchTab: "live", matchInnings: null });
      state.currentMatch = nextMatch; state.matchTab = "live"; state.matchInnings = null;
    }
    state.page = "match-detail";
    const seq = ++state.matchRequestSeq;
    state.matchRequestBusy = true;
    const scrollTop = Number(window.scrollY || 0);
    const selected = { engine: String(engine).toUpperCase(), id: String(id) };
    if (!options.silent) shell(`<div class="loading-line">Loading stored match events…</div>`, `Match #${id}`, "MATCH REPORT");
    try {
      const data = await api(`/api/matches/${encodeURIComponent(engine)}/${encodeURIComponent(id)}`);
      if (seq !== state.matchRequestSeq || state.page !== "match-detail" || !state.currentMatch || state.currentMatch.engine !== selected.engine || String(state.currentMatch.id) !== selected.id) return;
      renderMatchDetail(data);
      if (options.silent) window.scrollTo({ top: scrollTop, behavior: "auto" });
      if (state.matchTimer) clearInterval(state.matchTimer);
      state.matchTimer = null;
      if (data.is_live) state.matchTimer = setInterval(() => {
        if (state.page === "match-detail" && state.currentMatch && state.currentMatch.engine === selected.engine && String(state.currentMatch.id) === selected.id) {
          loadMatchDetail(engine, id, { skipRoute: true, silent: true });
        }
      }, 12000);
    } catch (error) {
      if (seq !== state.matchRequestSeq || state.page !== "match-detail" || !state.currentMatch || state.currentMatch.engine !== selected.engine || String(state.currentMatch.id) !== selected.id) return;
      if (options.silent) return;
      shell(`${errorState(error, "matches")}`, `Match #${id}`, "MATCH REPORT");
    } finally {
      if (seq === state.matchRequestSeq) state.matchRequestBusy = false;
    }
  }

  function openMatch(engine, id) {
    if (!engine || !Number(id)) return;
    clearInterval(state.matchTimer);
    const match = { engine: String(engine).toUpperCase(), id: Number(id) };
    pushRoute({ page: "match-detail", currentMatch: match, matchTab: "live", matchInnings: null });
    state.currentMatch = match; state.currentMatchData = null; state.matchTab = "live"; state.matchInnings = null;
    state.page = "match-detail";
    loadMatchDetail(match.engine, match.id, { skipRoute: true });
  }

  async function restoreRoute(route) {
    clearInterval(state.matchTimer);
    const value = route && route.crickium ? route : { page: "home", crickium: true, index: 0 };
    state.currentMatch = value.currentMatch || null;
    state.currentPlayerRef = value.currentPlayer || null;
    state.playerReturnPage = value.returnPage || "search";
    state.lineupSwap = value.lineupSwap || null;
    state.pendingKitbagClaimId = value.kitbagClaimId || null;
    state.currentNewsId = value.currentNewsId == null ? null : Number(value.currentNewsId);
    state.matchTab = value.matchTab || "live";
    state.matchInnings = value.matchInnings == null ? null : Number(value.matchInnings);
    if (value.page === "match-detail" && state.currentMatch) {
      state.page = "match-detail";
      await loadMatchDetail(state.currentMatch.engine, state.currentMatch.id, { skipRoute: true });
    } else if (value.page === "player-detail" && state.currentPlayerRef) {
      await loadPlayer(state.currentPlayerRef.kind, state.currentPlayerRef.id, { skipRoute: true, returnPage: state.playerReturnPage });
    } else if (value.page === "lineup-swap" && state.lineupSwap) {
      await loadLineupSwap({ skipRoute: true });
    } else if (value.page === "kitbag-replace") {
      await loadKitbagReplacement(state.pendingKitbagClaimId, { skipRoute: true });
    } else if (value.page === "lineup-order") {
      await loadPlayingOrder();
    } else if (value.page === "sell-multiple") {
      await loadSellMultiple();
    } else if (value.page === "news-detail" && state.currentNewsId) {
      await loadNewsDetail(state.currentNewsId, { skipRoute: true });
    } else if (value.page === "kitbag-reveal") {
      await restoreKitbagReveal(value.kitbagClaimId || state.pendingKitbagClaimId);
    } else {
      state.currentMatch = null;
      state.currentMatchData = null;
      await navigate(value.page || "home", { history: false });
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

  function renderKitbagReveal(player, claimId, requiresReplacement) {
    state.page = "kitbag-reveal";
    state.pendingKitbagClaimId = claimId || null;
    state.pendingKitbag = requiresReplacement ? { claim_id: claimId, player } : null;
    const rewardCopy = requiresReplacement
      ? `<p>Your squad is full at 25 players. This card is saved safely. Choose one player to replace and this card will take their squad slot.</p><button class="button primary full" data-action="open-kitbag-replace" data-claim-id="${esc(claimId)}">Choose player to replace</button>`
      : `<p>Added to your squad. Come back in an hour for your next free Kit Bag.</p><button class="button primary full" data-page="squad">View squad</button>`;
    shell(`<section class="reveal-card"><div class="reveal-burst">✦</div><span class="eyebrow">KIT BAG OPENED</span><h2>Your new player</h2>${playerCard(player)}${rewardCopy}</section>`, "Player acquired", "REWARD UNLOCKED");
    hydrateImages();
  }

  async function restoreKitbagReveal(claimId) {
    try {
      const rewards = await api("/api/rewards");
      const pending = rewards.pending_kitbag || null;
      if (pending && pending.claim_id === String(claimId || "")) {
        renderKitbagReveal(pending.player, pending.claim_id, true);
        return;
      }
      await navigate("rewards", { history: false });
    } catch (_) {
      await navigate("rewards", { history: false });
    }
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
        state.pendingKitbagClaimId = result.claim_id || null;
        pushRoute({ page: "kitbag-reveal", kitbagClaimId: state.pendingKitbagClaimId });
        renderKitbagReveal(player, result.claim_id || null, Boolean(result.requires_replacement));
      } else {
        const reward = result.reward || {};
        toast(`Day ${result.day} claimed · ${number(reward.coins)} coins${reward.rubies ? ` · ${number(reward.rubies)} rubies` : ""}`);
        await loadRewards();
      }
    } catch (error) {
      const payload = error.payload || { status: error.message };
      if (kind === "kitbag" && payload.status === "replacement_pending" && payload.claim_id) {
        await startKitbagReplacement(payload.claim_id);
        return;
      }
      toast(claimError(payload), true);
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
      replacement_pending: "A Kit Bag player is waiting for you to replace a squad card.",
      claim_already_resolved: "This Kit Bag replacement has already been completed.",
      replacement_not_owned: "That player is no longer in your squad. Refresh and choose another card.",
      reward_already_owned: "This Kit Bag player is already in your squad.",
      player_not_owned: "That player is no longer in your squad.",
      player_not_in_xi: "That player is no longer in the Playing XI.",
      player_already_in_xi: "That substitute is already in the Playing XI.",
      squad_too_small: "You need at least eleven squad players to manage the Playing XI.",
      minimum_squad_size: "Keep at least eleven players in your squad for a complete Playing XI.",
      card_not_owned: "This collection card is no longer available.",
      card_data_unavailable: "This card’s saved details could not be loaded.",
      playing_xi_player_selected: "Only substitute players can be sold in Sell Multiple.",
      lineup_changed: "Your Playing XI changed. Refresh and set the order again.",
      invalid_order: "The Playing XI order is invalid. Arrange all eleven players and try again.",
      invalid_selection: "Select one or more available substitutes.",
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

  async function navigate(page, options = {}) {
    if (!page) return;
    if (page !== state.page && options.history !== false) pushRoute({ page, currentMatch: null, matchTab: "live", matchInnings: null });
    if (page !== "match-detail") {
      state.currentMatch = null;
      state.currentMatchData = null;
    }
    if (page !== "player-detail") state.currentPlayerRef = null;
    if (page !== "lineup-swap") state.lineupSwap = null;
    if (page !== "news-detail") state.currentNewsId = null;
    state.page = page;
    state.drawer = false;
    clearInterval(state.matchTimer);
    document.querySelector(".drawer-scrim")?.remove();
    ping();
    switch (page) {
      case "home": return loadHome();
      case "search": return loadSearch("");
      case "matches":
        await loadMatches();
        state.matchTimer = setInterval(() => { if (state.page === "matches") loadMatches({ silent: true }); }, 30000);
        return;
      case "match-detail":
        if (state.currentMatch) return loadMatchDetail(state.currentMatch.engine, state.currentMatch.id, { skipRoute: true });
        return navigate("matches", { history: false });
      case "market": return loadMarket();
      case "rank": return loadRank();
      case "profile": return loadProfile();
      case "squad": return loadSquad();
      case "collection": return loadCollection();
      case "lineup-swap": return loadLineupSwap();
      case "lineup-order": return loadPlayingOrder();
      case "sell-multiple": return loadSellMultiple();
      case "kitbag-replace": return loadKitbagReplacement(state.pendingKitbagClaimId);
      case "news": return loadNews();
      case "news-detail": return loadNewsDetail(state.currentNewsId, { skipRoute: true });
      case "quests": return loadQuests();
      case "rewards": return loadRewards();
      case "wallet": return loadWallet();
      case "packs": return loadPacks();
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
      if (action === "back-page") { backInApp(); return; }
      if (action === "refresh") {
        if (state.page === "match-detail" && state.currentMatch) loadMatchDetail(state.currentMatch.engine, state.currentMatch.id, { skipRoute: true });
        else navigate(state.page, { history: false });
      }
      if (action === "daily-claim") performClaim("daily");
      if (action === "kitbag-claim") performClaim("kitbag");
      if (action === "match-filter") { state.matchesFilter = actionButton.dataset.filter || "ongoing"; loadMatches(); }
      if (action === "match-back") backInApp();
      if (action === "match-tab") {
        state.matchTab = actionButton.dataset.tab || "live";
        if (state.currentMatchData) renderMatchDetail(state.currentMatchData);
      }
      if (action === "match-innings") {
        state.matchInnings = Number(actionButton.dataset.innings || 1);
        if (state.currentMatchData) renderMatchDetail(state.currentMatchData);
      }
      if (action === "buy-player") {
        const { kind, id } = actionButton.dataset;
        showConfirm(`Buy ${actionButton.dataset.name} for ${number(actionButton.dataset.price)} coins? The server will verify your balance and add the card to your squad.`, "Buy player", () => buyPlayer(kind, id, actionButton));
      }
      if (action === "buy-pack") {
        const pack = state.packData?.packs?.find((item) => item.key === actionButton.dataset.packKey);
        if (pack) showConfirm(`Buy ${pack.name} for ${number(pack.price_sigils)} Sigils? Your balance is ${number(state.packData?.wallet?.sigils)} SG. The server will verify the price and deduct Sigils exactly once.`, "Confirm purchase", () => buyPack(pack.key));
      }
      if (action === "open-pack") {
        const pack = state.packData?.packs?.find((item) => item.key === actionButton.dataset.packKey);
        if (pack) showConfirm(`Open one ${pack.name}? You own ${number(pack.quantity)} unopened. The opening will consume one pack and persist ${number(pack.cards)} card${pack.cards === 1 ? "" : "s"} plus its configured rewards.`, "Open pack", () => openPack(pack.key));
      }
      if (action === "open-history") openPackHistory(actionButton.dataset.openingId);
      if (action === "close-pack-reveal") { state.packOpening = null; renderPackShop(state.packData); }
      if (action === "lineup-swap-start") startLineupSwap(actionButton.dataset.playerId, actionButton.dataset.mode);
      if (action === "confirm-lineup-swap") confirmLineupSwap(actionButton.dataset.playingPlayerId, actionButton.dataset.substitutePlayerId, actionButton);
      if (action === "set-playing-order") navigate("lineup-order");
      if (action === "move-playing-order") movePlayingOrder(actionButton.dataset.playerId, actionButton.dataset.direction);
      if (action === "save-playing-order") savePlayingOrder(actionButton);
      if (action === "open-sell-multiple") navigate("sell-multiple");
      if (action === "toggle-bulk-player") toggleBulkSellPlayer(actionButton.dataset.playerId);
      if (action === "confirm-sell-multiple") {
        const count = Number(actionButton.dataset.count || 0);
        const amount = Number(actionButton.dataset.amount || 0);
        if (count > 0) showConfirm(`Are you sure you want to sell ${count} player${count === 1 ? "" : "s"} and receive ${number(amount)} coins? This action cannot be undone.`, `Yes, sell ${count}`, () => sellMultiplePlayers(actionButton));
      }
      if (action === "open-news") openNewsArticle(actionButton.dataset.newsId);
      if (action === "sell-squad-player") {
        const name = actionButton.dataset.name || "this player";
        const price = Number(actionButton.dataset.sellPrice || 0);
        showConfirm(`Sell ${name} for ${number(price)} coins? The card will leave your squad. Keep at least eleven squad players for a complete Playing XI.`, "Sell player", () => sellSquadPlayer(actionButton.dataset.playerId, actionButton));
      }
      if (action === "sell-pack-card") {
        const name = actionButton.dataset.name || "this collection card";
        const price = Number(actionButton.dataset.sellPrice || 0);
        showConfirm(`Sell ${name} from your pack collection for ${number(price)} coins? This only removes the saved collection copy and does not change your squad.`, "Sell card", () => sellPackCard(actionButton.dataset.cardId, actionButton));
      }
      if (action === "open-kitbag-replace") startKitbagReplacement(actionButton.dataset.claimId || state.pendingKitbagClaimId);
      if (action === "replace-kitbag-player") {
        const pending = state.pendingKitbag || {};
        showConfirm(`Replace ${actionButton.dataset.name || "this player"} with ${pending.player?.name || "the saved Kit Bag player"}? This change is permanent.`, "Replace player", () => confirmKitbagReplacement(actionButton.dataset.claimId, actionButton.dataset.replacedPlayerId, actionButton));
      }
    }
    const card = event.target.closest("[data-player-kind]");
    if (card && !event.target.closest("button") && !event.target.closest("[data-action]")) loadPlayer(card.dataset.playerKind, card.dataset.playerId);
    const match = event.target.closest("[data-match-engine]");
    if (match) openMatch(match.dataset.matchEngine, match.dataset.matchId);
  });

  window.addEventListener("popstate", (event) => {
    const route = event.state || { page: "home", crickium: true, index: 0 };
    const currentKey = JSON.stringify([state.page, state.currentMatch?.engine, state.currentMatch?.id, state.currentPlayerRef?.kind, state.currentPlayerRef?.id, state.currentNewsId]);
    const targetKey = JSON.stringify([route.page, route.currentMatch?.engine, route.currentMatch?.id, route.currentPlayer?.kind, route.currentPlayer?.id, route.currentNewsId]);
    if (currentKey !== targetKey && state.internalBackStack.length) state.internalBackStack.pop();
    restoreRoute(route).catch(() => navigate("home", { history: false }));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.drawer) { state.drawer = false; document.querySelector(".drawer-scrim")?.remove(); }
  });

  if (tg) {
    try { tg.ready(); tg.expand(); tg.setHeaderColor?.("#071310"); tg.setBackgroundColor?.("#071310"); } catch (_) {}
  }
  try { if (window.history && !window.history.state?.crickium) window.history.replaceState({ crickium: true, page: "home", currentMatch: null, matchTab: "live", matchInnings: null, index: 0 }, "", window.location.href); } catch (_) {}
  loadHome();
})();
