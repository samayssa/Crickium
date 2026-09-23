from __future__ import annotations
import html, json
from handlers.registry import register_callback
from app import app
from database.playint_repo import get_match,set_team,set_xi,set_xi_confirmed,set_message_id
from database.playint_repo import get_team_players
from database.playint_teams_repo import TEAM_MAP,TEAMS_PAGE_1,TEAMS_PAGE_2,team_flag,team_name,team_label
from buttons.playint_buttons import team_keyboard
from utils.mentions import mention_html


def _team_text(match, p1=None, p2=None):
    a=mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name']); o=mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])
    t1=team_label(match.get('challenger_team_code')) if match.get('challenger_team_code') else '⏳ Choosing...'
    t2=team_label(match.get('opponent_team_code')) if match.get('opponent_team_code') else '⏳ Choosing...'
    return ("<b>╭━━〔 🌍 CHOOSE YOUR TEAM 〕━━╮\n\n"
            f"⚔️ {a} • {t1}\n🔥 {o} • {t2}\n\n"
            "</b><blockquote><b>Pick your national team for the T20I. 🏏</b></blockquote>\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━╯</b>")

async def send_team_selection(chat_id, match):
    """Publish the T20I team-selection stage before cleaning up the old
    challenge message.  This prevents a cross-transport edit failure from
    blocking progression after a challenge is accepted.
    """
    m = await get_match(match['match_id'])
    if not m:
        raise RuntimeError(f"PLAYINT match disappeared before team selection")

    team_text = _team_text(m)
    markup = team_keyboard(m['match_id'], 1)
    old_message_id = int(m.get('message_id') or 0)

    try:
        sent = await app.send_message(
            chat_id, team_text, parse_mode='HTML', reply_markup=markup
        )
    except Exception as send_exc:
        print(f"[playint] team-selection send failed for match_id={m['match_id']}: {send_exc!r}")
        if old_message_id <= 0:
            raise
        try:
            # Styled keyboards use the raw Bot API in app.py, so this fallback
            # does not depend on the MTProto edit path.
            await app.edit_message_text(
                chat_id, old_message_id, team_text,
                parse_mode='HTML', reply_markup=markup,
            )
            await set_message_id(m['match_id'], old_message_id)
            return {'message_id': old_message_id}
        except Exception as fallback_exc:
            print(f"[playint] team-selection fallback edit failed for match_id={m['match_id']}: {fallback_exc!r}")
            raise RuntimeError(
                f"Unable to publish T20I team-selection stage for match {m['match_id']}"
            ) from send_exc

    message_id = int(sent.get('message_id') or 0)
    if message_id <= 0:
        raise RuntimeError(f"Telegram returned no message_id for PLAYINT team selection (match {m['match_id']})")

    try:
        await set_message_id(m['match_id'], message_id)
    except Exception as db_exc:
        print(f"[playint] failed to persist team-selection message_id for match_id={m['match_id']}: {db_exc!r}")
        try:
            await app.delete_message(chat_id, message_id)
        except Exception as delete_exc:
            print(f"[playint] cleanup of detached team-selection message failed: {delete_exc!r}")
        if old_message_id > 0:
            try:
                await app.edit_message_text(
                    chat_id, old_message_id, team_text,
                    parse_mode='HTML', reply_markup=markup,
                )
                await set_message_id(m['match_id'], old_message_id)
                return {'message_id': old_message_id}
            except Exception as fallback_exc:
                print(f"[playint] DB-pointer fallback failed for match_id={m['match_id']}: {fallback_exc!r}")
        raise

    return sent

@register_callback('playint_team_page')
async def playint_team_page(callback_query):
    _,mid_s,move,page_s=callback_query['data'].split(':'); mid=int(mid_s); page=int(page_s); match=await get_match(mid)
    if not match: return
    if move=='next': page=2
    elif move=='prev': page=1
    await app.answer_callback_query(callback_query['id'], 'Team page updated.')
    await app.edit_message_text(match['chat_id'],match['message_id'],_team_text(match),parse_mode='HTML',reply_markup=team_keyboard(mid,page))

@register_callback('playint_team')
async def playint_team(callback_query):
    _,mid_s,code=callback_query['data'].split(':'); mid=int(mid_s); uid=int(callback_query['from']['id']); match=await get_match(mid)
    if not match or match['status'] not in {'accepted','team_selection'}:
        await app.answer_callback_query(callback_query['id'],'Team selection is no longer active.',show_alert=True); return
    # challenger always selects first
    if not match.get('challenger_team_code') and uid!=int(match['challenger_id']):
        await app.answer_callback_query(callback_query['id'],'The challenger must choose the first team.',show_alert=True); return
    if uid not in {int(match['challenger_id']),int(match['opponent_id'])}:
        await app.answer_callback_query(callback_query['id'],'You are not part of this match.',show_alert=True); return
    if uid==int(match['challenger_id']) and match.get('challenger_team_code'):
        await app.answer_callback_query(callback_query['id'],'Your team is already selected.',show_alert=True); return
    if uid==int(match['opponent_id']) and match.get('opponent_team_code'):
        await app.answer_callback_query(callback_query['id'],'Your team is already selected.',show_alert=True); return
    # Teams cannot be duplicated.
    other=match.get('opponent_team_code') if uid==int(match['challenger_id']) else match.get('challenger_team_code')
    if other==code:
        await app.answer_callback_query(callback_query['id'],'That national team is already selected.',show_alert=True); return
    await set_team(mid,uid,code,team_name(code)); await app.answer_callback_query(callback_query['id'],f'{team_name(code)} selected!')
    match=await get_match(mid)
    await app.edit_message_text(match['chat_id'],match['message_id'],_team_text(match),parse_mode='HTML',reply_markup=team_keyboard(mid,1))
    if match.get('challenger_team_code') and match.get('opponent_team_code'):
        await app.delete_message(match['chat_id'],match['message_id'])
        clash=(f"<b>🔥 GET READY FOR CLASH!</b>\n\n{team_label(match['challenger_team_code']).split(' ',1)[0]} <b>{team_name(match['challenger_team_code']).upper()}</b> VS {team_label(match['opponent_team_code']).split(' ',1)[0]} <b>{team_name(match['opponent_team_code']).upper()}</b>\n\n"
               f"👤 <b>{mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name'])}</b> is representing {team_name(match['challenger_team_code'])}\n"
               f"👤 <b>{mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])}</b> is representing {team_name(match['opponent_team_code'])}\n\n🏏 Build your best XI!")
        sent=await app.send_message(match['chat_id'],clash,parse_mode='HTML')
        await app.edit_message_text(match['chat_id'],sent['message_id'],clash,parse_mode='HTML')
        from .lineup import send_build_messages
        await send_build_messages(match['chat_id'],match)
