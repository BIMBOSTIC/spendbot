import logging
from telegram import Update
from telegram.ext import ContextTypes
from db.queries import get_user, create_borrow_entry, get_borrow_balances
from utils import fmt

logger = logging.getLogger(__name__)


def _parse_amount_and_party(args: list[str], preposition: str) -> tuple[float | None, str | None]:
    """Parse [amount, preposition?, name...] from command args."""
    if not args:
        return None, None
    try:
        amount = float(args[0].replace(",", ""))
    except ValueError:
        return None, None
    rest = args[1:]
    if rest and rest[0].lower() == preposition:
        rest = rest[1:]
    counterparty = " ".join(rest).strip() or None
    return amount, counterparty


async def borrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    amount, counterparty = _parse_amount_and_party(context.args or [], "from")
    if not amount:
        await update.message.reply_text("Usage: /borrow 2000 from mum")
        return
    if not counterparty:
        await update.message.reply_text(f"Include a name — e.g. borrow {int(amount)} from mum")
        return

    await _log_and_confirm(update.message.reply_text, user_row, counterparty, amount, "borrowed")


async def repaid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    amount, counterparty = _parse_amount_and_party(context.args or [], "to")
    if not amount:
        await update.message.reply_text("Usage: /repaid 4000 to mum")
        return
    if not counterparty:
        await update.message.reply_text(f"Include a name — e.g. repaid {int(amount)} to mum")
        return

    await _log_and_confirm(update.message.reply_text, user_row, counterparty, amount, "repaid")


async def handle_borrow_text(reply_fn, user_row: dict, parsed: dict) -> None:
    """Called from expense handler when parser detects borrow intent in free text."""
    borrow_data = parsed.get("borrow")
    if not borrow_data:
        await reply_fn("Couldn't parse that. Try: /borrow 2000 from mum")
        return
    direction = borrow_data.get("direction", "borrowed")
    if direction in ("lent", "lent_repaid", "collected"):
        from handlers.lend import handle_lend_text
        await handle_lend_text(reply_fn, user_row, parsed)
        return

    counterparty = borrow_data.get("counterparty")
    amount       = borrow_data.get("amount", 0)
    if not counterparty:
        if direction == "repaid":
            await reply_fn(f"Include a name — e.g. repaid {int(amount)} to mum")
        else:
            await reply_fn(f"Include a name — e.g. borrow {int(amount)} from mum")
        return

    await _log_and_confirm(reply_fn, user_row, counterparty, amount, direction)


async def _log_and_confirm(reply_fn, user_row: dict, counterparty: str, amount: float, direction: str) -> None:
    create_borrow_entry(user_row["id"], counterparty, amount, direction)

    all_balances = get_borrow_balances(user_row["id"])
    currency = user_row["currency"]
    name = counterparty.title()

    balance_row = next(
        (b for b in all_balances if b["counterparty"].lower() == counterparty.lower().strip()),
        None,
    )
    balance = balance_row["balance"] if balance_row else 0.0

    if direction == "borrowed":
        msg = (
            f"Noted — you owe {name} {fmt(amount, currency)}\n"
            f"Total owed to {name}: {fmt(balance, currency)}\n"
            f"Use /repaid {int(amount)} to {counterparty.lower()} when you pay some back."
        )
    else:
        if balance > 0:
            msg = (
                f"Logged {fmt(amount, currency)} repaid to {name}\n"
                f"Remaining balance with {name}: {fmt(balance, currency)}"
            )
        else:
            msg = f"Logged {fmt(amount, currency)} repaid to {name}\nYou're all settled with {name}!"

    await reply_fn(msg)


async def send_balances(reply_fn, user_row: dict) -> None:
    rows = get_borrow_balances(user_row["id"])
    open_rows = [r for r in rows if r["balance"] > 0]

    if not open_rows:
        await reply_fn("No open borrow balances.")
        return

    currency = user_row["currency"]
    lines = ["Borrow balances\n" + "─" * 22]
    shown, overflow = open_rows[:15], open_rows[15:]
    for row in shown:
        lines.append(
            f"{row['counterparty']}: you owe {fmt(row['balance'], currency)}"
            f"  (borrowed {fmt(row['borrowed'], currency)}, "
            f"repaid {fmt(row['repaid'], currency)})"
        )
    if overflow:
        lines.append(f"... and {len(overflow)} more")
    await reply_fn("\n".join(lines))


async def balances(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return
    await send_balances(update.message.reply_text, user_row)
