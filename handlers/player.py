print("player.py loaded")

import html

from handlers.registry import register
from app import app
from database.players_repo import get_player
from database.squads_repo import get_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price, format_price
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _player_bio_text(player: dict, owned: bool) -> str:
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    role = _escape(player.get("role") or "Unknown")
    bat_style = _escape(batting_style_text(player.get("batting_hand")))
    bowl_style = _escape(bowling_style_text(player.get("bowling_hand")))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)

    ovr = overall_rating(bat_level, bowl_level)
    rarity = _escape(get_rarity(ovr))
    buy_price, _sell_price = get_price(ovr)
    buy_price_text = _escape(format_price(buy_price))
    owned_text = "YES ✅" if owned else "NO ❌"

    title = "<b>╭━━━〔 🏏 PLAYER BIO 〕━━━╮</b>"

    name_block = f"<blockquote>👤 <b>{name}</b> {flag}</blockquote>"

    rarity_block = f"<blockquote>🏅 <b>Rarity.</b>    {rarity}</blockquote>"

    details_block = (
        "<blockquote>"
        f"⭐ <b>Role</b>        ➤ {role}\n"
        f"🏏 <b>Bat Style</b>   ➤ {bat_style}\n"
        f"🎯 <b>Bowl Style</b>  ➤ {bowl_style}"
        "</blockquote>"
    )

    separator = "════════════════════"

    stats_block = (
        "<blockquote expandable>"
        f"📈 <b>Bat Lv.</b>     ➤ {bat_level}\n"
        f"📉 <b>Bowl Lv.</b>    ➤ {bowl_level}\n"
        f"💰 <b>Buy Price</b>   ➤ {buy_price_text}\n"
        f"📦 <b>Owned</b>       ➤ {owned_text}"
        "</blockquote>"
    )

    return "\n".join([title, name_block, rarity_block, "", details_block, separator, stats_block])


@register("player")
async def player_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[player] /player invoked by user_id={user_id}")

    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please tell me which player to look up.</b>\nUsage: <code>/player Virat Kohli</code>",
            parse_mode="HTML",
        )
        return

    player = await get_player(name)
    if not player:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{_escape(name)}</b> found. Check the spelling, "
            f"or upload them first with /upload_pl.",
            parse_mode="HTML",
        )
        return

    squad = await get_team_squad(user_id) or []
    owned = any(int(p.get("player_id") or 0) == int(player["player_id"]) for p in squad)

    text = _player_bio_text(player, owned)

    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML")
    except Exception as exc:
        print(f"[player] Card image failed ({exc!r}), falling back to a text-only message.")
        await app.send_message(chat_id, text, parse_mode="HTML")

    print(f"[player] user_id={user_id} looked up player_id={player['player_id']} ({player['name']})")
