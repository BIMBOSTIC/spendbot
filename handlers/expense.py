import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db.queries import (
    get_user, get_category_id, create_expense, create_gave,
    get_daily_total, get_gave_total_ytd,
)
from llm_parser import parse_message
from utils import fmt

logger = logging.getLogger(__name__)

PENDING_KEY = "pending_expense"

CATEGORY_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("FOOD",      callback_data="cat_pick:FOOD"),
        InlineKeyboardButton("TRANSPORT", callback_data="cat_pick:TRANSPORT"),
    ],
    [
        InlineKeyboardButton("BILLS",     callback_data="cat_pick:BILLS"),
        InlineKeyboardButton("CLOTH",     callback_data="cat_pick:CLOTH"),
    ],
    [InlineKeyboardButton("OTHERS",       callback_data="cat_pick:OTHERS")],
])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    text = update.message.text.strip()
    text_lower = text.lower()

    # Plain-text command aliases (no slash required)
    if text_lower == "help":
        from bot import HELP_TEXT
        await update.message.reply_text(HELP_TEXT)
        return
    if text_lower == "undo":
        from handlers.undo import undo
        await undo(update, context)
        return
    _balance_exact = {"balance", "balances", "debt", "debts", "what i owe", "what do i owe"}
    _balance_prefix = ("my balance", "debt balance", "borrow balance", "repaid balance", "open balance")
    if text_lower in _balance_exact or any(text_lower.startswith(p) for p in _balance_prefix):
        from handlers.borrow import send_balances
        await send_balances(update.message.reply_text, user_row)
        return
    if text_lower == "gave":
        from handlers.gave import gave_summary
        await gave_summary(update, context)
        return
    _summary_aliases = ("summary", "report", "stats")
    _trends_aliases = ("trends", "history", "chart", "graph")
    if any(text_lower.startswith(a) for a in _summary_aliases):
        keyword = next(a for a in _summary_aliases if text_lower.startswith(a))
        rest = text_lower[len(keyword):].strip()
        for prefix in ("on ", "for "):
            if rest.startswith(prefix):
                rest = rest[len(prefix):]
        from handlers.summary import send_summary
        await send_summary(update.message.reply_text, user_row, rest or "today")
        return
    if any(text_lower.startswith(a) for a in _trends_aliases):
        keyword = next(a for a in _trends_aliases if text_lower.startswith(a))
        item = text[len(keyword):].strip()
        if item:
            from handlers.trends import _send_item_trend
            await _send_item_trend(update.message.reply_text, update.message.reply_photo, user_row, item.lower())
        else:
            await update.message.reply_text("Usage: trends <item>\nExample: trends bread")
        return

    # Savings log shortcut — catches "saved 500", "saved500" before hitting LLM
    _savings_aliases = ("saved ", "saving ")
    if any(text_lower.startswith(a) for a in _savings_aliases):
        keyword = next(a for a in _savings_aliases if text_lower.startswith(a))
        raw_amount = text[len(keyword):].strip()
        try:
            amount = float(raw_amount.replace(",", "."))
            if amount > 0:
                from handlers.savings import handle_savings_log
                await handle_savings_log(update.message.reply_text, user_row, amount)
                return
        except ValueError:
            pass  # fall through to LLM

    try:
        parsed = parse_message(text)
    except Exception:
        logger.exception("Parser failed for input: %s", text)
        await update.message.reply_text(
            "Couldn't parse that. Try: bread 100, milk 88"
        )
        return

    intent = parsed.get("intent", "unknown")

    if intent == "expense":
        await _handle_expense(update, context, user_row, text, parsed)
    elif intent == "gave":
        await _handle_gave(update, user_row, text, parsed)
    elif intent == "borrow":
        from handlers.borrow import handle_borrow_text
        await handle_borrow_text(update.message.reply_text, user_row, parsed)
    elif intent == "query":
        await _handle_query(update, user_row, parsed)
    else:
        await update.message.reply_text(
            "Hmm, didn't get that.\n"
            "Try: bread 100, milk 88  or  gave pedro 500"
        )


