import json
import logging
import urllib.parse
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from db.queries import (
    get_user, get_item_price_history, search_items, get_price_change_leaders,
)
from utils import fmt

logger = logging.getLogger(__name__)


async def trends_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = get_user(update.effective_user.id)
    if not user_row:
        await update.message.reply_text("Please run /start first.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /trends <item>\nExample: /trends bread")
        return

    query = " ".join(context.args).lower().strip()
    await _send_item_trend(update.message.reply_text, update.message.reply_photo, user_row, query)


async def _send_item_trend(reply_fn, reply_photo_fn, user_row: dict, query: str) -> None:
    history = get_item_price_history(user_row["id"], query)

    if not history:
        matches = search_items(user_row["id"], query)
        if not matches:
            await reply_fn(
                f"No price history for '{query}'.\n"
                "Log it a few times first."
            )
            return
        if len(matches) == 1:
            history = get_item_price_history(user_row["id"], matches[0])
            query = matches[0]
        else:
            opts = "\n".join(f"• {m}" for m in matches[:5])
            await reply_fn(f"Multiple matches for '{query}':\n{opts}\n\nBe more specific.")
            return

    currency = user_row["currency"]

    if len(history) == 1:
        p = history[0]
        await reply_fn(
            f"{query.title()} — only 1 data point\n"
            f"{fmt(p['amount'], currency)} on {p['date']}\n"
            "Log it more times to see a trend."
        )
        return

    first, last = history[0], history[-1]
    change_pct = ((last["amount"] - first["amount"]) / first["amount"]) * 100
    direction = "+" if change_pct >= 0 else ""

    caption = (
        f"{query.title()} — {len(history)} data points\n"
        f"First: {fmt(first['amount'], currency)} ({first['date']})\n"
        f"Latest: {fmt(last['amount'], currency)} ({last['date']})\n"
        f"Change: {direction}{change_pct:.1f}%"
    )

    chart_url = _build_chart_url(query, history)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(chart_url)
            resp.raise_for_status()
            await reply_photo_fn(photo=resp.content, caption=caption)
    except Exception:
        logger.exception("Chart fetch failed for %s", query)
        await reply_fn(caption + "\n\n(Chart unavailable)")


async def show_price_leaders(reply_fn, user_row: dict) -> None:
    leaders = get_price_change_leaders(user_row["id"], top_n=5)
    currency = user_row["currency"]

    if not leaders:
        await reply_fn(
            "Not enough data yet — log the same items a few times to track changes."
        )
        return

    lines = ["Price changes (first vs latest):\n"]
    for item in leaders:
        direction = "+" if item["change_pct"] >= 0 else ""
        lines.append(
            f"• {item['name'].title()}: "
            f"{fmt(item['first_amount'], currency)} → {fmt(item['last_amount'], currency)} "
            f"({direction}{item['change_pct']:.1f}%)"
        )

    await reply_fn("\n".join(lines))


def _build_chart_url(item_name: str, history: list[dict]) -> str:
    labels = [h["date"] for h in history]
    data = [h["amount"] for h in history]

    chart = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": item_name.title(),
                "data": data,
                "borderColor": "rgb(75, 192, 192)",
                "backgroundColor": "rgba(75, 192, 192, 0.1)",
                "tension": 0.3,
                "fill": True,
            }],
        },
        "options": {
            "plugins": {
                "title": {"display": True, "text": f"{item_name.title()} price history"},
            },
            "scales": {"y": {"beginAtZero": False}},
        },
    }

    encoded = urllib.parse.quote(json.dumps(chart, separators=(",", ":")))
    return f"https://quickchart.io/chart?c={encoded}&w=600&h=300&bkg=white"
