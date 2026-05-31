#!/usr/bin/env python3
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo, available_timezones

from dotenv import load_dotenv
load_dotenv("config")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from bible_api import fetch_passage, get_available_bibles, _clean_translation_label
from study import generate_daily_passage_and_study, generate_study_from_reference

from logging.handlers import RotatingFileHandler as _RotatingFileHandler
_LOG_DIR = os.getenv("LOG_DIR", "/var/log/brother-john")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    handlers=[_RotatingFileHandler(
        os.path.join(_LOG_DIR, "brother-john.log"),
        maxBytes=5*1024*1024,
        backupCount=10,
    )],
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DEFAULT_TZ = "America/New_York"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📖 Study", "🔍 Verse"],
        ["⚙️ Settings", "🌐 Translation", "🕐 Timezone"],
        ["📅 Daily On", "📅 Daily Off"],
    ],
    resize_keyboard=True,
)

# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

def _tz_regions() -> list[str]:
    regions = sorted({tz.split("/")[0] for tz in available_timezones() if "/" in tz})
    # Put America first
    if "America" in regions:
        regions.insert(0, regions.pop(regions.index("America")))
    return regions


def _tz_for_region(region: str) -> list[str]:
    return sorted(tz for tz in available_timezones() if tz.startswith(f"{region}/") and tz.count("/") == 1)


def _local_time_to_utc(hour: int, minute: int, tz_name: str) -> tuple[int, int]:
    """Convert a local HH:MM to UTC HH:MM for today's date."""
    tz = ZoneInfo(tz_name)
    local_dt = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    return utc_dt.hour, utc_dt.minute


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape for Telegram MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _get_translation(user_id: int) -> str:
    user = db.get_user(user_id)
    return user["translation"] if user else "KJV"


def _get_timezone(user_id: int) -> str:
    user = db.get_user(user_id)
    return user["timezone"] if user else DEFAULT_TZ


