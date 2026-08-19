from __future__ import annotations

print("daily.py loaded")

import html
import json
from datetime import datetime, timedelta, timezone

from handlers.registry import register
from app import app
from database.query import transaction
from database.squads_repo import get_team_squad
from services.player_card import overall_rating
from utils.mentions import mention
from utils.rarity import get_rarity
from utils.debut_gate import has_completed_debut


DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
MAX_SQUAD_SIZE = 25

REWARDS = {
    1: {"coins": 500, "rubies": 100, "player_range": None, "rarity": None},
    2: {"coins": 1000, "rubies": 200, "player_range": None, "rarity": None},
    3: {"coins": 1500, "rubies": 300, "player_range": None, "rarity": None},
    4: {"coins": 2500, "rubies": 0, "player_range": (55, 64), "rarity": "Common"},
    5: {"coins": 4000, "rubies": 0, "player_range": (65, 74), "rarity": "Medium"},
    6: {"coins": 7000, "rubies": 0, "player_range": (75, 84), "rarity": "Rare"},
    7: {"coins": 10000, "rubies": 0, "player_range": (85, 89), "rarity": "Epic"},
}


def _next_day(streak: int) -> int:
    return 1 if int(streak or 0) >= 7 else int(streak or 0) + 1


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _remaining_text(next_claim_at: datetime, now: datetime) -> str:
    seconds = max(0, int((next_claim_at - now).total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _daily_text(user_display: str, day: int, player: dict | None) -> str:
    reward = REWARDS[day]
    lines = [
        "╭━━〔 🎁 DAILY REWARD 〕━━╮",
        "",
        f"👤 {user_display} claimed ✅️",
        f"📅 Day {day} / 7",
        "",
        "<blockquote>",
        f"🪙 +{reward['coins']:,} Coins",
    ]
    if day <= 3:
        lines.append(f"💎 +{reward['rubies']:,} Rubies")
    else:
        lines.append("🎴 Random Player Card")
    lines.append("</blockquote>")

    if player is not None:
        ovr = overall_rating(player.get("bat_level") or 0, player.get("bowl_level") or 0)
        rarity = reward["rarity"] or get_rarity(ovr)
        lines.extend(
            [
                "",
                "<blockquote>",
                _escape(player.get("name") or "Player"),
                f"╰➤ {rarity} • OVR: {ovr}",
                "</blockquote>",
            ]
        )

    if day == 7:
        lines.extend(["", "🔥 7-Day Streak Complete!"])

    lines.extend(["", "╰━━━━━━━━━━━━━━━━━━╯"])
    return "\n".join(lines)


@register("daily")
async def daily_command(message):
    chat_id = int(message["chat"]["id"])
    from_user = message.get("from") or {}
    user_id = int(from_user.get("id") or 0)
    username = from_user.get("username")
    first_name = from_user.get("first_name") or "Player"

    if not await has_completed_debut(user_id):
        await app.send_message(
            chat_id,
            "<b>⚠️ Complete your /debut first to unlock daily rewards.</b>",
            parse_mode="HTML",
        )
        return

    display = mention(user_id, username, first_name)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async def _tx(conn):
            # Lock the user row so two simultaneous /daily clicks cannot both
            # pay out before either transaction commits.
            user_row = await conn.fetchrow(
                "SELECT user_id FROM users WHERE user_id = $1 FOR UPDATE;",
                user_id,
            )
            if not user_row:
                await conn.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, last_seen_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id) DO NOTHING;
                    """,
                    user_id, username, first_name,
                )
                user_row = await conn.fetchrow(
                    "SELECT user_id FROM users WHERE user_id = $1 FOR UPDATE;",
                    user_id,
                )

            daily = await conn.fetchrow(
                "SELECT streak, last_claim_at, next_claim_at FROM daily_rewards WHERE user_id = $1 FOR UPDATE;",
                user_id,
            )

            if daily and daily["next_claim_at"] is not None:
                next_claim = daily["next_claim_at"].replace(tzinfo=None) if getattr(daily["next_claim_at"], "tzinfo", None) else daily["next_claim_at"]
                if now < next_claim:
                    return {"status": "cooldown", "next_claim_at": next_claim}

            previous_streak = int(daily["streak"] or 0) if daily else 0
            day = _next_day(previous_streak)
            reward = REWARDS[day]

            player = None
            squad_row = await conn.fetchrow(
                "SELECT squad FROM team_squads WHERE user_id = $1 FOR UPDATE;",
                user_id,
            )
            squad = []
            if squad_row:
                raw = squad_row["squad"]
                if isinstance(raw, str):
                    squad = json.loads(raw)
                else:
                    squad = json.loads(json.dumps(raw, default=str))

            if reward["player_range"] is not None:
                if len(squad) >= MAX_SQUAD_SIZE:
                    return {"status": "full_squad", "day": day}

                owned_ids = [int(p.get("player_id") or 0) for p in squad if p.get("player_id")]
                low, high = reward["player_range"]

                if owned_ids:
                    player_row = await conn.fetchrow(
                        """
                        SELECT * FROM players
                        WHERE GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) BETWEEN $1 AND $2
                          AND NOT (player_id = ANY($3::bigint[]))
                        ORDER BY random()
                        LIMIT 1;
                        """,
                        low, high, owned_ids,
                    )
                else:
                    player_row = await conn.fetchrow(
                        """
                        SELECT * FROM players
                        WHERE GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) BETWEEN $1 AND $2
                        ORDER BY random()
                        LIMIT 1;
                        """,
                        low, high,
                    )

                if not player_row:
                    return {"status": "no_player", "day": day, "range": (low, high)}

                player = dict(player_row)
                squad.append(player)

                await conn.execute(
                    """
                    INSERT INTO team_squads (user_id, squad, updated_at)
                    VALUES ($1, $2::jsonb, NOW())
                    ON CONFLICT (user_id)
                    DO UPDATE SET squad = EXCLUDED.squad, updated_at = NOW();
                    """,
                    user_id, json.dumps(squad, default=str),
                )

            next_claim = now + timedelta(seconds=DAILY_COOLDOWN_SECONDS)
            await conn.execute(
                """
                UPDATE users
                SET balance = balance + $1,
                    rubies = rubies + $2,
                    last_seen_at = NOW()
                WHERE user_id = $3;
                """,
                int(reward["coins"]),
                int(reward["rubies"]),
                user_id,
            )
            await conn.execute(
                """
                INSERT INTO daily_rewards (
                    user_id, streak, total_claimed, last_claim_at, next_claim_at, updated_at
                )
                VALUES ($1, $2, 1, NOW(), $3, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    streak = EXCLUDED.streak,
                    total_claimed = daily_rewards.total_claimed + 1,
                    last_claim_at = EXCLUDED.last_claim_at,
                    next_claim_at = EXCLUDED.next_claim_at,
                    updated_at = NOW();
                """,
                user_id, day, next_claim,
            )
            return {"status": "success", "day": day, "player": player}

        result = await transaction(_tx)

        if result["status"] == "cooldown":
            remaining = _remaining_text(result["next_claim_at"], now)
            await app.send_message(
                chat_id,
                f"<b>⏳ Your next daily reward is ready in {html.escape(remaining)}.</b>",
                parse_mode="HTML",
            )
            return

        if result["status"] == "full_squad":
            await app.send_message(
                chat_id,
                "<b>⚠️ Your squad is full (25/25).</b>\n"
                "Sell a player before claiming a player reward from the daily streak.",
                parse_mode="HTML",
            )
            return

        if result["status"] == "no_player":
            low, high = result["range"]
            await app.send_message(
                chat_id,
                f"<b>⚠️ No available player was found in the {low}-{high} OVR range.</b>\n"
                "Your daily reward was not consumed. Please try again later.",
                parse_mode="HTML",
            )
            return

        text = _daily_text(display, result["day"], result["player"])
        await app.send_message(chat_id, text, parse_mode="HTML")

    except Exception as exc:
        print(f"[daily] Failed for user_id={user_id}: {exc!r}")
        await app.send_message(
            chat_id,
            "<b>⚠️ I couldn't process your daily reward right now. No reward was consumed.</b>",
            parse_mode="HTML",
        )
