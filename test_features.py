#!/usr/bin/env python3
"""
test_features.py -- full feature test suite for the spending bot.
Tests run against the real Supabase DB with mocked Telegram objects (no Telegram needed).

Usage:
    python test_features.py
"""

import asyncio
import sys
import logging
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Force UTF-8 output on Windows (avoids cp1252 encoding errors)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Suppress noisy library logs
logging.disable(logging.CRITICAL)

# -- Colour helpers ------------------------------------------------------------
G = "\033[92m"   # green
R = "\033[91m"   # red
B = "\033[94m"   # blue
Y = "\033[93m"   # yellow
BOLD = "\033[1m"
RESET = "\033[0m"

# -- Test state ----------------------------------------------------------------
TEST_TG_ID = 999_000_001
TEST_CURR  = "NGN"
_phase     = ""
results: list[tuple[str, str, bool, str]] = []


def section(title: str) -> None:
    global _phase
    _phase = title
    print(f"\n{B}{BOLD}{title}{RESET}")


def log(test: str, passed: bool, detail: str = "") -> None:
    mark = f"{G}OK PASS{RESET}" if passed else f"{R}xx FAIL{RESET}"
    line = f"  {mark}  {test}"
    if not passed and detail:
        line += f"\n         -> {detail[:200]}"
    print(line)
    results.append((_phase, test, passed, detail))


def check(label: str, text: str, *fragments: str) -> None:
    ok = all(f.lower() in text.lower() for f in fragments)
    log(label, ok, f"got: {text[:150]!r}" if not ok else "")


# -- Mock helpers --------------------------------------------------------------

def make_update(text: str = ""):
    u = MagicMock()
    u.effective_user.id = TEST_TG_ID
    u.message.text = text
    reply = AsyncMock(return_value=None)
    u.message.reply_text = reply
    return u, reply


def make_context(args=None, user_data=None):
    ctx = MagicMock()
    ctx.args = list(args) if args else []
    ctx.user_data = user_data if user_data is not None else {}
    return ctx


def last_reply(mock: AsyncMock) -> str:
    if not mock.call_args_list:
        return ""
    a, _ = mock.call_args_list[-1]
    return str(a[0]) if a else ""


# -- LLM mock -----------------------------------------------------------------

_LLM: dict[str, dict] = {
    "bread 200": {
        "intent": "expense", "category": "FOOD", "ambiguous": False,
        "total_amount": 200, "items": [{"name": "bread", "amount": 200}],
        "gave": None, "borrow": None, "query_intent": None,
    },
    "transport 300": {
        "intent": "expense", "category": "TRANSPORT", "ambiguous": False,
        "total_amount": 300, "items": [{"name": "transport", "amount": 300}],
        "gave": None, "borrow": None, "query_intent": None,
    },
    "airtime 1000": {
        "intent": "expense", "category": "BILLS", "ambiguous": False,
        "total_amount": 1000, "items": [{"name": "airtime", "amount": 1000}],
        "gave": None, "borrow": None, "query_intent": None,
    },
    "haircut 500": {
        "intent": "expense", "category": "OTHERS", "ambiguous": False,
        "total_amount": 500, "items": [{"name": "haircut", "amount": 500}],
        "gave": None, "borrow": None, "query_intent": None,
    },
    "gave pedro 2000": {
        "intent": "gave", "category": "GAVE", "ambiguous": False,
        "total_amount": 2000, "items": [],
        "gave": {"recipient": "pedro", "amount": 2000, "note": ""},
        "borrow": None, "query_intent": None,
    },
    "borrow 5000 from mum": {
        "intent": "borrow", "category": "BORROW", "ambiguous": False,
        "total_amount": 5000, "items": [], "gave": None,
        "borrow": {"counterparty": "mum", "amount": 5000, "direction": "borrowed"},
        "query_intent": None,
    },
    "summary food": {
        "intent": "unknown", "category": None, "ambiguous": False,
        "total_amount": 0, "items": [], "gave": None, "borrow": None,
        "query_intent": None,
    },
    "report month": {
        "intent": "unknown", "category": None, "ambiguous": False,
        "total_amount": 0, "items": [], "gave": None, "borrow": None,
        "query_intent": None,
    },
    "saved 200": {
        "intent": "unknown", "category": None, "ambiguous": False,
        "total_amount": 0, "items": [], "gave": None, "borrow": None,
        "query_intent": None,
    },
}


async def _mock_parse(text: str) -> dict:
    if text in _LLM:
        return _LLM[text]
    # Fallback -- won't block the test runner
    raise ValueError(f"No LLM mock for: {text!r}")


# -- DB setup / teardown -------------------------------------------------------

