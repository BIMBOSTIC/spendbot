import re
import logging
from datetime import date, timedelta
import sentry_setup
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from db.queries import get_user
from db.wallet_queries import (
    add_wallet, get_user_wallets, get_wallet_by_address,
    get_wallet_stats, get_trades, get_all_wallets,
)
from services.wallet_sync import sync_wallet, EVM_CHAINS
from config import HELIUS_API_KEY, MORALIS_API_KEY

logger = logging.getLogger(__name__)

AWAIT_ADDRESS = 0
AWAIT_CHAIN   = 1
AWAIT_LABEL   = 2

_SOL_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Inline keyboard rows for chain selection
_CHAIN_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("ETH",  callback_data="wchain_ETH"),
        InlineKeyboardButton("BASE", callback_data="wchain_BASE"),
        InlineKeyboardButton("BNB",  callback_data="wchain_BNB"),
    ],
    [
        InlineKeyboardButton("ARB",  callback_data="wchain_ARB"),
        InlineKeyboardButton("AVAX", callback_data="wchain_AVAX"),
        InlineKeyboardButton("RHC",  callback_data="wchain_RHC"),
    ],
])

_CHAIN_LABELS = {
    "ETH":  "Ethereum",
    "BASE": "Base",
    "BNB":  "BNB Smart Chain",
    "ARB":  "Arbitrum",
    "AVAX": "Avalanche",
    "RHC":  "Robinhood Chain",
}


def _short(address: str) -> str:
    return f"{address[:4]}...{address[-4:]}"


def _period_bounds(period: str) -> tuple[str, str]:
    today = date.today()
    if period in ("today", ""):
        start = today
        end   = today
    elif period == "week":
        start = today - timedelta(days=today.weekday())
        end   = today
    elif period == "month":
        start = today.replace(day=1)
        end   = today
    else:
        start = date(2020, 1, 1)
        end   = today
    return f"{start}T00:00:00+00:00", f"{end}T23:59:59+00:00"


def _fmt_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}${pnl:.2f}"


# ── /wallet command ───────────────────────────────────────────────────────────

async def wallet_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return ConversationHandler.END

    args = context.args or []
    if args and args[0].lower() == "add":
        context.user_data["_wallet_user"] = user_row
        await update.message.reply_text(
            "Paste your wallet address:\n"
            "• Solana — base58 address\n"
            "• EVM (ETH/BASE/BNB/ARB/AVAX/RHC) — 0x address"
        )
        return AWAIT_ADDRESS

    wallets = get_user_wallets(user_row["id"])
    if not wallets:
        await update.message.reply_text(
            "No wallets tracked yet.\n"
            "Add one with: /wallet add"
        )
        return ConversationHandler.END

    lines = ["Your tracked wallets:\n"]
    for w in wallets:
        chain  = w.get("chain", "SOL")
        label  = w.get("label") or ""
        synced = w["last_synced_at"][:10] if w.get("last_synced_at") else "never"
        lines.append(
            f"• [{chain}] {_short(w['address'])}"
            + (f" — {label}" if label else "")
            + f"  (synced: {synced})"
        )

    lines.append("\nCommands: /winrate  /trades  /wallet add")
    await update.message.reply_text("\n".join(lines))
    return ConversationHandler.END


# ── Conversation states ───────────────────────────────────────────────────────

