
const tg = window.Telegram?.WebApp || null;
const screen = document.getElementById("screen");
const loader = document.getElementById("loader");

const NAV = [
  { key: "home", label: "Home", icon: "🏠" },
  { key: "shop", label: "Shop", icon: "🛒" },
  { key: "matches", label: "Matches", icon: "🏏", center: true },
  { key: "search", label: "Search", icon: "🔎" },
  { key: "rank", label: "Rank", icon: "🏆" },
];

const DEFAULT_DEFAULT_SHOP_PLAYERS = [
{ name: "Virat Kohli", role: "Batsman", icon: "🏏", bat_level: 95, bowl_level: 40, price: 80000, ring: "gold" },
  { name: "Rohit Sharma", role: "Batsman", icon: "🏏", bat_level: 90, bowl_level: 20, price: 70000, ring: "gold" },
  { name: "Jasprit Bumrah", role: "Bowler", icon: "🎯", bat_level: 28, bowl_level: 96, price: 90000, ring: "green" },
  { name: "Mohammed Shami", role: "Bowler", icon: "🎯", bat_level: 22, bowl_level: 92, price: 75000, ring: "green" },
  { name: "Hardik Pandya", role: "All Rounder", icon: "⚡", bat_level: 85, bowl_level: 85, price: 85000, ring: "purple" },
  { name: "Rishabh Pant", role: "Wicket Keeper", icon: "🧤", bat_level: 80, bowl_level: 10, price: 60000, ring: "orange" },
];

const RANK_DATA = {
  global: {
    podium: [
      { pos: 2, name: "Arjun Playz", score: 48760, icon: "👤", className: "second" },
      { pos: 1, name: "Sam Cricket", score: 51250, icon: "👤", className: "first" },
      { pos: 3, name: "Rohit Warrior", score: 43210, icon: "👤", className: "third" },
    ],
    list: [
      { pos: 4, name: "Virat Fan", score: 41230 },
      { pos: 5, name: "Cricket King", score: 39870 },
      { pos: 6, name: "Legend 18", score: 37890 },
      { pos: 7, name: "Batting Beast", score: 36540 },
      { pos: 8, name: "Bowling Machine", score: 34980 },
      { pos: 9, name: "Sixer King", score: 32870 },
      { pos: 10, name: "Game Changer", score: 31520 },
    ],
  },
  country: {
    podium: [
      { pos: 2, name: "Delhi Drive", score: 40120, icon: "🇮🇳", className: "second" },
      { pos: 1, name: "Mumbai Max", score: 50100, icon: "🇮🇳", className: "first" },
      { pos: 3, name: "Punjab Power", score: 38440, icon: "🇮🇳", className: "third" },
    ],
    list: [
      { pos: 4, name: "Gujarat Grip", score: 32200 },
      { pos: 5, name: "Chennai Charge", score: 31890 },
      { pos: 6, name: "Kolkata Knight", score: 30550 },
      { pos: 7, name: "Hyderabad Heat", score: 30050 },
      { pos: 8, name: "Bangalore Blitz", score: 28990 },
      { pos: 9, name: "Rajasthan Rush", score: 27860 },
      { pos: 10, name: "Lucknow Legends", score: 26920 },
    ],
  },
  friends: {
    podium: [
      { pos: 2, name: "Aman Ace", score: 27500, icon: "👥", className: "second" },
      { pos: 1, name: "Samay Tyagi", score: 31550, icon: "👥", className: "first" },
      { pos: 3, name: "Rishi Runout", score: 24310, icon: "👥", className: "third" },
    ],
    list: [
      { pos: 4, name: "Yash Yorker", score: 23250 },
      { pos: 5, name: "Kabir Keeper", score: 22510 },
      { pos: 6, name: "Nitin No-Ball", score: 21980 },
      { pos: 7, name: "Vijay Volley", score: 20990 },
      { pos: 8, name: "Aarav Anchor", score: 19950 },
      { pos: 9, name: "Dev Dots", score: 18920 },
      { pos: 10, name: "Mohan Maiden", score: 17880 },
    ],
  },
  club: {
    podium: [
      { pos: 2, name: "Club Alpha", score: 27120, icon: "🏟️", className: "second" },
      { pos: 1, name: "Club Zenith", score: 32100, icon: "🏟️", className: "first" },
      { pos: 3, name: "Club Nova", score: 25010, icon: "🏟️", className: "third" },
    ],
    list: [
      { pos: 4, name: "Club Pulse", score: 23120 },
      { pos: 5, name: "Club Orbit", score: 22440 },
      { pos: 6, name: "Club Spark", score: 21990 },
      { pos: 7, name: "Club Bolt", score: 20710 },
      { pos: 8, name: "Club Forge", score: 20220 },
      { pos: 9, name: "Club Prime", score: 19800 },
      { pos: 10, name: "Club Rise", score: 19120 },
    ],
  },
};

