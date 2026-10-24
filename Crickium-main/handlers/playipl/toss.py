from __future__ import annotations
import asyncio
from handlers.registry import register_callback
from app import app
from database.playipl_repo import get_match,set_message_id,set_toss,set_decision
from utils.mentions import mention_html
from buttons.playipl_buttons import toss_call_keyboard,decision_keyboard
from engines.play_engine import flip_coin
from .lineup import send_build_messages

_FRAMES=['🪙 Tossing the coin...\n       ↻  ◌  ↺','🪙 Coin spinning high...\n       ◐  ◓  ◑',"🪙 And it's coming down...\n       ◒  ◉  ◐"]
LABEL={'heads':'🗿 HEADS','tails':'🦅 TAILS'}

def _toss_text(mention):
    return f"<b>╭━━〔 🪙 TOSS CALL 〕━━╮\n\n👤 {mention}\n\nThe toss is yours to call. 🏏\nChoose your side of the coin.\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>"
async def send_toss_call(chat_id,match):
    m=mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name'])
    sent=await app.send_message(chat_id,_toss_text(m),parse_mode='HTML',reply_markup=toss_call_keyboard(match['match_id']))
    await set_message_id(match['match_id'],sent['message_id'])

def _result(winner,call,res):
    return ("<b>╭━━〔 🪙 TOSS RESULT 〕━━╮\n\n" f"🏆 {winner} wins the toss!\n\n</b>"
            f"<blockquote><b>🎯 Call    ➤ {LABEL[call]}\n🪙 Result  ➤ {LABEL[res]}</b></blockquote>\n"
            "<b>\nYour call, skipper. 🏏\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>")

@register_callback('playipl_toss_call')
async def playipl_toss_call(callback_query):
    _,mid_s,call=callback_query['data'].split(':'); mid=int(mid_s); uid=int(callback_query['from']['id']); msg=callback_query['message']; match=await get_match(mid)
    if not match or match['status']!='pitch_selected': await app.answer_callback_query(callback_query['id'],'The toss call is no longer active.',show_alert=True); return
    if uid!=int(match['opponent_id']): await app.answer_callback_query(callback_query['id'],'Only the challenged player calls the toss!',show_alert=True); return
    await app.answer_callback_query(callback_query['id'],f'You called {call}!')
    for frame in _FRAMES:
        await app.edit_message_text(msg['chat']['id'],msg['message_id'],f'<b>{frame}</b>',parse_mode='HTML',reply_markup={'inline_keyboard':[]}); await asyncio.sleep(1)
    result=flip_coin(); winner=match['opponent_id'] if result==call else match['challenger_id']; await set_toss(mid,winner,call,result); match=dict(await get_match(mid))
    wmention=mention_html(match['opponent_id'],match['opponent_username'],match['opponent_name']) if int(winner)==int(match['opponent_id']) else mention_html(match['challenger_id'],match['challenger_username'],match['challenger_name'])
    await app.edit_message_text(msg['chat']['id'],msg['message_id'],_result(wmention,call,result),parse_mode='HTML',reply_markup=decision_keyboard(mid))

@register_callback('playipl_decision')
async def playipl_decision(callback_query):
    _,mid_s,decision=callback_query['data'].split(':'); mid=int(mid_s); uid=int(callback_query['from']['id']); msg=callback_query['message']; match=await get_match(mid)
    if not match or match['status']!='toss_done': await app.answer_callback_query(callback_query['id'],'This decision is no longer active.',show_alert=True); return
    if uid!=int(match['toss_winner_id']): await app.answer_callback_query(callback_query['id'],'Only the toss winner decides!',show_alert=True); return
    await set_decision(mid,decision); await app.answer_callback_query(callback_query['id'],f'You chose to {decision}!')
    winner=int(match['toss_winner_id']); batting=winner if decision=='bat' else (int(match['opponent_id']) if winner==int(match['challenger_id']) else int(match['challenger_id']))
    winner_mention=mention_html(winner,match['challenger_username'] if winner==int(match['challenger_id']) else match['opponent_username'],match['challenger_name'] if winner==int(match['challenger_id']) else match['opponent_name'])
    batting_mention=mention_html(batting,match['challenger_username'] if batting==int(match['challenger_id']) else match['opponent_username'],match['challenger_name'] if batting==int(match['challenger_id']) else match['opponent_name'])
    text=("<b>🏏 TOSS DECISION\n\n" f"🎯 {winner_mention} chose to {'BAT' if decision=='bat' else 'BOWL'}\n" f"🏏 {batting_mention} will BAT first\n\n🔒 Decision Locked</b>")
    await app.delete_message(msg['chat']['id'],msg['message_id']); await app.send_message(msg['chat']['id'],text,parse_mode='HTML'); await asyncio.sleep(2)
    match=dict(await get_match(mid)); from .live import begin_match_flow
    await begin_match_flow(msg['chat']['id'],match)
