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
