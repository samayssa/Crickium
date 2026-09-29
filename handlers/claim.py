from __future__ import annotations

print("claim.py loaded")

import asyncio
import html
import json
import random
from datetime import datetime

from pyrogram.errors import ChatWriteForbidden

from handlers.registry import register, register_callback
from app import app
from database.query import execute, fetch, fetchrow, transaction
from database.claims_repo import get_claim
from database.squads_repo import get_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.price_chart import get_price, format_price
from buttons.claim_buttons import retain_release_keyboard
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating
from utils.debut_gate import has_completed_debut
from utils.randomiser import weighted_claim_band

CLAIM_COOLDOWN_SECONDS = 3600
CLAIM_ATTEMPT_COOLDOWN_SECONDS = 10
CLAIM_PENDING_TIMEOUT_SECONDS = 60
MAX_SQUAD_SIZE = 25
NO_KEYBOARD = {"inline_keyboard": []}
_CLAIM_RELEASE_TASKS: dict[int, asyncio.Task] = {}


async def _safe_claim_send_message(chat_id, text, **kwargs):
    """Avoid a handler traceback when Telegram has revoked write rights in a chat."""
    try:
        return await app.send_message(chat_id, text, **kwargs)
    except ChatWriteForbidden as exc:
        print(f"[claim] Telegram denied message write in chat_id={chat_id}: {exc!r}")
        return None


def _format_remaining(seconds: float) -> str:
    remaining = max(0, int(CLAIM_COOLDOWN_SECONDS - seconds))
    minutes, secs = divmod(remaining, 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


async def _get_random_claim_player_cockroach() -> dict | None:
    """Choose a claim player without Cockroach pausable-portal patterns.

    CockroachDB can keep a pausable pgwire portal open for some read queries
    involving ORDER BY/LIMIT. The previous claim path selected with
    `ORDER BY random() LIMIT 1`, then immediately started a write transaction;
    on a reused asyncpg connection that could make the following INSERT/UPDATE
    fail with a `multiple active portals` FeatureNotSupportedError.

    Preserve the exact weighted level band and uniform-in-band randomness, but
    fetch only player IDs with a simple read-only query, choose one in Python,
    and fetch that player by primary key. Both result sets are fully consumed
    before the write transaction begins.
    """
    band = weighted_claim_band()

    rows = await fetch(
        """
        SELECT player_id
        FROM players
        WHERE GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) BETWEEN $1 AND $2;
        """,
        int(band["min"]),
        int(band["max"]),
    )

    if not rows:
        return None

    player_id = int(random.choice(rows)["player_id"])

    player = await fetchrow(
        "SELECT * FROM players WHERE player_id = $1;",
        player_id,
    )

    return dict(player) if player else None


def _player_card_text(
    player: dict,
    header: str,
    assignment_status: str,
    username: str | None = None,
    footer: str | None = None,
    squad_size: int | None = None,
    max_squad_size: int | None = None,
) -> str:
    """
    Build the formatted claim card using Telegram HTML entities
    """
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    bat_hand = _escape(batting_style_text(player.get("batting_hand")))
    bowl_style = _escape(bowling_style_text(player.get("bowling_hand")))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)
    ovr = overall_rating(bat_level, bowl_level)
    buy_price, sell_price = get_price(ovr)
    value_text = f"B: {format_price(buy_price)} | S: {format_price(sell_price)}"

    title = (
        "┏━━━━━━━━━━━━━━━━━━━━┓\n"
        f"    <b>{_escape(header)}</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"
    )

    user_display = f"👤 <b>Claimed By:</b> @{username}\n\n" if username else ""

    quote_block = (
        "<blockquote>\n"
        f"🏃 Player: {name} {flag}\n"
        "</blockquote>"
    )

    details_block = (
        "<blockquote>\n"
        f"↳ 📌 Assignment   : {_escape(assignment_status)}\n"
        f"↳ 💰 Claim Reward : +1000 Coins\n"
        f"↳ 🏏 Bat Style  : {bat_hand}\n"
        f"↳ 🎯 Bowl Style : {bowl_style}\n"
        f"↳ ⭐ Level        : 🏏 {bat_level} | 🎯 {bowl_level}\n"
        f"↳ 💎 Value        : {value_text}\n"
        "</blockquote>"
    )

    parts = [
        title,
        "",
        user_display + quote_block,
        details_block,
        "━━━━━━━━━━━━━━━━━━",
    ]

    if footer:
        parts.extend(["", footer])

    if squad_size is not None and max_squad_size is not None:
        parts.extend(["", f"Current Squad Size: {squad_size}/{max_squad_size}"])

    return "\n".join(parts)


