from __future__ import annotations
import asyncio
from handlers.registry import register, register_callback
from app import app
from config import ADMIN_USER_ID
from database.broadcast_repo import get_broadcast_targets
from buttons.post_buttons import post_confirm_keyboard

@register("post")
async def post_command(message):
    chat_id=int(message["chat"]["id"]); user_id=int((message.get("from") or {}).get("id") or 0)
    if user_id != int(ADMIN_USER_ID):
        await app.send_message(chat_id, "🚫 <b>This command is restricted to the bot owner only.</b>", parse_mode="HTML"); return
    reply=message.get("reply_to_message") or {}
    if not reply.get("message_id"):
        await app.send_message(chat_id, "⚠️ <b>Reply to the message you want to forward with /post.</b>", parse_mode="HTML"); return
    text=("<b>╭━━━〔 📢 POST FORWARD 〕━━━╮</b>\n\n"
          "<blockquote>Do you want to forward this message to all <b>known users</b>, <b>known groups</b> and <b>known channels</b>?</blockquote>\n\n"
          "<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>")
    await app.send_message(chat_id,text,parse_mode="HTML",reply_markup=post_confirm_keyboard(chat_id,int(reply["message_id"])))

@register_callback("post_no")
async def post_no(callback_query):
    if int((callback_query.get("from") or {}).get("id") or 0) != int(ADMIN_USER_ID):
        await app.answer_callback_query(callback_query["id"],"Owner only.",show_alert=True); return
    msg=callback_query["message"]; await app.answer_callback_query(callback_query["id"],"Cancelled.")
    await app.edit_message_text(msg["chat"]["id"],msg["message_id"],"❌ <b>Forwarding cancelled.</b>",parse_mode="HTML",reply_markup={"inline_keyboard":[]})

@register_callback("post_yes")
async def post_yes(callback_query):
    if int((callback_query.get("from") or {}).get("id") or 0) != int(ADMIN_USER_ID):
        await app.answer_callback_query(callback_query["id"],"Owner only.",show_alert=True); return
    parts=str(callback_query.get("data") or "").split(":")
    if len(parts)!=3:
        await app.answer_callback_query(callback_query["id"],"Invalid post request.",show_alert=True); return
    source_chat_id=int(parts[1]); source_message_id=int(parts[2])
    msg=callback_query["message"]; out_chat=int(msg["chat"]["id"]); out_mid=int(msg["message_id"])
    await app.answer_callback_query(callback_query["id"],"Forwarding...")
    targets=[t for t in await get_broadcast_targets() if int(t["chat_id"]) != out_chat]
    total=len(targets); sent=users=groups=channels=0; dot=1
    async def progress():
        nonlocal dot
        dots='.'*dot; dot=dot%3+1
        body=(f"<b>╭━━━〔 📤 FORWARDING MESSAGE 〕━━━╮</b>\n\n<blockquote><b>Forwarding message to all users{dots}</b></blockquote>\n\n<b>Progress:</b> {sent}/{total}\n\n<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>")
        await app.edit_message_text(out_chat,out_mid,body,parse_mode="HTML",reply_markup={"inline_keyboard":[]})
    await progress()
    for i,t in enumerate(targets,1):
        try:
            await app.forward_message(int(t["chat_id"]),source_chat_id,source_message_id); sent += 1
            if t["target_type"]=="user": users+=1
            elif t["target_type"]=="group": groups+=1
            elif t["target_type"]=="channel": channels+=1
        except Exception as exc:
            print(f"[post] Forward failed chat_id={t['chat_id']} type={t['target_type']}: {exc!r}")
        if i%5==0 or i==total:
            try: await progress()
            except Exception as exc: print(f"[post] progress edit failed: {exc!r}")
        await asyncio.sleep(0.05)
    try: await app.delete_message(out_chat,out_mid)
    except Exception as exc: print(f"[post] progress delete failed: {exc!r}")
    result=(f"<b>╭━━━〔 ✅ POST FORWARDED 〕━━━╮</b>\n\n<blockquote>👤 <b>Total Users:</b> {users}\n👥 <b>Total Groups:</b> {groups}\n📢 <b>Total Channels:</b> {channels}</blockquote>\n\n<b>📤 Total Sent:</b> {sent}\n\n<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>")
    await app.send_message(out_chat,result,parse_mode="HTML")
