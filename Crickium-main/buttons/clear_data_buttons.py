
from __future__ import annotations


def clear_data_step_one_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Yes, confirm", "callback_data": "cleardata_step1_yes"},
                {"text": "⬅️ No, back", "callback_data": "cleardata_step1_no"},
            ]
        ]
    }


def clear_data_step_two_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Yes, I want", "callback_data": "cleardata_step2_yes"},
                {"text": "❌ Never", "callback_data": "cleardata_step2_never"},
            ],
            [
                {"text": "🚫 No cancel", "callback_data": "cleardata_step2_cancel"},
                {"text": "⬅️ Back", "callback_data": "cleardata_step2_back"},
            ],
        ]
    }