const DEMO_HOME = {
  profile: {
    id: 1766243373,
    display_name: "SAMAY",
    first_name: "Samay",
    username: null,
    photo_url: null,
  },
  wallet: { coins: 3792000, rubies: 0, total_spent: 4200 },
  league: { label: "Diamond League", progress_percent: 88, progress_text: "400,000 / 400,001" },
  stats: {
    total_members: 88,
    total_players: 88,
    total_matches: 0,
    active_matches: 19,
    active_users: 0,
    matches_played: 88,
    matches_won: 19,
    win_percentage: 21.59,
  },
  daily_reward: { streak: 3, total_claimed: 1200, available: true, seconds_until_available: 0 },
};

const DEMO_PLAYER = {
  player_id: 1,
  name: "Virat Kohli",
  country: "India",
  role: "Batsman",
  role_icon: "🏏",
  bat_level: 95,
  bowl_level: 40,
  overall: 95,
  rarity: "Legendary",
  batting_style: "Right Hand Batsman",
  bowling_style: "Right Arm Medium",
  buy_price: 80000,
  owned: false,
  card_image_url: null,
  description: "Batsman • Right Hand Batsman • Right Arm Medium",
  ball_level: 40,
  card_type: "bat",
};

const state = {
  page: "home",
  drawerOpen: false,
  shopTab: "players",
  rankTab: "global",
  searchQuery: "",
  searchResults: [],
  home: null,
  player: null,
  shopPlayers: [],
  pageHistory: [],
  loadingText: "Loading lobby…",
  usingDemo: false,
};

let searchTimer = null;

function normalizeShopPlayer(item, index = 0) {
  const base = item || {};
  const bat = Number(base.bat_level ?? 0);
  const bowl = Number(base.bowl_level ?? 0);
  const overall = Math.max(bat, bowl);
  return {
    player_id: Number(base.player_id ?? index + 1),
    name: String(base.name ?? `Player ${index + 1}`),
    country: base.country ?? null,
    role: String(base.role ?? "Player"),
    role_icon: base.role_icon || roleEmoji(base.role),
    bat_level: bat,
    bowl_level: bowl,
    overall,
    rarity: base.rarity || (overall >= 95 ? "Legendary" : overall >= 85 ? "Epic" : overall >= 75 ? "Rare" : "Common"),
    batting_style: base.batting_style || (String(base.role || "").toLowerCase().includes("bowler") ? "Right Arm Medium" : "Right Hand Batsman"),
    bowling_style: base.bowling_style || (String(base.role || "").toLowerCase().includes("bowler") ? "Right Arm Medium" : "Right Arm Medium"),
    buy_price: Number(base.buy_price ?? base.price ?? 0),
    owned: Boolean(base.owned),
    card_image_url: base.card_image_url || null,
  };
}

function fallbackShopPlayers() {
  return DEFAULT_DEFAULT_SHOP_PLAYERS.map((item, index) => normalizeShopPlayer({
    ...item,
    buy_price: item.price,
    card_image_url: null,
  }, index));
}

async function loadShopPlayers() {
  const response = await fetchJSON('/api/players/search?q=&limit=12', null);
  const results = Array.isArray(response?.results) ? response.results : [];
  if (results.length) {
    state.shopPlayers = results.map((item, index) => normalizeShopPlayer(item, index));
    return state.shopPlayers;
  }
  state.shopPlayers = fallbackShopPlayers();
  return state.shopPlayers;
}


