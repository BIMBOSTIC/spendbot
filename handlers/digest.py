import logging
from telegram import Update
from telegram.ext import ContextTypes
from db.queries import get_user, update_digest_setting, get_digest_users

logger = logging.getLogger(__name__)


async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    arg = (context.args[0].lower() if context.args else "").strip()

    if arg == "on":
        update_digest_setting(user_row["id"], True)
        await update.message.reply_text(
            "Weekly digest enabled. I'll send you a spending summary every Monday morning."
        )
    elif arg == "off":
        update_digest_setting(user_row["id"], False)
        await update.message.reply_text("Weekly digest disabled.")
    else:
        status = "ON" if user_row.get("weekly_digest") else "OFF"
        await update.message.reply_text(
            f"Weekly digest is {status}.\n"
            "Use /digest on or /digest off to toggle."
        )


async def send_weekly_digests(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB JobQueue callback — runs every Monday, sends weekly summary to opted-in users."""
    from handlers.summary import send_summary

    users = get_digest_users()
    for user_row in users:
        chat_id = user_row["telegram_id"]

        async def reply_fn(text: str, cid: int = chat_id) -> None:
            await context.bot.send_message(chat_id=cid, text=text)

        try:
            await send_summary(reply_fn, user_row, "week")
        except Exception:
            logger.exception("Weekly digest failed for user %s", user_row.get("id"))
