from __future__ import annotations

import asyncio
import html
import secrets
import time
from typing import Any

from app import app
from database.query import fetch, transaction
from handlers.registry import register, register_callback
from utils.mentions import mention_html


_TERMINAL_STATUSES = (
    "completed",
    "ended",
    "declined",
    "expired",
    "timed_out",
    "abandoned",
    "cancelled",
    "canceled",
    "forfeited",
    "forfeit",
)
_CONFIRM_TTL_SECONDS = 10 * 60
_PENDING_CONFIRMATIONS: dict[str, dict[str, Any]] = {}
_USER_LOCKS: dict[int, asyncio.Lock] = {}

# All group-based challenge engines. These are fixed identifiers used in SQL;
# they are not sourced from user input.
_GAME_TABLES = (
    {
        "engine": "MATCH",
        "table": "match_challenges",
        "id_col": "challenge_id",
        "label": "CLASSIC T20",
    },
    {
        "engine": "PLAY",
        "table": "play_matches",
        "id_col": "match_id",
        "label": "PLAY • T20 1v1",
    },
    {
        "engine": "PLAYINT",
        "table": "playint_matches",
        "id_col": "match_id",
        "label": "PLAYINT • T20 International",
    },
    {
        "engine": "PLAYIPL",
        "table": "playipl_matches",
        "id_col": "match_id",
        "label": "PLAYIPL • Indian Premier League",
    },
    {
        "engine": "PLAYWPL",
        "table": "wpl_matches",
        "id_col": "match_id",
        "label": "PLAYWPL • Women's Premier League",
    },
    {
        "engine": "PLAYSO",
        "table": "playso_matches",
        "id_col": "match_id",
        "label": "PLAYSO • Super Over",
    },
)


def _user_lock(user_id: int) -> asyncio.Lock:
    return _USER_LOCKS.setdefault(int(user_id), asyncio.Lock())


def _cleanup_expired_confirmations() -> None:
    cutoff = time.time() - _CONFIRM_TTL_SECONDS
    for token, item in list(_PENDING_CONFIRMATIONS.items()):
        if float(item.get("created_at") or 0) < cutoff:
            _PENDING_CONFIRMATIONS.pop(token, None)