function bootTelegram() {
  if (!tg) return;
  tg.ready();
  tg.expand();
  try {
    tg.setHeaderColor?.("#07111f");
    tg.setBackgroundColor?.("#050b15");
  } catch (_) {}
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function compactMoney(value) {
  const amount = Number(value || 0);
  if (amount < 1000) return `${amount}`;
  if (amount < 100000) return `${(amount / 1000).toFixed(amount >= 10000 ? 1 : 2).replace(/\.0+$/, "")}K`;
  if (amount < 10000000) return `${(amount / 100000).toFixed(2).replace(/\.0+$/, "")}L`;
  return `${(amount / 1000000).toFixed(2).replace(/\.0+$/, "")}M`;
}

function prettyNumber(value) {
  return new Intl.NumberFormat("en-IN").format(Number(value || 0));
}

function formatPercent(value) {
  const num = Number(value || 0);
  return `${num.toFixed(num % 1 === 0 ? 0 : 2)}%`;
}

function roleEmoji(role) {
  const r = String(role || "").toLowerCase();
  if (r.includes("batsman")) return "🏏";
  if (r.includes("bowler")) return "🎯";
  if (r.includes("all")) return "⚡";
  if (r.includes("keeper") || r.includes("wk")) return "🧤";
  return "👤";
}

function drawerMenu() {
  return `
    <aside class="drawer ${state.drawerOpen ? "open" : ""}" aria-hidden="${state.drawerOpen ? "false" : "true"}">
      <div class="drawer-head">
        <div class="drawer-user">
          <div class="name">${escapeHtml((state.home?.profile?.display_name || DEMO_HOME.profile.display_name))}</div>
          <div class="id">ID: ${escapeHtml(state.home?.profile?.id || DEMO_HOME.profile.id)}</div>
        </div>
        <button class="icon-btn" data-action="toggle-drawer" aria-label="Close menu">✕</button>
      </div>

      <div class="drawer-menu">
        <button class="drawer-item" data-action="open-coming" data-title="My Team">
          <span class="ico">👥</span><span class="label">My Team</span><span class="chev">›</span>
        </button>
        <button class="drawer-item" data-action="set-rank-tab" data-tab="global">
          <span class="ico">🏆</span><span class="label">Leaderboard</span><span class="chev">›</span>
        </button>
        <button class="drawer-item" data-action="open-coming" data-title="My Club">
          <span class="ico">🛡️</span><span class="label">My Club</span><span class="chev">›</span>
        </button>
        <button class="drawer-item" data-action="set-shop-tab" data-tab="players">
          <span class="ico">⬆️</span><span class="label">Buy Upgrades</span><span class="chev">›</span>
        </button>
        <button class="drawer-item" data-action="set-shop-tab" data-tab="kitbags">
          <span class="ico">🎒</span><span class="label">Buy Kit Bag</span><span class="chev">›</span>
        </button>
      </div>
    </aside>
    <div class="drawer-backdrop ${state.drawerOpen ? "show" : ""}" data-action="toggle-drawer"></div>
  `;
}

function navMarkup() {
  return `
    <nav class="bottom-nav">
      ${NAV.map((item) => `
        <button class="nav-btn ${state.page === item.key ? "active" : ""} ${item.center ? "center" : ""}" data-action="nav" data-target="${item.key}">
          <span class="nav-icon">${item.icon}</span>
          <span>${item.label}</span>
        </button>
      `).join("")}
    </nav>
  `;
}

function topbarMarkup(isPlayerPage = false) {
  const home = state.home || DEMO_HOME;
  const coins = compactMoney(home.wallet?.coins || 0);
  const rubies = compactMoney(home.wallet?.rubies || 0);
  return `
    <header class="page-topbar">
      <button class="icon-btn" data-action="${isPlayerPage ? "back" : "toggle-drawer"}" aria-label="${isPlayerPage ? "Back" : "Menu"}">${isPlayerPage ? "←" : "☰"}</button>

      <div class="brand-wrap">
        <div class="brand">CRICIUM</div>
      </div>

      <div class="top-counters">
        <span class="top-counter"><span class="sym coin">🪙</span>+ ${coins}</span>
        <span class="top-divider"></span>
        <span class="top-counter"><span class="sym ruby">💎</span>+ ${rubies}</span>
      </div>

      <button class="icon-btn" data-action="notifications" aria-label="Notifications">🔔</button>
    </header>
  `;
}

function shellMarkup(content, { playerPage = false } = {}) {
  return `
    <div class="page-shell">
      ${drawerMenu()}
      ${topbarMarkup(playerPage)}
      <main class="page-content">${content}</main>
      ${navMarkup()}
    </div>
  `;
}

function rewardRow(icon, cls, label, btnLabel) {
  return `
    <div class="reward-row">
      <div class="reward-icon ${cls}">${icon}</div>
      <div class="reward-label">${label}</div>
      <button class="reward-btn">${btnLabel}</button>
    </div>
  `;
}

function homeContent(data) {
  const profile = data.profile || DEMO_HOME.profile;
  const wallet = data.wallet || DEMO_HOME.wallet;
  const stats = data.stats || DEMO_HOME.stats;
  const initial = (profile.display_name || "C").trim().slice(0, 1).toUpperCase();
  const avatar = profile.photo_url
    ? `<img src="${escapeHtml(profile.photo_url)}" alt="${escapeHtml(profile.display_name)}">`
    : `<div class="avatar-fallback">${escapeHtml(initial)}</div>`;

  return `
    <section class="hero-card card">
      <div class="hero-top">
        <div class="avatar">${avatar}</div>
        <div>
          <div class="user-name">${escapeHtml(profile.display_name)}</div>
          <div class="user-id">ID: ${escapeHtml(profile.id)}</div>
        </div>
        <div class="level-chip">
          <div>
            <div class="label">LEVEL</div>
            <div class="value">12</div>
          </div>
        </div>
      </div>

      <div class="league-row">
        <div class="league-badge">⭐</div>
        <div class="league-main">
          <div class="title">${escapeHtml(data.league?.label || DEMO_HOME.league.label)}</div>
          <div class="progress"><span style="width:${Number(data.league?.progress_percent || DEMO_HOME.league.progress_percent)}%"></span></div>
          <div class="progress-foot">
            <span>League Progress</span>
            <span>${escapeHtml(data.league?.progress_text || DEMO_HOME.league.progress_text)}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="grid-2">
      <div class="wallet-item wallet-coin">
        <div class="wallet-top"><span class="wallet-icon">👥</span><span>MY CLUB</span></div>
        <div class="wallet-value">0</div>
      </div>
      <div class="wallet-item wallet-ruby">
        <div class="wallet-top"><span class="wallet-icon">🧑‍🤝‍🧑</span><span>MY SQUAD</span></div>
        <div class="wallet-value">0 / 25</div>
      </div>
    </section>

    <section class="grid-3">
      <div class="stat-card">
        <div class="stat-title">Matches Played</div>
        <div class="stat-value">${prettyNumber(stats.matches_played || 0)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Matches Won</div>
        <div class="stat-value">${prettyNumber(stats.matches_won || 0)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Win Percentage</div>
        <div class="stat-value">${formatPercent(stats.win_percentage || 0)}</div>
      </div>
    </section>

    <section class="reward-card card">
      <div class="reward-head">
        <div>
          <div class="reward-title">Daily Rewards</div>
          <div class="page-subtitle" style="margin-top:6px">A quick little reward dock for the daily grind.</div>
        </div>
        <button class="reward-search" data-action="nav" data-target="search">🔎</button>
      </div>
      ${rewardRow("🎁", "blue", "Claim Today Reward", "CLAIM")}
      ${rewardRow("🧰", "purple", "Open Your Daily Chest", "OPEN")}
      ${rewardRow("🌀", "gold", "Spin Your Daily Spinner", "SPIN")}
    </section>
  `;
}


function shopPlayerCard(item) {
  const role = item.role || "Player";
  const roleIcon = item.role_icon || item.icon || roleEmoji(role);
  const ringClass = item.ring || (String(role).toLowerCase().includes("bowler") ? "green" : String(role).toLowerCase().includes("all") ? "purple" : "gold");
  const overall = Math.max(Number(item.bat_level || 0), Number(item.bowl_level || 0));
  const price = Number(item.buy_price ?? item.price ?? 0);
  const avatar = item.card_image_url
    ? `<img src="${escapeHtml(item.card_image_url)}" alt="${escapeHtml(item.name)}">`
    : `<span>${escapeHtml(roleIcon)}</span>`;

  const statsLine = String(role).toLowerCase().includes("bowler")
    ? `<div class="player-stats-line">Bowling Level: ${prettyNumber(item.bowl_level)} <span class="mini-bar"><span style="width:${Math.max(18, Math.min(100, item.bowl_level || 0))}%"></span></span></div>`
    : String(role).toLowerCase().includes("all")
      ? `<div class="player-stats-line">Batting Level: ${prettyNumber(item.bat_level)} <span class="mini-bar"><span style="width:${Math.max(18, Math.min(100, item.bat_level || 0))}%"></span></span> Bowling Level: ${prettyNumber(item.bowl_level)} <span class="mini-bar"><span style="width:${Math.max(18, Math.min(100, item.bowl_level || 0))}%"></span></span></div>`
      : `<div class="player-stats-line">Batting Level: ${prettyNumber(item.bat_level)} <span class="mini-bar"><span style="width:${Math.max(18, Math.min(100, item.bat_level || 0))}%"></span></span></div>`;

  return `
    <article class="player-card">
      <div class="player-avatar ${ringClass}">
        <div class="player-avatar-image">${avatar}</div>
      </div>
      <div class="player-main">
        <div class="player-name-row">
          <div class="player-name">${escapeHtml(item.name)}</div>
          <div class="player-role-emoji">${escapeHtml(roleIcon)}</div>
        </div>
        <div class="player-role">${escapeHtml(item.role || "Player")} • ${escapeHtml(item.rarity || "Common")}</div>
        ${statsLine}
      </div>
      <button class="player-buy">
        <span class="coin">🪙</span>
        <span>${prettyNumber(price)}</span>
      </button>
    </article>
  `;
}

function shopContent() {
  const tabs = [
    { key: "players", icon: "👤", label: "BUY PLAYERS" },
    { key: "coins", icon: "🪙", label: "BUY COINS" },
    { key: "rubies", icon: "💎", label: "BUY RUBIES" },
    { key: "kitbags", icon: "🎒", label: "BUY KIT BAGS" },
  ];

  const players = (state.shopPlayers && state.shopPlayers.length) ? state.shopPlayers : fallbackShopPlayers();

  let content = `
    <section class="shop-shell card">
      <div class="section-head">
        <div>
          <h1 class="section-title">SHOP</h1>
          <p class="section-subtitle">Build your team. Upgrade your game.</p>
        </div>
      </div>

      <div class="shop-tabs" style="margin-top:14px">
        ${tabs.map((tab) => `
          <button class="shop-tab-card ${state.shopTab === tab.key ? "active" : ""}" data-action="set-shop-tab" data-tab="${tab.key}">
            <span class="icon">${tab.icon}</span>
            <span class="label">${tab.label}</span>
          </button>
        `).join("")}
      </div>
  `;

  if (state.shopTab === "players") {
    content += `
      <div class="shop-list-head">
        <div class="section-title" style="font-size:1.08rem">ALL PLAYERS</div>
        <div class="sort-pill">⎇ DEFAULT <span style="font-size:1.4rem">⌄</span></div>
      </div>
      <div class="player-list player-list-shop" style="margin-top:12px">
        ${players.map(shopPlayerCard).join("")}
      </div>
    `;
  } else if (state.shopTab === "coins") {
    content += `
      <div class="shop-list-head">
        <div class="section-title" style="font-size:1.08rem">BUY COINS</div>
        <div class="sort-pill">💰 Offers</div>
      </div>
      <div class="player-list player-list-shop" style="margin-top:12px">
        ${[
          { name: "Starter Pack", role: "Coins", icon: "🪙", bat_level: 0, bowl_level: 0, price: 1000, ring: "blue", rarity: "Common" },
          { name: "Bronze Pack", role: "Coins", icon: "🪙", bat_level: 0, bowl_level: 0, price: 5000, ring: "green", rarity: "Common" },
          { name: "Pro Pack", role: "Coins", icon: "🪙", bat_level: 0, bowl_level: 0, price: 20000, ring: "purple", rarity: "Epic" },
          { name: "Mega Pack", role: "Coins", icon: "🪙", bat_level: 0, bowl_level: 0, price: 50000, ring: "gold", rarity: "Legendary" },
        ].map(shopPlayerCard).join("")}
      </div>
    `;
  } else if (state.shopTab === "rubies") {
    content += `
      <div class="shop-list-head">
        <div class="section-title" style="font-size:1.08rem">BUY RUBIES</div>
        <div class="sort-pill">💎 Offers</div>
      </div>
      <div class="player-list player-list-shop" style="margin-top:12px">
        ${[
          { name: "Mini Ruby Pack", role: "Rubies", icon: "💎", bat_level: 0, bowl_level: 0, price: 1200, ring: "blue", rarity: "Common" },
          { name: "Ruby Pack", role: "Rubies", icon: "💎", bat_level: 0, bowl_level: 0, price: 6000, ring: "purple", rarity: "Rare" },
          { name: "Mega Ruby Pack", role: "Rubies", icon: "💎", bat_level: 0, bowl_level: 0, price: 15000, ring: "gold", rarity: "Epic" },
        ].map(shopPlayerCard).join("")}
      </div>
    `;
  } else {
    content += `
      <div class="coming-note">Kit bags are coming soon. For now, explore players and packs.</div>
    `;
  }

  content += `</section>`;
  return content;
}


function searchContent() {
  return `
    <section class="search-shell card">
      <div class="section-head">
        <div>
          <h1 class="section-title">SEARCH</h1>
          <p class="section-subtitle">Search player or friends @ username</p>
        </div>
      </div>

      <div class="search-input-wrap" style="margin-top:14px">
        <span class="search-icon">⌕</span>
        <input
          class="search-input"
          id="searchInput"
          value="${escapeHtml(state.searchQuery)}"
          placeholder="Search player or friends @ username"
          autocomplete="off"
          spellcheck="false"
        />
      </div>

      <div class="search-hints">Search a player name to open the player profile page. Friend search will be wired later.</div>

      <div class="search-results" id="searchResults"></div>
    </section>
  `;
}

function searchResultsMarkup(results, query) {
  if (!query) {
    return `
      <div class="search-empty">
        Start typing a player name. Popular shortcuts: Virat Kohli, Rohit Sharma, Jasprit Bumrah, Mohammed Shami.
      </div>
    `;
  }

  if (!results || !results.length) {
    if (query.trim().startsWith("@")) {
      return `
        <div class="search-empty">
          Friend lookup is coming soon. Player search is ready right now.
        </div>
      `;
    }
    return `
      <div class="search-empty">
        No player found for <b>${escapeHtml(query)}</b>.
      </div>
    `;
  }

  return results.map((item) => `
    <button class="search-result" data-action="open-player" data-name="${escapeHtml(item.name)}">
      <div class="result-avatar">
        ${item.card_image_url ? `<img src="${escapeHtml(item.card_image_url)}" alt="${escapeHtml(item.name)}">` : `<span>${escapeHtml(item.role_icon || roleEmoji(item.role))}</span>`}
      </div>
      <div class="result-meta">
        <div class="result-name-row">
          <div class="result-name">${escapeHtml(item.name)}</div>
          <div class="player-role-emoji">${escapeHtml(item.role_icon || roleEmoji(item.role))}</div>
        </div>
        <div class="result-sub">${escapeHtml(item.role)} • ${escapeHtml(item.rarity)} • ${prettyNumber(item.buy_price)} coins</div>
      </div>
      <div class="result-badge">OPEN</div>
    </button>
  `).join("");
}

function comingSoonContent(title, subtitle) {
  return `
    <section class="coming-card card">
      <div class="coming-icon">⏳</div>
      <h1 class="coming-title">${escapeHtml(title)}</h1>
      <p class="coming-text">${escapeHtml(subtitle || "This section is not available yet. The engine is still being wired into Crickium.")}</p>
      <button class="back-btn" data-action="nav" data-target="home">Back to Home</button>
    </section>
  `;
}

function playerContent(player) {
  const heroImage = player.card_image_url
    ? `<img src="${escapeHtml(player.card_image_url)}" alt="${escapeHtml(player.name)}">`
    : `
      <div class="hero-media-fallback">
        <div>
          <div class="fallback-name">${escapeHtml(player.name)}</div>
          <div class="fallback-sub">${escapeHtml(player.description || `${player.role} • ${player.batting_style} • ${player.bowling_style}`)}</div>
        </div>
      </div>
    `;

  const ownedState = player.owned ? "yes" : "no";

  return `
    <section class="player-shell card">
      <div class="hero-media">
        ${heroImage}
      </div>

      <div class="player-title">${escapeHtml(player.name)} <span class="emoji">${player.role_icon || roleEmoji(player.role)}</span></div>

      <div class="profile-meta-grid">
        <div class="profile-meta-card">
          <div class="meta-label">Role</div>
          <div class="meta-value">${escapeHtml(player.role)}</div>
        </div>
        <div class="profile-meta-card">
          <div class="meta-label">Batting Style</div>
          <div class="meta-value">${escapeHtml(player.batting_style)}</div>
        </div>
        <div class="profile-meta-card">
          <div class="meta-label">Bowling Style</div>
          <div class="meta-value">${escapeHtml(player.bowling_style)}</div>
        </div>
        <div class="profile-meta-card">
          <div class="meta-label">Rarity</div>
          <div class="meta-value rarity-badge">⭐ ${escapeHtml(player.rarity)}</div>
        </div>
      </div>

      <div class="level-grid">
        <div class="summary-card level-green">
          <div class="summary-head">
            <div class="summary-label">Bat Level</div>
          </div>
          <div class="summary-value"><span class="big">${prettyNumber(player.bat_level)}</span><span class="small">/ 100</span></div>
          <div class="progress" style="margin-top:12px"><span style="width:${Math.max(0, Math.min(100, player.bat_level))}%"></span></div>
        </div>
        <div class="summary-card level-blue">
          <div class="summary-head">
            <div class="summary-label">Ball Level</div>
          </div>
          <div class="summary-value"><span class="big">${prettyNumber(player.ball_level ?? player.bowl_level ?? 0)}</span><span class="small">/ 100</span></div>
          <div class="progress" style="margin-top:12px"><span style="width:${Math.max(0, Math.min(100, player.ball_level ?? player.bowl_level ?? 0))}%"></span></div>
        </div>
      </div>

      <div class="detail-rows">
        <div class="detail-row">
          <div class="detail-icon">🏏</div>
          <div class="detail-main">
            <div class="detail-label">Role Category</div>
            <div class="detail-value">${escapeHtml(player.role)}</div>
          </div>
          <div class="detail-pill">${escapeHtml(player.rarity)}</div>
        </div>
        <div class="detail-row">
          <div class="detail-icon">🏏</div>
          <div class="detail-main">
            <div class="detail-label">Batting Category</div>
            <div class="detail-value">${escapeHtml(player.batting_style)}</div>
          </div>
          <div class="detail-pill">Batsman</div>
        </div>
        <div class="detail-row">
          <div class="detail-icon">🎯</div>
          <div class="detail-main">
            <div class="detail-label">Bowling Category</div>
            <div class="detail-value">${escapeHtml(player.bowling_style)}</div>
          </div>
          <div class="detail-pill">Style</div>
        </div>
      </div>

      <div class="owned-card">
        <div class="detail-label">You owned this player</div>
        <div class="state ${ownedState === "yes" ? "yes" : "no"}">${ownedState === "yes" ? "Yes" : "No"}</div>
      </div>
    </section>
  `;
}

function currentWallet() {
  return state.home?.wallet || DEMO_HOME.wallet;
}

function renderShellForPage() {
  const page = state.page;
  const home = state.home || DEMO_HOME;

  if (page === "home") {
    return shellMarkup(homeContent(home), { playerPage: false });
  }
  if (page === "shop") {
    return shellMarkup(shopContent(), { playerPage: false });
  }
  if (page === "rank") {
    return shellMarkup(rankContent(), { playerPage: false });
  }
  if (page === "search") {
    return shellMarkup(searchContent(), { playerPage: false });
  }
  if (page === "player") {
    return shellMarkup(playerContent(state.player || DEMO_PLAYER), { playerPage: true });
  }
  if (page === "matches") {
    return shellMarkup(comingSoonContent("Matches", "Match center is being stitched together. Stay tuned."), { playerPage: false });
  }
  if (page === "coming") {
    return shellMarkup(comingSoonContent("Coming Soon", "This section is still under construction."), { playerPage: false });
  }
  return shellMarkup(comingSoonContent("Coming Soon", "This section is still under construction."), { playerPage: false });
}

function setLoader(open, text) {
  if (!loader) return;
  if (text) {
    loader.querySelector(".loader-text").textContent = text;
  }
  loader.classList.toggle("show", !!open);
}

async function transitionRender(nextPage, options = {}) {
  state.page = nextPage;
  if (typeof options.shopTab === "string") state.shopTab = options.shopTab;
  if (typeof options.rankTab === "string") state.rankTab = options.rankTab;
  if (options.player) state.player = options.player;

  if (nextPage === "shop") {
    await loadShopPlayers();
  }
  setLoader(true, options.loadingText || "Loading lobby…");
  screen.classList.add("is-fading");
  await sleep(140);

  screen.innerHTML = renderShellForPage();
  await sleep(20);

  screen.classList.remove("is-fading");
  setLoader(false);

  bindSearchHandlers();
  if (state.page === "search") {
    await refreshSearchResults(state.searchQuery || "");
  }
}

function renderInPlace() {
  screen.innerHTML = renderShellForPage();
  bindSearchHandlers();
}

function bindSearchHandlers() {
  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.addEventListener("input", onSearchInput);
    searchInput.addEventListener("focus", () => {
      if (!state.searchQuery) refreshSearchResults("");
    });
  }
}

