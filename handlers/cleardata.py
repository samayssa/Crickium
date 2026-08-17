
from __future__ import annotations

print("cleardata.py loaded")

from handlers.registry import register, register_callback
from app import app
from config import ADMIN_USER_ID
from buttons.clear_data_buttons import clear_data_step_one_keyboard, clear_data_step_two_keyboard
from database.admin_repo import clear_all_game_data
from database.backup_repo import export_tables, CLEARDATA_BACKUP_TABLES
from engines.probability_engine import clear_probability_profile_cache
from datetime import datetime, timezone


async def _is_admin(user_id: int | None) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


async def _cancel_operation(chat_id: int, message_id: int | None, reason: str = "The operation was cancelled.") -> None:
    try:
        if message_id is not None:
            await app.delete_message(chat_id, message_id)
    finally:
        await app.send_message(chat_id, f"🛑 {reason}")


@register("cleardata")
async def cleardata_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    if not await _is_admin(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot admin only.")
        return

    text = (
        "⚠️ *Danger Zone*\n\n"
        "Are you sure you want to delete full database data?\n"
        "This will remove players, squads, claims, matches, probability profiles, and related game data."
    )
    await app.send_message(chat_id, text, parse_mode="Markdown", reply_markup=clear_data_step_one_keyboard())


@register_callback("cleardata_step1_yes")
async def cleardata_step1_yes(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    user_id = (callback_query.get("from") or {}).get("id")
    if not await _is_admin(user_id):
        await app.answer_callback_query(callback_query["id"], "🚫 Admin only.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Think again.")
    text = (
        "⚠️ *Final Confirmation*\n\n"
        "Are you sure you want to delete complete data from database?\n"
        "This action cannot be undone."
    )
    await app.edit_message_text(
        chat_id,
        callback_query["message"]["message_id"],
        text,
        parse_mode="Markdown",
        reply_markup=clear_data_step_two_keyboard(),
    )


@register_callback("cleardata_step1_no")
async def cleardata_step1_no(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    user_id = (callback_query.get("from") or {}).get("id")
    if not await _is_admin(user_id):
        await app.answer_callback_query(callback_query["id"], "🚫 Admin only.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Operation cancelled.")
    await _cancel_operation(chat_id, callback_query["message"]["message_id"], "Operation cancelled. Thank God, nothing was deleted.")


@register_callback("cleardata_step2_yes")
async def cleardata_step2_yes(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    user_id = (callback_query.get("from") or {}).get("id")
    if not await _is_admin(user_id):
        await app.answer_callback_query(callback_query["id"], "🚫 Admin only.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Resetting database...")

    # Back up exactly what's about to be destroyed BEFORE destroying it,
    # so a mistaken /cleardata is always recoverable via /recover.
    backup_bytes = None
    try:
        backup_bytes = await export_tables(CLEARDATA_BACKUP_TABLES, backup_type="cleardata")
    except Exception as exc:
        print(f"[cleardata] Pre-clear backup failed, proceeding anyway: {exc!r}")

    await clear_all_game_data()
    clear_probability_profile_cache()
    await app.edit_message_text(
        chat_id,
        callback_query["message"]["message_id"],
        (
            "✅ *Database reset completed.*\n\n"
            "All game data and probability profiles were cleared successfully.\n"
            "_Level-tier card images (bronze/silver/gold/.../legend) were left untouched._"
        ),
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": []},
    )

    if backup_bytes:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        try:
            await app.send_document(
                chat_id,
                backup_bytes,
                filename=f"cricklum_cleardata_backup_{timestamp}.json.gz",
                caption=(
                    "🗂 *Backup of the data that was just deleted.*\n"
                    "Reply to this file with /recover any time to restore it."
                ),
                parse_mode="Markdown",
            )
        except Exception as exc:
            print(f"[cleardata] Failed to send post-clear backup file: {exc!r}")
            await app.send_message(
                chat_id,
                "⚠️ The database was cleared, but I couldn't send the backup file. "
                "Please check the bot's logs - the data is not recoverable without it.",
            )
    else:
        await app.send_message(
            chat_id,
            "⚠️ The database was cleared, but creating the backup file failed beforehand. "
            "Please check the bot's logs.",
        )


@register_callback("cleardata_step2_never")
async def cleardata_step2_never(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    user_id = (callback_query.get("from") or {}).get("id")
    if not await _is_admin(user_id):
        await app.answer_callback_query(callback_query["id"], "🚫 Admin only.", show_alert=True)
        return
    await app.answer_callback_query(callback_query["id"], "Operation cancelled.")
    await _cancel_operation(chat_id, callback_query["message"]["message_id"], "Operation cancelled. The database stays warm and untouched.")


@register_callback("cleardata_step2_cancel")
async def cleardata_step2_cancel(callback_query):
    await cleardata_step2_never(callback_query)


@register_callback("cleardata_step2_back")
async def cleardata_step2_back(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    user_id = (callback_query.get("from") or {}).get("id")
    if not await _is_admin(user_id):
        await app.answer_callback_query(callback_query["id"], "🚫 Admin only.", show_alert=True)
        return
    await app.answer_callback_query(callback_query["id"], "Going back.")
    await app.edit_message_text(
        chat_id,
        callback_query["message"]["message_id"],
        (
            "⚠️ *Danger Zone*\n\n"
            "Are you sure you want to delete full database data?\n"
            "This will remove players, squads, claims, matches, probability profiles, and related game data."
        ),
        parse_mode="Markdown",
        reply_markup=clear_data_step_one_keyboard(),
    )
