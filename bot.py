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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.error import Forbidden, BadRequest
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

# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

DEFAULT_TZ = "America/New_York"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📖 Study", "📅 Daily", "🔍 Verse"],
        ["⚙️ Settings"],
    ],
    resize_keyboard=True,
)

# Context key used when waiting for a schedule time from the user
AWAITING_SCHEDULE_TIME = "awaiting_schedule_time"


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

def _tz_regions() -> list[str]:
    regions = sorted({tz.split("/")[0] for tz in available_timezones() if "/" in tz})
    if "America" in regions:
        regions.insert(0, regions.pop(regions.index("America")))
    return regions


def _tz_for_region(region: str) -> list[str]:
    return sorted(tz for tz in available_timezones() if tz.startswith(f"{region}/") and tz.count("/") == 1)


def _local_time_to_utc(hour: int, minute: int, tz_name: str) -> tuple[int, int]:
    tz = ZoneInfo(tz_name)
    local_dt = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    return utc_dt.hour, utc_dt.minute


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _get_translation(user_id: int) -> str:
    user = db.get_user(user_id)
    return user["translation"] if user else "KJV"


def _get_timezone(user_id: int) -> str:
    user = db.get_user(user_id)
    return user["timezone"] if user else DEFAULT_TZ


def _schedule_daily_job(app_or_context, user_id: int, chat_id: int, daily_time: str, tz_name: str):
    """Schedule (or reschedule) a user's daily job. Works with app or context."""
    job_queue = app_or_context.job_queue
    h, m = map(int, daily_time.split(":"))
    utc_h, utc_m = _local_time_to_utc(h, m, tz_name)
    jobs = job_queue.get_jobs_by_name(f"daily_{user_id}")
    for job in jobs:
        job.schedule_removal()
    job_queue.run_daily(
        _daily_job,
        time=time(hour=utc_h, minute=utc_m, tzinfo=ZoneInfo("UTC")),
        chat_id=chat_id,
        user_id=user_id,
        name=f"daily_{user_id}",
    )


async def _send_study(context: ContextTypes.DEFAULT_TYPE, chat_id: int, reference: str, translation: str):
    """Fetch passage, generate study, send to chat."""
    status = await context.bot.send_message(chat_id, "📖 Fetching passage and generating your study...")

    async def _edit(text: str):
        try:
            await context.bot.edit_message_text(text, chat_id=chat_id, message_id=status.message_id)
        except Exception:
            pass

    try:
        passage = await fetch_passage(reference, translation)
    except Exception as e:
        await _edit(f"⚠️ Couldn't fetch passage: {e}\n\nTry a reference like: John 3:16 or Romans 8:28-30")
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
        await _edit(f"⚠️ Couldn't generate study: {e}")
        return

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=status.message_id)
    except Exception:
        pass

    await _deliver_study(context.bot, chat_id, passage, study_text)


async def _send_cached_study(context: ContextTypes.DEFAULT_TYPE, chat_id: int, cached: dict) -> bool:
    """Send a pre-cached study. Returns True on success."""
    try:
        passage = await fetch_passage(cached["reference"], cached["translation"])
    except Exception:
        return False

    study_text = cached["study_text"]
    translation = cached["translation"]

    header = f"*{_escape(passage['reference'])}* \\({_escape(_clean_translation_label(translation))}\\)\n\n"
    verse_block = f"_{_escape(passage['text'])}_\n\n"
    divider = "—" * 20 + "\n\n"
    body = _escape(study_text)
    full_message = header + verse_block + _escape(divider) + body

    if len(full_message) <= 4096:
        await context.bot.send_message(chat_id, full_message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=MAIN_KEYBOARD)
    else:
        await context.bot.send_message(chat_id, header + verse_block, parse_mode=ParseMode.MARKDOWN_V2)
        await context.bot.send_message(chat_id, study_text, reply_markup=MAIN_KEYBOARD)
    return True