function getInitHeaders() {
  const headers = {};
  if (tg?.initData) headers["X-Telegram-Init-Data"] = tg.initData;
  return headers;
}

async function fetchJSON(url, fallback = null) {
  try {
    const response = await fetch(url, { headers: getInitHeaders() });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (err) {
    console.warn("[Crickium Mini App] fetch failed:", url, err);
    return fallback;
  }
}

async function loadHome() {
  const response = await fetchJSON("/api/home", DEMO_HOME);
  state.home = response || DEMO_HOME;
  state.usingDemo = response === DEMO_HOME;
}

async function refreshSearchResults(query) {
  const resultsBox = document.getElementById("searchResults");
  if (!resultsBox) return;

  const q = String(query || "").trim();
  if (!q) {
    const demoPlayers = DEFAULT_SHOP_PLAYERS.map((item, index) => ({
      player_id: index + 1,
      name: item.name,
      role: item.role,
      role_icon: item.icon,
      bat_level: item.bat_level,
      bowl_level: item.bowl_level,
      overall: Math.max(item.bat_level, item.bowl_level),
      rarity: Math.max(item.bat_level, item.bowl_level) >= 95 ? "Legendary" : Math.max(item.bat_level, item.bowl_level) >= 85 ? "Epic" : Math.max(item.bat_level, item.bowl_level) >= 75 ? "Rare" : "Common",
      batting_style: item.role === "Bowler" ? "Right Arm Medium" : item.role === "All Rounder" ? "Right Hand Bat" : "Right Hand Batsman",
      bowling_style: item.role === "Bowler" ? "Right Arm Medium" : "Right Arm Medium",
      buy_price: item.price,
      owned: false,
      card_image_url: null,
    }));
    resultsBox.innerHTML = searchResultsMarkup(demoPlayers.slice(0, 4), "");
    return;
  }

  const response = await fetchJSON(`/api/players/search?q=${encodeURIComponent(q)}&limit=8`, null);
  const results = response?.results || [];

  if (!results.length && q.startsWith("@")) {
    resultsBox.innerHTML = searchResultsMarkup([], q);
    return;
  }

  if (results.length) {
    resultsBox.innerHTML = searchResultsMarkup(results, q);
    return;
  }

  // local fallback
  const fallbackResults = DEFAULT_SHOP_PLAYERS
    .filter((item) => item.name.toLowerCase().includes(q.toLowerCase()))
    .map((item, index) => ({
      player_id: index + 1,
      name: item.name,
      role: item.role,
      role_icon: item.icon,
      bat_level: item.bat_level,
      bowl_level: item.bowl_level,
      overall: Math.max(item.bat_level, item.bowl_level),
      rarity: Math.max(item.bat_level, item.bowl_level) >= 95 ? "Legendary" : Math.max(item.bat_level, item.bowl_level) >= 85 ? "Epic" : Math.max(item.bat_level, item.bowl_level) >= 75 ? "Rare" : "Common",
      batting_style: item.role === "Bowler" ? "Right Arm Medium" : item.role === "All Rounder" ? "Right Hand Bat" : "Right Hand Batsman",
      bowling_style: item.role === "Bowler" ? "Right Arm Medium" : "Right Arm Medium",
      buy_price: item.price,
      owned: false,
      card_image_url: null,
    }));

  resultsBox.innerHTML = searchResultsMarkup(fallbackResults, q);
}

function onSearchInput(event) {
  const value = event.target.value || "";
  state.searchQuery = value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => refreshSearchResults(value), 180);
}

