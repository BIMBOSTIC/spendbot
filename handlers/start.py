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

logger = logging.getLogger(__name__)

ASK_CURRENCY = 0

DEFAULT_CATEGORIES = ["FOOD", "TRANSPORT", "BILLS", "CLOTH", "GAVE", "BORROW", "OTHERS"]

CURRENCY_OPTIONS = [
    ("NGN (Nigerian Naira)",  "NGN"),
    ("USD (US Dollar)",       "USD"),
    ("GBP (British Pound)",   "GBP"),
    ("EUR (Euro)",            "EUR"),
    ("GHS (Ghanaian Cedi)",   "GHS"),
    ("KES (Kenyan Shilling)", "KES"),
    ("ZAR (South African Rand)", "ZAR"),
    ("TRY (Turkish Lira)",    "TRY"),
]

# Import from utils to keep symbols in one place
from utils import CURRENCY_SYMBOLS

WELCOME_TEXT = (
    "Welcome to Daily Spend!\n\n"
    "Log expenses by just typing naturally:\n"
    "  bread 100, milk 88, transport 50\n"
    "  gave pedro 500 for data\n\n"
    "What currency should I use?"
)


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

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"currency:{code}")]
        for label, code in CURRENCY_OPTIONS
    ]
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_CURRENCY


_VALID_CURRENCIES = {code for _, code in CURRENCY_OPTIONS}


async def set_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 1)
    currency = parts[1] if len(parts) > 1 else ""
    if currency not in _VALID_CURRENCIES:
        await query.edit_message_text("Invalid selection — please run /start again.")
        return ConversationHandler.END
    tg_user = query.from_user
    db = get_db()

    insert_result = (
        db.table("users")
        .upsert(
            {
                "telegram_id":  tg_user.id,
                "display_name": tg_user.first_name,
                "currency":     currency,
                "timezone":     "Europe/Istanbul",
            },
            on_conflict="telegram_id",
        )
        .execute()
    )

    user_id = insert_result.data[0]["id"]
    _seed_categories(db, user_id)

    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    await query.edit_message_text(
        f"All set! Using {currency} ({symbol}).\n\n"
        "Start logging:\n"
        "  bread 100, milk 88\n"
        "  gave pedro 500\n\n"
        "Commands: /summary  /trends  /undo  /help"
    )
    return ConversationHandler.END


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
        },
        fallbacks=[CommandHandler("start", start)],
    )
