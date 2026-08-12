from telegram import Update
from telegram.ext import ContextTypes
from db.queries import get_user, clear_user_history


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    args = context.args or []
    if args and args[0].lower() == "confirm":
        ts = clear_user_history(user_row["id"])
        date_str = ts[:10]
        await update.message.reply_text(
            f"History archived as of {date_str}.\n\n"
            "Your data is still in the database — nothing was deleted.\n"
            "Summaries and reports will now start from today.\n\n"
            "To view old data: /summary <date range>  e.g. /summary jan to aug"
        )
    else:
        await update.message.reply_text(
            "This will hide all history logged before now from your summaries and reports.\n"
            "Your data stays in the database — nothing is deleted.\n\n"
            "To confirm, type:\n"
            "/clear confirm"
        )
