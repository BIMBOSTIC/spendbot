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
    return f"{symbol}{int(amount):,}"
