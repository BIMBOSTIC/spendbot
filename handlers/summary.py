import calendar
import logging
import re
from datetime import date, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from db.queries import get_user, get_summary, get_item_summary_by_prefix
from utils import fmt

logger = logging.getLogger(__name__)

_RANGE_RE = re.compile(r'(?:from\s+)?(.+?)\s+to\s+(.+)', re.IGNORECASE)

KNOWN_CATEGORIES = {"FOOD", "TRANSPORT", "BILLS", "CLOTH", "GAVE", "BORROW", "OTHERS"}

_DATE_KEYWORDS = {
    "today", "yesterday", "week", "month", "year",
    "last", "this", "previous", "prev", "current",
    "from", "to", "and",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april",
    "june", "july", "august", "september",
    "october", "november", "december",
}

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


def _date_label(d: date, today: date) -> str:
    return f"{d.day} {d.strftime('%b') if d.year == today.year else d.strftime('%b %Y')}"


def _resolve_period(arg: str) -> tuple[str, str, str]:
    """Return (start_iso, end_iso, label) for a natural-language period string."""
    today = date.today()
    a     = arg.lower().strip()

    # Strip "this " / "current " prefix
    for prefix in ("this ", "current "):
        if a.startswith(prefix):
            a = a[len(prefix):]
            break

    # ── Exact keyword matches ─────────────────────────────────────────────────
    if a in ("today", ""):
        return f"{today.isoformat()}T00:00:00", f"{today.isoformat()}T23:59:59", "Today"
    if a == "yesterday":
        d = today - timedelta(days=1)
        return f"{d.isoformat()}T00:00:00", f"{d.isoformat()}T23:59:59", "Yesterday"
    if a == "week":
        start = today - timedelta(days=today.weekday())
        return f"{start.isoformat()}T00:00:00", f"{today.isoformat()}T23:59:59", "This week"
    if a in ("last week", "previous week", "prev week"):
        end   = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return f"{start.isoformat()}T00:00:00", f"{end.isoformat()}T23:59:59", "Last week"
    if a == "month":
        start = today.replace(day=1)
        return f"{start.isoformat()}T00:00:00", f"{today.isoformat()}T23:59:59", "This month"
    if a in ("last month", "previous month", "prev month"):
        first_this = today.replace(day=1)
        end        = first_this - timedelta(days=1)
        start      = end.replace(day=1)
        return f"{start.isoformat()}T00:00:00", f"{end.isoformat()}T23:59:59", end.strftime("%B %Y")
    if a == "year":
        start = today.replace(month=1, day=1)
        return f"{start.isoformat()}T00:00:00", f"{today.isoformat()}T23:59:59", "This year"
    if a in ("last year", "previous year", "prev year"):
        y     = today.year - 1
        start = date(y, 1, 1)
        end   = date(y, 12, 31)
        return f"{start.isoformat()}T00:00:00", f"{end.isoformat()}T23:59:59", str(y)

    # ── 4-digit year: "2024", "2025" ─────────────────────────────────────────
    if re.fullmatch(r"\d{4}", a):
        y     = int(a)
        start = date(y, 1, 1)
        end   = min(date(y, 12, 31), today)
        return f"{start.isoformat()}T00:00:00", f"{end.isoformat()}T23:59:59", str(y)

    # ── Date range: check BEFORE month name so "jul 1 to 31" isn't swallowed ─
    m = _RANGE_RE.match(a)
    if m:
        try:
            from dateutil import parser as dup
            default  = date(today.year, 1, 1)
            start_d  = dup.parse(m.group(1).strip(), default=default, dayfirst=False).date()
            # Use start's month/year as default so "jul 1 to 31" → July 31
            end_default = date(start_d.year, start_d.month, 1)
            end_d    = dup.parse(m.group(2).strip(), default=end_default, dayfirst=False).date()
        except Exception:
            raise ValueError(f"Can't parse date range: {arg!r}")
        if end_d > today:
            end_d = today
        if start_d > end_d:
            raise ValueError("Start date is after end date")
        label = f"{_date_label(start_d, today)} – {_date_label(end_d, today)}"
        return f"{start_d.isoformat()}T00:00:00", f"{end_d.isoformat()}T23:59:59", label

    # ── Month name only, or month + 4-digit year: "jan", "july", "march 2024" ─
    # Does NOT match "jan 10" or "jan 1 to 31" — those fall to the date parser.
    words     = a.split()
    month_num = _MONTH_MAP.get(words[0])
    if month_num and (
        len(words) == 1
        or (len(words) == 2 and words[1].isdigit() and 1900 <= int(words[1]) <= 2100)
    ):
        year = today.year if len(words) == 1 else int(words[1])
        target_start = date(year, month_num, 1)
        if target_start > today:
            year        -= 1
            target_start = date(year, month_num, 1)
        last_day   = calendar.monthrange(year, month_num)[1]
        target_end = min(date(year, month_num, last_day), today)
        label      = target_start.strftime("%B %Y")
        return f"{target_start.isoformat()}T00:00:00", f"{target_end.isoformat()}T23:59:59", label

    # ── Single specific date: "jan 10", "jan 10th", "january 10 2024" ─────────
    try:
        from dateutil import parser as dup
        parsed_dt = dup.parse(arg, default=date(today.year, 1, 1), dayfirst=False)
        d         = parsed_dt.date()
        if d > today:
            d = d.replace(year=d.year - 1)
        return f"{d.isoformat()}T00:00:00", f"{d.isoformat()}T23:59:59", _date_label(d, today)
    except Exception:
        raise ValueError(f"Can't parse date: {arg!r}")


