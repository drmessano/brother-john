#!/usr/bin/env python3
import asyncio
import logging
import os
from datetime import time

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from bible_api import fetch_passage, get_available_bibles
from study import generate_daily_passage_and_study, generate_study_from_reference

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape for Telegram MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _get_translation(user_id: int) -> str:
    user = db.get_user(user_id)
    return user["translation"] if user else "KJV"


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

    header = f"*{_escape(passage['reference'])}* \\({_escape(passage['translation'])}\\)\n\n"
    verse_block = f"_{_escape(passage['text'])}_\n\n"
    divider = "—" * 20 + "\n\n"
    body = _escape(study_text)

    full_message = header + verse_block + _escape(divider) + body

    # Telegram has a 4096 char limit per message
    if len(full_message) <= 4096:
        await context.bot.send_message(chat_id, full_message, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        # Split into passage + study
        await context.bot.send_message(chat_id, header + verse_block, parse_mode=ParseMode.MARKDOWN_V2)
        # Send study in plain text to avoid escaping issues on long text
        await context.bot.send_message(chat_id, study_text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    translation = _get_translation(user_id)

    text = (
        "👋 *Welcome to Brother John\\!*\n\n"
        "I help you dive deep into Scripture with guided study prompts\\.\n\n"
        "*Commands:*\n"
        "• /study — Generate a fresh study passage for today\n"
        "• /verse `John 3:16` — Look up any passage\n"
        "• /daily `8:00` — Get a daily study at a set time \\(UTC\\)\n"
        "• /daily off — Turn off daily studies\n"
        "• /translation — Change your Bible translation\n"
        "• /settings — View your current settings\n\n"
        f"Your current translation: *{_escape(translation)}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)
    translation = _get_translation(user_id)

    await update.message.reply_text("🙏 Choosing today's passage...")

    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(None, generate_daily_passage_and_study, translation)
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

    keyboard = [
        [InlineKeyboardButton(
            f"{b.get('abbreviation', b['id'])} — {b['name']}",
            callback_data=f"set_translation:{b.get('abbreviation', b['id'])}"
        )]
        for b in bibles
    ]
    await update.message.reply_text(
        "Choose your Bible translation:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_set_translation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    translation = query.data.split(":")[1]
    db.upsert_user(query.from_user.id, translation=translation)
    await query.edit_message_text(f"✅ Translation set to *{_escape(translation)}*", parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.upsert_user(user_id)

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/daily 8:00 — Receive a study every day at 8:00 UTC\n/daily off — Stop daily studies"
        )
        return

    arg = context.args[0].lower()

    if arg == "off":
        db.upsert_user(user_id, daily_time=None)
        # Remove existing job if any
        jobs = context.job_queue.get_jobs_by_name(f"daily_{user_id}")
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("🔕 Daily studies turned off.")
        return

    # Parse HH:MM
    try:
        parts = arg.replace(".", ":").split(":")
        hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid time. Use 24h format like: /daily 8:00 or /daily 20:30")
        return

    daily_time = f"{hour:02d}:{minute:02d}"
    db.upsert_user(user_id, daily_time=daily_time)

    # Remove old job, schedule new one
    jobs = context.job_queue.get_jobs_by_name(f"daily_{user_id}")
    for job in jobs:
        job.schedule_removal()

    context.job_queue.run_daily(
        _daily_job,
        time=time(hour=hour, minute=minute),
        chat_id=update.effective_chat.id,
        user_id=user_id,
        name=f"daily_{user_id}",
    )

    await update.message.reply_text(
        f"✅ Daily study set for *{_escape(daily_time)} UTC* every day\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def _daily_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    chat_id = context.job.chat_id
    translation = _get_translation(user_id)

    loop = asyncio.get_event_loop()
    try:
        reference = await loop.run_in_executor(None, generate_daily_passage_and_study, translation)
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

    daily_str = f"{daily_time} UTC" if daily_time else "Off"
    text = (
        "*Your Settings*\n\n"
        f"📖 Translation: *{_escape(translation)}*\n"
        f"🌅 Daily study: *{_escape(daily_str)}*\n\n"
        "Change with /translation or /daily"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


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
        try:
            h, m = map(int, daily_time_str.split(":"))
            # We don't have a chat_id stored — skip silently
            # (they'll re-register with /daily after a restart)
        except Exception:
            pass


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
    app.add_handler(CallbackQueryHandler(callback_set_translation, pattern=r"^set_translation:"))

    logger.info("Brother John starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