async def _claim_attempt_gate(conn, user_id: int):
    row = await conn.fetchrow(
        """
        SELECT claim_attempt_at,
               EXTRACT(EPOCH FROM (NOW() - claim_attempt_at)) AS elapsed
        FROM users
        WHERE user_id = $1
        FOR UPDATE;
        """,
        user_id,
    )

    if row and row["claim_attempt_at"] is not None:
        elapsed = float(row["elapsed"] or 0)

        if elapsed < CLAIM_ATTEMPT_COOLDOWN_SECONDS:
            return CLAIM_ATTEMPT_COOLDOWN_SECONDS - elapsed

    await conn.execute(
        "UPDATE users SET claim_attempt_at = NOW() WHERE user_id = $1;",
        user_id,
    )

    return None


async def _auto_release_claim_once(claim_id: int) -> bool:
    """Auto-release one pending claim exactly once.

    The claim row is locked inside the transaction so manual Retain/Release
    callbacks and the expiry task cannot both resolve the same claim or grant
    the release reward twice.
    """

    async def _tx(conn):
        claim = await conn.fetchrow(
            """
            SELECT claim_id, user_id, player_id, chat_id, message_id, status, claimed_at
            FROM player_claims
            WHERE claim_id = $1
            FOR UPDATE;
            """,
            int(claim_id),
        )

        if not claim:
            return None

        if claim["status"] != "pending":
            return None

        due = await conn.fetchrow(
            "SELECT EXTRACT(EPOCH FROM (NOW() - $1::timestamptz)) AS elapsed;",
            claim["claimed_at"],
        )

        elapsed = float(due["elapsed"] or 0.0) if due else 0.0

        if elapsed < CLAIM_PENDING_TIMEOUT_SECONDS:
            return {
                "kind": "not_due",
                "remaining": max(
                    0.0,
                    CLAIM_PENDING_TIMEOUT_SECONDS - elapsed,
                ),
            }

        player = await conn.fetchrow(
            "SELECT * FROM players WHERE player_id = $1;",
            int(claim["player_id"]),
        )

        if not player:
            updated = await conn.execute(
                """
                UPDATE player_claims
                SET status = 'released'
                WHERE claim_id = $1
                  AND status = 'pending';
                """,
                int(claim_id),
            )

            if not updated.endswith(" 1"):
                return None

            return {
                "kind": "released",
                "chat_id": claim["chat_id"],
                "message_id": claim["message_id"],
                "claim_id": int(claim_id),
            }

        ovr = overall_rating(
            int(player.get("bat_level") or 0),
            int(player.get("bowl_level") or 0),
        )

        _buy_price, sell_price = get_price(ovr)

        updated = await conn.execute(
            """
            UPDATE player_claims
            SET status = 'released'
            WHERE claim_id = $1
              AND status = 'pending';
            """,
            int(claim_id),
        )

        if not updated.endswith(" 1"):
            return None

        credited = await conn.execute(
            """
            UPDATE users
            SET balance = balance + $1,
                last_seen_at = NOW()
            WHERE user_id = $2;
            """,
            int(sell_price),
            int(claim["user_id"]),
        )

        if not credited.endswith(" 1"):
            raise RuntimeError(
                f"Could not credit auto-release reward for user_id={claim['user_id']}"
            )

        return {
            "kind": "released",
            "claim_id": int(claim_id),
            "chat_id": (
                int(claim["chat_id"])
                if claim["chat_id"] is not None
                else None
            ),
            "message_id": (
                int(claim["message_id"])
                if claim["message_id"] is not None
                else None
            ),
        }

    result = await transaction(_tx)

    if not result:
        return False

    if result.get("kind") == "not_due":
        return False

    chat_id = result.get("chat_id")
    message_id = result.get("message_id")

    if chat_id is not None and message_id is not None:
        text = (
            "<b>⏱️ CLAIM EXPIRED</b>\n\n"
            "<b>⏳ You didn't choose Retain or Release within 1 minute.</b>\n\n"
            "<b>🔄 The player was automatically released.</b>"
        )

        try:
            await app.edit_message_text(
                chat_id,
                message_id,
                text,
                parse_mode="HTML",
                reply_markup=NO_KEYBOARD,
            )
        except Exception:
            try:
                await app.edit_message_caption(
                    chat_id,
                    message_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=NO_KEYBOARD,
                )
            except Exception as exc:
                print(
                    f"[claim] Could not update expired claim "
                    f"message claim_id={claim_id}: {exc!r}"
                )

    print(
        f"[claim] Auto-released claim_id={claim_id} after 60 seconds."
    )

    return True


