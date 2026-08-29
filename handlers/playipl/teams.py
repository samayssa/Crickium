from __future__ import annotations
import html, json
from handlers.registry import register_callback
from app import app
from database.playipl_repo import get_match,set_team,set_xi,set_xi_confirmed
from database.playipl_repo import get_team_players
from database.playipl_teams_repo import TEAM_MAP,TEAM_ORDER,team_name,team_label,team_button_label,team_short,team_color
from buttons.playipl_buttons import team_keyboard
from utils.mentions import mention_html


def _team_text(match, p1=None, p2=None):
    a=mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name']); o=mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])
    t1=team_label(match.get('challenger_team_code')) if match.get('challenger_team_code') else '⏳ Choosing...'
    t2=team_label(match.get('opponent_team_code')) if match.get('opponent_team_code') else '⏳ Choosing...'
    return ("<b>╭━━〔 🏏 CHOOSE YOUR IPL FRANCHISE 〕━━╮\n\n"
            f"⚔️ {a} • {t1}\n🔥 {o} • {t2}\n\n"
            "</b><blockquote><b>Pick your IPL franchise for the match. 🏏</b></blockquote>\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━╯</b>")

async def send_team_selection(chat_id,match):
    m=await get_match(match['match_id'])
    sent=await app.send_message(chat_id,_team_text(m),parse_mode='HTML',reply_markup=team_keyboard(m['match_id']))
    # The original challenge message is no longer the active message; store this one.
    from database.playipl_repo import set_message_id
    await set_message_id(m['match_id'],sent['message_id'])

@register_callback('playipl_team')
async def playipl_team(callback_query):
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
        await app.answer_callback_query(callback_query['id'],'That IPL franchise is already selected.',show_alert=True); return
    await set_team(mid,uid,code,team_name(code)); await app.answer_callback_query(callback_query['id'],f'{team_name(code)} selected!')
    match=await get_match(mid)
    await app.edit_message_text(match['chat_id'],match['message_id'],_team_text(match),parse_mode='HTML',reply_markup=team_keyboard(mid))
    if match.get('challenger_team_code') and match.get('opponent_team_code'):
        await app.delete_message(match['chat_id'],match['message_id'])
        clash=(f"<b>🔥 GET READY FOR CLASH!</b>\n\n{team_color(match['challenger_team_code'])} <b>{team_name(match['challenger_team_code']).upper()}</b> ({team_short(match['challenger_team_code'])}) VS {team_color(match['opponent_team_code'])} <b>{team_name(match['opponent_team_code']).upper()}</b> ({team_short(match['opponent_team_code'])})\n\n"
               f"👤 <b>{mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name'])}</b> is representing {team_name(match['challenger_team_code'])}\n"
               f"👤 <b>{mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])}</b> is representing {team_name(match['opponent_team_code'])}\n\n🏏 Build your best XI!")
        sent=await app.send_message(match['chat_id'],clash,parse_mode='HTML')
        await app.edit_message_text(match['chat_id'],sent['message_id'],clash,parse_mode='HTML')
        from .lineup import send_build_messages
        await send_build_messages(match['chat_id'],match)
