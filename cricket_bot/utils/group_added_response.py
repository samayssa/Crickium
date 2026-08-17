from __future__ import annotations


def format_group_added_response(*, username: str | None, group_name: str) -> str:
    username_text = f"@{username}" if username else "there"
    return (
        "🏏 Welcome to Crickium\n\n"
        f"🤝 Thank you, {username_text}, for adding me to {group_name}.\n\n"
        "⚡ Crickium is now ready to use.\n\n"
        "🎮 Create your squad using /debut, manage your players, and participate in matches "
        "to experience realistic cricket gameplay directly within the group.\n\n"
        "🏆 Let the game begin!"
    )