async function openPlayer(name) {
  state.pageHistory.push(state.page);
  setLoader(true, "Loading player profile…");
  screen.classList.add("is-fading");
  await sleep(120);

  const response = await fetchJSON(`/api/player?name=${encodeURIComponent(name)}`, null);
  const player = response || DEFAULT_SHOP_PLAYERS.find((p) => p.name.toLowerCase() === String(name).toLowerCase()) || DEMO_PLAYER;

  state.player = response || {
    ...DEMO_PLAYER,
    name: player.name || name,
    role: player.role || DEMO_PLAYER.role,
    role_icon: player.role_icon || roleEmoji(player.role || DEMO_PLAYER.role),
    bat_level: player.bat_level || DEMO_PLAYER.bat_level,
    bowl_level: player.bowl_level || DEMO_PLAYER.bowl_level,
    overall: Math.max(player.bat_level || DEMO_PLAYER.bat_level, player.bowl_level || DEMO_PLAYER.bowl_level),
    rarity: player.rarity || DEMO_PLAYER.rarity,
    batting_style: player.batting_style || DEMO_PLAYER.batting_style,
    bowling_style: player.bowling_style || DEMO_PLAYER.bowling_style,
    buy_price: player.buy_price || DEMO_PLAYER.buy_price,
    owned: player.owned || false,
    card_image_url: player.card_image_url || null,
    ball_level: player.ball_level ?? player.bowl_level ?? DEMO_PLAYER.ball_level,
    card_type: player.card_type || "bat",
    description: player.description || DEMO_PLAYER.description,
  };

  state.page = "player";
  screen.innerHTML = renderShellForPage();
  await sleep(20);
  screen.classList.remove("is-fading");
  setLoader(false);
}

