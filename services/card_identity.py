"""Canonical identity helpers for collectible player cards.

The bot historically used positive player_id values for global cards and
negative values for special-edition cards. Showcase cards keep that snapshot
namespace approach, but use a reserved, much larger negative range and an
explicit ``player_kind`` field so every downstream system can distinguish the
three card families without extra database joins.
"""
from __future__ import annotations

SHOWCASE_PLAYER_ID_OFFSET = 1_000_000_000_000


def showcase_squad_player_id(showcase_card_id: int) -> int:
    return -(SHOWCASE_PLAYER_ID_OFFSET + int(showcase_card_id))


def showcase_card_id_from_player_id(player_id: int) -> int | None:
    pid = int(player_id or 0)
    if pid <= -SHOWCASE_PLAYER_ID_OFFSET - 1:
        return abs(pid) - SHOWCASE_PLAYER_ID_OFFSET
    return None


def is_showcase_player_id(player_id: int) -> bool:
    return showcase_card_id_from_player_id(int(player_id or 0)) is not None


def player_kind(player: dict | None) -> str:
    """Return global/special/showcase without relying on signed IDs alone."""
    if not player:
        return "global"
    explicit = str(player.get("player_kind") or "").strip().lower()
    if explicit in {"global", "special", "showcase"}:
        return explicit
    if bool(player.get("is_showcase")) or player.get("showcase_card_id"):
        return "showcase"
    special = player.get("is_special") is True or str(player.get("is_special") or "").lower() in {"1", "true", "yes"}
    if special or int(player.get("player_id") or 0) < 0:
        if is_showcase_player_id(int(player.get("player_id") or 0)):
            return "showcase"
        return "special"
    return "global"


def is_special(player: dict | None) -> bool:
    return player_kind(player) == "special"


def is_showcase(player: dict | None) -> bool:
    return player_kind(player) == "showcase"


def card_entity_id(player: dict) -> int:
    kind = player_kind(player)
    if kind == "showcase":
        return int(player.get("showcase_card_id") or showcase_card_id_from_player_id(int(player.get("player_id") or 0)) or 0)
    if kind == "special":
        return int(player.get("special_edition_id") or abs(int(player.get("player_id") or 0)))
    return int(player.get("player_id") or 0)


def card_identity_key(player: dict) -> str:
    return f"{player_kind(player)}:{card_entity_id(player)}"


def owned_same_card(left: dict, right: dict) -> bool:
    return card_identity_key(left) == card_identity_key(right)


def version_label(player: dict) -> str | None:
    kind = player_kind(player)
    if kind == "showcase":
        return str(player.get("showcase_name") or player.get("set_name") or "Showcase")
    if kind == "special":
        return str(player.get("edition") or "Special Edition")
    return None


def display_card_name(player: dict | None) -> str:
    """Human-readable player/card name that preserves card-version identity."""
    if not player:
        return "Player"
    name = str(player.get("name") or "Player")
    kind = player_kind(player)
    if kind == "special" and player.get("edition"):
        return f"{name} ({player.get('edition')})"
    if kind == "showcase" and player.get("showcase_name"):
        return f"{name} [{player.get('showcase_name')}]"
    return name