def _game_key(game: dict[str, Any]) -> tuple[str, int]:
    return str(game["engine"]), int(game["game_id"])


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        import json

        try:
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _runtime_sessions_for_user(user_id: int) -> list[dict[str, Any]]:
    """Find in-memory sessions too, including orphaned sessions with old DB rows."""
    found: list[dict[str, Any]] = []

    try:
        from engines.match_engine import MATCH_ENGINE

        for session in list(MATCH_ENGINE.sessions.values()):
            challenger = getattr(session, "challenger", None)
            opponent = getattr(session, "opponent", None)
            participant_ids = {
                int(getattr(challenger, "user_id", 0) or 0),
                int(getattr(opponent, "user_id", 0) or 0),
            }
            challenge_id = int(getattr(session, "challenge_id", 0) or 0)
            chat_id = int(getattr(session, "chat_id", 0) or 0)
            if int(user_id) not in participant_ids or not challenge_id or not chat_id:
                continue
            found.append({
                "engine": "MATCH",
                "table": "match_challenges",
                "id_col": "challenge_id",
                "game_id": challenge_id,
                "chat_id": chat_id,
                "message_id": getattr(session, "message_id", None),
                "challenger_id": getattr(challenger, "user_id", None),
                "challenger_username": getattr(challenger, "username", None),
                "challenger_name": getattr(challenger, "first_name", None),
                "opponent_id": getattr(opponent, "user_id", None),
                "opponent_username": getattr(opponent, "username", None),
                "opponent_name": getattr(opponent, "first_name", None),
                "runtime_session": session,
                "runtime_only": True,
                "label": "CLASSIC T20",
            })
    except Exception as exc:
        print(f"[forfeitall] Could not inspect CLASSIC runtime sessions: {exc!r}")

    runtime_specs = (
        ("PLAY", "engines.play_runtime", "_SESSIONS", "play_matches", "match_id", "PLAY • T20 1v1"),
        ("PLAYINT", "engines.playint_runtime", "_PLAYINT_SESSIONS", "playint_matches", "match_id", "PLAYINT • T20 International"),
        ("PLAYIPL", "engines.playipl_runtime", "_PLAYIPL_SESSIONS", "playipl_matches", "match_id", "PLAYIPL • Indian Premier League"),
        ("PLAYWPL", "engines.playwpl_runtime", "_PLAYWPL_SESSIONS", "wpl_matches", "match_id", "PLAYWPL • Women's Premier League"),
    )
    for engine, module_name, registry_name, table, id_col, label in runtime_specs:
        try:
            module = __import__(module_name, fromlist=[registry_name])
            registry = getattr(module, registry_name)
            for session in list(registry.values()):
                match = _as_dict(getattr(session, "match", None))
                challenger_id = int(match.get("challenger_id") or 0)
                opponent_id = int(match.get("opponent_id") or 0)
                game_id = int(getattr(session, "match_id", 0) or match.get("match_id") or 0)
                chat_id = int(getattr(session, "chat_id", 0) or match.get("chat_id") or 0)
                if int(user_id) not in {challenger_id, opponent_id} or not game_id or not chat_id:
                    continue
                found.append({
                    "engine": engine,
                    "table": table,
                    "id_col": id_col,
                    "game_id": game_id,
                    "chat_id": chat_id,
                    "message_id": match.get("message_id"),
                    "challenger_id": challenger_id,
                    "challenger_username": match.get("challenger_username"),
                    "challenger_name": match.get("challenger_name"),
                    "opponent_id": opponent_id,
                    "opponent_username": match.get("opponent_username"),
                    "opponent_name": match.get("opponent_name"),
                    "runtime_session": session,
                    "runtime_only": True,
                    "label": label,
                })
        except Exception as exc:
            print(f"[forfeitall] Could not inspect {engine} runtime sessions: {exc!r}")

    return found


async def _find_user_games(user_id: int) -> tuple[list[dict[str, Any]], list[str]]:
    games: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, int]] = set()

    for spec in _GAME_TABLES:
        query = (
            f"SELECT * FROM {spec['table']} "
            f"WHERE (challenger_id=$1 OR opponent_id=$1) "
            "AND LOWER(COALESCE(status, '')) <> ALL($2::text[]) "
            f"ORDER BY created_at DESC, {spec['id_col']} DESC;"
        )
        try:
            rows = await fetch(query, int(user_id), list(_TERMINAL_STATUSES))
        except Exception as exc:
            errors.append(f"{spec['engine']}: {exc!r}")
            continue

        for row in rows or []:
            item = dict(row)
            game_id = int(item.get(spec["id_col"]) or 0)
            chat_id = int(item.get("chat_id") or 0)
            if not game_id or not chat_id:
                continue
            game = {
                **item,
                "engine": spec["engine"],
                "table": spec["table"],
                "id_col": spec["id_col"],
                "game_id": game_id,
                "chat_id": chat_id,
                "runtime_only": False,
                "label": spec["label"],
            }
            games.append(game)
            seen.add(_game_key(game))

    if errors:
        return games, errors

    # An active database row is the authoritative source. Runtime-only
    # entries additionally catch a stale in-memory game whose row has gone
    # missing/terminal, which otherwise could continue blocking that group.
    for runtime_game in _runtime_sessions_for_user(user_id):
        key = _game_key(runtime_game)
        if key in seen:
            existing = next((g for g in games if _game_key(g) == key), None)
            if existing is not None:
                existing["runtime_session"] = runtime_game.get("runtime_session")
            continue
        games.append(runtime_game)
        seen.add(key)

    games.sort(key=lambda g: (int(g.get("chat_id") or 0), str(g.get("label") or ""), int(g.get("game_id") or 0)))
    return games, errors