async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    address  = update.message.text.strip()
    user_row = context.user_data.get("_wallet_user") or get_user(update.effective_user.id)

    if _SOL_ADDR_RE.match(address):
        if not HELIUS_API_KEY:
            await update.message.reply_text(
                "Solana wallet tracking requires a Helius API key.\n"
                "Set HELIUS_API_KEY in your environment and redeploy."
            )
            context.user_data.pop("_wallet_user", None)
            return ConversationHandler.END

        if get_wallet_by_address(user_row["id"], address):
            await update.message.reply_text(
                f"{_short(address)} is already being tracked.\n"
                "Use /wallet to see your wallets."
            )
            context.user_data.pop("_wallet_user", None)
            return ConversationHandler.END

        context.user_data["_pending_address"] = address
        context.user_data["_pending_chain"]   = "SOL"
        await update.message.reply_text(
            "Add a label for this wallet? (e.g. 'main sol')\n"
            "Or type /skip to leave it unlabelled."
        )
        return AWAIT_LABEL

    if _EVM_ADDR_RE.match(address):
        if not MORALIS_API_KEY:
            await update.message.reply_text(
                "EVM wallet tracking requires a Moralis API key.\n"
                "Set MORALIS_API_KEY in your environment and redeploy."
            )
            context.user_data.pop("_wallet_user", None)
            return ConversationHandler.END

        if get_wallet_by_address(user_row["id"], address):
            await update.message.reply_text(
                f"{_short(address)} is already being tracked.\n"
                "Use /wallet to see your wallets."
            )
            context.user_data.pop("_wallet_user", None)
            return ConversationHandler.END

        context.user_data["_pending_address"] = address
        await update.message.reply_text(
            "Which chain is this address on?",
            reply_markup=_CHAIN_KEYBOARD,
        )
        return AWAIT_CHAIN

    await update.message.reply_text(
        "That doesn't look like a valid address.\n"
        "Paste a Solana (base58) or EVM (0x...) address:"
    )
    return AWAIT_ADDRESS


_VALID_CHAINS = set(_CHAIN_LABELS.keys())


async def chain_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chain = query.data.replace("wchain_", "")
    if chain not in _VALID_CHAINS:
        await query.edit_message_text("Invalid selection — please try /wallet add again.")
        return ConversationHandler.END
    label_str = _CHAIN_LABELS.get(chain, chain)
    context.user_data["_pending_chain"] = chain

    await query.edit_message_text(
        f"Chain: {label_str}\n\n"
        "Add a label for this wallet? (e.g. 'my eth main')\n"
        "Or type /skip to leave it unlabelled."
    )
    return AWAIT_LABEL