async function goBack() {
  const prev = state.pageHistory.pop() || "search";
  await transitionRender(prev);
  if (prev === "search") {
    await refreshSearchResults(state.searchQuery || "");
  }
}

async function handleNav(target) {
  if (target === state.page) return;
  if (target === "matches") {
    await transitionRender("matches");
    return;
  }
  await transitionRender(target);
}

async function openComingSoon(title) {
  setLoader(true, "Loading page…");
  screen.classList.add("is-fading");
  await sleep(120);
  state.page = "coming";
  screen.innerHTML = shellMarkup(comingSoonContent(title, `${title} is not available yet. The engine is still being wired into Crickium.`));
  await sleep(20);
  screen.classList.remove("is-fading");
  setLoader(false);
  bindSearchHandlers();
}

async function renderInitial() {
  bootTelegram();
  setLoader(true, "Loading Crickium lobby…");
  screen.innerHTML = `<div class="page-shell"><main class="page-content"></main></div>`;
  await loadHome();
  await loadShopPlayers();
  await sleep(220);
  screen.innerHTML = renderShellForPage();
  setLoader(false);
  bindSearchHandlers();
  if (state.page === "search") {
    await refreshSearchResults(state.searchQuery || "");
  }
}

document.addEventListener("click", async (event) => {
  const actionEl = event.target.closest("[data-action]");
  if (!actionEl) return;

  const action = actionEl.dataset.action;

  if (action === "toggle-drawer") {
    state.drawerOpen = !state.drawerOpen;
    renderInPlace();
    return;
  }

  if (action === "nav") {
    state.drawerOpen = false;
    const target = actionEl.dataset.target;
    if (target === "matches") {
      await transitionRender("matches");
      return;
    }
    await transitionRender(target);
    return;
  }

  if (action === "set-shop-tab") {
    const tab = actionEl.dataset.tab || "players";
    state.drawerOpen = false;
    await transitionRender("shop", { shopTab: tab });
    return;
  }

  if (action === "set-rank-tab") {
    const tab = actionEl.dataset.tab || "global";
    state.drawerOpen = false;
    await transitionRender("rank", { rankTab: tab });
    return;
  }

  if (action === "open-player") {
    await openPlayer(actionEl.dataset.name);
    return;
  }

  if (action === "back") {
    await goBack();
    return;
  }

  if (action === "open-coming") {
    const title = actionEl.dataset.title || "Coming soon";
    state.drawerOpen = false;
    await openComingSoon(title);
    return;
  }

  if (action === "notifications") {
    setLoader(true, "Notifications are coming soon…");
    await sleep(850);
    setLoader(false);
  }
});

renderInitial();
