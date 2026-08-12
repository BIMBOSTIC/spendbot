import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from db.client import get_db
from utils import CURRENCY_SYMBOLS

logger = logging.getLogger(__name__)

ASK_CURRENCY = 0
ASK_TIMEZONE = 1

DEFAULT_CATEGORIES = ["FOOD", "TRANSPORT", "BILLS", "CLOTH", "GAVE", "BORROW", "OTHERS"]

CURRENCY_OPTIONS = [
    ("NGN (Nigerian Naira)",     "NGN"),
    ("USD (US Dollar)",          "USD"),
    ("GBP (British Pound)",      "GBP"),
    ("EUR (Euro)",               "EUR"),
    ("GHS (Ghanaian Cedi)",      "GHS"),
    ("KES (Kenyan Shilling)",    "KES"),
    ("ZAR (South African Rand)", "ZAR"),
    ("TRY (Turkish Lira)",       "TRY"),
]

TIMEZONE_OPTIONS = [
    ("UTC-8 · US West",    "America/Los_Angeles"),
    ("UTC-5 · US East",    "America/New_York"),
    ("UTC+0 · UK/Ghana",   "UTC"),
    ("UTC+1 · Nigeria",    "Africa/Lagos"),
    ("UTC+2 · S.Africa",   "Africa/Johannesburg"),
    ("UTC+3 · E.Africa",   "Africa/Nairobi"),
    ("UTC+4 · UAE/Gulf",   "Asia/Dubai"),
    ("UTC+5:30 · India",   "Asia/Kolkata"),
    ("UTC+8 · Asia/PH",    "Asia/Shanghai"),
]

_VALID_CURRENCIES = {code for _, code in CURRENCY_OPTIONS}
_VALID_TIMEZONES  = {tz for _, tz in TIMEZONE_OPTIONS}

WELCOME_TEXT = (
    "Welcome to Daily Spend!\n\n"
    "Log expenses by just typing naturally:\n"
    "  bread 100, milk 88, transport 50\n"
    "  gave pedro 500 for data\n\n"
    "What currency should I use?"
)


def _currency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"currency:{code}")]
        for label, code in CURRENCY_OPTIONS
    ])


def _tz_keyboard(prefix: str = "tz_pick") -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(TIMEZONE_OPTIONS), 3):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"{prefix}:{tz}")
            for label, tz in TIMEZONE_OPTIONS[i:i+3]
        ])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_user = update.effective_user
    db = get_db()

    result = db.table("users").select("id, display_name").eq("telegram_id", tg_user.id).execute()
    if result.data:
        name = result.data[0].get("display_name") or tg_user.first_name
        await update.message.reply_text(
            f"Welcome back, {name}!\n"
            "Ready to log. Just type your expenses."
        )
        return ConversationHandler.END

    await update.message.reply_text(WELCOME_TEXT, reply_markup=_currency_keyboard())
    return ASK_CURRENCY


async def set_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts    = query.data.split(":", 1)
    currency = parts[1] if len(parts) > 1 else ""
    if currency not in _VALID_CURRENCIES:
        await query.edit_message_text("Invalid selection — please run /start again.")
        return ConversationHandler.END

    context.user_data["_currency"] = currency
    await query.edit_message_text(
        f"Got it — {currency}.\n\nWhat's your timezone?",
        reply_markup=_tz_keyboard("tz_pick"),
    )
    return ASK_TIMEZONE


async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 1)
    tz    = parts[1] if len(parts) > 1 else ""
    if tz not in _VALID_TIMEZONES:
        await query.edit_message_text("Invalid selection — please run /start again.")
        return ConversationHandler.END

    currency = context.user_data.pop("_currency", "USD")
    tg_user  = query.from_user
    db       = get_db()

    insert_result = (
        db.table("users")
        .upsert(
            {
                "telegram_id":  tg_user.id,
                "display_name": tg_user.first_name,
                "currency":     currency,
                "timezone":     tz,
            },
            on_conflict="telegram_id",
        )
        .execute()
    )

    user_id = insert_result.data[0]["id"]
    _seed_categories(db, user_id)

    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    await query.edit_message_text(
        f"All set! Using {currency} ({symbol}) · {tz}.\n\n"
        "Start logging:\n"
        "  bread 100, milk 88\n"
        "  gave pedro 500\n\n"
        "Commands: /summary  /trends  /undo  /help\n"
        "Change timezone anytime with /timezone"
    )
    return ConversationHandler.END


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Let existing users change their timezone."""
    from db.queries import get_user
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return
    current = user_row.get("timezone") or "UTC"
    await update.message.reply_text(
        f"Current timezone: {current}\n\nPick your timezone:",
        reply_markup=_tz_keyboard("tz_change"),
    )


async def handle_tz_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for /timezone — update an existing user's timezone."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 1)
    tz    = parts[1] if len(parts) > 1 else ""
    if tz not in _VALID_TIMEZONES:
        await query.edit_message_text("Invalid selection.")
        return

    from db.queries import get_user
    user_row = get_user(query.from_user.id)
    if not user_row:
        await query.edit_message_text("Session expired. Run /start.")
        return

    get_db().table("users").update({"timezone": tz}).eq("id", user_row["id"]).execute()
    await query.edit_message_text(f"Timezone updated to {tz}.")


def _seed_categories(db, user_id: str) -> None:
    db.table("categories").insert([
        {"user_id": user_id, "name": cat, "is_default": True}
        for cat in DEFAULT_CATEGORIES
    ]).execute()


async def _remind_currency(update: Update, _: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Please pick a currency first to get started.")
    return ASK_CURRENCY


def build_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_CURRENCY: [
                CallbackQueryHandler(set_currency, pattern=r"^currency:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _remind_currency),
            ],
            ASK_TIMEZONE: [
                CallbackQueryHandler(set_timezone, pattern=r"^tz_pick:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _remind_currency),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )
