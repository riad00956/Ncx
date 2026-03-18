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

# ---------- Configuration ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
CREDIT = "「 Prime Xyron 」👨‍💻"

# External Database API Configuration
API_BASE_URL = "https://phpbot.top/api/external/"
DB_ID = "db_9J0TaLB4XlvNp10MGtHbcrc8uKSdfc1o"

bot = AsyncTeleBot(BOT_TOKEN)

# Shared state (protected by locks)
user_states = {}           # login flow state
active_clients = {}        # uid -> Telethon client
reply_tracking = {}        # uid -> current reply counter

state_lock = asyncio.Lock()
clients_lock = asyncio.Lock()
reply_lock = asyncio.Lock()

# ---------- API Helpers ----------
async def api_request(action: str, method: str = "GET", data: dict = None):
    """Make a request to the external database API."""
    url = API_BASE_URL
    params = {"db": DB_ID}
    if action:
        params["action"] = action

    async with aiohttp.ClientSession() as session:
        if method == "GET":
            async with session.get(url, params=params, json=data) as resp:
                return await resp.json()
        elif method == "POST":
            async with session.post(url, params=params, json=data) as resp:
                return await resp.json()
        else:
            raise ValueError(f"Unsupported method: {method}")

# ---------- Database helpers (via API) ----------
async def db_init():
    """Test API connection."""
    try:
        await api_request("ping", method="GET")
    except Exception as e:
        print(f"Database API connection failed: {e}")

async def db_get_user(user_id: int):
    """Retrieve user data from API."""
    try:
        resp = await api_request("get_user", method="GET", data={"user_id": user_id})
        if resp.get("status") == "success":
            data = resp.get("data")
            if data:
                # Return tuple in same order as before: (api_id, api_hash, string_session, custom_reply, is_enabled)
                return (
                    data.get("api_id"),
                    data.get("api_hash"),
                    data.get("string_session"),
                    data.get("custom_reply", "I'm currently offline."),
                    data.get("is_enabled", 1)
                )
        return None
    except Exception as e:
        print(f"db_get_user error: {e}")
        return None

async def db_update_user(user_id: int, **kwargs):
    """Update user data via API."""
    data = kwargs.copy()
    data["user_id"] = user_id
    try:
        resp = await api_request("update_user", method="POST", data=data)
        return resp.get("status") == "success"
    except Exception as e:
        print(f"db_update_user error: {e}")
        return False

async def db_insert_user(user_id: int):
    """Insert a new user via API."""
    try:
        resp = await api_request("insert_user", method="POST", data={"user_id": user_id})
        return resp.get("status") == "success"
    except Exception as e:
        print(f"db_insert_user error: {e}")
        return False

async def db_get_active_users():
    """Retrieve all active users (is_active=1) from API."""
    try:
        resp = await api_request("get_active_users", method="GET")
        if resp.get("status") == "success":
            users = resp.get("data", [])
            # Each user should be a dict with keys: user_id, api_id, api_hash, string_session
            return [(u["user_id"], u["api_id"], u["api_hash"], u["string_session"]) for u in users]
        return []
    except Exception as e:
        print(f"db_get_active_users error: {e}")
        return []

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

                await asyncio.sleep(15)

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
        "👻 Phantom Reply\n\n"
        "Welcome to your Telegram shadow.\n"
        "When you are offline, I automatically reply to messages for you.\n\n"
        "I never reply when you are online.\n\n"
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
        "⚙️ Settings Panel\n\n"
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
            "🔑 API Authentication\n\n"
            "Send your credentials in this format:\n\n"
            "API_ID:API_HASH\n\n"
            "Example:\n"
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
            "🔴 Session Removed\n\nYour Telegram session has been cleared."
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

    # Ignore unknown steps (like wait_reply) – let other handlers process them
    if step not in ("api", "phone", "otp", "2fa"):
        return

    if step == "api" and ":" in text:
        api_id, api_hash = text.split(":", 1)
        async with state_lock:
            user_states[uid].update({
                "api_id": int(api_id.strip()),
                "api_hash": api_hash.strip(),
                "step": "phone"
            })
        await bot.send_message(uid,
            "📱 Phone Verification\n\n"
            "Send your Telegram phone number.\n\n"
            "Example:\n"
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
                "📩 OTP Code\n\n"
                "Enter the login code you received.\n\n"
                "Example:\n"
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
                "✅ Login Success\n\n"
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
                "🔐 2FA Security\n\n"
                "Your account has Two-Step Verification enabled.\n\n"
                "Please enter your password to continue."
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
            await bot.send_message(uid, "✅ Login Success")
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
        "✏️ Custom Auto Reply\n\n"
        "Send the message you want people to receive when you are offline.\n\n"
        "Example:\n"
        "I'm currently offline. I'll reply later."
    )

@bot.message_handler(func=lambda m: m.from_user.id in user_states and user_states[m.from_user.id]["step"] == "wait_reply")
async def save_reply(message):
    uid = message.from_user.id
    try:
        await db_update_user(uid, custom_reply=message.text)
        await bot.send_message(uid,
            "✅ Reply Saved\n\nYour auto-reply message has been updated successfully."
        )
    finally:
        # Always remove the state, even if an error occurred
        async with state_lock:
            user_states.pop(uid, None)

@bot.message_handler(func=lambda m: m.text == "📊 Status")
async def status_check(message):
    uid = message.from_user.id
    row = await db_get_user(uid)
    if row and row[2]:          # has session
        status = "Active" if row[4] == 1 else "Disabled"
        text = (
            f"📊 Bot Status\n\n"
            f"Reply : {row[3]}\n\n"
            f"Listener : {status}\n\n"
            f"Mode : Smart Offline Only\n\n"
            f"I never reply when you are online."
        )
    else:
        text = "❌ Not connected."
    await bot.send_message(uid, text)

@bot.message_handler(commands=['admin'])
async def admin_backup(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        resp = await api_request("get_all_users", method="GET")
        if resp.get("status") == "success":
            data = resp.get("data", [])
            backup_content = json.dumps(data, indent=2)
            # Send as file
            await bot.send_document(
                message.chat.id,
                ("backup.json", backup_content),
                caption="📊 Admin Backup (from API)"
            )
        else:
            await bot.send_message(message.chat.id, "❌ Failed to fetch backup data.")
    except Exception as e:
        await bot.send_message(message.chat.id, f"❌ Backup error: {e}")

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
    active_users = await db_get_active_users()
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