async def _deliver_study(bot, chat_id: int, passage: dict, study_text: str):
    """Format and send a study passage."""
    header = f"*{_escape(passage['reference'])}* \\({_escape(_clean_translation_label(passage['translation']))}\\)\n\n"
    verse_block = f"_{_escape(passage['text'])}_\n\n"
    divider = "—" * 20 + "\n\n"
    body = _escape(study_text)
    full_message = header + verse_block + _escape(divider) + body

    if len(full_message) <= 4096:
        await bot.send_message(chat_id, full_message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=MAIN_KEYBOARD)
    else:
        await bot.send_message(chat_id, header + verse_block, parse_mode=ParseMode.MARKDOWN_V2)
        await bot.send_message(chat_id, study_text, reply_markup=MAIN_KEYBOARD)


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
        "• /study — Generate a fresh personalized study\n"
        "• /daily — Show today's pre\\-generated daily study\n"
        "• /verse `John 3:16` — Deep\\-dive into any passage\n"
        "• /schedule `8:00` — Set your daily study time\n"
        "• /schedule on/off — Enable or disable daily studies\n"
        "• /settings — View and change your settings\n"
        "• /help — Show this message\n\n"
        f"Translation: *{_escape(translation)}* \\| Timezone: *{_escape(tz)}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=MAIN_KEYBOARD)


async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Always generate a fresh personalized study."""
    user_id = update.effective_user.id
    db.upsert_user(user_id)

    allowed, _ = db.check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text("⏱ You've reached the limit of 20 studies per hour. Please try again later.")
        return

    translation = _get_translation(user_id)
    chat_id = update.effective_chat.id

    status = await update.message.reply_text("🙏 Choosing today's passage...")
    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(None, lambda: generate_daily_passage_and_study(translation, user_id))
    except Exception as e:
        await context.bot.edit_message_text(f"⚠️ Couldn't choose a passage: {e}", chat_id=chat_id, message_id=status.message_id)
        return

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=status.message_id)
    except Exception:
        pass

    await _send_study(context, chat_id, reference, translation)


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's pre-fetched daily study."""
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    translation = _get_translation(user_id)
    chat_id = update.effective_chat.id

    cached = db.get_cached_study(translation)
    if cached:
        sent = await _send_cached_study(context, chat_id, cached)
        if sent:
            return

    # No cache yet — generate one now
    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(
            None, lambda: generate_daily_passage_and_study(translation, user_id=0)
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Couldn't generate today's study: {e}")
        return

    await _send_study(context, chat_id, reference, translation)


async def cmd_verse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)

    allowed, _ = db.check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text("⏱ You've reached the limit of 20 studies per hour. Please try again later.")
        return

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
    await query.edit_message_text(f"✅ Translation set to *{_escape(_clean_translation_label(translation))}*", parse_mode=ParseMode.MARKDOWN_V2)