async def send_summary(reply_fn, user_row: dict, period_arg: str, category: str | None = None) -> None:
    item_prefix = None
    resolved = None

    try:
        resolved = _resolve_period(period_arg)
    except ValueError:
        pass

    if resolved is None:
        # Try extracting an item name from the period string — e.g. "breadaz week"
        words = period_arg.lower().split()
        for i, w in enumerate(words):
            if w in _DATE_KEYWORDS or not w or w[0].isdigit():
                continue
            rest = " ".join(words[:i] + words[i + 1:]).strip() or "today"
            try:
                resolved = _resolve_period(rest)
                item_prefix = w
                break
            except ValueError:
                continue

    if resolved is None:
        await reply_fn(
            "Couldn't understand that date.\n"
            "Try: today · yesterday · week · last week · month · last month · year\n"
            "     jan · july · march 2024 · jan 10 · jan 10 to jan 20"
        )
        return

    start, end, label = resolved
    currency = user_row["currency"]

    if item_prefix:
        data = get_item_summary_by_prefix(user_row["id"], item_prefix, start, end)
        header = label + f" · '{item_prefix}'"

        if data["total"] == 0:
            await reply_fn(f"No expenses for '{item_prefix}' in {label.lower()}.")
            return

        lines = [f"{header} — {fmt(data['total'], currency)}\n"]
        for v in data["variants"]:
            pct = (v["total"] / data["total"]) * 100 if data["total"] > 0 else 0
            lines.append(f"  {v['name']:<14} {fmt(v['total'], currency):>10}  ({pct:.0f}%)")
        if len(data["variants"]) > 1:
            lines.append(f"\nFor price history on a specific variant: /trends <name>")

        await reply_fn("\n".join(lines))
        return

    data     = get_summary(user_row["id"], start, end, category=category)
    header   = label + (f" · {category.title()}" if category else "")

    if data["total"] == 0:
        await reply_fn(f"No expenses logged for {header.lower()}.")
        return

    lines = [f"{header} — {fmt(data['total'], currency)}\n"]
    for cat in data["by_category"]:
        pct = (cat["total"] / data["total"]) * 100 if data["total"] > 0 else 0
        lines.append(
            f"  {cat['name']:<12} {fmt(cat['total'], currency):>10}  ({pct:.0f}%)"
        )

    await reply_fn("\n".join(lines))


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    args     = context.args or []
    category = None
    period_parts: list[str] = []

    for arg in args:
        if arg.upper() in KNOWN_CATEGORIES:
            category = arg.upper()
        else:
            period_parts.append(arg)

    period_arg = " ".join(period_parts) if period_parts else "today"
    await send_summary(update.message.reply_text, user_row, period_arg, category=category)
