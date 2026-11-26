from __future__ import annotations

import importlib

_ENGINES = (
    ("PLAYIPL", "database.playipl_repo", "PlayIPL"),
    ("PLAYWPL", "database.playwpl_repo", "PlayWPL"),
    ("PLAYWBBL", "database.playwbbl_repo", "PlayWBBL"),
    ("PLAYBBL", "database.playbbl_repo", "PlayBBL"),
    ("PLAYILT", "database.playilt_repo", "PlayILT"),
)
_NEW_ENGINES = {"PLAYWBBL", "PLAYBBL", "PLAYILT"}


def _targets(exclude: str, new_only: bool):
    for key, module, label in _ENGINES:
        if key == exclude:
            continue
        if new_only and key not in _NEW_ENGINES:
            continue
        yield key, module, label


async def chat_conflict(chat_id: int, exclude: str = "", new_only: bool = False) -> str | None:
    for key, module, label in _targets(exclude, new_only):
        try:
            repo = importlib.import_module(module)
            if await repo.get_active_match_in_chat(int(chat_id)):
                return label
        except Exception:
            continue
    return None


async def user_conflict(user_id: int, exclude: str = "", new_only: bool = False) -> str | None:
    for key, module, label in _targets(exclude, new_only):
        try:
            repo = importlib.import_module(module)
            if await repo.get_active_match_for_user(int(user_id)):
                return label
        except Exception:
            continue
    return None
