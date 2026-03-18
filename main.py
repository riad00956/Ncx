import os
import asyncio
import json
from threading import Thread

import aiohttp
from flask import Flask
from waitress import serve
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telethon import TelegramClient, errors, events, functions, types
from telethon.sessions import StringSession

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
CREDIT = "「 𝗣𝗿𝗶𝗺𝗲 𝗫𝘆𝗿𝗼𝗻 」👨‍💻"

CF_ACCOUNT_ID = "57bdaf73b4ceb569b6de021f12d0ea3d"
CF_DATABASE_ID = "ea1cb292-aaab-4780-af6b-42b42537b0b7"
CF_API_TOKEN = "YBeQB3ib_0ReV-jP8zyfj5dXWWhRpkEExXwGIyKm"

bot = AsyncTeleBot(BOT_TOKEN)

user_states = {}
active_clients = {}
reply_tracking = {}

state_lock = asyncio.Lock()
clients_lock = asyncio.Lock()
reply_lock = asyncio.Lock()

async def d1_query(sql: str, params: list = None):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"sql": sql}
    if params:
        payload["params"] = [str(p) if not isinstance(p, (int, float)) else p for p in params]
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            return await resp.json()

async def db_init():
    sql = """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        api_id INTEGER,
        api_hash TEXT,
        string_session TEXT,
        custom_reply TEXT DEFAULT 'I am currently offline.',
        is_enabled INTEGER DEFAULT 1,
        is_active INTEGER DEFAULT 0
    )
    """
    await d1_query(sql)

async def db_get_user(user_id: int):
    sql = "SELECT api_id, api_hash, string_session, custom_reply, is_enabled FROM users WHERE user_id = ?"
    res = await d1_query(sql, [user_id])
    try:
        if res.get("success"):
            results = res["result"][0]["results"]
            if results:
                r = results[0]
                return (
                    r.get("api_id"),
                    r.get("api_hash"),
                    r.get("string_session"),
                    r.get("custom_reply") or "I am currently offline.",
                    r.get("is_enabled")
                )
    except:
        pass
    return None

async def db_update_user(user_id: int, **kwargs):
    if not kwargs: return False
    cols = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    params = list(kwargs.values())
    params.append(user_id)
    sql = f"UPDATE users SET {cols} WHERE user_id = ?"
    res = await d1_query(sql, params)
    return res.get("success", False)

async def db_insert_user(user_id: int):
    sql = "INSERT OR IGNORE INTO users (user_id) VALUES (?)"
    res = await d1_query(sql, [user_id])
    return res.get("success", False)

async def db_get_active_users():
    sql = "SELECT user_id, api_id, api_hash, string_session FROM users WHERE is_active = 1 AND string_session IS NOT NULL"
    res = await d1_query(sql)
    users = []
    try:
        if res.get("success"):
            results = res["result"][0]["results"]
            for r in results:
                users.append((r["user_id"], r["api_id"], r["api_hash"], r["string_session"]))
    except:
        pass
    return users

async def user_listener(uid: int, api_id: int, api_hash: str, session_str: str):
    async with clients_lock:
        if uid in active_clients:
            try:
                await active_clients[uid].disconnect()
            except:
                pass
            active_clients.pop(uid)

    client = TelegramClient(StringSession(session_str), api_id, api_hash, auto_reconnect=True)
    async with clients_lock:
        active_clients[uid] = client

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await db_update_user(uid, is_active=0)
            return

        @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def handler(event):
            row = await db_get_user(uid)
            if not row or row[4] == 0:
                return

            try:
                me = await client(functions.users.GetUsersRequest(id=["me"]))
                if isinstance(me[0].status, types.UserStatusOnline):
                    return

                await event.reply(row[3])
                await client(functions.account.UpdateStatusRequest(offline=False))

                async with reply_lock:
                    reply_tracking[uid] = reply_tracking.get(uid, 0) + 1
                    current_call = reply_tracking[uid]

                await asyncio.sleep(15)

                async with reply_lock:
                    if reply_tracking.get(uid) == current_call:
                        await client(functions.account.UpdateStatusRequest(offline=True))
            except:
                pass

        await client.run_until_disconnected()
    except:
        pass
    finally:
        async with clients_lock:
            active_clients.pop(uid, None)
        await asyncio.sleep(10)
        if await db_get_user(uid):
            asyncio.create_task(user_listener(uid, api_id, api_hash, session_str))

