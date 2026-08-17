def heads_tails_keyboard(challenge_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "🪙 Heads", "callback_data": f"toss_call:{challenge_id}:heads"},
            {"text": "🪙 Tails", "callback_data": f"toss_call:{challenge_id}:tails"},
        ]]
    }
