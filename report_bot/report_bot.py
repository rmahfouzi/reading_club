"""
Reading Club — Report Bot

A small, deterministic Telegram bot (separate from @clubKetab_bot) that
collects daily reading check-ins via DM and appends them to daily_logs.txt
in the same JSON-Lines format the reading-club-enforcer cron skill expects.

This intentionally contains NO LLM / prompt-injection surface: every reply
is a fixed template, every branch is plain Python. The only "intelligence"
is the conversation state machine below.

Run:
    pip install -r requirements.txt
    export REPORT_BOT_TOKEN=...
    export TELEGRAM_GROUP_CHAT_ID=-100...
    python report_bot.py
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ── Configuration ────────────────────────────────────────────────────────────

REPORT_BOT_TOKEN = os.environ["REPORT_BOT_TOKEN"]
GROUP_CHAT_ID = os.environ["TELEGRAM_GROUP_CHAT_ID"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = Path(os.environ.get("LOG_FILE", PROJECT_ROOT / "daily_logs.txt"))
MSG_COUNT_FILE = Path(os.environ.get("MSG_COUNT_FILE", PROJECT_ROOT / "message_counts.json"))

MAX_DAILY_MESSAGES = int(os.environ.get("MAX_DAILY_MESSAGES", "8"))
USER_TZ = ZoneInfo("Europe/Stockholm")

# Conversation states
ASK_CHECKIN, ASK_BOOK, ASK_TAKEAWAY = range(3)

# ── Persian message templates ───────────────────────────────────────────────

MSG_NOT_MEMBER = "این ربات فقط برای اعضای باشگاه کتاب‌خوانیه. 📚"
MSG_QUOTA_EXCEEDED = "⛔ امروز سهمیه‌ی پیامت تموم شده! فردا دوباره برگرد. 📅"
MSG_ALREADY_CHECKED_IN = "امروز قبلاً گزارشت رو ثبت کردم! ✅ فردا دوباره برگرد. کتاب بخون! 📚"
MSG_OPENER = "سلام! 👋 امروز ۱۵ دقیقه کتاب خوندی؟"
MSG_YES_FOLLOWUP = (
    "آفرین! ✅ حضورت ثبت شد. 💪\n"
    "داری چی می‌خونی؟ اگه دوست داری بگو، وگرنه دکمه‌ی رد کردن رو بزن! (اختیاریه)"
)
MSG_NO = (
    "اشکالی نداره! ولی یادت باشه اگه این هفته کمتر از ۵ روز بخونی، یه ❤️ از "
    "دست می‌دی. تا آخر هفته وقت داری! 💪"
)
MSG_BOOK_FOLLOWUP = "خوبه! 📖 یه جمله هم بگو — امروز از این کتاب چی به ذهنت رسید؟ (اختیاریه)"
MSG_FAREWELL = "باشه! 👍 تا فردا. کتاب خوندن رو ادامه بده! 🌟"
MSG_TAKEAWAY_THANKS = "ممنون که شریک شدی! 🌟 تا فردا."
MSG_OFF_TOPIC = "من فقط گزارش مطالعه‌ی روزانه دریافت می‌کنم. امروز ۱۵ دقیقه خوندی؟ 📚"

BTN_YES = "بله ✅"
BTN_NO = "نه ❌"
BTN_SKIP = "رد کردن ⏭️"

CB_YES = "checkin_yes"
CB_NO = "checkin_no"
CB_SKIP = "skip"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("report_bot")


# ── Helpers ──────────────────────────────────────────────────────────────────

def today_local() -> str:
    return datetime.now(USER_TZ).strftime("%Y-%m-%d")


def load_json(path: Path, default):
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Could not parse %s, treating as empty", path)
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_and_increment_quota(uid: str) -> bool:
    """Returns True if the user is still within today's quota (and increments
    the counter), False if the quota is already exhausted."""
    counts = load_json(MSG_COUNT_FILE, {})
    today = today_local()

    # Drop old dates to keep the file small.
    counts = {today: counts.get(today, {})}

    used = counts[today].get(uid, 0)
    if used >= MAX_DAILY_MESSAGES:
        save_json(MSG_COUNT_FILE, counts)
        return False

    counts[today][uid] = used + 1
    save_json(MSG_COUNT_FILE, counts)
    return True


def has_checked_in_today(uid: str) -> bool:
    if not LOG_FILE.exists():
        return False
    today = today_local()
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "CHECKIN" or entry.get("uid") != uid:
            continue
        ts = entry.get("ts", "")
        if ts.startswith(today) or _utc_ts_to_local_date(ts) == today:
            return True
    return False


def _utc_ts_to_local_date(ts: str) -> str | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(USER_TZ).strftime("%Y-%m-%d")


def append_log_entry(uid: str, username: str, full_name: str, entry_type: str,
                      book: str | None = None, takeaway: str | None = None) -> None:
    entry = {
        "ts": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "uid": uid,
        "username": username,
        "full_name": full_name,
        "type": entry_type,
        "book": book,
        "takeaway": takeaway,
    }
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def is_group_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
    except Exception:
        log.exception("getChatMember failed for user %s", user_id)
        return False
    return member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    )


def user_identity(update: Update) -> tuple[str, str, str]:
    user = update.effective_user
    uid = str(user.id)
    username = user.username or "unknown"
    full_name = user.full_name or username
    return uid, username, full_name


# ── Conversation handlers ───────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid, _, _ = user_identity(update)

    if not await is_group_member(context, update.effective_user.id):
        await update.message.reply_text(MSG_NOT_MEMBER)
        return ConversationHandler.END

    if not check_and_increment_quota(uid):
        await update.message.reply_text(MSG_QUOTA_EXCEEDED)
        return ConversationHandler.END

    if has_checked_in_today(uid):
        await update.message.reply_text(MSG_ALREADY_CHECKED_IN)
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(BTN_YES, callback_data=CB_YES),
        InlineKeyboardButton(BTN_NO, callback_data=CB_NO),
    ]])
    await update.message.reply_text(MSG_OPENER, reply_markup=keyboard)
    return ASK_CHECKIN


async def handle_checkin_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid, username, full_name = user_identity(update)

    if query.data == CB_NO:
        await query.edit_message_text(MSG_NO)
        return ConversationHandler.END

    # CB_YES — re-check quota/duplicate in case of a race since /start.
    if not await is_group_member(context, update.effective_user.id):
        await query.edit_message_text(MSG_NOT_MEMBER)
        return ConversationHandler.END

    if has_checked_in_today(uid):
        await query.edit_message_text(MSG_ALREADY_CHECKED_IN)
        return ConversationHandler.END

    append_log_entry(uid, username, full_name, "CHECKIN")

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(BTN_SKIP, callback_data=CB_SKIP)]])
    await query.edit_message_text(MSG_YES_FOLLOWUP, reply_markup=keyboard)
    return ASK_BOOK


async def handle_book_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(MSG_FAREWELL)
    return ConversationHandler.END


async def handle_book_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid, username, full_name = user_identity(update)
    book = update.message.text.strip()
    append_log_entry(uid, username, full_name, "READING_NOTE", book=book)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(BTN_SKIP, callback_data=CB_SKIP)]])
    await update.message.reply_text(MSG_BOOK_FOLLOWUP, reply_markup=keyboard)
    return ASK_TAKEAWAY


async def handle_takeaway_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(MSG_FAREWELL)
    return ConversationHandler.END


async def handle_takeaway_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid, username, full_name = user_identity(update)
    takeaway = update.message.text.strip()
    append_log_entry(uid, username, full_name, "READING_NOTE", takeaway=takeaway)
    await update.message.reply_text(MSG_TAKEAWAY_THANKS)
    return ConversationHandler.END


async def off_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MSG_OFF_TOPIC)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(MSG_FAREWELL)
    return ConversationHandler.END


def build_app() -> Application:
    app = Application.builder().token(REPORT_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, start),
        ],
        states={
            ASK_CHECKIN: [CallbackQueryHandler(handle_checkin_choice, pattern=f"^({CB_YES}|{CB_NO})$")],
            ASK_BOOK: [
                CallbackQueryHandler(handle_book_skip, pattern=f"^{CB_SKIP}$"),
                MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_book_text),
            ],
            ASK_TAKEAWAY: [
                CallbackQueryHandler(handle_takeaway_skip, pattern=f"^{CB_SKIP}$"),
                MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_takeaway_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=600,  # 10 minutes of inactivity ends the flow
    )

    app.add_handler(conv)
    # Any other private-chat message that doesn't fit the conversation
    # (e.g. arrives after a timeout) gets the off-topic nudge.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, off_topic))
    return app


if __name__ == "__main__":
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    application = build_app()
    log.info("Report bot starting (log file: %s)", LOG_FILE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