async def receive_label(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    label = update.message.text.strip() or None
    return await _finish_add(update, context, label)


async def skip_label(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _finish_add(update, context, None)


async def _finish_add(update, context, label: str | None) -> int:
    user_row = context.user_data.pop("_wallet_user", None) or get_user(update.effective_user.id)
    address  = context.user_data.pop("_pending_address", None)
    chain    = context.user_data.pop("_pending_chain", "SOL")

    if not address:
        await update.message.reply_text("Something went wrong. Try /wallet add again.")
        return ConversationHandler.END

    wallet    = add_wallet(user_row["id"], address, label, chain)
    label_str = f" ({label})" if label else ""
    chain_name = _CHAIN_LABELS.get(chain, chain)

    await update.message.reply_text(
        f"Tracking {_short(address)}{label_str} on {chain_name}.\n"
        "Syncing your recent trades now..."
    )

    new_count = await sync_wallet(wallet)

    if new_count > 0:
        await update.message.reply_text(
            f"Found {new_count} swap{'s' if new_count != 1 else ''}.\n"
            "Use /winrate or /trades to see them."
        )
    else:
        await update.message.reply_text(
            "No recent swaps found yet — I'll check again daily.\n"
            "Use /wallet to see your wallets."
        )

    return ConversationHandler.END


# ── /winrate ──────────────────────────────────────────────────────────────────

async def winrate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return
    period = (context.args[0].lower() if context.args else "all")
    if period not in ("today", "week", "month", "all"):
        period = "all"
    await send_wallet_stats(update.message.reply_text, user_row, period)


async def send_wallet_stats(reply_fn, user_row: dict, period: str = "all") -> None:
    wallets = get_user_wallets(user_row["id"])
    if not wallets:
        await reply_fn("No wallets tracked. Add one with /wallet add")
        return

    start, end   = _period_bounds(period)
    period_label = period if period != "all" else "all time"

    for w in wallets:
        await sync_wallet(w)

    blocks = []
    for w in wallets:
        stats      = get_wallet_stats(w["id"], start, end)
        chain      = w.get("chain", "SOL")
        label      = w.get("label") or f"{chain} {_short(w['address'])}"

        if stats["total"] == 0:
            blocks.append(f"{label}: no trades {period_label}")
            continue

        wr    = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "n/a"
        lines = [
            f"{label} — {period_label}",
            f"{stats['total']} trades: {stats['wins']} wins, {stats['losses']} losses",
            f"Win rate: {wr}",
        ]
        if stats["has_pnl_data"]:
            lines.append(f"Total P&L: {_fmt_pnl(stats['total_pnl'])}")
            if stats["biggest_win"] is not None:
                lines.append(f"Best trade: +${stats['biggest_win']:.2f}")
            if stats["biggest_loss"] is not None:
                lines.append(f"Worst trade: ${stats['biggest_loss']:.2f}")
        blocks.append("\n".join(lines))

    await reply_fn("\n\n".join(blocks))


# ── /trades ───────────────────────────────────────────────────────────────────

async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return
    period = (context.args[0].lower() if context.args else "today")
    if period not in ("today", "week", "month"):
        period = "today"
    await send_trades(update.message.reply_text, user_row, period)


async def send_trades(reply_fn, user_row: dict, period: str = "today") -> None:
    wallets = get_user_wallets(user_row["id"])
    if not wallets:
        await reply_fn("No wallets tracked. Add one with /wallet add")
        return

    start, end = _period_bounds(period)
    wallet     = wallets[0]
    chain      = wallet.get("chain", "SOL")
    label      = wallet.get("label") or f"{chain} {_short(wallet['address'])}"

    trades = get_trades(wallet["id"], start, end, limit=15)
    if not trades:
        await reply_fn(
            f"{label}: no trades {period}.\n"
            "Try /trades week or /trades month"
        )
        return

    lines = [f"{label} — trades {period}:\n"]
    for t in trades:
        day  = t["traded_at"][:10]
        pair = f"{t.get('token_in', '?')} → {t.get('token_out', '?')}"
        if t.get("pnl_usd") is not None:
            result = _fmt_pnl(float(t["pnl_usd"]))
        else:
            result = "P&L unknown"
        lines.append(f"• {day}  {pair}  {result}")

    await reply_fn("\n".join(lines))


# ── Daily P&L report job ──────────────────────────────────────────────────────

async def send_daily_wallet_reports(context) -> None:
    wallets = get_all_wallets()
    if not wallets:
        return

    today = date.today().isoformat()
    start = f"{today}T00:00:00+00:00"
    end   = f"{today}T23:59:59+00:00"

    by_user: dict[int, list[dict]] = {}
    for w in wallets:
        user_info = w.get("users") or {}
        tg_id     = user_info.get("telegram_id")
        if tg_id:
            by_user.setdefault(int(tg_id), []).append(w)

    for tg_id, user_wallets in by_user.items():
        try:
            report_blocks = []
            for w in user_wallets:
                await sync_wallet(w)
                stats  = get_wallet_stats(w["id"], start, end)
                chain  = w.get("chain", "SOL")
                label  = w.get("label") or f"{chain} {_short(w['address'])}"

                if stats["total"] == 0:
                    continue

                result_line = ""
                if stats["has_pnl_data"]:
                    outcome     = "profit" if stats["total_pnl"] >= 0 else "loss"
                    result_line = f"\nP&L: {_fmt_pnl(stats['total_pnl'])} ({outcome})"

                report_blocks.append(
                    f"{label}\n"
                    f"{stats['total']} trades — {stats['wins']} wins / {stats['losses']} losses"
                    + result_line
                )

            if report_blocks:
                msg = f"Daily wallet report — {today}\n\n" + "\n\n".join(report_blocks)
                await context.bot.send_message(chat_id=tg_id, text=msg)

        except Exception as exc:
            logger.exception("Daily wallet report failed for tg_id %s", tg_id)
            sentry_setup.capture(exc, user_id=tg_id, tag="daily_wallet_report")


# ── ConversationHandler builder ───────────────────────────────────────────────

def build_wallet_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("wallet", wallet_entry)],
        states={
            AWAIT_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address),
            ],
            AWAIT_CHAIN: [
                CallbackQueryHandler(chain_pick, pattern=r"^wchain_"),
            ],
            AWAIT_LABEL: [
                CommandHandler("skip", skip_label),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_label),
            ],
        },
        fallbacks=[CommandHandler("wallet", wallet_entry)],
    )
