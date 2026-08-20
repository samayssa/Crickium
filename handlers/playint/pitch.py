from __future__ import annotations
import asyncio
from handlers.registry import register_callback
from app import app
from database.playint_repo import get_match,set_pitch,set_message_id
from database.playint_teams_repo import team_label
from buttons.playint_buttons import pitch_keyboard
from engines.play_engine import pitch_label
from .toss import send_toss_call

@register_callback('playint_pitch')
async def playint_pitch(callback_query):
    _,mid_s,pitch=callback_query['data'].split(':'); mid=int(mid_s); uid=int(callback_query['from']['id']); msg=callback_query['message']; match=await get_match(mid)
    if not match or match['status'] not in {'team_selection','lineup','pitch_selected'}:
        await app.answer_callback_query(callback_query['id'],'Pitch selection is no longer active.',show_alert=True); return
    if uid!=int(match['challenger_id']):
        await app.answer_callback_query(callback_query['id'],'Only the challenger selects the pitch!',show_alert=True); return
    await set_pitch(mid,pitch); match=dict(await get_match(mid)); await app.answer_callback_query(callback_query['id'],'Pitch locked!')
    await app.edit_message_text(msg['chat']['id'],msg['message_id'],f"<b>🏟️ {team_label(match['challenger_team_code'])} selected {pitch_label(pitch)} Pitch.\n\n🔒 Pitch locked. Get ready for the toss! 🏏</b>",parse_mode='HTML',reply_markup={'inline_keyboard':[]})
    await asyncio.sleep(1); await send_toss_call(msg['chat']['id'],match)

async def send_pitch_selection(chat_id,match):
    # same pitch options as /play, but isolated callbacks
    a=f"{team_label(match['challenger_team_code'])}"
    text=(f"<b>╭━━〔 🏟️ PITCH SELECT 〕━━╮\n\n👤 {a}\n\n"
          "The match is set. Now choose your battlefield. 🏏\n\nSelect the pitch you want to play on.\nYour choice could shape the entire match. ⚡\n\n╰━━━━━━━━━━━━━━━━━━━━━━╯</b>")
    sent=await app.send_message(chat_id,text,parse_mode='HTML',reply_markup=pitch_keyboard(match['match_id']))
    await set_message_id(match['match_id'],sent['message_id'])
