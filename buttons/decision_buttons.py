def bat_bowl_keyboard(challenge_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "🏏 Bat", "callback_data": f"decision:{challenge_id}:bat"},
            {"text": "🎯 Bowl", "callback_data": f"decision:{challenge_id}:bowl"},
        ]]
    }
