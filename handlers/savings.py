import logging
import re
from datetime import date, timedelta
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from db.queries import get_user
from db.savings_queries import (
    get_savings_goal, set_savings_goal, log_saving,
    get_today_savings, get_savings_stats,
)
from utils import fmt

logger = logging.getLogger(__name__)

AWAIT_TARGET = 0

_AMOUNT_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def _parse_amount(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    try:
        v = float(text)
        return v if v > 0 else None
    except ValueError:
        return None


def _period_dates(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "today":
        return today, today
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return today.replace(day=1), today
    return date(2020, 1, 1), today  # "all" — savings_stats bounds it to goal_started


# ── /saving — setup / status ──────────────────────────────────────────────────

async def saving_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return ConversationHandler.END

    args = context.args or []

    # /saving set <amount>
    if len(args) >= 2 and args[0].lower() == "set":
        amount = _parse_amount(args[1])
        if not amount:
            await update.message.reply_text("Usage: /saving set 500")
            return ConversationHandler.END
        goal = set_savings_goal(user_row["id"], amount)
        await update.message.reply_text(
            f"Daily savings target updated to {fmt(amount, user_row['currency'])}."
        )
        return ConversationHandler.END

    goal = get_savings_goal(user_row["id"])

    # First time — start setup conversation
    if not goal:
        context.user_data["_saving_user"] = user_row
        await update.message.reply_text(
            "Let's set up your savings tracker.\n\n"
            f"How much do you want to save each day? (in {user_row['currency']})"
        )
        return AWAIT_TARGET

    # Already set — show quick status
    currency = user_row["currency"]
    target = float(goal["daily_target"])
    today_total = get_today_savings(user_row["id"])
    stats = get_savings_stats(user_row["id"])

    progress = f"{fmt(today_total, currency)} saved today"
    if today_total >= target:
        progress += " — target hit!"
    elif today_total > 0:
        progress += f" ({fmt(target - today_total, currency)} to go)"

    streak_line = ""
    if stats and stats["current_streak"] > 0:
        streak_line = f"\nStreak: {stats['current_streak']} day{'s' if stats['current_streak'] != 1 else ''}"

    await update.message.reply_text(
        f"Daily target: {fmt(target, currency)}\n"
        + progress
        + streak_line
        + "\n\nLog with: saved <amount>\n"
        "/savings — full summary  |  /saving set <amount> — change target"
    )
    return ConversationHandler.END


async def receive_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = _parse_amount(update.message.text)
    user_row = context.user_data.pop("_saving_user", None) or get_user(update.effective_user.id)

    if not amount:
        await update.message.reply_text(
            f"Enter a number — how much to save each day in {user_row['currency']}:"
        )
        context.user_data["_saving_user"] = user_row
        return AWAIT_TARGET

    set_savings_goal(user_row["id"], amount)
    await update.message.reply_text(
        f"Done — your daily savings target is {fmt(amount, user_row['currency'])}.\n\n"
        "Log a saving any time:\n"
        "  saved 500\n"
        "  /saved 500\n\n"
        "/savings — see your progress"
    )
    return ConversationHandler.END


# ── /saved <amount> ───────────────────────────────────────────────────────────

async def saved_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /saved <amount>\nExample: /saved 500")
        return

    amount = _parse_amount(context.args[0])
    if not amount:
        await update.message.reply_text("Couldn't read the amount. Try: /saved 500")
        return

    await handle_savings_log(update.message.reply_text, user_row, amount)


async def handle_savings_log(reply_fn, user_row: dict, amount: float) -> None:
    """Log a saving deposit and send a confirmation. Called by both /saved and free-text."""
    goal = get_savings_goal(user_row["id"])
    if not goal:
        await reply_fn(
            f"No savings target set yet.\n"
            "Run /saving to set one first."
        )
        return

    log_saving(user_row["id"], amount)
    today_total = get_today_savings(user_row["id"])
    currency = user_row["currency"]
    target = float(goal["daily_target"])

    # Progress vs target
    if today_total >= target:
        progress = f"Target hit! ({fmt(today_total, currency)} saved today)"
    else:
        remaining = target - today_total
        progress = (
            f"{fmt(today_total, currency)} saved today "
            f"({fmt(remaining, currency)} short of {fmt(target, currency)} target)"
        )
        if today_total < amount:  # first log of the day (amount == today_total)
            pass  # no stack note needed
        else:
            progress += "\nYou can log more today — amounts stack."

    # Streak
    stats = get_savings_stats(user_row["id"])
    streak_line = ""
    if stats and stats["current_streak"] > 0:
        streak_line = f"\nStreak: {stats['current_streak']} day{'s' if stats['current_streak'] != 1 else ''}"

    # Monthly progress
    month_stats = get_savings_stats(
        user_row["id"],
        period_start=date.today().replace(day=1),
        period_end=date.today(),
    )
    month_line = ""
    if month_stats and month_stats["days_saved"] > 0:
        month_line = (
            f"\nThis month: {fmt(month_stats['total_saved'], currency)} "
            f"across {month_stats['days_saved']} day{'s' if month_stats['days_saved'] != 1 else ''}"
        )

    await reply_fn(
        f"{fmt(amount, currency)} saved."
        + f"\n{progress}"
        + streak_line
        + month_line
    )


# ── /savings — full summary ───────────────────────────────────────────────────

async def savings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return
    period = (context.args[0].lower() if context.args else "all")
    if period not in ("today", "week", "month", "all"):
        period = "all"
    await send_savings_summary(update.message.reply_text, user_row, period)


async def send_savings_summary(reply_fn, user_row: dict, period: str = "all") -> None:
    goal = get_savings_goal(user_row["id"])
    if not goal:
        await reply_fn(
            "No savings tracker set up yet.\n"
            "Run /saving to set a daily target."
        )
        return

    currency = user_row["currency"]
    target = float(goal["daily_target"])
    period_start, period_end = _period_dates(period)
    stats = get_savings_stats(user_row["id"], period_start, period_end)

    if not stats or stats["total_days"] == 0:
        await reply_fn("No data in this period yet.")
        return

    period_label = {
        "today": "Today",
        "week":  "This week",
        "month": "This month",
        "all":   f"Since {goal['started_at']}",
    }.get(period, "Summary")

    lines = [f"Savings summary — {period_label}\n"]
    lines.append(f"Target: {fmt(target, currency)}/day")
    lines.append(
        f"Days saved: {stats['days_saved']} / {stats['total_days']}"
        + (f"  ({stats['days_missed']} missed)" if stats["days_missed"] else "")
    )
    lines.append(f"Total saved: {fmt(stats['total_saved'], currency)}")

    if stats["current_streak"] > 0:
        lines.append(f"Current streak: {stats['current_streak']} day{'s' if stats['current_streak'] != 1 else ''}")

    if stats["best_streak"] > 0 and stats["best_streak_start"]:
        lines.append(
            f"Best streak: {stats['best_streak']} days"
            + (f" ({stats['best_streak_start']} to {stats['best_streak_end']})"
               if stats["best_streak_start"] != stats["best_streak_end"] else
               f" ({stats['best_streak_start']})")
        )

    # Pace vs target
    expected = target * stats["total_days"]
    if expected > 0:
        pace_pct = (stats["total_saved"] / expected) * 100
        pace_word = "ahead of" if pace_pct >= 100 else "behind"
        lines.append(
            f"Pace: {fmt(stats['total_saved'], currency)} of {fmt(expected, currency)} expected "
            f"({pace_pct:.0f}% — {pace_word} target)"
        )

    await reply_fn("\n".join(lines))


# ── ConversationHandler builder ───────────────────────────────────────────────

def build_saving_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("saving", saving_entry)],
        states={
            AWAIT_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_target),
            ],
        },
        fallbacks=[CommandHandler("saving", saving_entry)],
    )
