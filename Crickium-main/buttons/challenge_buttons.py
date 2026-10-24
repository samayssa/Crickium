def accept_decline_keyboard(challenge_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Accept Challenge", "callback_data": f"challenge_accept:{challenge_id}"},
            {"text": "❌ Decline", "callback_data": f"challenge_decline:{challenge_id}"},
        ]]
    }