async def _send_study(context: ContextTypes.DEFAULT_TYPE, chat_id: int, reference: str, translation: str):
    """Fetch passage, generate study, send to chat."""
    await context.bot.send_message(chat_id, "📖 Fetching passage and generating your study... (this takes ~15 seconds)")

    try:
        passage = await fetch_passage(reference, translation)
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Couldn't fetch passage: {e}\n\nTry a reference like: John 3:16 or Romans 8:28-30")
        return

    loop = asyncio.get_event_loop()
    try:
        study_text = await loop.run_in_executor(
            None,
            generate_study_from_reference,
            passage["reference"],
            passage["text"],
            passage["translation"],
        )
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Couldn't generate study: {e}")
        return

    header = f"*{_escape(passage['reference'])}* \\({_escape(_clean_translation_label(passage['translation']))}\\)\n\n"
    verse_block = f"_{_escape(passage['text'])}_\n\n"
    divider = "—" * 20 + "\n\n"
    body = _escape(study_text)

    full_message = header + verse_block + _escape(divider) + body

    if len(full_message) <= 4096:
        await context.bot.send_message(chat_id, full_message, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await context.bot.send_message(chat_id, header + verse_block, parse_mode=ParseMode.MARKDOWN_V2)
        await context.bot.send_message(chat_id, study_text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    translation = _get_translation(user_id)
    tz = _get_timezone(user_id)

    text = (
        "👋 *Welcome to Brother John\\!*\n\n"
        "I help you dive deep into Scripture with guided study prompts\\.\n\n"
        "*Commands:*\n"
        "• /study — Generate a fresh study passage for today\n"
        "• /verse `John 3:16` — Look up any passage\n"
        "• /daily `8:00` — Get a daily study at a set time\n"
        "• /daily off — Turn off daily studies\n"
        "• /translation — Change your Bible translation\n"
        "• /timezone — Set your timezone\n"
        "• /settings — View your current settings\n\n"
        f"Translation: *{_escape(translation)}* \\| Timezone: *{_escape(tz)}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=MAIN_KEYBOARD)


async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    translation = _get_translation(user_id)

    await update.message.reply_text("🙏 Choosing today's passage...")

    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(None, lambda: generate_daily_passage_and_study(translation, user_id))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Couldn't choose a passage: {e}")
        return

    await _send_study(context, update.effective_chat.id, reference, translation)


async def cmd_verse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    translation = _get_translation(user_id)

    if not context.args:
        await update.message.reply_text(
            "Please provide a reference, e.g.:\n/verse John 3:16\n/verse Romans 8:28-30"
        )
        return

    reference = " ".join(context.args)
    await _send_study(context, update.effective_chat.id, reference, translation)


async def cmd_translation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching available translations...")
    try:
        bibles = await get_available_bibles()
    except Exception as e:
        await update.message.reply_text(f"⚠️ Couldn't fetch translations: {e}")
        return

    if not bibles:
        await update.message.reply_text("⚠️ No translations available. Make sure BIBLE_API_KEY is set.")
        return

    current = _get_translation(update.effective_user.id)
    keyboard = [
        [InlineKeyboardButton(
            f"{'✅ ' if b.get('abbreviation', b['id']) == current else ''}{_clean_translation_label(b.get('abbreviation', b['id']))} — {b['name']}",
            callback_data=f"set_translation:{b.get('abbreviation', b['id'])}"
        )]
        for b in bibles
    ]
    await update.message.reply_text(
        f"Choose your Bible translation \\(current: *{_escape(current)}*\\):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_set_translation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    translation = query.data.split(":")[1]
    db.upsert_user(query.from_user.id, translation=translation)
    await query.edit_message_text(f"✅ Translation set to *{_escape(translation)}*", parse_mode=ParseMode.MARKDOWN_V2)


# ---------------------------------------------------------------------------
# Timezone command — two-step drill-down
# ---------------------------------------------------------------------------

async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    current_tz = _get_timezone(user_id)

    regions = _tz_regions()
    keyboard = [
        [InlineKeyboardButton(f"✅ Keep: {current_tz}", callback_data="tz_keep")]
    ] + [
        [InlineKeyboardButton(r, callback_data=f"tz_region:{r}")]
        for r in regions
    ]
    await update.message.reply_text(
        f"🌍 Your current timezone: *{_escape(current_tz)}*\n\nSelect a region or keep your current setting:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_tz_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    region = query.data.split(":", 1)[1]
    current_tz = _get_timezone(query.from_user.id)

    timezones = _tz_for_region(region)

    # Put the user's current tz at the top if it's in this region
    if current_tz in timezones:
        timezones = [current_tz] + [tz for tz in timezones if tz != current_tz]

    keyboard = [
        [InlineKeyboardButton(
            f"{'✅ ' if tz == current_tz else ''}{tz.split('/', 1)[1]}",
            callback_data=f"tz_set:{tz}"
        )]
        for tz in timezones
    ] + [
        [InlineKeyboardButton("« Back to regions", callback_data="tz_back")]
    ]
    await query.edit_message_text(
        f"Select a timezone in *{_escape(region)}*:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_tz_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tz_name = query.data.split(":", 1)[1]
    db.upsert_user(query.from_user.id, timezone=tz_name)
    await query.edit_message_text(
        f"✅ Timezone set to *{_escape(tz_name)}*",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_tz_keep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👍 Timezone unchanged.")


async def callback_tz_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_tz = _get_timezone(query.from_user.id)
    regions = _tz_regions()
    keyboard = [
        [InlineKeyboardButton(f"✅ Keep: {current_tz}", callback_data="tz_keep")]
    ] + [
        [InlineKeyboardButton(r, callback_data=f"tz_region:{r}")]
        for r in regions
    ]
    await query.edit_message_text(
        f"🌍 Your current timezone: *{_escape(current_tz)}*\n\nSelect a region or keep your current setting:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# Daily study scheduling
# ---------------------------------------------------------------------------

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    tz_name = _get_timezone(user_id)

    if not context.args:
        await update.message.reply_text(
            f"Usage:\n/daily 8:00 — Receive a study every day at 8:00 in your timezone \\({_escape(tz_name)}\\)\n/daily off — Stop daily studies",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    arg = context.args[0].lower()

    if arg == "off":
        db.upsert_user(user_id, daily_time=None)
        jobs = context.job_queue.get_jobs_by_name(f"daily_{user_id}")
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("🔕 Daily studies turned off.")
        return

    try:
        parts = arg.replace(".", ":").split(":")
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid time. Use format like: /daily 8:00 or /daily 20:30")
        return

    daily_time = f"{hour:02d}:{minute:02d}"
    db.upsert_user(user_id, daily_time=daily_time)

    utc_hour, utc_minute = _local_time_to_utc(hour, minute, tz_name)

    jobs = context.job_queue.get_jobs_by_name(f"daily_{user_id}")
    for job in jobs:
        job.schedule_removal()

    context.job_queue.run_daily(
        _daily_job,
        time=time(hour=utc_hour, minute=utc_minute, tzinfo=ZoneInfo("UTC")),
        chat_id=update.effective_chat.id,
        user_id=user_id,
        name=f"daily_{user_id}",
    )

    await update.message.reply_text(
        f"✅ Daily study set for *{_escape(daily_time)}* in *{_escape(tz_name)}* every day\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def _daily_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    chat_id = context.job.chat_id
    translation = _get_translation(user_id)

    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(None, lambda: generate_daily_passage_and_study(translation, user_id))
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Daily study error: {e}")
        return

    await context.bot.send_message(chat_id, "🌅 *Good morning\\! Here is today\\'s Bible study\\.*", parse_mode=ParseMode.MARKDOWN_V2)
    await _send_study(context, chat_id, reference, translation)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    user = db.get_user(user_id)
    translation = user["translation"] if user else "KJV"
    daily_time = user["daily_time"] if user else None
    tz_name = user["timezone"] if user else DEFAULT_TZ

    daily_str = f"{daily_time} ({tz_name})" if daily_time else "Off"
    text = (
        "*Your Settings*\n\n"
        f"📖 Translation: *{_escape(translation)}*\n"
        f"🕐 Timezone: *{_escape(tz_name)}*\n"
        f"🌅 Daily study: *{_escape(daily_str)}*\n\n"
        "Change with /translation, /timezone, or /daily"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def handle_keyboard_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📖 Study":
        await cmd_study(update, context)
    elif text == "🔍 Verse":
        await update.message.reply_text("Send me a reference, e.g.:\n/verse John 3:16")
    elif text == "⚙️ Settings":
        await cmd_settings(update, context)
    elif text == "🌐 Translation":
        await cmd_translation(update, context)
    elif text == "🕐 Timezone":
        await cmd_timezone(update, context)
    elif text == "📅 Daily On":
        await update.message.reply_text("Send me a time, e.g.:\n/daily 8:00")
    elif text == "📅 Daily Off":
        context.args = ["off"]
        await cmd_daily(update, context)


# ---------------------------------------------------------------------------
# Restore daily jobs on startup
# ---------------------------------------------------------------------------

async def post_init(app: Application):
    db.init_db()
    subscribers = db.get_daily_subscribers()
    logger.info(f"Restoring {len(subscribers)} daily jobs")
    for user in subscribers:
        user_id = user["user_id"]
        daily_time_str = user["daily_time"]
        tz_name = user["timezone"] or DEFAULT_TZ
        try:
            h, m = map(int, daily_time_str.split(":"))
            utc_h, utc_m = _local_time_to_utc(h, m, tz_name)
            # chat_id not stored — user must re-register with /daily after restart
            logger.info(f"Would restore daily job for user {user_id} at {h:02d}:{m:02d} {tz_name}")
        except Exception as e:
            logger.warning(f"Could not restore daily job for user {user_id}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment")

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("study", cmd_study))
    app.add_handler(CommandHandler("verse", cmd_verse))
    app.add_handler(CommandHandler("translation", cmd_translation))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CallbackQueryHandler(callback_set_translation, pattern=r"^set_translation:"))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^(📖 Study|🔍 Verse|⚙️ Settings|🌐 Translation|🕐 Timezone|📅 Daily On|📅 Daily Off)$"),
        handle_keyboard_button,
    ))
    app.add_handler(CallbackQueryHandler(callback_tz_region, pattern=r"^tz_region:"))
    app.add_handler(CallbackQueryHandler(callback_tz_set, pattern=r"^tz_set:"))
    app.add_handler(CallbackQueryHandler(callback_tz_keep, pattern=r"^tz_keep$"))
    app.add_handler(CallbackQueryHandler(callback_tz_back, pattern=r"^tz_back$"))

    logger.info("Brother John starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


PID_FILE = "/var/run/brother-john.pid"


def daemonize():
    if os.fork() > 0:
        raise SystemExit(0)
    os.setsid()
    if os.fork() > 0:
        raise SystemExit(0)

    devnull_r = open(os.devnull, "r")
    devnull_w = open(os.devnull, "w")
    os.dup2(devnull_r.fileno(), 0)
    os.dup2(devnull_w.fileno(), 1)
    os.dup2(devnull_w.fileno(), 2)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def cli_start():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            print(f"Brother John is already running (PID {pid})")
            raise SystemExit(1)
        except ProcessLookupError:
            os.remove(PID_FILE)

    daemonize()
    main()


def cli_stop():
    if not os.path.exists(PID_FILE):
        print("Brother John is not running")
        raise SystemExit(1)

    with open(PID_FILE) as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        os.remove(PID_FILE)
        print(f"Brother John stopped (PID {pid})")
    except ProcessLookupError:
        os.remove(PID_FILE)
        print("Process not found — PID file removed")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("start", "stop"):
        print("Usage: bot.py start|stop")
        raise SystemExit(1)

    if sys.argv[1] == "start":
        cli_start()
    elif sys.argv[1] == "stop":
        cli_stop()
