import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db.queries import (
    get_user, get_category_id, get_or_create_category, create_expense, create_gave,
    get_daily_total, get_gave_total_ytd, log_parse_failure,
)
from llm_parser import parse_message
from utils import fmt

logger = logging.getLogger(__name__)

PENDING_KEY    = "pending_expense"
CUSTOM_CAT_KEY = "awaiting_custom_cat"

_VALID_CAT_PICKS = {"FOOD", "TRANSPORT", "BILLS", "CLOTH", "OTHERS", "CUSTOM"}

CATEGORY_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("FOOD",      callback_data="cat_pick:FOOD"),
        InlineKeyboardButton("TRANSPORT", callback_data="cat_pick:TRANSPORT"),
    ],
    [
        InlineKeyboardButton("BILLS",     callback_data="cat_pick:BILLS"),
        InlineKeyboardButton("CLOTH",     callback_data="cat_pick:CLOTH"),
    ],
    [
        InlineKeyboardButton("OTHERS",    callback_data="cat_pick:OTHERS"),
        InlineKeyboardButton("Custom...", callback_data="cat_pick:CUSTOM"),
    ],
])

# Only auto-save for these clearly unambiguous categories; ask for everything else
_AUTO_SAVE_CATS = {"FOOD", "TRANSPORT", "BILLS"}

