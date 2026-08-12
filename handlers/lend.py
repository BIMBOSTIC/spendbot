import logging
import re
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes
from db.queries import get_user, create_borrow_entry, get_lend_balances
from utils import fmt

_ISO_DATE_RE    = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_VALID_LEND_DIR = {"lent", "lent_repaid"}

logger = logging.getLogger(__name__)


def _parse_amount_and_party(args: list[str], preposition: str) -> tuple[float | None, str | None]:
    if not args:
        return None, None
    try:
        amount = float(args[0].replace(",", ""))
    except ValueError:
        return None, None
    if amount <= 0:
        return None, None
    rest = args[1:]
    if rest and rest[0].lower() == preposition:
        rest = rest[1:]
    counterparty = " ".join(rest).strip() or None
    return amount, counterparty


async def lend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    args = context.args or []

    if not args or args[0].lower() == "balances":
        await send_lend_balances(update.message.reply_text, user_row)
        return

    if args[0].lower() in ("repaid", "collected"):
        amount, counterparty = _parse_amount_and_party(args[1:], "from")
        if not amount:
            await update.message.reply_text("Usage: /lend repaid 500 from pedro")
            return
        if not counterparty:
            await update.message.reply_text(f"Include a name — e.g. lend repaid {int(amount)} from pedro")
            return
        await _log_and_confirm(update.message.reply_text, user_row, counterparty, amount, "lent_repaid")
        return

    amount, counterparty = _parse_amount_and_party(args, "to")
    if not amount:
        await update.message.reply_text(
            "Usage:\n"
            "/lend 500 to pedro — you lent money\n"
            "/lend repaid 500 from pedro — they paid you back\n"
            "/lend balances — who owes you"
        )
        return
    if not counterparty:
        await update.message.reply_text(f"Include a name — e.g. lend {int(amount)} to pedro")
        return

    await _log_and_confirm(update.message.reply_text, user_row, counterparty, amount, "lent")


async def _log_and_confirm(reply_fn, user_row: dict, counterparty: str, amount: float, direction: str, entry_date: str | None = None) -> None:
    create_borrow_entry(user_row["id"], counterparty, amount, direction, entry_date=entry_date)

    balances  = get_lend_balances(user_row["id"])
    currency  = user_row["currency"]
    name      = counterparty.title()
    date_note = f" (logged for {entry_date})" if entry_date else ""

    balance_row = next(
        (b for b in balances if b["counterparty"].lower() == counterparty.lower().strip()),
        None,
    )
    outstanding = balance_row["outstanding"] if balance_row else 0.0

    if direction == "lent":
        msg = (
            f"Noted — {name} owes you {fmt(amount, currency)}{date_note}\n"
            f"Total owed by {name}: {fmt(outstanding, currency)}\n"
            f"Use /lend repaid {int(amount)} from {counterparty.lower()} when they pay back."
        )
    else:
        if outstanding > 0:
            msg = (
                f"Logged {fmt(amount, currency)} received from {name}{date_note}\n"
                f"Remaining: {name} still owes you {fmt(outstanding, currency)}"
            )
        else:
            msg = f"Logged {fmt(amount, currency)} received from {name}{date_note}\nAll settled with {name}!"

    await reply_fn(msg)


async def handle_lend_text(reply_fn, user_row: dict, parsed: dict) -> None:
    borrow_data = parsed.get("borrow")
    if not borrow_data:
        await reply_fn("Couldn't parse that. Try: /lend 500 to pedro")
        return
    direction = borrow_data.get("direction", "lent")
    if direction == "collected":
        direction = "lent_repaid"
    if direction not in _VALID_LEND_DIR:
        await reply_fn("Couldn't parse that lend entry. Try: /lend 500 to pedro")
        return

    counterparty = borrow_data.get("counterparty")
    amount       = borrow_data.get("amount", 0)
    if not counterparty:
        if direction == "lent_repaid":
            await reply_fn(f"Include a name — e.g. lend repaid {int(amount)} from pedro")
        else:
            await reply_fn(f"Include a name — e.g. lend {int(amount)} to pedro")
        return

    raw_date   = parsed.get("entry_date") or None
    entry_date = (
        raw_date
        if (raw_date and _ISO_DATE_RE.match(str(raw_date)) and raw_date <= date.today().isoformat())
        else None
    )
    await _log_and_confirm(reply_fn, user_row, counterparty, amount, direction, entry_date=entry_date)


async def send_lend_balances(reply_fn, user_row: dict) -> None:
    rows = get_lend_balances(user_row["id"])
    open_rows = [r for r in rows if r["outstanding"] > 0]

    if not open_rows:
        await reply_fn("No outstanding lends. Use /lend 500 to <name> to log one.")
        return

    currency = user_row["currency"]
    lines = ["Lend balances — who owes you\n" + "─" * 28]
    for row in open_rows[:15]:
        lines.append(
            f"{row['counterparty']}: owes you {fmt(row['outstanding'], currency)}"
            f"  (lent {fmt(row['lent'], currency)}, "
            f"repaid {fmt(row['repaid'], currency)})"
        )
    await reply_fn("\n".join(lines))
