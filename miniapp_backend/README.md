# Crickium Telegram Mini App

This Mini App is served by the existing Python/ASGI backend and reads Crickium's existing PostgreSQL data. It does not run a second cricket simulator. Screens with no persisted source data explain that limitation instead of inventing values.

## Quick start

From the project root:

```bash
cp .env.example .env
# Fill the private .env file with the existing deployment configuration.
python -m pip install -r miniapp_backend/requirements.txt
python -m miniapp_backend.main
```

The ASGI startup hook connects through the bot's provider-aware database pool and applies the Mini App's additive `miniapp_kitbag_claims` migration. Configure `API_ID`, `API_HASH`, `BOT_TOKEN`, and `DATABASE_URL` from the existing private deployment values before starting the bot. Optional admin/group/channel IDs are also listed in `.env.example`. The delivered `config.py` reads settings from environment/`.env`; private values from the uploaded baseline are intentionally not bundled in the returned archive. Do not place credentials in source control or send them in chat.

The root `launcher.py` starts the backend and a Cloudflare quick tunnel. On Linux x86-64, the verified `tools/cloudflared-linux-amd64` binary is selected automatically. For another operating system or CPU architecture, install the official Cloudflare `cloudflared` build and put it on `PATH`, set `CLOUDFLARED_BIN`, or replace the bundled executable with a compatible official build. Quick tunnel URLs are temporary; update Telegram's Mini App URL whenever it changes.

### Local browser testing

Telegram signs `initData`; the backend verifies the HMAC and rejects stale data. A normal browser does not have valid Telegram initialization data, so authenticated routes intentionally return 401.

For local-only development, you can set:

```bash
MINIAPP_DEBUG_AUTH=1
```

This enables the existing debug viewer fallback. Never enable it on a public deployment. The bot's general `DEBUG` flag does not enable Mini App authentication bypass.

## Screens and routes

Primary navigation: Home, Search, Matches, Market, Rank. Home also previews today's Crickium Quest challenges; the dedicated Crickium Quest board shows the user's daily, weekly, and monthly assignments. The drawer opens Profile, Squad, Collection, Crickium Quest, Rewards, Wallet, Settings, Help, and About.

| Route | Purpose |
| --- | --- |
| `GET /api/home`, `/api/profile` | Telegram viewer, wallet, XP, aggregate stats, squad count, recent and active matches, daily claim state |
| `GET /api/players/search?q=...` | Search global, special-edition and active showcase cards |
| `GET /api/player?kind=...&id=...` | Card details and values recorded in `player_user_match_stats` |
| `GET /api/market?...` | Catalog cards, role/level/country/price filters, and prices from `utils.price_chart` |
| `POST /api/market/purchase` | Server-validated coin debit and squad add in one transaction |
| `GET /api/squad`, `/api/collection` | The authenticated user's actual `team_squads` cards |
| `GET /api/matches` | User's stored quest summaries and active match/challenge setup records |
| `GET /api/matches/{engine}/{id}` | Only the authenticated user's available match summary / scorecard data |
| `GET /api/leaderboard` | Real user levels, XP and recorded match stats |
| `GET /api/quests`, `/api/achievements` | Current daily, weekly and monthly assignments from the existing Crickium quest engine |
| `GET /api/rewards` | Daily streak and hourly Kit Bag eligibility |
| `POST /api/rewards/daily`, `/api/rewards/kitbag` | Server-side reward claims; cooldown and squad limits checked in transactions |
| `GET /api/wallet/transactions` | Quest reward credits recorded in the existing completion table |
| `GET /api/image/player`, `/api/image/profile` | Authenticated server-side Telegram file proxy; bot token is never included in browser URLs |
| `GET /api/health` | Process health |

The browser sends Telegram's signed initialization string in the `X-Telegram-Init-Data` header. It never supplies an authoritative user ID or reward amount.

## Data and feature boundaries

- Market purchase uses the current catalog price chart and locks the user's account/squad rows before checking balance, ownership, and the 25-player cap. Buying is a Mini App-specific wrapper over the existing economy fields; it does not modify bot commands.
- The Kit Bag grants an unowned global-catalog player with OVR exactly 85, if one exists, at most once per hour. Claim time and the granted player are persisted by the backend. A unique database constraint on `user_id` and time-window claim enforcement are deliberately not assumed; user-row locking serializes concurrent requests and the claim history determines cooldown.
- Daily streak claims mirror the existing seven-day reward schedule and debut gate. They are granted by the server, not by the client.
- Match history is only as complete as the repository's persisted rows. The bot does not expose a universal completed-match scorecard repository; full batting/bowling/innings data appears only when stored in the user's quest summary or the existing PLAYSO state.
- Fours, sixes, partnerships, fall of wickets, user profile photos on the leaderboard, and a separate persistent achievement catalog are not present in the queried sources. Those are not fabricated here.
- Wallet history is not a unified ledger in the current bot schema. The Mini App labels the available Quest reward entries and does not imply that they are every coin/ruby movement.
- Squad and collection screens are read-only; existing bot squad-management commands remain authoritative.

## Security and deployment checklist

1. Provide `DATABASE_URL` and `BOT_TOKEN` through your host's secret manager. Do not commit secrets in a config file.
2. Install `miniapp_backend/requirements.txt`; start `python -m miniapp_backend.main` or `python launcher.py`.
3. Set the Telegram Mini App URL to the public HTTPS origin. The app URL must point to the root path, not the API.
4. Keep `MINIAPP_DEBUG_AUTH` unset/false in production. Use HTTPS and a restrictive host-level firewall.
5. Monitor application logs and DB limits. The backend applies per-user request throttles, body-size limits, transaction-level purchase/reward checks, and authenticated access to account data.
6. Use the Cloudflare quick tunnel only for testing. For a stable production domain, configure a named Cloudflare Tunnel using your own Cloudflare account; keep tunnel credentials outside the project ZIP.

See `docs/CRICKIUM_MINIAPP_BLUEPRINT.pdf` for the design map, architecture, API/data boundary, deployment, and verification plan.