async def _handle_expense(update, context, user_row, raw, parsed):
    if parsed.get("ambiguous") or not parsed.get("category"):
        context.user_data[PENDING_KEY] = {"parsed": parsed, "raw": raw}
        await update.message.reply_text(
            f"Got {fmt(parsed['total_amount'], user_row['currency'])} — what category?",
            reply_markup=CATEGORY_KEYBOARD,
        )
        return

    cat_id = get_category_id(user_row["id"], parsed["category"])
    await _save_and_confirm(
        update.message.reply_text, user_row, raw, parsed, parsed["category"], cat_id
    )


async def _handle_gave(update, user_row, raw, parsed):
    gave = parsed.get("gave")
    if not gave:
        await update.message.reply_text(
            "Couldn't read the GAVE entry. Try: gave pedro 500"
        )
        return

    cat_id = get_category_id(user_row["id"], "GAVE")
    create_gave(user_row["id"], raw, parsed, cat_id)

    currency = user_row["currency"]
    recipient = gave["recipient"].title()
    ytd = get_gave_total_ytd(user_row["id"], gave["recipient"])

    await update.message.reply_text(
        f"Logged {fmt(gave['amount'], currency)} to {recipient} under GAVE\n"
        f"You've given {recipient} {fmt(ytd, currency)} total this year."
    )


async def _save_and_confirm(reply_fn, user_row, raw, parsed, cat_name, cat_id):
    create_expense(user_row["id"], raw, parsed, cat_id)
    daily = get_daily_total(user_row["id"])
    currency = user_row["currency"]

    items = parsed.get("items", [])
    item_line = ""
    if len(items) > 1:
        item_line = "\n" + "  ".join(
            f"- {it['name']} {fmt(it['amount'], currency)}" for it in items
        )

    await reply_fn(
        f"Logged under {cat_name} — {fmt(parsed['total_amount'], currency)} total"
        + item_line
        + f"\nToday so far: {fmt(daily, currency)}"
    )


async def _handle_query(update, user_row, parsed) -> None:
    from handlers.summary import send_summary
    from handlers.trends import show_price_leaders

    query_intent = (parsed.get("query_intent") or "").lower()

    balance_keywords = ["balance", "owe", "debt", "borrow", "lend", "repaid"]
    if any(kw in query_intent for kw in balance_keywords):
        from handlers.borrow import send_balances
        await send_balances(update.message.reply_text, user_row)
        return

    wallet_keywords = ["win rate", "winrate", "wallet", "trade", "trades", "pnl", "profit loss"]
    if any(kw in query_intent for kw in wallet_keywords):
        from handlers.wallet import send_wallet_stats
        await send_wallet_stats(update.message.reply_text, user_row)
        return

    savings_keywords = ["saving", "saved", "savings", "streak", "missed saving", "deposit"]
    if any(kw in query_intent for kw in savings_keywords):
        from handlers.savings import send_savings_summary
        await send_savings_summary(update.message.reply_text, user_row)
        return

    change_keywords = ["gone up", "increased", "price change", "inflation", "highest", "most expensive"]
    if any(kw in query_intent for kw in change_keywords):
        await show_price_leaders(update.message.reply_text, user_row)
        return

    period = None
    if "today" in query_intent:
        period = "today"
    elif "week" in query_intent:
        period = "week"
    elif "month" in query_intent:
        period = "month"
    elif "year" in query_intent:
        period = "year"

    if period:
        await send_summary(update.message.reply_text, user_row, period)
        return

    await update.message.reply_text(
        "Try:\n"
        "/summary today|week|month|year\n"
        "/trends <item> — price history for a specific item\n"
        "Or ask: 'what's gone up the most?'"
    )


async def handle_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_row = get_user(query.from_user.id)
    if not user_row:
        return

    cat_name = query.data.split(":")[1]
    pending = context.user_data.pop(PENDING_KEY, None)
    if not pending:
        await query.edit_message_text("Session expired — please retype your expense.")
        return

    cat_id = get_category_id(user_row["id"], cat_name)
    await _save_and_confirm(
        query.edit_message_text,
        user_row,
        pending["raw"],
        pending["parsed"],
        cat_name,
        cat_id,
    )
