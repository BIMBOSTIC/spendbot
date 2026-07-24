import logging
from telegram import Update
from telegram.ext import ContextTypes
from db.queries import get_user, get_gave_summary
from utils import fmt

logger = logging.getLogger(__name__)


async def gave_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    rows = get_gave_summary(user_row["id"])
    if not rows:
        await update.message.reply_text("No GAVE entries yet.")
        return

    currency = user_row["currency"]
    lines = ["GAVE summary\n" + "─" * 22]
    grand_total = sum(r["total"] for r in rows)
    shown, overflow = rows[:15], rows[15:]

    for row in shown:
        month_note = (
            f"  (this month: {fmt(row['this_month'], currency)})"
            if row["this_month"] > 0 else ""
        )
        lines.append(f"{row['recipient']}: {fmt(row['total'], currency)}{month_note}")

    if overflow:
        lines.append(f"... and {len(overflow)} more")

    lines.append("─" * 22)
    lines.append(f"Total: {fmt(grand_total, currency)}")

    await update.message.reply_text("\n".join(lines))
