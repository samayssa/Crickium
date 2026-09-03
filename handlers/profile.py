from __future__ import annotations

print("profile.py loaded")

from handlers.registry import register
from app import app
from database.captain_repo import get_captain_id
from database.query import execute
from database.squads_repo import get_team_squad
from database.tier_card_images_repo import get_tier_card_image
from database.user_stats_repo import ensure_franchise_name, get_global_rank, get_profile_snapshot
from engines.level_engine import get_tier
from services.profile_card import render_profile_card
from utils.mentions import mention_name_only_html

MAX_SQUAD_SIZE = 25


async def _ensure_user_row(user_id, username, first_name):
    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, last_seen_at = NOW();
        """,
        user_id, username, first_name,
    )


async def _resolve_captain_name(user_id: int, squad: list[dict]) -> str:
    # /teamcap stores the assigned captain's player_id on the user row - the
    # profile card must read that same value (like /squad and /pxl already
    # do), not guess a "best player" from the squad.
    captain_id = await get_captain_id(user_id)
    if captain_id is None:
        return "Not assigned"
    match = next((p for p in squad if int(p.get("player_id") or 0) == int(captain_id)), None)
    return str(match.get("name")) if match and match.get("name") else "Not assigned"


def _profile_caption(*, player_mention, tier_emoji, tier_key, snapshot, franchise_name, global_rank, captain_name, squad_len):
    return (
        "<b>╭━━〔 🏏 PROFILE 〕━━╮</b>\n\n"
        f"<blockquote><b>│ 👤 Owner ➤ {player_mention}</b></blockquote>\n\n"
        f"<blockquote><b>{tier_emoji} {tier_key.capitalize()} {snapshot['level']} — {get_tier(snapshot['level'])[2]}</b></blockquote>\n\n"
        "<blockquote><b>┌─ SQUAD ────────┐\n"
        f"│ 🎽 Franchise ➤ {franchise_name}\n"
        f"│ 🌍 Rank      ➤ #{global_rank}\n"
        f"│ 🧢 Cap  ➤ {captain_name}\n"
        f"│ 👥 Squad     ➤ {squad_len}/{MAX_SQUAD_SIZE}\n"
        "└────────────────┘\n\n"
        f"🎯 {snapshot['matches']} M   🏆 {snapshot['wins']} W   📊 {snapshot['win_pct']}%</b></blockquote>\n\n"
        "<b>Keep climbing, champion! 📈\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def _download_user_avatar_bytes(user_id: int) -> bytes | None:
    try:
        user = await app._client.get_users(user_id)
    except Exception as exc:
        print(f"[profile] get_users failed for user_id={user_id}: {exc!r}")
        return None

    photo = getattr(user, "photo", None)
    if not photo:
        return None

    file_id = getattr(photo, "big_file_id", None) or getattr(photo, "file_id", None) or getattr(photo, "small_file_id", None)
    if not file_id:
        return None

    try:
        return await app.download_media(file_id)
    except Exception as exc:
        print(f"[profile] download_media failed for avatar file_id={file_id[:16] if file_id else None}: {exc!r}")
        return None


@register("profile")
async def profile_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username")
    first_name = from_user.get("first_name")
    last_name = from_user.get("last_name")

    print(f"[profile] /profile invoked by user_id={user_id} in chat_id={chat_id}")

    await _ensure_user_row(user_id, username, first_name)

    franchise_name = await ensure_franchise_name(user_id, first_name)
    snapshot = await get_profile_snapshot(user_id)
    global_rank = await get_global_rank(user_id)
    squad = await get_team_squad(user_id) or []
    captain_name = await _resolve_captain_name(user_id, squad)

    # Tier is derived from the player's current level, so the card image
    # (and the tier line itself) automatically switches the moment they
    # level into the next tier - no manual step needed.
    tier_emoji, tier_key, tier_title = get_tier(snapshot["level"])
    player_mention = mention_name_only_html(user_id, first_name)

    caption = _profile_caption(
        player_mention=player_mention, tier_emoji=tier_emoji, tier_key=tier_key,
        snapshot=snapshot, franchise_name=franchise_name, global_rank=global_rank,
        captain_name=captain_name, squad_len=len(squad),
    )

    tier_image = await get_tier_card_image(tier_key)

    if tier_image and tier_image.get("file_id"):
        template_bytes = await app.download_media(tier_image["file_id"])
        avatar_bytes = await _download_user_avatar_bytes(user_id)
        card_bytes = render_profile_card(
            template_bytes,
            avatar_bytes=avatar_bytes,
            first_name=first_name,
            last_name=last_name,
            tier_key=tier_key,
            tier_title=tier_title,
            level=snapshot["level"],
            snapshot=snapshot,
            franchise_name=franchise_name,
            global_rank=global_rank,
            captain_name=captain_name,
            squad_len=len(squad),
            max_squad_size=MAX_SQUAD_SIZE,
        )
        await app.send_photo(chat_id, photo=card_bytes, caption=caption, parse_mode="HTML")
    else:
        # No card image uploaded for this tier yet (via /upload_img <tier>) -
        # fall back to a text-only profile instead of failing silently.
        await app.send_message(chat_id, caption, parse_mode="HTML")

    print(f"[profile] Sent profile for user_id={user_id}: tier={tier_key}, level={snapshot['level']}, rank=#{global_rank}")