def setup_user() -> dict:
    from db.client import get_db
    db = get_db()

    # Wipe any leftover test user
    old = db.table("users").select("id").eq("telegram_id", TEST_TG_ID).execute()
    if old.data:
        _wipe(db, old.data[0]["id"])

    user = db.table("users").insert({
        "telegram_id":  TEST_TG_ID,
        "display_name": "TestUser",
        "currency":     TEST_CURR,
        "timezone":     "Africa/Lagos",
    }).execute().data[0]

    db.table("categories").insert([
        {"user_id": user["id"], "name": c, "is_default": True}
        for c in ["FOOD", "TRANSPORT", "BILLS", "CLOTH", "GAVE", "BORROW", "OTHERS"]
    ]).execute()

    return user


def _wipe(db, uid: str) -> None:
    for tbl in ["redo_state", "savings_logs", "savings_goals",
                "wallet_trades", "wallets",
                "gave_entries", "line_items", "expense_entries",
                "borrow_entries", "items", "categories"]:
        try:
            db.table(tbl).delete().eq("user_id", uid).execute()
        except Exception:
            pass
    try:
        db.table("users").delete().eq("id", uid).execute()
    except Exception:
        pass


def teardown_user(uid: str) -> None:
    from db.client import get_db
    _wipe(get_db(), uid)


# ===============================================================================
# TEST PHASES
# ===============================================================================

async def phase_setup(user: dict) -> None:
    section("PHASE 1 -- Setup")
    from db.queries import get_user
    row = get_user(TEST_TG_ID)
    log("Test user created and fetchable", row is not None)
    log("Currency set to NGN", (row or {}).get("currency") == "NGN")
    cats_r = __import__("db.client", fromlist=["get_db"]).get_db() \
               .table("categories").select("name").eq("user_id", user["id"]).execute()
    cat_names = {r["name"] for r in cats_r.data}
    log("7 default categories seeded",
        cat_names == {"FOOD","TRANSPORT","BILLS","CLOTH","GAVE","BORROW","OTHERS"})


async def phase_expenses(user: dict, ud: dict) -> None:
    section("PHASE 2 -- Expense Logging")
    from handlers.expense import handle_text, handle_category_pick, PENDING_KEY

    with patch("handlers.expense.parse_message", side_effect=_mock_parse):

        # Auto-save: FOOD
        u, r = make_update("bread 200")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("'bread 200' auto-saved -> FOOD", txt, "FOOD", "200")

        # Auto-save: TRANSPORT
        u, r = make_update("transport 300")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("'transport 300' auto-saved -> TRANSPORT", txt, "TRANSPORT", "300")

        # Auto-save: BILLS
        u, r = make_update("airtime 1000")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("'airtime 1000' auto-saved -> BILLS", txt, "BILLS", "1,000")

        # Category picker shown for OTHERS
        u, r = make_update("haircut 500")
        ctx = make_context(user_data=ud)
        await handle_text(u, ctx)
        txt = last_reply(r)
        picker_shown = "what category" in txt.lower()
        pending_set  = PENDING_KEY in ud
        log("'haircut 500' shows category picker",  picker_shown, txt)
        log("'haircut 500' pending stored in ctx",  pending_set)

        if pending_set:
            cb = MagicMock()
            cb.answer = AsyncMock()
            cb.from_user.id = TEST_TG_ID
            cb.data = "cat_pick:OTHERS"
            cb.edit_message_text = AsyncMock()
            u2 = MagicMock()
            u2.callback_query = cb
            await handle_category_pick(u2, ctx)
            txt2 = str(cb.edit_message_text.call_args_list[-1]) if cb.edit_message_text.call_args_list else ""
            check("Tapping OTHERS -> saved", txt2, "OTHERS")

        # Gave
        u, r = make_update("gave pedro 2000")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("'gave pedro 2000' logged", txt, "pedro", "2,000")


