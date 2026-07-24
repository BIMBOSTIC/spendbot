import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from db.queries import get_user, get_recent_entries, get_last_entry, delete_entry, get_daily_total
from utils import fmt

logger = logging.getLogger(__name__)

PICK_EDIT = 0


# ── /undo ─────────────────────────────────────────────────────────────────────

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    entry = get_last_entry(user_row["id"])
    if not entry:
        await update.message.reply_text("Nothing to undo.")
        return

    currency = user_row["currency"]
    amount = entry["total_amount"]
    raw = entry.get("raw_message") or ""

    delete_entry(entry["id"], user_row["id"])
    daily = get_daily_total(user_row["id"])

    await update.message.reply_text(
        f"Undone: {fmt(amount, currency)}"
        + (f'  ("{raw}")' if raw else "")
        + f"\nToday so far: {fmt(daily, currency)}"
    )


# ── /edit ─────────────────────────────────────────────────────────────────────

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return ConversationHandler.END

    entries = get_recent_entries(user_row["id"], limit=3)
    if not entries:
        await update.message.reply_text("Nothing to edit.")
        return ConversationHandler.END

    currency = user_row["currency"]
    keyboard = [
        [InlineKeyboardButton(
            f"{fmt(e['total_amount'], currency)}  \"{(e.get('raw_message') or '')[:30]}\"",
            callback_data=f"edit_pick:{e['id']}",
        )]
        for e in entries
    ]
    await update.message.reply_text(
        "Which entry do you want to delete and retype?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return PICK_EDIT


async def edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user_row = get_user(query.from_user.id)
    if not user_row:
        await query.edit_message_text("Session expired. Please /start again.")
        return ConversationHandler.END

    entry_id = query.data.split(":")[1]
    delete_entry(entry_id, user_row["id"])

    await query.edit_message_text(
        "Entry deleted. Retype the corrected version now."
    )
    return ConversationHandler.END


async def _cancel_and_handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed something while waiting for edit pick — cancel edit and process it normally."""
    from handlers.expense import handle_text
    await handle_text(update, context)
    return ConversationHandler.END


def build_edit_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("edit", edit_start),
            MessageHandler(filters.Regex(r"(?i)^edit$"), edit_start),
        ],
        states={
            PICK_EDIT: [CallbackQueryHandler(edit_pick, pattern=r"^edit_pick:")],
        },
        fallbacks=[
            CommandHandler("edit", edit_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, _cancel_and_handle),
        ],
    )
