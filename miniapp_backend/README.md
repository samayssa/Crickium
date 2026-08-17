# Crickium Mini App

This folder contains the FastAPI backend and the static Telegram Mini App lobby.

## Local test run

From the project root:

```bash
pip install -r miniapp_backend/requirements.txt
python -m miniapp_backend.main
```

If you open `miniapp_backend/main.py` directly in Pydroid, it also works after the path bootstrap added in this update.

## Telegram note

- The bot's Mini App button is shown in **private chat** to avoid Telegram's button-type validation error in groups.
- In groups and supergroups, the bot now replies with a safe text-only message.

## What is ready now

- Home lobby UI
- Telegram profile photo, display name, and user ID
- Coins, rubies, league progress, and home stats
- Bottom tabs for Home, Shop, Matches, Daily Rewards, and Rank
- Non-home tabs open a coming-soon screen

## What you still need for Telegram launch

- Set `MINIAPP_URL` to a public HTTPS URL
- Run the backend on a host or tunnel reachable from Telegram
- Point the bot to that same URL


## URL sync note
If you run a temporary tunnel (Colab/ngrok/cloudflared), write the public HTTPS URL to `cricket_bot/miniapp_url.txt` or set `MINIAPP_URL` in the environment. The bot's `/app` command reads the latest valid URL automatically.