# ---------------------------------------------------------------------------
# /schedule command
# ---------------------------------------------------------------------------

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage daily study schedule.
    /schedule 8:00     — set time and enable
    /schedule on       — enable using stored time
    /schedule off      — disable
    /schedule timezone — open timezone picker
    """
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    tz_name = _get_timezone(user_id)

    if not context.args:
        user = db.get_user(user_id)
        daily_time = user.get("daily_time") if user else None
        status = f"*{_escape(daily_time)} \\({_escape(tz_name)}\\)*" if daily_time else "*Off*"
        await update.message.reply_text(
            f"📅 Daily study is currently {status}\\.\n\n"
            "Usage:\n"
            "`/schedule 8:00` — set time \\& enable\n"
            "`/schedule on` — re\\-enable\n"
            "`/schedule off` — disable\n"
            "`/schedule timezone` — change timezone",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    arg = context.args[0].lower()

    if arg == "timezone":
        await cmd_timezone(update, context)
        return

    if arg == "off":
        db.upsert_user(user_id, daily_time=None)
        jobs = context.job_queue.get_jobs_by_name(f"daily_{user_id}")
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("🔕 Daily studies turned off.")
        return

    if arg == "on":
        user = db.get_user(user_id)
        daily_time = user.get("daily_time") if user else None
        if not daily_time:
            await update.message.reply_text("⚠️ No time set. Use `/schedule 8:00` to set a time first.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        _schedule_daily_job(context, user_id, update.effective_chat.id, daily_time, tz_name)
        h, m = daily_time.split(":")
        await update.message.reply_text(
            f"✅ Daily study re\\-enabled at *{_escape(daily_time)}* \\({_escape(tz_name)}\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Parse as time
    try:
        parts = arg.replace(".", ":").split(":")
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid time. Use format like: /schedule 8:00 or /schedule 20:30")
        return

    daily_time = f"{hour:02d}:{minute:02d}"
    db.upsert_user(user_id, daily_time=daily_time, chat_id=update.effective_chat.id)
    _schedule_daily_job(context, user_id, update.effective_chat.id, daily_time, tz_name)

    await update.message.reply_text(
        f"✅ Daily study set for *{_escape(daily_time)}* \\({_escape(tz_name)}\\) every day\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


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
# Settings submenu
# ---------------------------------------------------------------------------

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    await _show_settings(update.message.reply_text, user_id)


async def _show_settings(reply_fn, user_id: int):
    user = db.get_user(user_id)
    translation = user["translation"] if user else "KJV"
    daily_time = user["daily_time"] if user else None
    tz_name = user["timezone"] if user else DEFAULT_TZ

    daily_str = f"{daily_time} \\({_escape(tz_name)}\\)" if daily_time else "Off"
    daily_toggle_label = "🔕 Turn Off Daily" if daily_time else "🔔 Turn On Daily"

    text = (
        "*Your Settings*\n\n"
        f"📖 Translation: *{_escape(_clean_translation_label(translation))}*\n"
        f"🌍 Timezone: *{_escape(tz_name)}*\n"
        f"📅 Daily Study: *{daily_str}*"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Change Translation", callback_data="settings_translation")],
        [InlineKeyboardButton("🌍 Change Timezone", callback_data="settings_timezone")],
        [
            InlineKeyboardButton(daily_toggle_label, callback_data="settings_daily_toggle"),
            InlineKeyboardButton("⏰ Change Time", callback_data="settings_daily_time"),
        ],
    ])

    await reply_fn(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)


async def callback_settings_translation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Fetch bibles and show translation picker
    try:
        bibles = await get_available_bibles()
    except Exception as e:
        await query.edit_message_text(f"⚠️ Couldn't fetch translations: {e}")
        return

    current = _get_translation(query.from_user.id)
    keyboard = [
        [InlineKeyboardButton(
            f"{'✅ ' if b.get('abbreviation', b['id']) == current else ''}{_clean_translation_label(b.get('abbreviation', b['id']))} — {b['name']}",
            callback_data=f"set_translation:{b.get('abbreviation', b['id'])}"
        )]
        for b in bibles
    ]
    await query.edit_message_text(
        f"Choose your Bible translation \\(current: *{_escape(_clean_translation_label(current))}*\\):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_settings_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        f"🌍 Your current timezone: *{_escape(current_tz)}*\n\nSelect a region:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_settings_daily_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = db.get_user(user_id)
    daily_time = user.get("daily_time") if user else None

    if daily_time:
        # Turn off
        db.upsert_user(user_id, daily_time=None)
        jobs = context.job_queue.get_jobs_by_name(f"daily_{user_id}")
        for job in jobs:
            job.schedule_removal()
        await query.answer("🔕 Daily studies turned off.", show_alert=False)
    else:
        # Can't turn on without a time — prompt
        await query.answer("Set a time first using 'Change Time'.", show_alert=True)
        return

    await _edit_settings_message(query, user_id)


async def callback_settings_daily_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    context.user_data[AWAITING_SCHEDULE_TIME] = {
        "chat_id": query.message.chat_id,
        "user_id": user_id,
    }
    await query.edit_message_text(
        "⏰ Send me your daily study time \\(e\\.g\\. `8:00` or `20:30`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def handle_schedule_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text time input after tapping 'Change Time' in Settings."""
    if AWAITING_SCHEDULE_TIME not in context.user_data:
        return

    user_id = update.effective_user.id
    tz_name = _get_timezone(user_id)
    text = update.message.text.strip()

    try:
        parts = text.replace(".", ":").split(":")
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid time. Try something like: 8:00 or 20:30")
        return

    daily_time = f"{hour:02d}:{minute:02d}"
    chat_id = update.effective_chat.id
    db.upsert_user(user_id, daily_time=daily_time, chat_id=chat_id)
    _schedule_daily_job(context, user_id, chat_id, daily_time, tz_name)
    del context.user_data[AWAITING_SCHEDULE_TIME]

    await update.message.reply_text(
        f"✅ Daily study set for *{_escape(daily_time)}* \\({_escape(tz_name)}\\) every day\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def _edit_settings_message(query, user_id: int):
    user = db.get_user(user_id)
    translation = user["translation"] if user else "KJV"
    daily_time = user["daily_time"] if user else None
    tz_name = user["timezone"] if user else DEFAULT_TZ

    daily_str = f"{daily_time} \\({_escape(tz_name)}\\)" if daily_time else "Off"
    daily_toggle_label = "🔕 Turn Off Daily" if daily_time else "🔔 Turn On Daily"

    text = (
        "*Your Settings*\n\n"
        f"📖 Translation: *{_escape(_clean_translation_label(translation))}*\n"
        f"🌍 Timezone: *{_escape(tz_name)}*\n"
        f"📅 Daily Study: *{daily_str}*"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Change Translation", callback_data="settings_translation")],
        [InlineKeyboardButton("🌍 Change Timezone", callback_data="settings_timezone")],
        [
            InlineKeyboardButton(daily_toggle_label, callback_data="settings_daily_toggle"),
            InlineKeyboardButton("⏰ Change Time", callback_data="settings_daily_time"),
        ],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Daily delivery job
# ---------------------------------------------------------------------------

async def _daily_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    chat_id = context.job.chat_id
    translation = _get_translation(user_id)

    await context.bot.send_message(chat_id, "🌅 *Good morning\\! Here is today\\'s Bible study\\.*", parse_mode=ParseMode.MARKDOWN_V2)

    cached = db.get_cached_study(translation)
    if cached:
        sent = await _send_cached_study(context, chat_id, cached)
        if sent:
            return

    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(None, lambda: generate_daily_passage_and_study(translation, user_id))
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ Daily study error: {e}")
        return

    await _send_study(context, chat_id, reference, translation)


# ---------------------------------------------------------------------------
# Misc handlers
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def handle_keyboard_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If we're waiting for schedule time input, handle it first
    if AWAITING_SCHEDULE_TIME in context.user_data:
        await handle_schedule_time_input(update, context)
        return

    text = update.message.text
    if text == "📖 Study":
        await cmd_study(update, context)
    elif text == "📅 Daily":
        await cmd_daily(update, context)
    elif text == "🔍 Verse":
        await update.message.reply_text("Send me a reference, e.g.:\n/verse John 3:16")
    elif text == "⚙️ Settings":
        await cmd_settings(update, context)


# ---------------------------------------------------------------------------
# Keyboard refresh on restart
# ---------------------------------------------------------------------------

async def _keyboard_refresh_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    user_id = context.job.user_id
    try:
        await context.bot.send_message(
            chat_id,
            "🙏 Brother John has been updated\\!",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=MAIN_KEYBOARD,
            disable_notification=True,
        )
    except (Forbidden, BadRequest) as e:
        logger.info(f"Purging inactive user {user_id}: {e}")
        db.delete_user(user_id)
    except Exception as e:
        logger.warning(f"Keyboard refresh failed for chat {chat_id}: {e}")


# ---------------------------------------------------------------------------
# Midnight pre-fetch job
# ---------------------------------------------------------------------------

async def _prefetch_job(context: ContextTypes.DEFAULT_TYPE):
    db.clear_study_cache()
    translations = db.get_active_translations()
    logger.info(f"Pre-fetching daily studies for translations: {translations}")

    loop = asyncio.get_event_loop()
    for translation in translations:
        try:
            reference = await loop.run_in_executor(
                None, lambda t=translation: generate_daily_passage_and_study(t, user_id=0)
            )
            passage = await fetch_passage(reference, translation)
            study_text = await loop.run_in_executor(
                None,
                generate_study_from_reference,
                passage["reference"],
                passage["text"],
                passage["translation"],
            )
            db.set_cached_study(translation, passage["reference"], study_text)
            logger.info(f"Pre-fetched study for {translation}: {passage['reference']}")
        except Exception as e:
            logger.error(f"Pre-fetch failed for {translation}: {e}")


# ---------------------------------------------------------------------------
# Restore daily jobs on startup
# ---------------------------------------------------------------------------

async def post_init(app: Application):
    db.init_db()

    await app.bot.set_my_commands([
        BotCommand("study",    "Generate a fresh personalized Bible study"),
        BotCommand("daily",    "Show today's pre-generated daily study"),
        BotCommand("verse",    "Study a specific passage, e.g. /verse John 3:16"),
        BotCommand("schedule", "Manage daily schedule: /schedule 8:00 | on | off | timezone"),
        BotCommand("settings", "View and change your settings"),
        BotCommand("help",     "Show help"),
    ])
    logger.info("Bot commands registered")

    app.job_queue.run_daily(
        _prefetch_job,
        time=time(hour=0, minute=0, tzinfo=ZoneInfo("UTC")),
        name="prefetch_daily",
    )
    logger.info("Scheduled midnight pre-fetch job")

    # Schedule staggered keyboard refresh for all users
    import random
    all_users = db.get_all_users()
    for user in all_users:
        chat_id = user.get("chat_id") or user.get("user_id")
        user_id = user.get("user_id")
        if not chat_id or not user_id:
            continue
        delay = random.uniform(20, 60)
        app.job_queue.run_once(
            _keyboard_refresh_job,
            when=delay,
            chat_id=chat_id,
            user_id=user_id,
            name=f"keyboard_refresh_{chat_id}",
        )
    logger.info(f"Scheduled keyboard refresh for {len(all_users)} users")

    subscribers = db.get_daily_subscribers()
    logger.info(f"Restoring {len(subscribers)} daily jobs")
    for user in subscribers:
        user_id = user["user_id"]
        chat_id = user.get("chat_id", user_id)
        daily_time_str = user["daily_time"]
        tz_name = user.get("timezone") or DEFAULT_TZ
        try:
            _schedule_daily_job(app, user_id, chat_id, daily_time_str, tz_name)
            logger.info(f"Restored daily job for user {user_id} at {daily_time_str} {tz_name}")
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
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("verse", cmd_verse))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("translation", cmd_translation))
    app.add_handler(CommandHandler("timezone", cmd_timezone))

    app.add_handler(CallbackQueryHandler(callback_set_translation, pattern=r"^set_translation:"))
    app.add_handler(CallbackQueryHandler(callback_tz_region, pattern=r"^tz_region:"))
    app.add_handler(CallbackQueryHandler(callback_tz_set, pattern=r"^tz_set:"))
    app.add_handler(CallbackQueryHandler(callback_tz_keep, pattern=r"^tz_keep$"))
    app.add_handler(CallbackQueryHandler(callback_tz_back, pattern=r"^tz_back$"))
    app.add_handler(CallbackQueryHandler(callback_settings_translation, pattern=r"^settings_translation$"))
    app.add_handler(CallbackQueryHandler(callback_settings_timezone, pattern=r"^settings_timezone$"))
    app.add_handler(CallbackQueryHandler(callback_settings_daily_toggle, pattern=r"^settings_daily_toggle$"))
    app.add_handler(CallbackQueryHandler(callback_settings_daily_time, pattern=r"^settings_daily_time$"))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"^(📖 Study|📅 Daily|🔍 Verse|⚙️ Settings)$"),
        handle_keyboard_button,
    ))
    # Catch-all for awaiting schedule time input
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_schedule_time_input,
    ))

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
    if len(sys.argv) < 2 or sys.argv[1] not in ("start", "stop", "run"):
        print("Usage: bot.py start|stop|run")
        raise SystemExit(1)

    if sys.argv[1] == "start":
        cli_start()
    elif sys.argv[1] == "stop":
        cli_stop()
    elif sys.argv[1] == "run":
        main()
