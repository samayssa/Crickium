from __future__ import annotations
import html
from handlers.registry import register, register_callback
from app import app
from database.query import fetchrow
from database.playint_repo import create_match,get_match,set_message_id,update_status,get_active_match_in_chat,get_active_match_for_user
from buttons.playint_buttons import challenge_keyboard
from utils.mentions import mention_html
from utils.debut_gate import has_minimum_team,get_playing_xi_status
from .teams import send_team_selection


def _challenge_text(a,o):
    return ("<b>╭━━〔 🏏 T20I MATCH 〕━━╮\n\n"
            f"⚔️ {a}\n              VS\n🔥 {o}\n\n</b>"
            "<blockquote><b>🏏 T20 International\n🌍 National Squads</b></blockquote>\n\n"
            "<b>💬 International challenge received!\n\n"
            "Choose your country, build your Playing XI,\n"
            "and battle for the win! 🏆🔥\n\n"
            "╰━━━━━━━━━━━━━━━━━━╯</b>")


def parse_target(text):
    parts=(text or '').split()
    for p in parts[1:]:
        if p.startswith('@') and len(p)>1: return ('username',p[1:])
        if p.isdigit(): return ('id',int(p))
    return None

@register('playint')
async def playint_command(message):
    chat_id=int(message['chat']['id']); u=message.get('from',{}); challenger_id=int(u.get('id'))
    # Player eligibility is the existing entry gate; national team selection follows acceptance.
    if not await has_minimum_team(challenger_id):
        await app.send_message(chat_id,'<b>⚠️ You need a minimum 11 players team to challenge.</b>',parse_mode='HTML'); return
    active=await get_active_match_in_chat(chat_id)
    if active:
        await app.send_message(chat_id,'<b>⚠️ A PlayInt game is already going on in this group.</b>',parse_mode='HTML'); return
    active=await get_active_match_for_user(challenger_id)
    if active:
        await app.send_message(chat_id,"<b>⚠️ You're already in a game. Please finish it first.</b>",parse_mode='HTML'); return

    reply=(message.get('reply_to_message') or {}).get('from') or {}
    target=None
    if reply.get('id'):
        if reply.get('is_bot'):
            await app.send_message(chat_id,'<b>⚠️ You can\'t challenge a bot!</b>',parse_mode='HTML'); return
        target=(int(reply['id']),reply.get('username'),reply.get('first_name'))
    else:
        parsed=parse_target(message.get('text',''))
        if parsed is None:
            await app.send_message(chat_id,'<b>⚠️ To challenge someone, reply to their message with /playint, use /playint @username, or use /playint USER_ID.</b>',parse_mode='HTML'); return
        kind,val=parsed
        if kind=='username': row=await fetchrow('SELECT user_id, username, first_name FROM users WHERE LOWER(username)=LOWER($1) LIMIT 1;',val)
        else: row=await fetchrow('SELECT user_id, username, first_name FROM users WHERE user_id=$1;',val)
        if not row:
            await app.send_message(chat_id,'<b>⚠️ I could not find that user. Ask them to use the bot first.</b>',parse_mode='HTML'); return
        target=(int(row['user_id']),row['username'],row['first_name'])
    opponent_id, opponent_username, opponent_name = target
    if opponent_id==challenger_id:
        await app.send_message(chat_id,"<b>⚠️ You can't challenge yourself!</b>",parse_mode='HTML'); return
    if not await has_minimum_team(opponent_id):
        await app.send_message(chat_id,'<b>⚠️ The opponent needs a minimum 11 players team.</b>',parse_mode='HTML'); return
    if await get_active_match_for_user(opponent_id):
        om=mention_html(opponent_id,opponent_username,opponent_name)
        await app.send_message(chat_id,f'<b>⚠️ {om} is already in another game.</b>',parse_mode='HTML'); return

    match=await create_match(chat_id,challenger_id,u.get('username'),u.get('first_name'),opponent_id,opponent_username,opponent_name)
    a=mention_html(challenger_id,u.get('username'),u.get('first_name')); o=mention_html(opponent_id,opponent_username,opponent_name)
    sent=await app.send_message(chat_id,_challenge_text(a,o),parse_mode='HTML',reply_markup=challenge_keyboard(match['match_id']))
    await set_message_id(match['match_id'],sent['message_id'])

@register_callback('playint_accept')
async def playint_accept(callback_query):
    mid=int(callback_query['data'].split(':')[1]); presser=callback_query['from']; msg=callback_query['message']; match=await get_match(mid)
    if not match or match['status']!='pending':
        await app.answer_callback_query(callback_query['id'],'This challenge is no longer active.',show_alert=True); return
    if int(presser['id'])!=int(match['opponent_id']):
        await app.answer_callback_query(callback_query['id'],"This challenge isn't for you!",show_alert=True); return
    await update_status(mid,'accepted'); await app.answer_callback_query(callback_query['id'],'Challenge accepted!')
    a=mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name']); o=mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])
    text=("<b>╭━━〔 🏏 T20I MATCH 〕━━╮\n\n" f"⚔️ {a}\n              VS\n🔥 {o}\n\n</b>"
          "<blockquote><b>🏏 T20 International\n🌍 National Squads</b></blockquote>\n\n"
          "✅ <b>Challenge Accepted!</b>\n\nGet ready to choose your country. 🏏🔥\n\n"
          "<b>╰━━━━━━━━━━━━━━━━━━╯</b>")
    await app.edit_message_text(msg['chat']['id'],msg['message_id'],text,parse_mode='HTML',reply_markup={'inline_keyboard':[]})
    await send_team_selection(msg['chat']['id'],match)

@register_callback('playint_decline')
async def playint_decline(callback_query):
    mid=int(callback_query['data'].split(':')[1]); presser=callback_query['from']; msg=callback_query['message']; match=await get_match(mid)
    if not match or match['status']!='pending':
        await app.answer_callback_query(callback_query['id'],'This challenge is no longer active.',show_alert=True); return
    if int(presser['id'])!=int(match['opponent_id']):
        await app.answer_callback_query(callback_query['id'],"This challenge isn't for you!",show_alert=True); return
    await update_status(mid,'declined'); await app.answer_callback_query(callback_query['id'],'Challenge declined.')
    a=mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name']); o=mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])
    text=("<b>╭━━〔 🏏 T20I MATCH 〕━━╮\n\n" f"⚔️ {a}\n              VS\n🔥 {o}\n\n</b>"
          "<blockquote><b>🏏 T20 International\n🌍 National Squads</b></blockquote>\n\n"
          "❌ <b>Challenge Declined!</b>\n\nMaybe next time. 🏏\n\n"
          "<b>╰━━━━━━━━━━━━━━━━━━╯</b>")
    await app.edit_message_text(msg['chat']['id'],msg['message_id'],text,parse_mode='HTML',reply_markup={'inline_keyboard':[]})