async def _group_details(chat_ids: list[int]) -> dict[int, dict[str, Any]]:
    details: dict[int, dict[str, Any]] = {}
    unique_ids = sorted({int(cid) for cid in chat_ids if int(cid)})
    if not unique_ids:
        return details

    try:
        rows = await fetch(
            "SELECT chat_id, title FROM broadcast_targets WHERE chat_id = ANY($1::bigint[]) AND target_type IN ('group','channel');",
            unique_ids,
        )
        for row in rows or []:
            item = dict(row)
            details[int(item["chat_id"])] = {"title": item.get("title"), "username": None}
    except Exception as exc:
        print(f"[forfeitall] Group-title database lookup failed: {exc!r}")

    for chat_id in unique_ids:
        if details.get(chat_id, {}).get("title"):
            continue
        try:
            chat = await app._call_bot_api("getChat", json_payload={"chat_id": int(chat_id)})
            details[chat_id] = {
                "title": chat.get("title") or chat.get("first_name") or f"Group {chat_id}",
                "username": chat.get("username"),
            }
        except Exception as exc:
            print(f"[forfeitall] getChat failed for chat_id={chat_id}: {exc!r}")
            details.setdefault(chat_id, {"title": f"Group {chat_id}", "username": None})

    return details


def _group_title_html(chat_id: int, details: dict[int, dict[str, Any]]) -> str:
    info = details.get(int(chat_id)) or {}
    title = html.escape(str(info.get("title") or f"Group {chat_id}"))
    username = str(info.get("username") or "").strip().lstrip("@")
    if username:
        return f'<a href="https://t.me/{html.escape(username, quote=True)}">{title}</a>'
    return title


def _opponent_html(game: dict[str, Any], requester_id: int) -> str:
    challenger_id = int(game.get("challenger_id") or 0)
    opponent_id = int(game.get("opponent_id") or 0)
    if challenger_id == int(requester_id):
        other_id = opponent_id
        username = game.get("opponent_username")
        first_name = game.get("opponent_name")
    else:
        other_id = challenger_id
        username = game.get("challenger_username")
        first_name = game.get("challenger_name")
    if other_id:
        return mention_html(other_id, username, first_name)
    if username:
        return "@" + html.escape(str(username).lstrip("@"))
    return "Unknown player"


def _confirmation_keyboard(token: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {
                "text": "✅ Yes, Forfeit All",
                "callback_data": f"forfeitall_yes:{token}",
                "style": "success",
            },
            {
                "text": "❌ No, Cancel",
                "callback_data": f"forfeitall_cancel:{token}",
                "style": "danger",
            },
        ]]
    }


def _build_confirmation_text(
    games: list[dict[str, Any]],
    details: dict[int, dict[str, Any]],
    requester_id: int,
) -> str:
    group_count = len({int(game["chat_id"]) for game in games})
    lines = [
        "<b>╭━━〔 🏳️ FORFEIT ALL GAMES 〕━━╮</b>",
        "",
        f"<b>Found {len(games)} active game session(s) across {group_count} group(s):</b>",
        "",
    ]
    for index, game in enumerate(games, 1):
        chat_id = int(game["chat_id"])
        lines.extend([
            f"<b>{index}. {html.escape(str(game.get('label') or game.get('engine') or 'GAME'))}</b>",
            f"🏟️ { _group_title_html(chat_id, details) } <code>({chat_id})</code>",
            f"⚔️ Opponent: {_opponent_html(game, requester_id)}",
            "",
        ])
    lines.extend([
        "<b>Confirming will close every listed game, clear its saved session, and release both players.</b>",
        "<b>No winner, loser, or exit-coin penalty will be applied.</b>",
        "",
        "<b>Are you sure?</b>",
    ])
    return "\n".join(lines)


def _runtime_message_ids(game: dict[str, Any]) -> list[int]:
    session = game.get("runtime_session")
    values: list[int] = []
    if session is not None:
        for attr in (
            "live_message_id",
            "message_id",
            "ready_message_id",
            "short_message_id",
        ):
            try:
                value = int(getattr(session, attr, 0) or 0)
                if value:
                    values.append(value)
            except (TypeError, ValueError):
                pass

        meta = getattr(session, "meta", None)
        if isinstance(meta, dict):
            for key, value in meta.items():
                if str(key).endswith("_message_id"):
                    try:
                        value = int(value or 0)
                        if value:
                            values.append(value)
                    except (TypeError, ValueError):
                        pass

    try:
        value = int(game.get("message_id") or 0)
        if value:
            values.append(value)
    except (TypeError, ValueError):
        pass

    unique: list[int] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