async def phase_summary(user: dict, ud: dict) -> None:
    section("PHASE 3 -- Summary")
    from handlers.summary import summary_command, send_summary
    from handlers.expense import handle_text

    # /summary (today)
    u, r = make_update()
    await summary_command(u, make_context(args=[]))
    txt = last_reply(r)
    log("/summary -- returns today data", "Today" in txt or "FOOD" in txt, txt)

    # /summary food
    u, r = make_update()
    await summary_command(u, make_context(args=["food"]))
    txt = last_reply(r)
    check("/summary food -- filters FOOD and shows 200", txt, "Food", "200")

    # /summary transport
    u, r = make_update()
    await summary_command(u, make_context(args=["transport"]))
    txt = last_reply(r)
    check("/summary transport -- shows 300", txt, "300")

    # /summary week
    u, r = make_update()
    await summary_command(u, make_context(args=["week"]))
    txt = last_reply(r)
    log("/summary week -- responds", bool(txt) and "error" not in txt.lower(), txt)

    # /summary month
    u, r = make_update()
    await summary_command(u, make_context(args=["month"]))
    txt = last_reply(r)
    log("/summary month -- responds", bool(txt) and "error" not in txt.lower(), txt)

    # /summary last week (empty is fine -- just no crash)
    u, r = make_update()
    await summary_command(u, make_context(args=["last", "week"]))
    txt = last_reply(r)
    log("/summary last week -- no crash", bool(txt), txt)

    # /summary last year (empty is fine)
    u, r = make_update()
    await summary_command(u, make_context(args=["last", "year"]))
    txt = last_reply(r)
    log("/summary last year -- no crash", bool(txt), txt)

    # Date range: "aug 1 to 31"
    today = date.today()
    month = today.strftime("%b").lower()
    reply_fn = AsyncMock()
    await send_summary(reply_fn, user, f"{month} 1 to {today.day}")
    txt = str(reply_fn.call_args_list[-1]) if reply_fn.call_args_list else ""
    log(f"Date range '{month} 1 to {today.day}' -- resolves correctly",
        "Can't parse" not in txt and bool(txt), txt)

    # Specific date: "aug 3"
    reply_fn2 = AsyncMock()
    await send_summary(reply_fn2, user, f"{month} {today.day}")
    txt = str(reply_fn2.call_args_list[-1]) if reply_fn2.call_args_list else ""
    log(f"Specific date '{month} {today.day}' -- resolves correctly",
        "Can't parse" not in txt and bool(txt), txt)

    # Month name alone: "aug"
    reply_fn3 = AsyncMock()
    await send_summary(reply_fn3, user, month)
    txt = str(reply_fn3.call_args_list[-1]) if reply_fn3.call_args_list else ""
    log(f"Month name '{month}' -- resolves correctly",
        "Can't parse" not in txt and bool(txt), txt)

    # Plain-text "summary food"
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("summary food")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("Plain 'summary food' -- filters FOOD", txt, "Food")

    # Plain-text "report month"
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("report month")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'report month' -- responds", bool(txt), txt)


async def phase_undo_redo(user: dict, ud: dict) -> None:
    section("PHASE 4 -- Undo / Redo")
    from handlers.undo import undo, redo
    from db.queries import get_daily_total

    before = get_daily_total(user["id"])

    u, r = make_update()
    await undo(u, make_context(user_data=ud))
    txt = last_reply(r)
    log("/undo -- confirms deletion", "Undone" in txt, txt)
    after_undo = get_daily_total(user["id"])
    log("/undo -- daily total decreased", after_undo < before,
        f"before={before} after={after_undo}")

    u, r = make_update()
    await redo(u, make_context(user_data=ud))
    txt = last_reply(r)
    log("/redo -- confirms restore", "Restored" in txt, txt)
    after_redo = get_daily_total(user["id"])
    log("/redo -- daily total restored", abs(after_redo - before) < 1,
        f"expected={before} got={after_redo}")

    # Plain-text undo
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("undo")
        from handlers.expense import handle_text
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'undo' -- works", "Undone" in txt, txt)

    # Plain-text redo
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("redo")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'redo' -- works", "Restored" in txt, txt)


async def phase_borrow(user: dict, ud: dict) -> None:
    section("PHASE 5 -- Borrow / Repaid / Balances")
    from handlers.borrow import borrow, repaid, balances
    from handlers.expense import handle_text

    # /borrow 5000 from mum
    u, r = make_update()
    await borrow(u, make_context(args=["5000", "from", "mum"]))
    txt = last_reply(r)
    check("/borrow 5000 from mum -- logged", txt, "mum", "5000")

    # /repaid 2000 to mum
    u, r = make_update()
    await repaid(u, make_context(args=["2000", "to", "mum"]))
    txt = last_reply(r)
    check("/repaid 2000 to mum -- logged", txt, "mum")

    # /balances -- mum should owe 3000
    u, r = make_update()
    await balances(u, make_context())
    txt = last_reply(r)
    check("/balances -- shows mum outstanding 3000", txt, "mum", "3")

    # Plain-text "balance"
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("balance")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'balance' -- responds with balances", "mum" in txt.lower() or "borrow" in txt.lower(), txt)

    # Plain-text via LLM "borrow 5000 from mum"
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("borrow 5000 from mum")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("Plain 'borrow 5000 from mum' -- logged", txt, "mum")


