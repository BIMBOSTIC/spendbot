from datetime import date, datetime, timezone as _utc
from zoneinfo import ZoneInfo

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "TRY": "₺",
    "NGN": "₦",
    "GHS": "₵",
    "KES": "KSh",
    "ZAR": "R",
    "INR": "₹",
    "JPY": "¥",
    "CNY": "¥",
    "CAD": "CA$",
    "AUD": "A$",
    "AED": "د.إ",
}


def _user_tz(user_row: dict) -> ZoneInfo:
    tz_name = (user_row.get("timezone") or "UTC").strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def user_today(user_row: dict) -> date:
    """Return the current date in the user's local timezone."""
    return datetime.now(_user_tz(user_row)).date()


def user_day_bounds_utc(user_row: dict, d: date | None = None) -> tuple[str, str]:
    """Return (start_utc, end_utc) ISO strings for a day in the user's timezone."""
    tz = _user_tz(user_row)
    if d is None:
        d = datetime.now(tz).date()
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz).astimezone(_utc)
    end   = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(_utc)
    return start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")


def fmt(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    try:
        if amount != amount or amount == float("inf") or amount == float("-inf"):
            return f"{symbol}?"
        if amount == int(amount):
            return f"{symbol}{int(amount):,}"
        return f"{symbol}{amount:,.2f}"
    except (TypeError, ValueError, OverflowError):
        return f"{symbol}?"