async def _close_database_rows(user_id: int, games: list[dict[str, Any]]) -> set[tuple[str, int]]:
    db_games = [game for game in games if not game.get("runtime_only")]
    if not db_games:
        return set()

    async def _close_all(conn):
        closed: set[tuple[str, int]] = set()
        for game in db_games:
            table = game["table"]
            id_col = game["id_col"]
            row = await conn.fetchrow(
                f"UPDATE {table} SET status='ended' "
                f"WHERE {id_col}=$1 AND (challenger_id=$2 OR opponent_id=$2) "
                "AND LOWER(COALESCE(status, '')) <> ALL($3::text[]) "
                f"RETURNING {id_col};",
                int(game["game_id"]),
                int(user_id),
                list(_TERMINAL_STATUSES),
            )
            if row:
                closed.add(_game_key(game))
        return closed

    return await transaction(_close_all)


async def _clear_runtime(game: dict[str, Any]) -> None:
    engine = str(game["engine"]).upper()
    game_id = int(game["game_id"])
    chat_id = int(game["chat_id"])

    try:
        from utils.timers import cancel_timer

        for scope in ("challenge", "playso_challenge", "toss_call", "decision"):
            cancel_timer(scope, game_id)
    except Exception as exc:
        print(f"[forfeitall] Timer cleanup failed for {engine}:{game_id}: {exc!r}")

    try:
        from utils.game_inactivity import cancel_match

        cancel_match(engine, game_id)
    except Exception as exc:
        print(f"[forfeitall] Inactivity cleanup failed for {engine}:{game_id}: {exc!r}")

    try:
        if engine == "MATCH":
            from engines.match_engine import MATCH_ENGINE

            MATCH_ENGINE.clear_session(chat_id)
        elif engine == "PLAY":
            from engines.play_runtime import clear_session

            clear_session(game_id)
        elif engine == "PLAYINT":
            from engines.playint_runtime import clear_playint_session

            clear_playint_session(game_id)
        elif engine == "PLAYIPL":
            from engines.playipl_runtime import clear_playipl_session

            clear_playipl_session(game_id)
        elif engine == "PLAYWPL":
            from engines.playwpl_runtime import clear_playwpl_session

            clear_playwpl_session(game_id)
        elif engine == "PLAYSO":
            state = _as_dict(game.get("state"))
            from services.super_over_bridge import force_close_origin

            await force_close_origin(state, "ended")
    except Exception as exc:
        print(f"[forfeitall] Runtime cleanup failed for {engine}:{game_id}: {exc!r}")

    # Remove the persisted recovery snapshot after the runtime has been
    # removed, so the background saver cannot recreate it on the next tick.
    try:
        from services.game_session_recovery import delete_snapshot

        await delete_snapshot(engine, game_id)
    except Exception as exc:
        print(f"[forfeitall] Snapshot cleanup failed for {engine}:{game_id}: {exc!r}")

    if engine == "PLAYSO":
        state = _as_dict(game.get("state"))
        origin_engine = str(state.get("origin_engine") or "").upper()
        origin_id = int(state.get("origin_match_id") or 0)
        if origin_engine and origin_id:
            try:
                from services.game_session_recovery import delete_snapshot

                await delete_snapshot(origin_engine, origin_id)
            except Exception as exc:
                print(f"[forfeitall] Origin snapshot cleanup failed for {origin_engine}:{origin_id}: {exc!r}")


