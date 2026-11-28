# 🏏 Crickium

**Crickium** is a cricket management game that runs as a **Telegram bot** with a companion
**Telegram Mini App**. You collect player cards, build a squad and a Playing XI, play
ball-by-ball matches across real cricket leagues, complete daily quests, and run your own
tournaments with live player auctions.

Everything is stored in PostgreSQL, so progress is shared between the bot and the Mini App.

---

## ✨ What you can do

| Area | What it gives you |
| --- | --- |
| 🎴 **Card collection** | Claim one free card every hour, buy/sell cards, evolve a card into a better edition, trade cards with other users |
| 📦 **Packs** | Five pack tiers (Bronze → Platinum) with guaranteed OVR bands, plus coins and Rubies inside |
| 🏏 **Matches** | Ball-by-ball T20 and Super Over cricket with toss, pitch report, lineup, tactics and live commentary |
| 🏆 **Tournaments** | Create a league, invite teams, run a live auction with purse/bid steps, then play the schedule |
| 💰 **Economy** | Coins, Rubies and Sigils, a coin shop, player upgrades with equippable items, and a leaderboard |
| 🎯 **Progression** | XP and levels, daily streaks, hourly Kit Bag, daily/weekly/monthly quests, achievements, referrals |
| 🎁 **Social** | Gifts, giveaways, head-to-head records, team logos, and a Mini App leaderboard |
| 📱 **Mini App** | Home, Search, Matches, Market, My Gifts, Rank, Squad, Collection, Wallet and Settings inside Telegram |

---

## 🏟 Games and leagues

Each game mode is its own engine with its own teams, squads and scoring:

- `/play` — play with your **own custom XI**
- `/playipl` — **Indian Premier League**
- `/playwpl` — **Women's Premier League**
- `/playbbl` — **Big Bash League**
- `/playwbbl` — **Women's Big Bash League**
- `/playilt` — **International League T20**
- `/playint` — **International T20**
- `/playso` — **Super Over** (one-over shootout)
- `/playai` — quick match against the AI
- `/tplay` — join your scheduled **tournament** match

A match runs on a toss → pitch report → lineup → delivery/shot decision loop, with live
scores, commentary, scorecards and match rewards. Sessions can be paused (`/pause`) and
restored (`/resume`, `/recover`).

---

## 🎴 Cards, rarity and economy

A player's **OVR** is the higher of their batting and bowling level. Rarity comes from OVR:

| OVR | Rarity |
| --- | --- |
| 55 – 64 | Common |
| 65 – 74 | Medium |
| 75 – 84 | Rare |
| 85 – 89 | Epic |
| 90 – 94 | Elite |
| 95 – 97 | Legendary |
| 98 – 99 | Iconic |

Cards exist in three families: **Global** (standard catalog), **Special Edition** and
**Legacy Showcase**. `utils/price_chart.py` sets the buy price and sell value for every OVR.

**Packs** (`/packs`) are bought with **Sigils (SG)**:

| Pack | Price | Cards | Core OVR | Bonus OVR |
| --- | --- | --- | --- | --- |
| Bronze | 200 SG | 2 | 80–85 | 66–74 |
| Silver | 450 SG | 2 | 83–87 | 70–79 |
| Gold | 850 SG | 3 | 85–90 | 75–84 |
| Diamond | 1,500 SG | 3 | 88–94 | 80–89 |
| Platinum | 2,500 SG | 4 | 92–99 | 85–94 |

Currencies: **🪙 Coins** (buy cards, coin shop), **💎 Rubies** (premium rewards),
**✨ Sigils** (packs). Upgrades are bought with coins and equipped onto individual players.

---

## 🏆 Tournaments and auctions

Host your own league without leaving Telegram:

1. `/createtour`, `/teamname`, `/myteam` — create the tournament and set up your team
2. `/setpurse`, `/setsteps`, `/setroles`, `/setsquad`, `/setoverseas`, `/prize`, `/prizesplit` — auction rules
3. `/auction`, `/bid`, `/aucpause`, `/aucresume` — run the live auction
4. `/tsched`, `/tour`, `/tourstats`, `/tplay` — schedule, standings, stats and matches
5. `/cobidder`, `/coowner`, `/teamown`, `/endtour` — team management

---

## 💬 Main commands

The bot registers its full command list with Telegram; `/start` and `/menu` open the
button-driven menu. The most useful ones:

**Start & profile**
`/start` `/app` `/menu` `/debut` `/profile` `/mybank` `/achievements` `/plstats` `/id`

**Rewards & quests**
`/daily` `/claim` `/cquest` `/refer` `/myreferrals`

**Cards & squad**
`/pshop` `/player` `/buy` `/sell` `/evolve` `/trade` `/packs` `/mypacks` `/openpack`
`/packcards` `/buypack` `/pxl` `/squad` `/buildxl` `/changexl` `/teamcap` `/team`

**Games**
`/play` `/playipl` `/playwpl` `/playbbl` `/playwbbl` `/playilt` `/playint` `/playso`
`/playai` `/matchscore` `/pitchreport` `/pause` `/resume` `/recover` `/forfeitall`
`/endgame` `/exitgame`

**Shop & upgrades**
`/cshop` `/cbuy` `/ushop` `/ubuy` `/ulevelup` `/inventory` `/equip` `/equiplist` `/unequip`

**Tournament & auction**
`/createtour` `/myteam` `/teamname` `/setpurse` `/setsteps` `/aucrules` `/setroles` `/setsquad`
`/setoverseas` `/prize` `/prizesplit` `/teamown` `/cobidder` `/coowner` `/auction` `/bid`
`/aucpause` `/aucresume` `/tplay` `/tour` `/tourstats` `/tsched` `/endtour`

**Community & support**
`/h2h` `/pnews` `/give` `/gift` `/mygift` `/giveaway` `/coinlb` `/setteam` `/setlogo` `/teamlogo`
`/bstatus` `/bug` `/feedback`

---

## 📱 Mini App

`miniapp_backend/` serves a single-page app that reads the same PostgreSQL data as the bot
(through an ASGI backend, no second simulator):

- **Home, Search, Matches, Market, My Gifts, Rank** as primary navigation
- Squad, Collection, Quest board, Rewards, Wallet, Settings in the drawer
- Market purchases, daily streaks, Kit Bag claims and pack openings are validated
  **server-side** in transactions
- Telegram `initData` is verified with HMAC; the client never supplies an authoritative
  user ID or reward amount

The Mini App posts to endpoints under `/api/...` (see `miniapp_backend/README.md` for the
full route list and security checklist).

---

## 🧱 Tech stack

- **Python 3.10+**, `asyncio`
- **Kurigram / Pyrogram** client for the Telegram Bot API (rich messages supported)
- **PostgreSQL** via `asyncpg` with hand-written SQL repositories (`database/`)
- **Pillow** for rendered player/profile cards
- Optional **Redis** cache for command and snapshot caching
- **ASGI** Mini App backend (`uvicorn`) + static front end

```
main.py / handlers/     Telegram bot: command + callback handlers
engines/                Match simulation: toss, pitch, delivery, shot, innings, score, commentary
services/               Game systems: cards, packs, quests, tournaments, auctions, rewards
database/               PostgreSQL repos, migrations, pool, cache
miniapp_backend/        Mini App API + static UI
utils/                  Pricing, rarity, premium emoji, flags, timers, helpers
buttons/, models/       Keyboards and domain models
launcher.py             Local run: Mini App backend + Cloudflare quick tunnel
railway_start.py        One-process deploy: bot + web server + keep-alive
```

---

## 🚀 Getting started

```bash
# 1. Install bot dependencies
python -m pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env      # then fill API_ID, API_HASH, BOT_TOKEN, DATABASE_URL

# 3. Run the bot (applies migrations on start)
python main.py
```

Optional Mini App:

```bash
python -m pip install -r miniapp_backend/requirements.txt
python -m miniapp_backend.main        # API + UI on MINIAPP_PORT (default 8000)
# or, for a public URL during development:
python launcher.py                    # backend + Cloudflare quick tunnel
```

For a single hosted service (Railway/Render), `python railway_start.py` starts the bot and
the web server together and pings `/api/health` to keep the instance awake.

**Environment variables** (full list in `.env.example`): `API_ID`, `API_HASH`, `BOT_TOKEN`,
`DATABASE_URL`, optional `REDIS_URL`, `MINIAPP_URL`, `ADMIN_USER_ID`,
`NOTIFICATION_GROUP_ID`, `AUTO_BACKUP_*`.

> ⚠️ Never commit `.env` or any token. Set `MINIAPP_DEBUG_AUTH=1` only for local testing —
> it bypasses Telegram `initData` verification and must stay off in production.

---

Built with ❤️ for cricket fans on Telegram.
