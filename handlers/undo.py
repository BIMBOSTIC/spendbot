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
import json as _json
from db.queries import (
    get_user, get_recent_entries, get_last_entry,
    delete_entry, get_daily_total, restore_expense_entry,
    save_redo_state, pop_redo_state, get_line_items_for_entry,
)
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
    amount   = entry["total_amount"]
    raw      = entry.get("raw_message") or ""

    # Persist redo state in DB so it survives bot restarts
    items = get_line_items_for_entry(entry["id"])
    save_redo_state(user_row["id"], raw, float(amount), entry.get("category_id"), items=items)

    delete_entry(entry["id"], user_row["id"])
    daily = get_daily_total(user_row["id"], after=user_row.get("history_cleared_at"), tz_name=user_row.get("timezone", "UTC"))

    await update.message.reply_text(
        f"Undone: {fmt(amount, currency)}"
        + (f'  ("{raw}")' if raw else "")
        + f"\nToday so far: {fmt(daily, currency)}"
        + "\n\nType /redo or 'redo' to restore it."
    )


# ── /redo ─────────────────────────────────────────────────────────────────────

async def redo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    redo_data = pop_redo_state(user_row["id"])
    if not redo_data:
        await update.message.reply_text("Nothing to redo — only works right after /undo.")
        return

    items_str = redo_data.get("items_json")
    items     = _json.loads(items_str) if items_str else None
    restore_expense_entry(
        user_row["id"],
        redo_data["raw"],
        float(redo_data["total_amount"]),
        redo_data.get("category_id"),
        items=items,
    )
    daily    = get_daily_total(user_row["id"], after=user_row.get("history_cleared_at"), tz_name=user_row.get("timezone", "UTC"))
    currency = user_row["currency"]

    await update.message.reply_text(
        f"Restored: {fmt(float(redo_data['total_amount']), currency)}"
        + (f'  ("{redo_data["raw"]}")' if redo_data.get("raw") else "")
        + f"\nToday so far: {fmt(daily, currency)}"
    )


# ── /edit ─────────────────────────────────────────────────────────────────────

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return ConversationHandler.END

    entries = get_recent_entries(user_row["id"], limit=5)
    if not entries:
        await update.message.reply_text("Nothing to edit.")
        return ConversationHandler.END

    currency = user_row["currency"]

    # Store raw messages keyed by entry id so edit_pick can show them
    context.user_data["_edit_raws"] = {e["id"]: e.get("raw_message") or "" for e in entries}

    keyboard = [
        [InlineKeyboardButton(
            f"{fmt(e['total_amount'], currency)}  \"{(e.get('raw_message') or '')[:30]}\"",
            callback_data=f"edit_pick:{e['id']}",
        )]
        for e in entries
    ]
    await update.message.reply_text(
        "Pick the entry to fix:",
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

    entry_id = query.data.split(":", 1)[1]
    raws     = context.user_data.pop("_edit_raws", {})
    raw      = raws.get(entry_id, "")

    delete_entry(entry_id, user_row["id"])

    reply = "Entry deleted."
    if raw:
        reply += f'\n\nOld: "{raw}"\n\nSend the corrected version:'
    else:
        reply += "\n\nSend the corrected version:"

    await query.edit_message_text(reply)
    return ConversationHandler.END


async def _cancel_and_handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
