print("changexl.py loaded")

from handlers.registry import register
from app import app
from engines.lineup_engine import load_squad, find_player_by_id, default_lineup_ids
from database.lineups_repo import get_lineup_ids, save_lineup_ids
from database.squads_repo import save_team_squad

XI_SIZE = 11


def _parse_args(text: str) -> tuple[int, int] | None:
    parts = text.split()[1:]
    if len(parts) != 2:
        return None
    try:
        a = int(parts[0])
        b = int(parts[1])
    except ValueError:
        return None
    return a, b


@register("changexl")
async def changexl_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    text = message.get("text", "")

    print(f"[changexl] /changeXL invoked by user_id={user_id} text={text!r}")

    squad = await load_squad(user_id)
    if not squad:
        await app.send_message(chat_id, "⚠️ No squad found yet. Use /debut first to create your team.")
        return

    args = _parse_args(text)
    if args is None:
        await app.send_message(
            chat_id,
            "⚠️ *Usage:* `/changeXL <XI slot 1-11> <squad position>`\n\n"
            "Example: `/changeXL 1 13` — replaces XI slot 1 with the player at squad position 13.\n"
            "Example: `/changeXL 1 5` — swaps XI slots 1 and 5 (if 5 is within your XI).\n\n"
            "Check /squad for squad position numbers and /pxl for current XI slot numbers.",
            parse_mode="Markdown",
        )
        return

    slot, target = args
    if not (1 <= slot <= XI_SIZE):
        await app.send_message(chat_id, f"⚠️ The first number must be an XI slot from 1 to {XI_SIZE}.")
        return
    if target < 1 or target > len(squad):
        await app.send_message(chat_id, f"⚠️ The second number must be a squad position from 1 to {len(squad)}.")
        return

    lineup_ids = await get_lineup_ids(user_id)
    if not lineup_ids or len(lineup_ids) < XI_SIZE:
        lineup_ids = default_lineup_ids(squad)
    lineup_ids = list(lineup_ids)

    outgoing_id = lineup_ids[slot - 1]
    outgoing_player = find_player_by_id(squad, outgoing_id)
    outgoing_name = outgoing_player.get("name") if outgoing_player else "Unknown"
    outgoing_squad_idx = next(
        (i for i, p in enumerate(squad) if int(p.get("player_id") or 0) == outgoing_id), None
    )

    if 1 <= target <= XI_SIZE:
        other_id = lineup_ids[target - 1]
        other_player = find_player_by_id(squad, other_id)
        other_name = other_player.get("name") if other_player else "Unknown"
        other_squad_idx = next(
            (i for i, p in enumerate(squad) if int(p.get("player_id") or 0) == other_id), None
        )

        lineup_ids[slot - 1], lineup_ids[target - 1] = lineup_ids[target - 1], lineup_ids[slot - 1]
        await save_lineup_ids(user_id, lineup_ids)

        if outgoing_squad_idx is not None and other_squad_idx is not None and outgoing_squad_idx != other_squad_idx:
            squad[outgoing_squad_idx], squad[other_squad_idx] = squad[other_squad_idx], squad[outgoing_squad_idx]
            await save_team_squad(user_id, squad)

        await app.send_message(
            chat_id,
            f"*🔁 XI POSITIONS SWAPPED*\n\n"
            f"*Slot {slot}:* {outgoing_name} ➝ {other_name}\n"
            f"*Slot {target}:* {other_name} ➝ {outgoing_name}",
            parse_mode="Markdown",
        )
        print(f"[changexl] user_id={user_id} swapped XI slots {slot} and {target}")
        return

    incoming_player = squad[target - 1]
    incoming_id = int(incoming_player.get("player_id") or 0)

    if incoming_id in lineup_ids:
        existing_slot = lineup_ids.index(incoming_id) + 1
        await app.send_message(
            chat_id,
            f"⚠️ {incoming_player.get('name')} is already in your XI at slot {existing_slot}.\n"
            f"Use `/changeXL {slot} {existing_slot}` to swap positions instead.",
            parse_mode="Markdown",
        )
        return

    lineup_ids[slot - 1] = incoming_id
    await save_lineup_ids(user_id, lineup_ids)

    if outgoing_squad_idx is not None and outgoing_squad_idx != target - 1:
        squad[outgoing_squad_idx], squad[target - 1] = squad[target - 1], squad[outgoing_squad_idx]
        await save_team_squad(user_id, squad)

    incoming_name = incoming_player.get("name") or "Unknown"
    outgoing_ovr = max(int((outgoing_player or {}).get("bat_level") or 0), int((outgoing_player or {}).get("bowl_level") or 0))
    incoming_ovr = max(int(incoming_player.get("bat_level") or 0), int(incoming_player.get("bowl_level") or 0))
    await app.send_message(
        chat_id,
        f"*🔁 PLAYING XI CHANGED*\n\n"
        f"*Out / Slot {slot}:* {outgoing_name} • OVR {outgoing_ovr}\n"
        f"*In / Squad {target}:* {incoming_name} • OVR {incoming_ovr}\n\n"
        f"*✅ Slot {slot}* ➝ {incoming_name}\n"
        f"*🪑 {outgoing_name}* moved to the bench.\n\n"
        f"Use /pxl to view your Playing XI.\n"
        f"Use /squad to view squad order.",
        parse_mode="Markdown",
    )
    print(f"[changexl] user_id={user_id} replaced XI slot {slot} ({outgoing_name}) with squad#{target} ({incoming_player.get('name')})")