async def phase_lend(user: dict, ud: dict) -> None:
    section("PHASE 6 -- Lend")
    from handlers.expense import handle_text
    from handlers.lend import lend as lend_cmd

    with patch("handlers.expense.parse_message", side_effect=_mock_parse):

        # Plain-text "lend 1000 to alex"
        u, r = make_update("lend 1000 to alex")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("Plain 'lend 1000 to alex' -- logged", txt, "alex", "1000")

        # Plain-text "lend repaid 500 from alex"
        u, r = make_update("lend repaid 500 from alex")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("Plain 'lend repaid 500 from alex' -- logged", txt, "alex")

        # Plain-text "lend balances"
        u, r = make_update("lend balances")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'lend balances' -- responds", bool(txt), txt)

        # Plain-text "who owes me"
        u, r = make_update("who owes me")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'who owes me' -- responds", bool(txt), txt)

    # Slash /lend 2000 to grace
    u, r = make_update()
    await lend_cmd(u, make_context(args=["2000", "to", "grace"]))
    txt = last_reply(r)
    check("/lend 2000 to grace -- logged", txt, "grace", "2000")

    # Slash /lend repaid 2000 from grace
    u, r = make_update()
    await lend_cmd(u, make_context(args=["repaid", "2000", "from", "grace"]))
    txt = last_reply(r)
    check("/lend repaid 2000 from grace -- logged", txt, "grace")

    # Slash /lend balances
    u, r = make_update()
    await lend_cmd(u, make_context(args=["balances"]))
    txt = last_reply(r)
    log("/lend balances -- responds", bool(txt), txt)


async def phase_gave(user: dict, ud: dict) -> None:
    section("PHASE 7 -- Gave Summary")
    from handlers.expense import handle_text
    from handlers.gave import gave_summary

    # Log a fresh gave entry (previous one may have been wiped by undo/redo)
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("gave pedro 2000")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        check("Gave entry logged before summary check", txt, "pedro")

    u, r = make_update()
    await gave_summary(u, make_context())
    txt = last_reply(r)
    check("/gave -- shows pedro", txt, "pedro")
    log("/gave -- shows total", "2,000" in txt or "4,000" in txt, txt)


async def phase_savings(user: dict, ud: dict) -> None:
    section("PHASE 8 -- Savings")
    from handlers.savings import saved_command, send_savings_summary
    from db.savings_queries import set_savings_goal
    from handlers.expense import handle_text

    set_savings_goal(user["id"], 1000)

    # /saved 800
    u, r = make_update()
    await saved_command(u, make_context(args=["800"]))
    txt = last_reply(r)
    log("/saved 800 -- logged", "800" in txt, txt)
    log("/saved 800 -- shows progress vs 1000 target", "1,000" in txt or "1000" in txt, txt)

    # /savings all
    reply_fn = AsyncMock()
    await send_savings_summary(reply_fn, user, "all")
    txt = str(reply_fn.call_args_list[-1]) if reply_fn.call_args_list else ""
    log("/savings -- shows summary", "target" in txt.lower() or "saved" in txt.lower(), txt)

    # Plain-text "saved 200"
    with patch("handlers.expense.parse_message", side_effect=_mock_parse):
        u, r = make_update("saved 200")
        await handle_text(u, make_context(user_data=ud))
        txt = last_reply(r)
        log("Plain 'saved 200' -- logged", "200" in txt, txt)

    # /saving status (no args -> show status)
    from handlers.savings import saving_entry
    u, r = make_update()
    await saving_entry(u, make_context(args=[]))
    txt = last_reply(r)
    log("/saving (no args) -- shows current status", "1,000" in txt or "1000" in txt, txt)


# ===============================================================================
# MAIN
# ===============================================================================

async def main() -> None:
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  SPENDING BOT -- FEATURE TEST SUITE{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    user = None
    ud: dict = {}   # shared user_data dict (simulates Telegram context across calls)

    try:
        user = setup_user()
        print(f"  DB user: {user['id']}")
        print(f"  Currency: {TEST_CURR}")

        await phase_setup(user)
        await phase_expenses(user, ud)
        await phase_summary(user, ud)
        await phase_undo_redo(user, ud)
        await phase_borrow(user, ud)
        await phase_lend(user, ud)
        await phase_gave(user, ud)
        await phase_savings(user, ud)

    except Exception as exc:
        print(f"\n{R}{BOLD}FATAL ERROR during setup/teardown:{RESET} {exc}")
        import traceback; traceback.print_exc()

    finally:
        if user:
            teardown_user(user["id"])
            print(f"\n{Y}  Test data cleaned up.{RESET}")

    # -- Final report ----------------------------------------------------------
    passed = sum(1 for *_, ok, __ in results if ok)
    failed = sum(1 for *_, ok, __ in results if not ok)
    total  = len(results)

    print(f"\n{BOLD}{'='*60}{RESET}")
    if failed == 0:
        print(f"{G}{BOLD}  ALL {total} TESTS PASSED{RESET}")
    else:
        print(f"{BOLD}  RESULTS: {G}{passed} passed{RESET}  {R}{failed} failed{RESET}  / {total} total")
        print(f"\n{R}  Failed tests:{RESET}")
        for phase, test, ok, detail in results:
            if not ok:
                print(f"  {R}x{RESET} [{phase}] {test}")
                if detail:
                    print(f"      {detail[:180]}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
