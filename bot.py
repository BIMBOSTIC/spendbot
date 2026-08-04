import datetime
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from config import TELEGRAM_TOKEN
from handlers.start import build_handler as start_conv
from handlers.expense import handle_text, handle_category_pick
from handlers.undo import undo, redo, build_edit_handler
from handlers.gave import gave_summary
from handlers.borrow import borrow, repaid, balances
from handlers.summary import summary_command
from handlers.trends import trends_command
from handlers.digest import digest_command, send_weekly_digests
from handlers.wallet import build_wallet_handler, winrate_command, trades_command, send_daily_wallet_reports
from handlers.savings import build_saving_handler, saved_command, savings_command
from handlers.lend import lend
from handlers.admin import admin

logging.basicConfig(
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Daily Spend — commands\n\n"
    "Just type expenses naturally:\n"
    "  bread 100, milk 88\n"
    "  gave pedro 500 for data\n"
    "  borrow 2000 from mum\n"
    "  lent pedro 500\n\n"
    "/undo        — remove the last entry\n"
    "/redo        — restore after undo\n"
    "/edit        — pick a recent entry to fix\n"
    "/gave        — per-person GAVE totals\n"
    "/borrow      — log borrowed money  (/borrow 2000 from mum)\n"
    "/repaid      — log a repayment     (/repaid 500 to mum)\n"
    "/balances    — open borrow balances\n"
    "/lend        — log money you lent   (/lend 500 to pedro)\n"
    "/lend repaid — log when they pay back  (/lend repaid 500 from pedro)\n"
    "/lend balances — who owes you\n"
    "/summary     — spending summary  (today/week/last week/month/last month/year/jan/july 2024)\n"
    "               filter by category: /summary food week\n"
    "/trends      — price history chart for an item\n"
    "/digest      — toggle weekly Monday summary  (on/off)\n"
    "/wallet      — manage tracked wallets  (/wallet add) — SOL, ETH, BASE, BNB, ARB, AVAX, RHC\n"
    "/winrate     — trade win rate  (today/week/month/all)\n"
    "/trades      — recent trades   (today/week/month)\n"
    "/saving      — set daily savings target  (/saving set <amount> to update)\n"
    "/saved       — log today's saving  (/saved 500)\n"
    "/savings     — savings summary  (today/week/month/all)\n"
    "/help        — this message"
)


async def help_command(update: Update, _) -> None:
    await update.message.reply_text(HELP_TEXT)


async def error_handler(update: object, context) -> None:
    logger.exception("Unhandled error: %s", context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Something went wrong — please try again.")


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandlers must be registered before catch-all handlers
    app.add_handler(start_conv())
    app.add_handler(build_edit_handler())
    app.add_handler(build_wallet_handler())
    app.add_handler(build_saving_handler())

    app.add_handler(CommandHandler("undo",     undo))
    app.add_handler(CommandHandler("redo",     redo))
    app.add_handler(CommandHandler("lend",     lend))
    app.add_handler(CommandHandler("gave",     gave_summary))
    app.add_handler(CommandHandler("borrow",   borrow))
    app.add_handler(CommandHandler("repaid",   repaid))
    app.add_handler(CommandHandler("balances", balances))
    app.add_handler(CommandHandler("summary",  summary_command))
    app.add_handler(CommandHandler("trends",   trends_command))
    app.add_handler(CommandHandler("digest",   digest_command))
    app.add_handler(CommandHandler("winrate",  winrate_command))
    app.add_handler(CommandHandler("trades",   trades_command))
    app.add_handler(CommandHandler("saved",    saved_command))
    app.add_handler(CommandHandler("savings",  savings_command))
    app.add_handler(CommandHandler("help",     help_command))
    app.add_handler(CommandHandler("admin",    admin))

    app.add_handler(CallbackQueryHandler(handle_category_pick, pattern=r"^cat_pick:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    # Weekly digest — every Monday at 08:00 UTC
    app.job_queue.run_daily(
        send_weekly_digests,
        time=datetime.time(8, 0, 0),
        days=(0,),
    )

    # Daily wallet P&L report — every day at 21:00 UTC
    app.job_queue.run_daily(
        send_daily_wallet_reports,
        time=datetime.time(21, 0, 0),
    )

    logger.info("Bot started — polling for updates...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
