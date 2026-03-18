import os
import asyncio
import zipfile
from threading import Thread

import aiosqlite
from flask import Flask
from waitress import serve
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telethon import TelegramClient, errors, events, functions, types
from telethon.sessions import StringSession

# ---------- Configuration ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
DB_PATH = "data.db"
CREDIT = "「 Prime Xyron 」👨‍💻"

bot = AsyncTeleBot(BOT_TOKEN)

# Shared state (protected by locks)
user_states = {}           # login flow state
active_clients = {}        # uid -> Telethon client
reply_tracking = {}        # uid -> current reply counter

state_lock = asyncio.Lock()
clients_lock = asyncio.Lock()
reply_lock = asyncio.Lock()

# ---------- Database helpers (async) ----------
async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            api_id INTEGER,
            api_hash TEXT,
            string_session TEXT,
            custom_reply TEXT DEFAULT "I'm currently offline.",
            is_active INTEGER DEFAULT 0,
            is_enabled INTEGER DEFAULT 1
        )''')
        await db.commit()

async def db_get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT api_id, api_hash, string_session, custom_reply, is_enabled FROM users WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return row

async def db_update_user(user_id: int, **kwargs):
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {sets} WHERE user_id = ?", values)
        await db.commit()

async def db_insert_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

# ---------- User Listener (ghost mode) ----------
async def user_listener(uid: int, api_id: int, api_hash: str, session_str: str):
    """Start a Telethon client for a user and handle incoming messages."""
    # Remove old client if exists
    async with clients_lock:
        if uid in active_clients:
            old = active_clients.pop(uid)
            try:
                await old.disconnect()
            except:
                pass

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
            # Check if this user has enabled the bot
            row = await db_get_user(uid)
            if not row or row[4] == 0:          # is_enabled == 0
                return

            try:
                # 1. Get the user's own online status
                me = await client(functions.users.GetUsersRequest(id=["me"]))
                if isinstance(me[0].status, types.UserStatusOnline):
                    return                       # user is online – do nothing

                # 2. Send the custom reply
                await event.reply(row[3])         # custom_reply

                # 3. Mark user as online (briefly)
                await client(functions.account.UpdateStatusRequest(offline=False))

                # 4. Manage the 15‑second offline delay
                async with reply_lock:
                    reply_tracking[uid] = reply_tracking.get(uid, 0) + 1
                    current_call = reply_tracking[uid]

                await asyncio.sleep(5)

                async with reply_lock:
                    # If no new reply came during the sleep, go back offline
                    if reply_tracking.get(uid) == current_call:
                        await client(functions.account.UpdateStatusRequest(offline=True))

            except Exception as e:
                print(f"[User {uid}] Handler error: {e}")

        await client.run_until_disconnected()
    except Exception as e:
        print(f"[User {uid}] Listener crashed: {e}")
    finally:
        async with clients_lock:
            active_clients.pop(uid, None)
        # Attempt to restart after a short delay (auto‑reconnect)
        await asyncio.sleep(5)
        asyncio.create_task(user_listener(uid, api_id, api_hash, session_str))

# ---------- Bot Command Handlers ----------
@bot.message_handler(commands=['start'])
async def cmd_start(message):
    uid = message.from_user.id
    await db_insert_user(uid)

    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⚙️ Settings", "✏️ Set Reply", "📊 Status")
    text = (
        "👻 𝙿𝚑𝚊𝚗𝚝𝚘𝚖 𝚁𝚎𝚙𝚕𝚢\n\n"
        "Welcome to your Telegram shadow.\n"
        "When you are offline, I automatically reply to messages for you.\n\n"
        "⚡ Smart Presence Detection\n"
        "💬 Custom Auto Reply\n"
        "🔐 Secure Login System\n\n"
        f"Powered by {CREDIT}"
    )
    await bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "⚙️ Settings")
async def settings(message):
    uid = message.from_user.id
    row = await db_get_user(uid)

    status = "Connected" if row and row[2] else "Not Connected"
    markup = InlineKeyboardMarkup()
    if status == "Connected":
        toggle = "🟢 Bot Enabled" if row[4] == 1 else "🔴 Bot Disabled"
        markup.add(InlineKeyboardButton(toggle, callback_data="toggle"))
        markup.add(InlineKeyboardButton("❌ Logout", callback_data="logout"))
    else:
        markup.add(InlineKeyboardButton("➕ Login Account", callback_data="login"))

    text = (
        "⚙️ 𝚂𝚎𝚝𝚝𝚒𝚗𝚐𝚜 𝙿𝚊𝚗𝚎𝚕\n\n"
        f"Account Status : {status}\n\n"
        "If your account is not connected,\n"
        "please login using your Telegram API.\n\n"
        "🔒 Your session will remain private."
    )
    await bot.send_message(uid, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: True)
async def callback_handler(call):
    uid = call.from_user.id
    data = call.data

    if data == "login":
        async with state_lock:
            user_states[uid] = {"step": "api"}
        await bot.send_message(uid,
            "🔑 𝙰𝙿𝙸 𝙰𝚞𝚝𝚑𝚎𝚗𝚝𝚒𝚌𝚊𝚝𝚒𝚘𝚗\n\n"
            "Send your credentials in this format :\n\n"
            "API_ID:API_HASH\n\n"
            "Example :\n"
            "123456:abcd1234efgh5678\n\n"
            "⚠️ Never share your API with anyone."
        )
    elif data == "toggle":
        row = await db_get_user(uid)
        if row:
            new_enabled = 1 - row[4]          # flip
            await db_update_user(uid, is_enabled=new_enabled)
        await settings(call.message)          # refresh
    elif data == "logout":
        await db_update_user(uid, string_session=None, is_active=0)
        async with clients_lock:
            if uid in active_clients:
                await active_clients[uid].disconnect()
        await bot.send_message(uid,
            "🔴 𝚂𝚎𝚜𝚜𝚒𝚘𝚗 𝚁𝚎𝚖𝚘𝚟𝚎𝚍\n\nYour Telegram session has been cleared."
        )
    await bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in user_states)
async def login_flow(message):
    uid = message.from_user.id
    async with state_lock:
        state = user_states.get(uid)
    if not state:
        return

    step = state["step"]
    text = message.text.strip()

    if step == "api" and ":" in text:
        api_id, api_hash = text.split(":", 1)
        async with state_lock:
            user_states[uid].update({
                "api_id": int(api_id.strip()),
                "api_hash": api_hash.strip(),
                "step": "phone"
            })
        await bot.send_message(uid,
            "📱 𝙿𝚑𝚘𝚗𝚎 𝚅𝚎𝚛𝚒𝚏𝚒𝚌𝚊𝚝𝚒𝚘𝚗\n\n"
            "Send your Telegram phone number.\n\n"
            "Example :\n"
            "+8801XXXXXXXXX\n\n"
            "OTP code will be sent to your Telegram."
        )
    elif step == "phone":
        phone = text
        api_id = state["api_id"]
        api_hash = state["api_hash"]
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            async with state_lock:
                user_states[uid].update({
                    "phone": phone,
                    "hash": sent.phone_code_hash,
                    "step": "otp",
                    "client": client
                })
            await bot.send_message(uid,
                "📩 𝙾𝚃𝙿 𝙲𝚘𝚍𝚎\n\n"
                "Enter the login code you received.\n\n"
                "Example :\n"
                "1 2 3 4 5\n\n"
                "⏳ Please enter it quickly before it expires."
            )
        except Exception as e:
            await bot.send_message(uid, f"❌ Error: {e}")
            async with state_lock:
                user_states.pop(uid, None)
    elif step == "otp":
        code = text.replace(" ", "")
        client = state["client"]
        try:
            await client.sign_in(
                phone=state["phone"],
                code=code,
                phone_code_hash=state["hash"]
            )
            session_str = client.session.save()
            await db_update_user(uid,
                api_id=state["api_id"],
                api_hash=state["api_hash"],
                string_session=session_str,
                is_active=1
            )
            await bot.send_message(uid,
                "✅ 𝙻𝚘𝚐𝚒𝚗 𝚂𝚞𝚌𝚌𝚎𝚜𝚜\n\n"
                "Your Telegram account is now connected.\n\n"
                "👻 Phantom Reply is now active."
            )
            asyncio.create_task(user_listener(
                uid, state["api_id"], state["api_hash"], session_str
            ))
            async with state_lock:
                user_states.pop(uid, None)
        except errors.SessionPasswordNeededError:
            async with state_lock:
                user_states[uid]["step"] = "2fa"
            await bot.send_message(uid,
                "🔐 𝟸𝙵𝙰 𝚂𝚎𝚌𝚞𝚛𝚒𝚝𝚢\n\n"
                "Your account has Two-Step Verification enabled.\n\n"
                "Please enter your password to continue.\n\n"
                "নিরাপত্তার জন্য এটি প্রয়োজন."
            )
        except Exception as e:
            await bot.send_message(uid, f"❌ Error: {e}")
    elif step == "2fa":
        client = state["client"]
        try:
            await client.sign_in(password=text)
            session_str = client.session.save()
            await db_update_user(uid,
                string_session=session_str,
                is_active=1
            )
            await bot.send_message(uid, "✅ 𝙻𝚘𝚐𝚒𝚗 𝚂𝚞𝚌𝚌𝚎𝚜𝚜")
            asyncio.create_task(user_listener(
                uid, state["api_id"], state["api_hash"], session_str
            ))
            async with state_lock:
                user_states.pop(uid, None)
        except Exception as e:
            await bot.send_message(uid, f"❌ Error: {e}")

@bot.message_handler(func=lambda m: m.text == "✏️ Set Reply")
async def set_reply(message):
    uid = message.from_user.id
    async with state_lock:
        user_states[uid] = {"step": "wait_reply"}
    await bot.send_message(uid,
        "✏️ 𝙲𝚞𝚜𝚝𝚘𝚖 𝙰𝚞𝚝𝚘 𝚁𝚎𝚙𝚕𝚢\n\n"
        "Send the message you want people to receive when you are offline.\n\n"
        "Example :\n"
        "I'm currently offline. I'll reply later.\n\n"
        "✅"
    )

@bot.message_handler(func=lambda m: m.from_user.id in user_states and user_states[m.from_user.id]["step"] == "wait_reply")
async def save_reply(message):
    uid = message.from_user.id
    await db_update_user(uid, custom_reply=message.text)
    await bot.send_message(uid,
        "✅ 𝚁𝚎𝚙𝚕𝚢 𝚂𝚊𝚟𝚎𝚍\n\nYour auto-reply message has been updated successfully."
    )
    async with state_lock:
        user_states.pop(uid, None)

@bot.message_handler(func=lambda m: m.text == "📊 Status")
async def status_check(message):
    uid = message.from_user.id
    row = await db_get_user(uid)
    if row and row[2]:          # has session
        status = "Active" if row[4] == 1 else "Disabled"
        text = (
            f"📊 𝙱𝚘𝚝 𝚂𝚝𝚊𝚝𝚞𝚜\n\n"
            f"Reply : {row[3]}\n\n"
            f"Listener : {status}\n\n"
            f"Mode : Smart Offline Only\n\n"
            f"「 Prime Xyron 」👨‍💻"
        )
    else:
        text = "❌ Not connected."
    await bot.send_message(uid, text)

@bot.message_handler(commands=['admin'])
async def admin_backup(message):
    if message.from_user.id != ADMIN_ID:
        return
    with zipfile.ZipFile("backup.zip", 'w') as z:
        if os.path.exists(DB_PATH):
            z.write(DB_PATH)
    with open("backup.zip", 'rb') as f:
        await bot.send_document(message.chat.id, f, caption="📊 Admin Backup")
    os.remove("backup.zip")

# ---------- Flask Keep‑Alive ----------
app = Flask(__name__)

@app.route('/')
def home():
    return "Phantom Ghost System is Live"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    serve(app, host='0.0.0.0', port=port)

# ---------- Startup ----------
async def on_startup():
    await db_init()
    # Restore active listeners from database
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, api_id, api_hash, string_session FROM users WHERE is_active = 1"
        ) as cursor:
            active_users = await cursor.fetchall()
    for uid, api_id, api_hash, session in active_users:
        if api_id and api_hash and session:
            asyncio.create_task(user_listener(uid, api_id, api_hash, session))
    print(f"Phantom Burst Online | {CREDIT}")

async def main():
    await on_startup()
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    # Start Flask in a background thread
    Thread(target=run_flask, daemon=True).start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
        # Disconnect all user clients
        async def disconnect_all():
            async with clients_lock:
                for client in active_clients.values():
                    await client.disconnect()
        asyncio.run(disconnect_all())
