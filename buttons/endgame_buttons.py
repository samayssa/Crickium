def endgame_confirm_keyboard(chat_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Yes, I want", "callback_data": f"endgame_yes:{chat_id}"},
            {"text": "❌ Cancel", "callback_data": f"endgame_cancel:{chat_id}"},
        ]]
    }