# Canonical category names for free-text routing
_CATS_LOWER = {"food", "transport", "bills", "cloth", "gave", "borrow", "others"}


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text(
            "You haven't set up yet. Run /start to get started."
        )
        return

    text       = update.message.text.strip()
    text_lower = text.lower()

    # ── Custom category awaiting input ────────────────────────────────────────
    if context.user_data.get(CUSTOM_CAT_KEY):
        cat_name = text.strip().upper()
        if not cat_name:
            await update.message.reply_text("Category name can't be empty — type a name (e.g. HEALTH, FUEL):")
            return  # leave flags intact so next message is still intercepted
        pending = context.user_data.pop(PENDING_KEY, None)
        context.user_data.pop(CUSTOM_CAT_KEY)
        if not pending:
            await update.message.reply_text("Session expired — please retype your expense.")
            return
        cat_id = get_or_create_category(user_row["id"], cat_name)
        await _save_and_confirm(update.message.reply_text, user_row, pending["raw"], pending["parsed"], cat_name, cat_id)
        return

    # ── Plain-text command aliases ────────────────────────────────────────────

    if text_lower == "help":
        from bot import HELP_TEXT
        await update.message.reply_text(HELP_TEXT)
        return

    if text_lower in ("undo", "undo last"):
        from handlers.undo import undo
        await undo(update, context)
        return

    if text_lower in ("redo",):
        from handlers.undo import redo
        await redo(update, context)
        return

    _balance_exact  = {"balance", "balances", "debt", "debts", "what i owe", "what do i owe"}
    _balance_prefix = ("my balance", "debt balance", "borrow balance", "repaid balance", "open balance")
    if text_lower in _balance_exact or any(text_lower.startswith(p) for p in _balance_prefix):
        from handlers.borrow import send_balances
        await send_balances(update.message.reply_text, user_row)
        return

    if text_lower in ("who owes me", "who owes me money"):
        from handlers.lend import send_lend_balances
        await send_lend_balances(update.message.reply_text, user_row)
        return

    # "lend", "lend balances", "lend 500 to pedro", "lend repaid 500 from pedro"
    if text_lower == "lend" or text_lower.startswith("lend "):
        context.args = text.split()[1:]
        from handlers.lend import lend as lend_cmd
        await lend_cmd(update, context)
        return

    if text_lower == "gave":
        from handlers.gave import gave_summary
        await gave_summary(update, context)
        return

    if text_lower in ("savings", "saving", "my savings", "saving status", "savings status"):
        from handlers.savings import send_savings_summary
        await send_savings_summary(update.message.reply_text, user_row, "all")
        return

    if text_lower in ("wallet", "wallets", "my wallet", "my wallets"):
        from handlers.wallet import send_wallet_stats
        await send_wallet_stats(update.message.reply_text, user_row, "all")
        return

    # ── Summary aliases ───────────────────────────────────────────────────────

    _summary_aliases = ("summary", "report", "stats")
    _trends_aliases  = ("trends", "history", "chart", "graph")

    if any(text_lower.startswith(a) for a in _summary_aliases):
        keyword = next(a for a in _summary_aliases if text_lower.startswith(a))
        rest    = text_lower[len(keyword):].strip()
        for prefix in ("on ", "for "):
            if rest.startswith(prefix):
                rest = rest[len(prefix):]

        # Extract category from any position in the rest string
        category   = None
        rest_words = rest.split()
        non_cat    = []
        for w in rest_words:
            if w in _CATS_LOWER:
                category = w.upper()
            else:
                non_cat.append(w)
        period_str = " ".join(non_cat).strip() or "today"

        from handlers.summary import send_summary
        await send_summary(update.message.reply_text, user_row, period_str, category=category)
        return

    if any(text_lower.startswith(a) for a in _trends_aliases):
        keyword = next(a for a in _trends_aliases if text_lower.startswith(a))
        item    = text[len(keyword):].strip()
        if item:
            from handlers.trends import _send_item_trend
            await _send_item_trend(update.message.reply_text, update.message.reply_photo, user_row, item.lower())
        else:
            await update.message.reply_text("Usage: trends <item>\nExample: trends bread")
        return

    # ── Savings log shortcut — "saved 500" / "saving 500" ────────────────────

    _savings_prefixes = ("saved ", "saving ")
    if any(text_lower.startswith(p) for p in _savings_prefixes):
        prefix     = next(p for p in _savings_prefixes if text_lower.startswith(p))
        raw_amount = text[len(prefix):].strip()
        try:
            amount = float(raw_amount.replace(",", "."))
            if amount > 0:
                from handlers.savings import handle_savings_log
                await handle_savings_log(update.message.reply_text, user_row, amount)
                return
        except ValueError:
            pass  # fall through to LLM

    # ── LLM parsing ───────────────────────────────────────────────────────────

    try:
        parsed = await parse_message(text)
    except Exception as exc:
        logger.exception("Parser failed for input: %s", text)
        log_parse_failure(user_row["id"], text, error_reason=f"parser_exception: {exc}")
        await update.message.reply_text(
            "Couldn't read that. Try:\n"
            "• Expense: bread 100  or  haircut 300\n"
            "• Multiple: bread 100, milk 80\n"
            "• Gave: gave pedro 500\n"
            "• Type /help for all commands"
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
        log_parse_failure(user_row["id"], text, error_reason=f"unknown_intent: {intent!r}")
        await _handle_unknown(update, parsed)


async def _handle_unknown(update, parsed: dict) -> None:
    amount = parsed.get("total_amount", 0)
    items  = parsed.get("items", [])

    if items and amount == 0:
        await update.message.reply_text(
            "Got the item but couldn't find an amount.\n"
            f"Try: {items[0]['name']} 300"
        )
        return
    if not items and amount > 0:
        await update.message.reply_text(
            "Got the amount but didn't catch what it's for.\n"
            "Try: haircut 300  or  bread 100"
        )
        return

    await update.message.reply_text(
        "Didn't get that. Try:\n"
        "• Log expense: bread 100\n"
        "• Multiple items: bread 100, milk 80\n"
        "• Gave money: gave pedro 500\n"
        "• Borrowed: borrow 2000 from mum\n"
        "• Type /help for all commands"
    )


async def _handle_expense(update, context, user_row, raw, parsed):
    raw_cat = parsed.get("category")
    cat     = raw_cat.strip().upper() if raw_cat else None  # normalise LLM output
    amount  = parsed.get("total_amount", 0)

    if amount == 0:
        items = parsed.get("items", [])
        hint  = items[0]["name"] if items else "item"
        await update.message.reply_text(
            f"Looks like the amount is missing.\n"
            f"Try: {hint} 300"
        )
        return

    # Ask the user to pick unless it's one of the clearly obvious categories
    if parsed.get("ambiguous") or not cat or cat not in _AUTO_SAVE_CATS:
        context.user_data[PENDING_KEY] = {"parsed": parsed, "raw": raw}
        await update.message.reply_text(
            f"Got {fmt(amount, user_row['currency'])} — what category?",
            reply_markup=CATEGORY_KEYBOARD,
        )
        return

    cat_id = get_category_id(user_row["id"], cat)
    await _save_and_confirm(update.message.reply_text, user_row, raw, parsed, cat, cat_id)


async def _handle_gave(update, user_row, raw, parsed):
    gave = parsed.get("gave")
    if not gave:
        await update.message.reply_text(
            "Couldn't read the GAVE entry.\n"
            "Try: gave pedro 500  or  paid mum 200"
        )
        return

    cat_id    = get_category_id(user_row["id"], "GAVE")
    create_gave(user_row["id"], raw, parsed, cat_id)

    currency  = user_row["currency"]
    recipient = gave["recipient"].title()
    ytd       = get_gave_total_ytd(user_row["id"], gave["recipient"])

    await update.message.reply_text(
        f"Logged {fmt(gave['amount'], currency)} to {recipient} under GAVE\n"
        f"You've given {recipient} {fmt(ytd, currency)} total this year."
    )


async def _save_and_confirm(reply_fn, user_row, raw, parsed, cat_name, cat_id):
    create_expense(user_row["id"], raw, parsed, cat_id)
    daily    = get_daily_total(user_row["id"])
    currency = user_row["currency"]

    items     = parsed.get("items", [])
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

    balance_keywords = ["balance", "owe", "debt", "borrow", "repaid"]
    if any(kw in query_intent for kw in balance_keywords):
        from handlers.borrow import send_balances
        await send_balances(update.message.reply_text, user_row)
        return

    lend_keywords = ["lend", "lent", "who owes me", "owe me"]
    if any(kw in query_intent for kw in lend_keywords):
        from handlers.lend import send_lend_balances
        await send_lend_balances(update.message.reply_text, user_row)
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
    elif "yesterday" in query_intent:
        period = "yesterday"
    elif "last week" in query_intent:
        period = "last week"
    elif "week" in query_intent:
        period = "week"
    elif "last month" in query_intent:
        period = "last month"
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
        "/trends <item> — price history\n"
        "Or ask: 'what's gone up the most?'"
    )


async def handle_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_row = get_user(query.from_user.id)
    if not user_row:
        return

    parts = query.data.split(":", 1)
    cat_name = parts[1].upper() if len(parts) > 1 else ""
    if cat_name not in _VALID_CAT_PICKS:
        return

    if cat_name == "CUSTOM":
        if not context.user_data.get(PENDING_KEY):
            await query.edit_message_text("Session expired — please retype your expense.")
            return
        context.user_data[CUSTOM_CAT_KEY] = True
        await query.edit_message_text(
            "What category should I log this under?\n"
            "Type a name (e.g. HEALTH, FUEL, GYM):"
        )
        return

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
