print("upload_img.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID, PLAYER_IMAGE_CHANNEL_ID
from database.access_repo import has_upload_access
from database.players_repo import get_player
from database.special_players_repo import split_player_edition, get_special_player, display_edition
from database.card_images_repo import save_player_card_image, save_special_player_card_image, save_card_template_image
from database.tier_card_images_repo import save_tier_card_image
from engines.level_engine import TIER_KEYS
from utils.style import describe_player_styles


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _player_caption(player: dict, file_id: str | None = None) -> str:
    styles = describe_player_styles(player)
    lines = [
        "🖼 *Player Card Image*",
        "",
        f"*🏏 Name:* {player['name']}",
        f"*🌍 Country:* {player.get('country') or 'Unknown'}",
        f"*📋 Role:* {player['role']}",
        f"*💪 Batting Level:* {player['bat_level']}/100 ({styles['batting']})",
        f"*🎯 Bowling Level:* {player['bowl_level']}/100 ({styles['bowling']})",
    ]
    if file_id:
        lines.append("")
        lines.append(f"*File ID:*\n`{file_id}`")
    else:
        lines.append("")
        lines.append("_Uploading..._")
    return "\n".join(lines)


CARD_TYPE_KEYWORDS = {"bat-card": "bat", "ball-card": "ball"}
_CARD_TYPE_LABELS = {"bat": "Bat Card", "ball": "Ball Card"}


def _template_caption(card_type: str, file_id: str | None = None) -> str:
    label = _CARD_TYPE_LABELS[card_type]
    lines = [f"🖼 *Default {label} Template*"]
    if file_id:
        lines.append("")
        lines.append(f"_Used for every player rendered as a {label.lower()} without a custom card image._")
        lines.append("")
        lines.append(f"*File ID:*\n`{file_id}`")
    else:
        lines.append("")
        lines.append("_Uploading..._")
    return "\n".join(lines)


def _tier_caption(tier_key: str, file_id: str | None = None) -> str:
    lines = [f"🏅 *Level Tier Card - {tier_key.capitalize()}*"]
    if file_id:
        lines.append("")
        lines.append(f"_Shown on the /profile card of every player currently on the {tier_key.capitalize()} tier._")
        lines.append("")
        lines.append(f"*File ID:*\n`{file_id}`")
    else:
        lines.append("")
        lines.append("_Uploading..._")
    return "\n".join(lines)


@register("upload_img")
async def upload_img_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[upload_img] invoked by user_id={user_id}")

    # ---- Owner, or a user granted access via /access ----
    if user_id != ADMIN_USER_ID and not await has_upload_access(user_id):
        print(f"[upload_img] REJECTED: user_id={user_id} is not the owner and has no granted access.")
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner, or users the owner has granted access to via /access."
        )
        return

    reply_to = message.get("reply_to_message")
    photo = (reply_to or {}).get("photo")
    if not reply_to or not photo:
        await app.send_message(
            chat_id,
            "*⚠️ Please use /upload_img as a reply to a photo.*\n\n"
            "• `/upload_img <Player Name>` - sets a custom card image for that player\n"
            "• `/upload_img bat-card` - sets the default template for batsman/WK cards\n"
            "• `/upload_img ball-card` - sets the default template for bowler cards\n"
            "• `/upload_img <tier>` - sets the /profile card image for a level tier "
            "(bronze, silver, gold, platinum, diamond, legend)",
            parse_mode="Markdown",
        )
        return

    arg = _parse_arg(message.get("text", ""))
    if not arg:
        await app.send_message(
            chat_id,
            "*⚠️ Please tell me who this image is for.*\n\n"
            "• `/upload_img <Player Name>` - sets a custom card image for that player\n"
            "• `/upload_img bat-card` - sets the default template for batsman/WK cards\n"
            "• `/upload_img ball-card` - sets the default template for bowler cards\n"
            "• `/upload_img <tier>` - sets the /profile card image for a level tier "
            "(bronze, silver, gold, platinum, diamond, legend)",
            parse_mode="Markdown",
        )
        return

    card_type = CARD_TYPE_KEYWORDS.get(arg.strip().lower())
    is_template = card_type is not None
    tier_key = arg.strip().lower() if arg.strip().lower() in TIER_KEYS else None
    is_tier = tier_key is not None
    player = None
    is_special = False
    special_edition = None
    if not is_template and not is_tier:
        base_name, special_edition = split_player_edition(arg)
        if special_edition:
            player = await get_special_player(base_name, special_edition)
            is_special = True
            if not player:
                await app.send_message(
                    chat_id,
                    f"⚠️ No special edition player named *{base_name} ({special_edition})* found in the special database.",
                    parse_mode="Markdown",
                )
                return
        else:
            player = await get_player(arg)
            if not player:
                await app.send_message(
                    chat_id,
                    f"⚠️ No player named *{arg}* found. Check the spelling, or upload them first with /upload_pl.",
                    parse_mode="Markdown",
                )
                return

    original_file_id = photo["file_id"]

    # ---- Step 1: upload to the channel (caption filled in once we know the channel's own file_id) ----
    if is_template:
        placeholder_caption = _template_caption(card_type)
    elif is_tier:
        placeholder_caption = _tier_caption(tier_key)
    else:
        placeholder_caption = _player_caption(player)
        if is_special:
            placeholder_caption = placeholder_caption.replace("🖼 *Player Card Image*", "🖼 *Special Edition Player Card Image*")
    try:
        sent = await app.send_photo(
            PLAYER_IMAGE_CHANNEL_ID,
            photo=original_file_id,
            caption=placeholder_caption,
            parse_mode="Markdown",
        )
    except Exception as exc:
        print(f"[upload_img] Failed to send photo to channel {PLAYER_IMAGE_CHANNEL_ID}: {exc!r}")
        await app.send_message(
            chat_id,
            "*🚫 Failed to upload image to the storage channel.*\n\n"
            f"Channel ID: `{PLAYER_IMAGE_CHANNEL_ID}`\n\n"
            "Please verify:\n"
            "1. The bot is an administrator in the channel.\n"
            "2. The channel ID is correct.\n"
            "3. The bot has permission to post messages.\n\n"
            f"*Telegram error:*\n`{exc}`",
            parse_mode="Markdown",
        )
        return

    channel_photo = sent.get("photo") or {}
    stored_file_id = channel_photo.get("file_id") or original_file_id
    channel_message_id = sent.get("message_id")

    # ---- Step 2: fill in the real file_id as the caption ----
    if is_template:
        final_caption = _template_caption(card_type, stored_file_id)
    elif is_tier:
        final_caption = _tier_caption(tier_key, stored_file_id)
    else:
        final_caption = _player_caption(player, stored_file_id)
        if is_special:
            final_caption = final_caption.replace("🖼 *Player Card Image*", "🖼 *Special Edition Player Card Image*")
    await app.edit_message_caption(PLAYER_IMAGE_CHANNEL_ID, channel_message_id, final_caption, parse_mode="Markdown")

    # ---- Step 3: save the reference in the database ----
    if is_template:
        await save_card_template_image(card_type, stored_file_id, channel_message_id, uploaded_by=user_id)
        label = _CARD_TYPE_LABELS[card_type]
        await app.send_message(
            chat_id,
            f"*✅ Default {label} template updated.*\nEvery player rendered as a {label.lower()} without a custom image will now use this design.",
            parse_mode="Markdown",
        )
        print(f"[upload_img] {label} template updated. file_id={stored_file_id} channel_message_id={channel_message_id}")
    elif is_tier:
        await save_tier_card_image(tier_key, stored_file_id, channel_message_id, uploaded_by=user_id)
        await app.send_message(
            chat_id,
            f"*✅ {tier_key.capitalize()} tier card image updated.*\nEvery player currently on the {tier_key.capitalize()} tier will now see this image on their /profile card.",
            parse_mode="Markdown",
        )
        print(f"[upload_img] {tier_key} tier card updated. file_id={stored_file_id} channel_message_id={channel_message_id}")
    else:
        if is_special:
            await save_special_player_card_image(player["special_edition_id"], stored_file_id, channel_message_id, uploaded_by=user_id)
        else:
            await save_player_card_image(player["player_id"], stored_file_id, channel_message_id, uploaded_by=user_id)
        if is_special:
            edition_text = display_edition(player.get("edition")) or str(player.get("edition") or "Special Edition")
            await app.send_message(
                chat_id,
                f"*✅ Custom card image saved for {player['name']} ({edition_text}).*",
                parse_mode="Markdown",
            )
            print(f"[upload_img] Special player image saved. special_player_id={player['player_id']} file_id={stored_file_id} channel_message_id={channel_message_id}")
        else:
            await app.send_message(
                chat_id,
                f"*✅ Custom card image saved for {player['name']}.*",
                parse_mode="Markdown",
            )
            print(f"[upload_img] Player image saved. player_id={player['player_id']} file_id={stored_file_id} channel_message_id={channel_message_id}")