def _cancel_claim_release_task(claim_id: int) -> None:
    """Cancel and forget the in-memory expiry task for one claim."""
    task = _CLAIM_RELEASE_TASKS.pop(int(claim_id), None)

    if task is None or task.done():
        return

    current = asyncio.current_task()

    if task is not current:
        task.cancel()


def _schedule_claim_release_task(claim_id: int, delay: float) -> None:
    """Schedule exactly one in-memory expiry task for a pending claim."""
    claim_id = int(claim_id)

    _cancel_claim_release_task(claim_id)

    delay = max(0.0, float(delay))

    async def _runner() -> None:
        current = asyncio.current_task()

        try:
            if delay > 0:
                await asyncio.sleep(delay)

            retry_delays = (5.0, 15.0, 30.0)

            for attempt in range(len(retry_delays) + 1):
                try:
                    await _auto_release_claim_once(claim_id)
                    return

                except asyncio.CancelledError:
                    raise

                except Exception as exc:
                    if attempt >= len(retry_delays):
                        print(
                            f"[claim] Expiry task failed permanently "
                            f"for claim_id={claim_id}: {exc!r}"
                        )
                        return

                    print(
                        f"[claim] Expiry task retry "
                        f"{attempt + 1}/{len(retry_delays)} "
                        f"for claim_id={claim_id}: {exc!r}"
                    )

                    await asyncio.sleep(retry_delays[attempt])

        except asyncio.CancelledError:
            return

        finally:
            if _CLAIM_RELEASE_TASKS.get(claim_id) is current:
                _CLAIM_RELEASE_TASKS.pop(claim_id, None)

    try:
        _CLAIM_RELEASE_TASKS[claim_id] = asyncio.create_task(_runner())
    except RuntimeError:
        _CLAIM_RELEASE_TASKS.pop(claim_id, None)


async def start_claim_maintenance():
    """Recover and reschedule pending claim expiries after process startup."""
    rows = await fetch(
        """
        SELECT claim_id,
               claimed_at,
               EXTRACT(EPOCH FROM (NOW() - claimed_at)) AS elapsed
        FROM player_claims
        WHERE status = 'pending'
        ORDER BY claimed_at ASC;
        """
    )

    recovered = 0
    scheduled = 0

    for row in rows:
        claim_id = int(row["claim_id"])
        elapsed = float(row["elapsed"] or 0.0)

        remaining = max(
            0.0,
            CLAIM_PENDING_TIMEOUT_SECONDS - elapsed,
        )

        if remaining <= 0:
            try:
                if await _auto_release_claim_once(claim_id):
                    recovered += 1

            except Exception as exc:
                print(
                    f"[claim] Startup expiry recovery failed "
                    f"claim_id={claim_id}: {exc!r}"
                )
        else:
            _schedule_claim_release_task(
                claim_id,
                remaining,
            )
            scheduled += 1

    if recovered or scheduled:
        print(
            f"[claim] Startup claim maintenance: "
            f"released={recovered}, timers={scheduled}."
        )