@bot.message_handler(commands=['start'])
async def cmd_start(message):
    uid = message.from_user.id
    await db_insert_user(uid)
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⚙️ 𝚂𝚎𝚝𝚝𝚒𝚗𝚐𝚜", "✏️ 𝚂𝚎𝚝 𝚁𝚎𝚙𝚕𝚢")
    markup.add("📊 𝚂𝚝𝚊𝚝𝚞𝚜")
    text = (
        "👻 𝗣𝗵𝗮𝗻𝘁𝗼𝗺 𝗥𝗲𝗽𝗹𝘆\n\n"
        "𝚆𝚎𝚕𝚌𝚘𝚖𝚎 𝚝𝚘 𝚢𝚘𝚞𝚛 𝚃𝚎𝚕𝚎𝚐𝚛𝚊𝚖 𝚜𝚑𝚊𝚍𝚘𝚠.\n"
        "𝚆𝚑𝚎𝚗 𝚢𝚘𝚞 𝚊𝚛𝚎 𝚘𝚏𝚏𝚕𝚒𝚗𝚎, 𝙸 𝚊𝚞𝚝𝚘𝚖𝚊𝚝𝚒𝚌𝚊𝚕𝚕𝚢 𝚛𝚎𝚙𝚕𝚢.\n\n"
        f"𝙿𝚘𝚠𝚎𝚛𝚎𝚍 𝚋𝚢 {CREDIT}"
    )
    await bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "⚙️ 𝚂𝚎𝚝𝚝𝚒𝚗𝚐𝚜")
async def settings(message):
    uid = message.from_user.id
    row = await db_get_user(uid)
    status = "✅ 𝙲𝚘𝚗𝚗𝚎𝚌𝚝𝚎𝚍" if row and row[2] else "❌ 𝙽𝚘𝚝 𝙲𝚘𝚗𝚗𝚎𝚌𝚝𝚎𝚍"
    markup = InlineKeyboardMarkup()
    if row and row[2]:
        toggle_text = "🟢 𝙱𝚘𝚝: 𝙴𝚗𝚊𝚋𝚕𝚎𝚍" if row[4] == 1 else "🔴 𝙱𝚘𝚝: 𝙳𝚒𝚜𝚊𝚋𝚕𝚎𝚍"
        markup.add(InlineKeyboardButton(toggle_text, callback_data="toggle"))
        markup.add(InlineKeyboardButton("🗑️ 𝙻𝚘𝚐𝚘𝚞𝚝", callback_data="logout"))
    else:
        markup.add(InlineKeyboardButton("🔑 𝙻𝚘𝚐𝚒𝚗 𝙰𝚌𝚌𝚘𝚞𝚗𝚝", callback_data="login"))
    
    await bot.send_message(uid, f"⚙️ 𝗣𝗮𝗻𝗲𝗹 𝗦𝗲𝘁𝘁𝗶𝗻𝗴𝘀\n\n𝗦𝘁𝗮𝘁𝘂𝘀: {status}", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: True)
async def callback_handler(call):
    uid = call.from_user.id
    if call.data == "login":
        async with state_lock:
            user_states[uid] = {"step": "api"}
        await bot.send_message(uid, "🔑 𝚂𝚎𝚗𝚍 𝙰𝙿𝙸_𝙸𝙳:𝙰𝙿𝙸_𝙷𝙰𝚂𝙷\n\n𝙴𝚡𝚊𝚖𝚙𝚕𝚎: `12345:abcd6789efg`", parse_mode="Markdown")
    elif call.data == "toggle":
        row = await db_get_user(uid)
        if row:
            new_val = 1 - row[4]
            await db_update_user(uid, is_enabled=new_val)
            await bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=None)
            await settings(call.message)
    elif call.data == "logout":
        await db_update_user(uid, string_session=None, is_active=0)
        async with clients_lock:
            if uid in active_clients:
                await active_clients[uid].disconnect()
        await bot.send_message(uid, "🔴 𝚂𝚎𝚜𝚜𝚒𝚘𝚗 𝚃𝚎𝚛𝚖𝚒𝚗𝚊𝚝𝚎𝚍.")
    await bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in user_states and user_states[m.from_user.id].get("step") in ("api", "phone", "otp", "2fa"))
async def login_flow(message):
    uid = message.from_user.id
    async with state_lock:
        state = user_states.get(uid)
    if not state: return
    
    step = state["step"]
    text = message.text.strip()

    if step == "api" and ":" in text:
        aid, ahash = text.split(":", 1)
        async with state_lock:
            user_states[uid].update({"api_id": int(aid), "api_hash": ahash, "step": "phone"})
        await bot.send_message(uid, "📱 𝚂𝚎𝚗𝚍 𝚢𝚘𝚞𝚛 𝙿𝚑𝚘𝚗𝚎 𝙽𝚞𝚖𝚋𝚎𝚛\n𝙴𝚡𝚊𝚖𝚙𝚕𝚎: `+88017...`", parse_mode="Markdown")
    elif step == "phone":
        client = TelegramClient(StringSession(), state["api_id"], state["api_hash"])
        await client.connect()
        try:
            sent = await client.send_code_request(text)
            async with state_lock:
                user_states[uid].update({"phone": text, "hash": sent.phone_code_hash, "step": "otp", "client": client})
            await bot.send_message(uid, "📩 𝚂𝚎𝚗𝚍 𝙾𝚃𝙿 𝙲𝚘𝚍𝚎")
        except Exception as e:
            await bot.send_message(uid, f"❌ 𝙴𝚛𝚛𝚘𝚛: {e}")
            async with state_lock: user_states.pop(uid, None)
    elif step == "otp":
        client = state["client"]
        try:
            await client.sign_in(phone=state["phone"], code=text.replace(" ", ""), phone_code_hash=state["hash"])
            ss = client.session.save()
            await db_update_user(uid, api_id=state["api_id"], api_hash=state["api_hash"], string_session=ss, is_active=1)
            await bot.send_message(uid, "✅ 𝗟𝗼𝗴𝗶𝗻 𝗦𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹")
            asyncio.create_task(user_listener(uid, state["api_id"], state["api_hash"], ss))
            async with state_lock: user_states.pop(uid, None)
        except errors.SessionPasswordNeededError:
            async with state_lock: user_states[uid]["step"] = "2fa"
            await bot.send_message(uid, "🔐 𝙴𝚗𝚝𝚎𝚛 𝟸𝙵𝙰 𝙿𝚊𝚜𝚜𝚠𝚘𝚛𝚍")
        except Exception as e:
            await bot.send_message(uid, f"❌ 𝙴𝚛𝚛𝚘𝚛: {e}")
    elif step == "2fa":
        client = state["client"]
        try:
            await client.sign_in(password=text)
            ss = client.session.save()
            await db_update_user(uid, string_session=ss, is_active=1)
            await bot.send_message(uid, "✅ 𝗟𝗼𝗴𝗶𝗻 𝗦𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹")
            asyncio.create_task(user_listener(uid, state["api_id"], state["api_hash"], ss))
            async with state_lock: user_states.pop(uid, None)
        except Exception as e:
            await bot.send_message(uid, f"❌ 𝙴𝚛𝚛𝚘𝚛: {e}")

@bot.message_handler(func=lambda m: m.text == "✏️ 𝚂𝚎𝚝 𝚁𝚎𝚙𝚕𝚢")
async def set_reply(message):
    uid = message.from_user.id
    async with state_lock:
        user_states[uid] = {"step": "wait_reply"}
    await bot.send_message(uid, "✏️ 𝗘𝗱𝗶𝘁 𝗥𝗲𝗽𝗹𝘆\n\n𝚂𝚎𝚗𝚍 𝚢𝚘𝚞𝚛 𝚗𝚎𝚠 𝚊𝚞𝚝𝚘-𝚛𝚎𝚙𝚕𝚢 𝚖𝚎𝚜𝚜𝚊𝚐𝚎.")

@bot.message_handler(func=lambda m: m.from_user.id in user_states and user_states[m.from_user.id].get("step") == "wait_reply")
async def save_reply(message):
    uid = message.from_user.id
    await db_update_user(uid, custom_reply=message.text)
    await bot.send_message(uid, "✅ 𝗥𝗲𝗽𝗹𝘆 𝗨𝗽𝗱𝗮𝘁𝗲𝗱!")
    async with state_lock:
        user_states.pop(uid, None)

@bot.message_handler(func=lambda m: m.text == "📊 𝚂𝚝𝚊𝚝𝚞𝚜")
async def status_check(message):
    uid = message.from_user.id
    row = await db_get_user(uid)
    if row and row[2]:
        bot_status = "🟢 𝙰𝚌𝚝𝚒𝚟𝚎" if row[4] == 1 else "🔴 𝙳𝚒𝚜𝚊𝚋𝚕𝚎𝚍"
        text = (
            "📊 𝗦𝘆𝘀𝘁𝗲𝗺 𝗦𝘁𝗮𝘁𝘂𝘀\n\n"
            f"𝗕𝗼𝘁: {bot_status}\n"
            f"𝗥𝗲𝗽𝗹𝘆: `{row[3]}`"
        )
        await bot.send_message(uid, text, parse_mode="Markdown")
    else:
        await bot.send_message(uid, "❌ 𝙽𝚘𝚝 𝚌𝚘𝚗𝚗𝚎𝚌𝚝𝚎𝚍 𝚢𝚎𝚝.")

@bot.message_handler(commands=['admin'])
async def admin_backup(message):
    if message.from_user.id != ADMIN_ID: return
    res = await d1_query("SELECT * FROM users")
    if res.get("success"):
        data = res["result"][0]["results"]
        await bot.send_document(message.chat.id, ("backup.json", json.dumps(data, indent=2).encode()), caption="𝙳𝟷 𝙱𝚊𝚌𝚔𝚞𝚙")

app = Flask(__name__)
@app.route('/')
def home(): return "Phantom Ghost Online"

def run_flask(): serve(app, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

async def main():
    await db_init()
    active_users = await db_get_active_users()
    for u in active_users:
        asyncio.create_task(user_listener(u[0], u[1], u[2], u[3]))
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