async def _finish_group_messages(game: dict, requester: dict[str, Any]) -> None:
    chat_id = int(game["chat_id"])
    engine = str(game["engine"]).upper()
    ids = _runtime_message_ids(game)
    user_id = int(requester.get("id") or 0)
    actor = mention_html(user_id, requester.get("username"), requester.get("first_name"))
    finished_text = (
        "<b>🏳️ GAME SESSION CLOSED</b>\n\n"
        f"{actor} used <code>/forfeitall</code>.\n"
        "This game has been closed and both players have been released.\n"
        "<b>No winner, loser, or exit penalty.</b>"
    )

    if ids:
        primary, *others = ids
        try:
            await app.edit_message_text(
                chat_id,
                int(primary),
                finished_text,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": []},
            )
        except Exception as exc:
            print(f"[forfeitall] Could not edit primary game message in {chat_id} ({engine}): {exc!r}")
            try:
                await app.delete_message(chat_id, int(primary))
            except Exception:
                pass
        for message_id in others:
            try:
                await app.delete_message(chat_id, int(message_id))
            except Exception:
                pass



@register("forfeitall")
async def forfeitall_command(message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    user_id = int(sender.get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    if not user_id or not chat_id:
        return

    _cleanup_expired_confirmations()
    try:
        games, errors = await _find_user_games(user_id)
    except Exception as exc:
        print(f"[forfeitall] Active-game lookup failed for user_id={user_id}: {exc!r}")
        await app.send_message(
            chat_id,
            "<b>⚠️ I couldn't check your active game sessions right now. No games were changed. Please try /forfeitall again.</b>",
            parse_mode="HTML",
        )
        return

    if errors:
        print(f"[forfeitall] Incomplete active-game lookup for user_id={user_id}: {errors!r}")
        await app.send_message(
            chat_id,
            "<b>⚠️ I couldn't verify every game mode, so I won't risk a partial reset. No games were changed. Please try /forfeitall again.</b>",
            parse_mode="HTML",
        )
        return

    if not games:
        await app.send_message(
            chat_id,
            "<b>✅ No active group game sessions were found for your account. You're clear to play.</b>",
            parse_mode="HTML",
        )
        return

    details = await _group_details([int(game["chat_id"]) for game in games])
    token = secrets.token_urlsafe(7)
    _PENDING_CONFIRMATIONS[token] = {
        "user_id": user_id,
        "user": {
            "id": user_id,
            "username": sender.get("username"),
            "first_name": sender.get("first_name"),
        },
        "games": games,
        "created_at": time.time(),
    }

    await app.send_message(
        chat_id,
        _build_confirmation_text(games, details, user_id),
        parse_mode="HTML",
        reply_markup=_confirmation_keyboard(token),
    )


@register_callback("forfeitall_yes")
async def forfeitall_yes(callback_query: dict[str, Any]) -> None:
    parts = str(callback_query.get("data") or "").split(":", 1)
    token = parts[1] if len(parts) == 2 else ""
    sender = callback_query.get("from") or {}
    user_id = int(sender.get("id") or 0)
    message = callback_query.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    message_id = int(message.get("message_id") or 0)

    pending = _PENDING_CONFIRMATIONS.get(token)
    if not pending or int(pending.get("user_id") or 0) != user_id:
        await app.answer_callback_query(
            callback_query.get("id"),
            "This confirmation expired or belongs to another user. Run /forfeitall again.",
            show_alert=True,
        )
        return
    if time.time() - float(pending.get("created_at") or 0) > _CONFIRM_TTL_SECONDS:
        _PENDING_CONFIRMATIONS.pop(token, None)
        await app.answer_callback_query(callback_query.get("id"), "This confirmation expired. Run /forfeitall again.", show_alert=True)
        return

    async with _user_lock(user_id):
        pending = _PENDING_CONFIRMATIONS.pop(token, None)
        if not pending or int(pending.get("user_id") or 0) != user_id:
            await app.answer_callback_query(callback_query.get("id"), "This confirmation is no longer active.", show_alert=True)
            return

        try:
            closed_keys = await _close_database_rows(user_id, pending["games"])
        except Exception as exc:
            print(f"[forfeitall] Atomic close failed for user_id={user_id}: {exc!r}")
            await app.answer_callback_query(callback_query.get("id"), "Could not close the games safely. No database changes were applied.", show_alert=True)
            return

        user = pending.get("user") or {"id": user_id}
        games_to_clear: list[dict[str, Any]] = []
        for game in pending["games"]:
            key = _game_key(game)
            has_runtime = game.get("runtime_session") is not None
            if key in closed_keys or bool(game.get("runtime_only")) or has_runtime:
                games_to_clear.append(game)

        if not games_to_clear:
            await app.answer_callback_query(callback_query.get("id"), "Those games have already ended. No active sessions remain.")
            try:
                await app.edit_message_text(
                    chat_id,
                    message_id,
                    "<b>✅ Those games have already ended. No active sessions remain.</b>",
                    parse_mode="HTML",
                    reply_markup={"inline_keyboard": []},
                )
            except Exception:
                pass
            return

        await app.answer_callback_query(callback_query.get("id"), "Forfeiting all your listed games...")

        groups: dict[int, list[dict[str, Any]]] = {}
        for game in games_to_clear:
            await _clear_runtime(game)
            groups.setdefault(int(game["chat_id"]), []).append(game)

        # Update/remove the old game controls, then send one notice per group.
        for game in games_to_clear:
            await _finish_group_messages(game, user)

        actor = mention_html(int(user.get("id") or 0), user.get("username"), user.get("first_name"))
        for affected_chat_id, group_games in groups.items():
            mode_lines = "\n".join(
                f"• {html.escape(str(game.get('label') or game.get('engine') or 'GAME'))}"
                for game in group_games
            )
            group_notice = (
                "<b>🏳️ GAME SESSION(S) CLOSED</b>\n\n"
                f"{actor} used <code>/forfeitall</code>.\n"
                f"<b>Closed mode(s):</b>\n{mode_lines}\n\n"
                "All listed sessions have been cleared and both players released.\n"
                "<b>No winner, loser, or exit penalty.</b>"
            )
            try:
                await app.send_message(affected_chat_id, group_notice, parse_mode="HTML")
            except Exception as exc:
                print(f"[forfeitall] Could not notify group {affected_chat_id}: {exc!r}")

        if closed_keys:
            result_text = (
                "<b>🏳️ FORFEIT ALL COMPLETE</b>\n\n"
                f"Closed <b>{len(closed_keys)}</b> active game session(s) across "
                f"<b>{len(groups)}</b> group(s).\n"
                "All listed sessions have been cleared. You're free to start a new game. 🏏"
            )
        else:
            result_text = (
                "<b>🧹 STALE GAME SESSIONS CLEARED</b>\n\n"
                "Your leftover in-memory game session(s) have been removed. You're free to start a new game. 🏏"
            )
        try:
            await app.edit_message_text(
                chat_id,
                message_id,
                result_text,
                parse_mode="HTML",
                reply_markup={"inline_keyboard": []},
            )
        except Exception as exc:
            print(f"[forfeitall] Could not update confirmation message: {exc!r}")


@register_callback("forfeitall_cancel")
async def forfeitall_cancel(callback_query: dict[str, Any]) -> None:
    parts = str(callback_query.get("data") or "").split(":", 1)
    token = parts[1] if len(parts) == 2 else ""
    sender = callback_query.get("from") or {}
    user_id = int(sender.get("id") or 0)
    message = callback_query.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    message_id = int(message.get("message_id") or 0)

    pending = _PENDING_CONFIRMATIONS.get(token)
    if not pending or int(pending.get("user_id") or 0) != user_id:
        await app.answer_callback_query(callback_query.get("id"), "This cancel button is no longer active.", show_alert=True)
        return

    _PENDING_CONFIRMATIONS.pop(token, None)
    await app.answer_callback_query(callback_query.get("id"), "Cancelled. All games will continue.")
    try:
        await app.edit_message_text(
            chat_id,
            message_id,
            "<b>✅ Forfeit-all cancelled. Your game sessions have not been changed.</b>",
            parse_mode="HTML",
            reply_markup={"inline_keyboard": []},
        )
    except Exception as exc:
        print(f"[forfeitall] Could not update cancelled prompt: {exc!r}")