@register("claim")
async def claim_command(message):
    chat_id = message["chat"]["id"]

    from_user = message.get("from", {})

    user_id = from_user.get("id")

    username = (
        from_user.get("username")
        or from_user.get("first_name")
        or "User"
    )

    print(
        f"[claim] /claim invoked by user_id={user_id}"
    )

    if not await has_completed_debut(int(user_id)):
        await _safe_claim_send_message(
            chat_id,
            "<b>⚠️ Complete your /debut first to unlock player collection.</b>",
            parse_mode="HTML",
        )
        return

    await execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name,
            last_seen_at
        )
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            last_seen_at = NOW();
        """,
        user_id,
        from_user.get("username"),
        from_user.get("first_name"),
    )

    async def _attempt_tx(conn):
        return await _claim_attempt_gate(
            conn,
            int(user_id),
        )

    attempt_remaining = await transaction(_attempt_tx)

    if attempt_remaining is not None:
        remaining_text = f"{max(1, int(attempt_remaining + 0.999))}s"

        await _safe_claim_send_message(
            chat_id,
            (
                f"<b>⏳ Claim cooldown active.</b>\n\n"
                f"<b>You can use /claim again in "
                f"{html.escape(remaining_text)}.</b>"
            ),
            parse_mode="HTML",
        )
        return

    current_squad = await get_team_squad(user_id) or []

    if len(current_squad) >= MAX_SQUAD_SIZE:
        await _safe_claim_send_message(
            chat_id,
            (
                f"<b>⚠️ Your squad is full "
                f"({MAX_SQUAD_SIZE}/{MAX_SQUAD_SIZE}).</b>\n"
                "Sell a player before claiming another one."
            ),
            parse_mode="HTML",
        )
        return

    # Select the random player before starting the write transaction.
    # The selection uses fully-consumed read-only queries, while the actual
    # reservation transaction below starts with a write. This avoids the
    # CockroachDB pausable-portal incompatibility encountered when
    # ORDER BY random() was followed immediately by writes on a reused
    # asyncpg connection.
    player = await _get_random_claim_player_cockroach()

    if not player:
        await _safe_claim_send_message(
            chat_id,
            (
                "<b>⚠️ No players available to claim yet.</b>\n"
                "Ask the bot admin to /upload_pl players first."
            ),
            parse_mode="HTML",
        )
        return

    async def _claim_reservation_tx(conn):
        # First statement is an atomic conditional UPDATE.
        #
        # It preserves the original one-hour cooldown by checking the latest
        # claim history in the database while also crediting the +1000 reward.
        #
        # We intentionally avoid SELECT ... FOR UPDATE here. CockroachDB's
        # pgwire pausable-portal implementation can reject a write after a
        # locking SELECT has left a portal active on the same session.
        #
        # CockroachDB's serializable retry handling in database.query.transaction()
        # handles concurrent transactions that contend on the same user row.
        updated = await conn.execute(
            """
            UPDATE users
               SET balance = balance + 1000,
                   last_seen_at = NOW()
             WHERE user_id = $1
               AND NOT EXISTS (
                   SELECT 1
                   FROM player_claims
                   WHERE user_id = $1
                     AND claimed_at >= NOW() - INTERVAL '1 hour'
               );
            """,
            int(user_id),
        )

        if not updated.endswith(" 1"):
            return {
                "kind": "cooldown"
            }

        # Keep the INSERT as the final statement in the transaction so its
        # RETURNING portal does not need to coexist with another write/query.
        claim_row = await conn.fetchrow(
            """
            INSERT INTO player_claims (
                user_id,
                player_id,
                status,
                chat_id,
                message_id
            )
            VALUES ($1, $2, 'pending', $3, $4)
            RETURNING *;
            """,
            int(user_id),
            int(player["player_id"]),
            int(chat_id),
            None,
        )

        if not claim_row:
            raise RuntimeError(
                f"Could not create claim for user_id={user_id}"
            )

        return {
            "kind": "reserved",
            "claim": dict(claim_row),
            "player": player,
        }

    reservation = await transaction(
        _claim_reservation_tx
    )

    if (
        isinstance(reservation, dict)
        and reservation.get("kind") == "cooldown"
    ):
        # This read happens after the write transaction has completed,
        # so it cannot hold a portal while the reservation transaction writes.
        cooldown_row = await fetchrow(
            """
            SELECT EXTRACT(
                EPOCH FROM (NOW() - claimed_at)
            ) AS elapsed
            FROM player_claims
            WHERE user_id = $1
            ORDER BY claimed_at DESC
            LIMIT 1;
            """,
            int(user_id),
        )

        elapsed = (
            float(cooldown_row["elapsed"] or 0.0)
            if cooldown_row
            else 0.0
        )

        remaining_text = _format_remaining(elapsed)

        await _safe_claim_send_message(
            chat_id,
            (
                "<b>⏳ You've already claimed a player recently!</b>\n\n"
                f"<b>Try again in "
                f"{html.escape(remaining_text)}.</b>"
            ),
            parse_mode="HTML",
        )
        return

    if reservation == "no_player":
        # Compatibility with older transaction return shapes.
        await _safe_claim_send_message(
            chat_id,
            (
                "<b>⚠️ No players available to claim yet.</b>\n"
                "Ask the bot admin to /upload_pl players first."
            ),
            parse_mode="HTML",
        )
        return

    claim = reservation["claim"]
    player = reservation["player"]

    squad = current_squad

    text = _player_card_text(
        player,
        "PLAYER ASSIGNMENT",
        assignment_status="Pending",
        username=username,
        footer="❓ Do you want to assign this player to your squad?",
        squad_size=len(squad),
        max_squad_size=MAX_SQUAD_SIZE,
    )

    keyboard = retain_release_keyboard(
        claim["claim_id"]
    )

    sent_message = None

    try:
        image_bytes, _is_custom = await get_player_card_bytes(
            player
        )

        sent_message = await app.send_photo(
            chat_id,
            photo=image_bytes,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception as exc:
        print(
            f"[claim] Card image failed ({exc!r}), "
            "falling back to a text-only message."
        )

        sent_message = await _safe_claim_send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    sent_message_id = None

    if isinstance(sent_message, dict):
        sent_message_id = sent_message.get("message_id")
    else:
        sent_message_id = getattr(
            sent_message,
            "id",
            None,
        )

    if sent_message_id:
        try:
            await execute(
                """
                UPDATE player_claims
                SET message_id = $1
                WHERE claim_id = $2;
                """,
                int(sent_message_id),
                int(claim["claim_id"]),
            )

        except Exception as exc:
            print(
                f"[claim] Could not save claim message_id "
                f"for auto-expiry claim_id={claim['claim_id']}: {exc!r}"
            )

    claimed_at = claim.get("claimed_at")

    delay = CLAIM_PENDING_TIMEOUT_SECONDS

    if claimed_at is not None:
        try:
            now = (
                datetime.now(tz=claimed_at.tzinfo)
                if getattr(claimed_at, "tzinfo", None)
                else datetime.now()
            )

            delay = max(
                0.0,
                CLAIM_PENDING_TIMEOUT_SECONDS
                - (now - claimed_at).total_seconds(),
            )

        except Exception:
            delay = CLAIM_PENDING_TIMEOUT_SECONDS

    _schedule_claim_release_task(
        int(claim["claim_id"]),
        delay,
    )

    print(
        f"[claim] user_id={user_id} "
        f"claimed player_id={player['player_id']} "
        f"({player['name']}), "
        f"claim_id={claim['claim_id']}, "
        "+1000 coins"
    )


@register_callback("claim_retain")
async def on_claim_retain(callback_query):
    claim_id = int(
        callback_query["data"].split(":")[1]
    )

    presser = callback_query["from"]

    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    user_id = int(presser["id"])

    username = (
        presser.get("username")
        or presser.get("first_name")
        or "User"
    )

    async def _retain_tx(conn):
        # Serialize all clicks on this exact claim.
        # CockroachDB does not provide PostgreSQL's
        # pg_advisory_xact_lock(); the claim row itself is the
        # transactional lock and is locked by FOR UPDATE below.
        claim = await conn.fetchrow(
            """
            SELECT *
            FROM player_claims
            WHERE claim_id = $1
            FOR UPDATE;
            """,
            claim_id,
        )

        if not claim or int(claim["user_id"]) != user_id:
            return {"kind": "invalid"}

        if claim["status"] != "pending":
            return {"kind": "resolved"}

        claimed_player = await conn.fetchrow(
            "SELECT * FROM players WHERE player_id = $1;",
            int(claim["player_id"]),
        )

        if not claimed_player:
            await conn.execute(
                """
                UPDATE player_claims
                SET status = 'retained'
                WHERE claim_id = $1
                  AND status = 'pending';
                """,
                claim_id,
            )

            return {"kind": "missing_player"}

        squad_row = await conn.fetchrow(
            """
            SELECT squad
            FROM team_squads
            WHERE user_id = $1
            FOR UPDATE;
            """,
            user_id,
        )

        raw_squad = (
            squad_row["squad"]
            if squad_row
            else []
        )

        if isinstance(raw_squad, str):
            squad = json.loads(raw_squad)

        elif isinstance(raw_squad, list):
            squad = list(raw_squad)

        else:
            squad = json.loads(
                json.dumps(
                    raw_squad,
                    default=str,
                )
            )

        already_in_squad = any(
            int(p.get("player_id") or 0)
            == int(claimed_player["player_id"])
            for p in squad
            if isinstance(p, dict)
        )

        if (
            not already_in_squad
            and len(squad) >= MAX_SQUAD_SIZE
        ):
            return {
                "kind": "full",
                "size": len(squad),
            }

        if not already_in_squad:
            squad.append(dict(claimed_player))

            squad_json = json.dumps(
                squad,
                default=str,
            )

            await conn.execute(
                """
                INSERT INTO team_squads (
                    user_id,
                    squad,
                    updated_at
                )
                VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    squad = EXCLUDED.squad,
                    updated_at = NOW();
                """,
                user_id,
                squad_json,
            )

            await conn.execute(
                """
                DELETE FROM player_user_match_stats
                WHERE user_id = $1
                  AND player_id = $2;
                """,
                user_id,
                int(claimed_player["player_id"]),
            )

        updated = await conn.execute(
            """
            UPDATE player_claims
            SET status = 'retained'
            WHERE claim_id = $1
              AND status = 'pending';
            """,
            claim_id,
        )

        if not updated.endswith(" 1"):
            return {"kind": "resolved"}

        return {
            "kind": "retained",
            "player": dict(claimed_player),
            "squad": squad,
            "already_in_squad": already_in_squad,
        }

    result = await transaction(_retain_tx)

    if result["kind"] == "invalid":
        await app.answer_callback_query(
            callback_query["id"],
            "This isn't your claim!",
            show_alert=True,
        )
        return

    if result["kind"] == "resolved":
        await app.answer_callback_query(
            callback_query["id"],
            "This claim has already been resolved.",
            show_alert=True,
        )

        _cancel_claim_release_task(
            claim_id
        )

        return

    if result["kind"] == "full":
        await app.answer_callback_query(
            callback_query["id"],
            "Your squad is full. Sell a player before retaining this claim.",
            show_alert=True,
        )
        return

    _cancel_claim_release_task(
        claim_id
    )

    claimed_player = result.get("player")

    squad = result.get("squad") or []

    if result["kind"] == "missing_player":
        text = (
            "<b>🤝 Player retained and added "
            "to your collection!</b>"
        )

    else:
        already_in_squad = bool(
            result.get("already_in_squad")
        )

        footer = (
            "ℹ️ This player is already in your collection."
            if already_in_squad
            else "✅ This player has been successfully added to your collection!"
        )

        text = _player_card_text(
            claimed_player,
            "PLAYER ASSIGNED",
            assignment_status="Assigned",
            username=username,
            footer=footer,
            squad_size=len(squad),
            max_squad_size=MAX_SQUAD_SIZE,
        )

    await app.answer_callback_query(
        callback_query["id"],
        "Player retained!",
    )

    if (
        callback_query.get("message") or {}
    ).get("photo"):
        await app.edit_message_caption(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    else:
        await app.edit_message_text(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )

    try:
        from services.referrals import refresh_referral_progress

        await refresh_referral_progress(
            user_id
        )

    except Exception as exc:
        print(
            f"[claim] Referral progress update failed "
            f"for user_id={user_id}: {exc!r}"
        )


@register_callback("claim_release")
async def on_claim_release(callback_query):
    claim_id = int(
        callback_query["data"].split(":")[1]
    )

    presser = callback_query["from"]

    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    username = (
        presser.get("username")
        or presser.get("first_name")
        or "User"
    )

    claim = await get_claim(
        claim_id
    )

    if (
        not claim
        or claim["user_id"] != presser["id"]
    ):
        await app.answer_callback_query(
            callback_query["id"],
            "This isn't your claim!",
            show_alert=True,
        )
        return

    if claim["status"] != "pending":
        await app.answer_callback_query(
            callback_query["id"],
            "This claim has already been resolved.",
            show_alert=True,
        )
        return

    claimed_player = await fetchrow(
        "SELECT * FROM players WHERE player_id = $1;",
        claim["player_id"],
    )

    if not claimed_player:
        await app.answer_callback_query(
            callback_query["id"],
            "Player could not be found.",
            show_alert=True,
        )
        return

    ovr = overall_rating(
        int(claimed_player.get("bat_level") or 0),
        int(claimed_player.get("bowl_level") or 0),
    )

    _buy_price, sell_price = get_price(ovr)

    async def _release_tx(conn):
        row = await conn.fetchrow(
            """
            SELECT status
            FROM player_claims
            WHERE claim_id = $1
            FOR UPDATE;
            """,
            claim_id,
        )

        if not row or row["status"] != "pending":
            return False

        await conn.execute(
            """
            UPDATE player_claims
            SET status = 'released'
            WHERE claim_id = $1;
            """,
            claim_id,
        )

        updated = await conn.execute(
            """
            UPDATE users
            SET balance = balance + $1,
                last_seen_at = NOW()
            WHERE user_id = $2;
            """,
            int(sell_price),
            int(presser["id"]),
        )

        if not updated.endswith(" 1"):
            raise RuntimeError(
                f"Could not credit release reward "
                f"for user_id={presser['id']}"
            )

        return True

    released = await transaction(
        _release_tx
    )

    if not released:
        await app.answer_callback_query(
            callback_query["id"],
            "This claim has already been resolved.",
            show_alert=True,
        )

        _cancel_claim_release_task(
            claim_id
        )

        return

    _cancel_claim_release_task(
        claim_id
    )

    if claimed_player:
        footer = (
            "🔄 This player has been released "
            "back to the global pool."
        )

        text = _player_card_text(
            claimed_player,
            "PLAYER RELEASED",
            assignment_status="Released",
            username=username,
            footer=footer,
        )
    else:
        text = (
            "<b>🔄 Player released back "
            "to the pool.</b>"
        )

    await app.answer_callback_query(
        callback_query["id"],
        "Player released.",
    )

    if (
        callback_query.get("message") or {}
    ).get("photo"):
        await app.edit_message_caption(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    else:
        await app.edit_message_text(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )