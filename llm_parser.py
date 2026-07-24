import json
import logging
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_client = Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM = """\
You are a spending-log parser for a personal finance Telegram bot.
Extract structured data from the user's message and return ONLY valid JSON — no markdown, no explanation.

Intent rules:
  expense  — the user is logging a purchase (default when unclear)
  gave     — money given to a specific person ("gave pedro 500", "paid mum 200")
  borrow   — borrowing or repaying money ("borrow 2000 from mum", "repaid 500 to ali")
  query    — asking about past spending ("how much did I spend this week?")
  unknown  — none of the above

Item name normalisation: shorten to the canonical noun ("loaf of bread" → "bread", "taxi ride" → "taxi").

Category guessing:
  FOOD      — food, drinks, groceries, restaurants, coffee, snacks
  TRANSPORT — taxi, bus, metro, fuel, parking, transport
  BILLS     — utilities, rent, subscriptions, phone, internet
  CLOTH     — clothing, shoes, accessories, laundry
  GAVE      — any gave/paid-to-person intent
  BORROW    — any borrow/repaid intent
  OTHERS    — everything else

Set ambiguous=true ONLY when there is genuinely no reasonable category guess.
If only one item is in the message, items[] still contains that one item.
total_amount must equal the sum of all items[].amount values.

Return this exact JSON schema (null for unused fields):
{
  "intent": "expense"|"gave"|"borrow"|"query"|"unknown",
  "category": "FOOD"|"TRANSPORT"|"BILLS"|"CLOTH"|"GAVE"|"BORROW"|"OTHERS"|null,
  "ambiguous": false,
  "total_amount": <number>,
  "items": [{"name": "<canonical>", "amount": <number>}],
  "gave": {"recipient": "<name>", "amount": <number>, "note": "<note or empty string>"} | null,
  "borrow": {"counterparty": "<name>", "amount": <number>, "direction": "borrowed"|"repaid"} | null,
  "query_intent": "<plain description of what user is asking>" | null
}
"""


def parse_message(text: str) -> dict:
    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    raw = response.content[0].text.strip() if response.content else ""
    if not raw:
        raise ValueError("Empty LLM response")
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)
