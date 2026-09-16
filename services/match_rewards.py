from __future__ import annotations

from database.query import transaction
from engines.level_engine import add_xp, WIN_XP

WIN_COINS = 10_000
WIN_RUBIES = 50
LOSS_COINS = 5_000
LOSS_RUBIES = 25
LOSS_XP = 0

async def award_competitive_rewards(winner_id: int, loser_id: int) -> dict:
    async def _tx(conn):
        for uid, coins, rubies, xp_gain in (
            (winner_id, WIN_COINS, WIN_RUBIES, WIN_XP),
            (loser_id, LOSS_COINS, LOSS_RUBIES, LOSS_XP),
        ):
            row = await conn.fetchrow("SELECT level, xp FROM users WHERE user_id=$1 FOR UPDATE;", uid)
            level = int(row["level"] or 1) if row else 1
            xp = int(row["xp"] or 0) if row else 0
            new_level, new_xp = add_xp(level, xp, xp_gain)
            await conn.execute(
                "UPDATE users SET balance=balance+$1, rubies=COALESCE(rubies,0)+$2, level=$3, xp=$4 WHERE user_id=$5;",
                coins, rubies, new_level, new_xp, uid,
            )
        await conn.execute(
            "INSERT INTO player_stats (user_id,matches,wins,losses) VALUES ($1,1,1,0) ON CONFLICT (user_id) DO UPDATE SET matches=player_stats.matches+1,wins=player_stats.wins+1;",
            winner_id,
        )
        await conn.execute(
            "INSERT INTO player_stats (user_id,matches,wins,losses) VALUES ($1,1,0,1) ON CONFLICT (user_id) DO UPDATE SET matches=player_stats.matches+1,losses=player_stats.losses+1;",
            loser_id,
        )
    await transaction(_tx)
    try:
        from services.referrals import refresh_referral_progress
        await refresh_referral_progress(int(winner_id))
        await refresh_referral_progress(int(loser_id))
    except Exception as exc:
        print(f"[match_rewards] Referral progress update failed: {exc!r}")
    return {"winner_coins": WIN_COINS, "winner_rubies": WIN_RUBIES, "winner_xp": WIN_XP,
            "loser_coins": LOSS_COINS, "loser_rubies": LOSS_RUBIES, "loser_xp": LOSS_XP}

async def award_timeout_rewards(winner_id: int, loser_id: int) -> dict:
    """Inactivity-only settlement. Normal match rewards remain unchanged."""
    TIMEOUT_WIN_COINS = 5_000
    TIMEOUT_WIN_RUBIES = 0
    TIMEOUT_LOSS_COINS = -5_000
    TIMEOUT_LOSS_RUBIES = -25

    async def _tx(conn):
        # Do not call award_competitive_rewards here: inactivity has a distinct
        # economy rule and must never inherit the normal +10k/+50 payout.
        for uid, coins_delta, rubies_delta in (
            (int(winner_id), TIMEOUT_WIN_COINS, TIMEOUT_WIN_RUBIES),
            (int(loser_id), TIMEOUT_LOSS_COINS, TIMEOUT_LOSS_RUBIES),
        ):
            row = await conn.fetchrow("SELECT level, xp FROM users WHERE user_id=$1 FOR UPDATE;", uid)
            if not row:
                continue
            await conn.execute(
                "UPDATE users SET balance=COALESCE(balance,0)+$1, rubies=COALESCE(rubies,0)+$2, xp=0 WHERE user_id=$3;",
                coins_delta, rubies_delta, uid,
            )
        await conn.execute(
            "INSERT INTO player_stats (user_id,matches,wins,losses) VALUES ($1,1,1,0) ON CONFLICT (user_id) DO UPDATE SET matches=player_stats.matches+1,wins=player_stats.wins+1;",
            int(winner_id),
        )
        await conn.execute(
            "INSERT INTO player_stats (user_id,matches,wins,losses) VALUES ($1,1,0,1) ON CONFLICT (user_id) DO UPDATE SET matches=player_stats.matches+1,losses=player_stats.losses+1;",
            int(loser_id),
        )

    await transaction(_tx)
    try:
        from services.referrals import refresh_referral_progress
        await refresh_referral_progress(int(winner_id))
        await refresh_referral_progress(int(loser_id))
    except Exception as exc:
        print(f"[match_rewards] Referral progress update after inactivity failed: {exc!r}")
    return {
        "winner_coins": TIMEOUT_WIN_COINS, "winner_rubies": TIMEOUT_WIN_RUBIES, "winner_xp": 0,
        "loser_coins": TIMEOUT_LOSS_COINS, "loser_rubies": TIMEOUT_LOSS_RUBIES, "loser_xp": 0,
    }


def build_timeout_message(winner_mention: str, loser_mention: str, engine: str) -> str:
    mode = {"PLAY":"PLAY", "PLAYINT":"PLAY INTERNATIONAL", "PLAYIPL":"PLAY IPL"}.get(engine, engine)
    return (
        "<b>╭━━〔 🏆 MATCH DECIDED 〕━━╮</b>\n\n"
        f"🎮 <b>{mode}</b>\n\n"
        f"⚠️ {loser_mention} did not respond within <b>3 minutes</b>.\n\n"
        f"🏆 Winner ➤ {winner_mention}\n"
        "🎁 Winning Reward ➤ 🪙 <b>+5,000</b> • 💎 <b>0 Rubies</b> • ⭐ <b>0 XP</b>\n\n"
        f"❌ Timeout Loss ➤ {loser_mention}\n"
        "🎁 Match Reward ➤ 🪙 <b>-5,000</b> • 💎 <b>-25 Rubies</b> • ⭐ <b>0 XP</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏱️ <b>Match ended because a required response was not received.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def build_result_caption(winner_mention: str, loser_mention: str) -> str:
    return (
        "<b>🏆 MATCH REWARDS</b>\n\n"
        f"🥇 Winner ➤ {winner_mention}\n"
        "🪙 +10,000 Coins • 💎 +50 Rubies • ⭐ +50 XP\n\n"
        f"🥈 Loser ➤ {loser_mention}\n"
        "🪙 +5,000 Coins • 💎 +25 Rubies • ⭐ 0 XP"
    )
