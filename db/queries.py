from __future__ import annotations
from datetime import date, datetime, timezone
from db.client import get_db


# ── User ─────────────────────────────────────────────────────────────────────

def get_user(telegram_id: int) -> dict | None:
    r = get_db().table("users").select("*").eq("telegram_id", telegram_id).execute()
    return r.data[0] if r.data else None


# ── Categories ────────────────────────────────────────────────────────────────

def get_category_id(user_id: str, name: str) -> str | None:
    r = (
        get_db().table("categories")
        .select("id")
        .eq("user_id", user_id)
        .eq("name", name.strip().upper())
        .execute()
    )
    return r.data[0]["id"] if r.data else None


def get_or_create_category(user_id: str, name: str) -> str:
    db = get_db()
    name_upper = name.strip().upper()
    r = (
        db.table("categories")
        .select("id")
        .eq("user_id", user_id)
        .eq("name", name_upper)
        .execute()
    )
    if r.data:
        return r.data[0]["id"]
    ins = db.table("categories").insert({
        "user_id": user_id,
        "name": name_upper,
        "is_default": False,
    }).execute()
    return ins.data[0]["id"]


# ── Items ─────────────────────────────────────────────────────────────────────

def get_or_create_item(user_id: str, canonical_name: str, category_id: str | None) -> str:
    db = get_db()
    r = (
        db.table("items")
        .select("id")
        .eq("user_id", user_id)
        .eq("canonical_name", canonical_name)
        .execute()
    )
    if r.data:
        return r.data[0]["id"]
    ins = db.table("items").insert({
        "user_id": user_id,
        "canonical_name": canonical_name,
        "category_id": category_id,
    }).execute()
    return ins.data[0]["id"]


# ── Expense entries ───────────────────────────────────────────────────────────

def create_expense(user_id: str, raw: str, parsed: dict, category_id: str | None, entry_date: str | None = None) -> dict:
    db = get_db()
    row: dict = {
        "user_id": user_id,
        "raw_message": raw,
        "category_id": category_id,
        "total_amount": parsed["total_amount"],
    }
    if entry_date:
        row["created_at"] = f"{entry_date}T12:00:00"

    entry = db.table("expense_entries").insert(row).execute().data[0]

    try:
        for item in parsed.get("items", []):
            item_id = get_or_create_item(user_id, item["name"], category_id)
            li_row: dict = {
                "expense_entry_id": entry["id"],
                "item_id": item_id,
                "amount": item["amount"],
            }
            if entry_date:
                li_row["created_at"] = f"{entry_date}T12:00:00"
            db.table("line_items").insert(li_row).execute()
    except Exception:
        try:
            db.table("expense_entries").delete().eq("id", entry["id"]).execute()
        except Exception:
            pass
        raise

    return entry


def get_recent_entries(user_id: str, limit: int = 3) -> list[dict]:
    r = (
        get_db().table("expense_entries")
        .select("id, total_amount, raw_message, created_at, category_id")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return r.data


def restore_expense_entry(user_id: str, raw: str, total_amount: float, category_id: str | None, items: list[dict] | None = None) -> dict:
    db = get_db()
    r = db.table("expense_entries").insert({
        "user_id": user_id,
        "raw_message": raw,
        "category_id": category_id,
        "total_amount": total_amount,
    }).execute()
    entry = r.data[0]

    if items:
        for item in items:
            if not item.get("name"):
                continue
            try:
                item_id = get_or_create_item(user_id, item["name"], category_id)
                db.table("line_items").insert({
                    "expense_entry_id": entry["id"],
                    "item_id": item_id,
                    "amount": item["amount"],
                }).execute()
            except Exception:
                pass  # best-effort — don't fail the whole redo

    return entry


def get_last_entry(user_id: str) -> dict | None:
    entries = get_recent_entries(user_id, limit=1)
    return entries[0] if entries else None


def delete_entry(entry_id: str, user_id: str) -> None:
    get_db().table("expense_entries").delete().eq("id", entry_id).eq("user_id", user_id).execute()


def get_daily_total(user_id: str, target_date: str | None = None, after: str | None = None) -> float:
    d     = target_date or date.today().isoformat()
    lower = f"{d}T00:00:00"
    # Respect history_cleared_at — don't count entries before the clear boundary
    if after:
        cleared_norm = str(after).replace(" ", "T")[:19]
        if cleared_norm > lower:
            lower = cleared_norm
    r = (
        get_db().table("expense_entries")
        .select("total_amount")
        .eq("user_id", user_id)
        .gte("created_at", lower)
        .lte("created_at", f"{d}T23:59:59")
        .execute()
    )
    return float(sum(row["total_amount"] for row in r.data))


# ── Gave entries ──────────────────────────────────────────────────────────────

def create_gave(user_id: str, raw: str, parsed: dict, category_id: str | None, entry_date: str | None = None) -> dict:
    db = get_db()
    gave = parsed["gave"]
    row: dict = {
        "user_id": user_id,
        "raw_message": raw,
        "category_id": category_id,
        "total_amount": gave["amount"],
    }
    if entry_date:
        row["created_at"] = f"{entry_date}T12:00:00"
    entry = db.table("expense_entries").insert(row).execute().data[0]

    try:
        db.table("gave_entries").insert({
            "expense_entry_id": entry["id"],
            "recipient_name": gave["recipient"].lower().strip(),
            "amount": gave["amount"],
        }).execute()
    except Exception:
        try:
            db.table("expense_entries").delete().eq("id", entry["id"]).execute()
        except Exception:
            pass
        raise

    return entry


def get_gave_total_ytd(user_id: str, recipient: str) -> float:
    year_start = f"{date.today().year}-01-01T00:00:00"
    db = get_db()
    entries = (
        db.table("expense_entries")
        .select("id")
        .eq("user_id", user_id)
        .gte("created_at", year_start)
        .execute()
    )
    if not entries.data:
        return 0.0
    ids = [e["id"] for e in entries.data]
    gave = (
        db.table("gave_entries")
        .select("amount")
        .eq("recipient_name", recipient.lower().strip())
        .in_("expense_entry_id", ids)
        .execute()
    )
    return float(sum(row["amount"] for row in gave.data))


def get_gave_summary(user_id: str) -> list[dict]:
    db = get_db()
    # 5-year lookback — keeps memory bounded for long-term users
    cutoff = date(date.today().year - 5, 1, 1).isoformat()
    entries = (
        db.table("expense_entries")
        .select("id, created_at")
        .eq("user_id", user_id)
        .gte("created_at", f"{cutoff}T00:00:00")
        .execute()
    )
    if not entries.data:
        return []

    month_start = date.today().replace(day=1).isoformat()
    entry_dates = {e["id"]: e["created_at"] for e in entries.data}
    ids = list(entry_dates.keys())

    gave = (
        db.table("gave_entries")
        .select("recipient_name, amount, expense_entry_id")
        .in_("expense_entry_id", ids)
        .execute()
    )

    summary: dict[str, dict] = {}
    for row in gave.data:
        name = row["recipient_name"]
        if name not in summary:
            summary[name] = {"recipient": name.title(), "total": 0.0, "this_month": 0.0}
        amount = float(row["amount"])
        summary[name]["total"] += amount
        created_at = entry_dates.get(row["expense_entry_id"], "")
        if created_at[:10] >= month_start:
            summary[name]["this_month"] += amount

    return sorted(summary.values(), key=lambda x: x["total"], reverse=True)


# ── Borrow ledger ─────────────────────────────────────────────────────────────

def create_borrow_entry(user_id: str, counterparty: str, amount: float, direction: str, note: str = "", entry_date: str | None = None) -> dict:
    row: dict = {
        "user_id": user_id,
        "counterparty_name": counterparty.lower().strip(),
        "amount": amount,
        "direction": direction,
        "note": note or None,
    }
    if entry_date:
        row["created_at"] = f"{entry_date}T12:00:00"
    r = get_db().table("borrow_entries").insert(row).execute()
    return r.data[0]


def get_borrow_balances(user_id: str) -> list[dict]:
    r = (
        get_db().table("borrow_entries")
        .select("counterparty_name, amount, direction")
        .eq("user_id", user_id)
        .execute()
    )

    ledger: dict[str, dict] = {}
    for row in r.data:
        name = row["counterparty_name"]
        if name not in ledger:
            ledger[name] = {"counterparty": name.title(), "borrowed": 0.0, "repaid": 0.0}
        if row["direction"] == "borrowed":
            ledger[name]["borrowed"] += float(row["amount"])
        else:
            ledger[name]["repaid"] += float(row["amount"])

    result = [
        {
            "counterparty": data["counterparty"],
            "balance": data["borrowed"] - data["repaid"],
            "borrowed": data["borrowed"],
            "repaid": data["repaid"],
        }
        for data in ledger.values()
    ]
    return sorted(result, key=lambda x: abs(x["balance"]), reverse=True)


def get_gave_total_period(user_id: str, start: str, end: str) -> float:
    db = get_db()
    entries = (
        db.table("expense_entries")
        .select("id")
        .eq("user_id", user_id)
        .gte("created_at", start)
        .lte("created_at", end)
        .execute()
    )
    if not entries.data:
        return 0.0
    ids = [e["id"] for e in entries.data]
    gave = (
        db.table("gave_entries")
        .select("amount")
        .in_("expense_entry_id", ids)
        .execute()
    )
    return float(sum(row["amount"] for row in gave.data))


# ── Lend ledger ──────────────────────────────────────────────────────────────

def get_lend_balances(user_id: str) -> list[dict]:
    r = (
        get_db().table("borrow_entries")
        .select("counterparty_name, amount, direction")
        .eq("user_id", user_id)
        .in_("direction", ["lent", "lent_repaid"])
        .execute()
    )
    ledger: dict[str, dict] = {}
    for row in r.data:
        name = row["counterparty_name"]
        if name not in ledger:
            ledger[name] = {"counterparty": name.title(), "lent": 0.0, "repaid": 0.0}
        if row["direction"] == "lent":
            ledger[name]["lent"] += float(row["amount"])
        else:
            ledger[name]["repaid"] += float(row["amount"])
    result = [
        {
            "counterparty": data["counterparty"],
            "outstanding": data["lent"] - data["repaid"],
            "lent": data["lent"],
            "repaid": data["repaid"],
        }
        for data in ledger.values()
    ]
    return sorted(result, key=lambda x: abs(x["outstanding"]), reverse=True)


# ── Summary ───────────────────────────────────────────────────────────────────

def get_summary(user_id: str, start: str, end: str, category: str | None = None) -> dict:
    db = get_db()
    excluded = {"BORROW"}

    # Resolve category name → category_id for a direct DB-level filter
    category_id: str | None = None
    if category:
        cat_upper = category.upper()
        if cat_upper in excluded:
            return {"total": 0.0, "by_category": []}
        cat_r = (
            db.table("categories")
            .select("id")
            .eq("user_id", user_id)
            .eq("name", cat_upper)
            .execute()
        )
        if not cat_r.data:
            return {"total": 0.0, "by_category": []}
        category_id = cat_r.data[0]["id"]

    q = (
        db.table("expense_entries")
        .select("total_amount, categories(name)")
        .eq("user_id", user_id)
        .gte("created_at", start)
        .lte("created_at", end)
    )
    if category_id:
        q = q.eq("category_id", category_id)

    r = q.execute()

    total = 0.0
    by_cat: dict[str, float] = {}

    for row in r.data:
        cat_row = row.get("categories")
        cat_name = cat_row["name"] if cat_row else "OTHERS"
        if cat_name in excluded:
            continue
        amt = float(row["total_amount"])
        total += amt
        by_cat[cat_name] = by_cat.get(cat_name, 0.0) + amt

    return {
        "total": total,
        "by_category": sorted(
            [{"name": k, "total": v} for k, v in by_cat.items()],
            key=lambda x: x["total"],
            reverse=True,
        ),
    }


# ── Redo state (DB-backed so it survives bot restarts) ───────────────────────

def get_line_items_for_entry(entry_id: str) -> list[dict]:
    r = (
        get_db().table("line_items")
        .select("amount, items(canonical_name)")
        .eq("expense_entry_id", entry_id)
        .execute()
    )
    return [
        {
            "name":   (row.get("items") or {}).get("canonical_name", ""),
            "amount": float(row["amount"]),
        }
        for row in r.data
        if (row.get("items") or {}).get("canonical_name")
    ]


def save_redo_state(user_id: str, raw: str, total_amount: float, category_id: str | None, items: list[dict] | None = None) -> None:
    import json as _json
    payload: dict = {
        "user_id":      user_id,
        "raw":          raw,
        "total_amount": total_amount,
        "category_id":  category_id,
    }
    if items is not None:
        try:
            payload["items_json"] = _json.dumps(items)
            get_db().table("redo_state").upsert(payload).execute()
            return
        except Exception:
            payload.pop("items_json", None)
    get_db().table("redo_state").upsert(payload).execute()


def pop_redo_state(user_id: str) -> dict | None:
    r = get_db().table("redo_state").select("*").eq("user_id", user_id).execute()
    if not r.data:
        return None
    row = r.data[0]
    get_db().table("redo_state").delete().eq("user_id", user_id).execute()
    return row


# ── Admin stats ──────────────────────────────────────────────────────────────

def get_admin_stats(today: str) -> dict:
    db    = get_db()
    start = f"{today}T00:00:00"
    end   = f"{today}T23:59:59"

    entries = (
        db.table("expense_entries")
        .select("id, user_id")
        .gte("created_at", start)
        .lte("created_at", end)
        .execute()
    ).data

    active_users   = len({r["user_id"] for r in entries})
    total_messages = len(entries)

    top_items: list[tuple[str, int]] = []
    if entries:
        entry_ids = [r["id"] for r in entries]
        li = (
            db.table("line_items")
            .select("items(canonical_name)")
            .in_("expense_entry_id", entry_ids)
            .execute()
        ).data
        counts: dict[str, int] = {}
        for row in li:
            name = (row.get("items") or {}).get("canonical_name")
            if name:
                counts[name] = counts.get(name, 0) + 1
        top_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]

    parse_failures = 0
    try:
        pf = (
            db.table("parse_failures")
            .select("id, error_reason")
            .gte("created_at", start)
            .lte("created_at", end)
            .execute()
        )
        parse_failures = len(pf.data)
    except Exception:
        pass

    return {
        "active_users":   active_users,
        "total_messages": total_messages,
        "parse_failures": parse_failures,
        "top_items":      top_items,
    }


def clear_user_history(user_id: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    get_db().table("users").update({"history_cleared_at": now}).eq("id", user_id).execute()
    return now


def get_entries_for_export(user_id: str, start: str, end: str) -> list[dict]:
    db = get_db()
    entries_r = (
        db.table("expense_entries")
        .select("id, total_amount, raw_message, created_at, categories(name)")
        .eq("user_id", user_id)
        .gte("created_at", start)
        .lte("created_at", end)
        .order("created_at")
        .execute()
    )

    entries = []
    for entry in entries_r.data:
        cat_name = (entry.get("categories") or {}).get("name", "OTHERS")
        if cat_name == "BORROW":
            continue
        entries.append({
            "id":           entry["id"],
            "date":         entry["created_at"][:10],
            "category":     cat_name,
            "total_amount": float(entry["total_amount"]),
            "raw_message":  entry["raw_message"],
            "items":        [],
        })

    if not entries:
        return []

    entry_ids = [e["id"] for e in entries]
    li_r = (
        db.table("line_items")
        .select("expense_entry_id, amount, items(canonical_name)")
        .in_("expense_entry_id", entry_ids)
        .execute()
    )
    entry_map = {e["id"]: e for e in entries}
    for li in li_r.data:
        eid = li["expense_entry_id"]
        if eid in entry_map:
            name = (li.get("items") or {}).get("canonical_name", "")
            entry_map[eid]["items"].append({"name": name, "amount": float(li["amount"])})

    return entries


def log_parse_failure(user_id: str | None, raw: str, error_reason: str = "") -> None:
    try:
        get_db().table("parse_failures").insert({
            "user_id":      user_id,
            "raw_message":  raw[:500],
            "error_reason": error_reason[:200],
        }).execute()
    except Exception:
        pass


# ── Digest settings ──────────────────────────────────────────────────────────

def update_digest_setting(user_id: str, enabled: bool) -> None:
    get_db().table("users").update({"weekly_digest": enabled}).eq("id", user_id).execute()


def get_digest_users() -> list[dict]:
    r = get_db().table("users").select("*").eq("weekly_digest", True).execute()
    return r.data


# ── Price history ─────────────────────────────────────────────────────────────

def get_item_price_history(user_id: str, canonical_name: str) -> list[dict]:
    db = get_db()
    item_r = (
        db.table("items")
        .select("id")
        .eq("user_id", user_id)
        .eq("canonical_name", canonical_name)
        .execute()
    )
    if not item_r.data:
        return []
    item_id = item_r.data[0]["id"]

    li = (
        db.table("line_items")
        .select("amount, expense_entries(created_at)")
        .eq("item_id", item_id)
        .execute()
    )

    results = [
        {
            "date": row["expense_entries"]["created_at"][:10],
            "amount": float(row["amount"]),
        }
        for row in li.data
        if row.get("expense_entries")
    ]
    return sorted(results, key=lambda x: x["date"])


def _esc_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_items(user_id: str, query: str) -> list[str]:
    safe = _esc_like(query.lower())
    r = (
        get_db().table("items")
        .select("canonical_name")
        .eq("user_id", user_id)
        .ilike("canonical_name", f"%{safe}%")
        .execute()
    )
    return [row["canonical_name"] for row in r.data]


def search_items_by_prefix(user_id: str, query: str) -> list[str]:
    safe = _esc_like(query.lower())
    r = (
        get_db().table("items")
        .select("canonical_name")
        .eq("user_id", user_id)
        .ilike("canonical_name", f"{safe}%")
        .execute()
    )
    return [row["canonical_name"] for row in r.data]


def get_item_summary_by_prefix(user_id: str, prefix: str, start: str, end: str) -> dict:
    """Aggregate line_items for all items whose canonical_name starts with prefix."""
    db = get_db()
    safe_prefix = _esc_like(prefix.lower())
    items_r = (
        db.table("items")
        .select("id, canonical_name")
        .eq("user_id", user_id)
        .ilike("canonical_name", f"{safe_prefix}%")
        .execute()
    )
    if not items_r.data:
        return {"total": 0.0, "variants": []}

    item_map = {item["id"]: item["canonical_name"] for item in items_r.data}

    entries_r = (
        db.table("expense_entries")
        .select("id")
        .eq("user_id", user_id)
        .gte("created_at", start)
        .lte("created_at", end)
        .execute()
    )
    if not entries_r.data:
        return {"total": 0.0, "variants": []}

    entry_ids = [e["id"] for e in entries_r.data]
    li_r = (
        db.table("line_items")
        .select("item_id, amount")
        .in_("expense_entry_id", entry_ids)
        .in_("item_id", list(item_map.keys()))
        .execute()
    )

    by_variant: dict[str, float] = {}
    total = 0.0
    for row in li_r.data:
        name = item_map[row["item_id"]]
        amt = float(row["amount"])
        by_variant[name] = by_variant.get(name, 0.0) + amt
        total += amt

    return {
        "total": total,
        "variants": sorted(
            [{"name": k, "total": v} for k, v in by_variant.items()],
            key=lambda x: x["total"], reverse=True,
        ),
    }


def get_price_change_leaders(user_id: str, top_n: int = 5) -> list[dict]:
    db = get_db()
    items_r = (
        db.table("items")
        .select("id, canonical_name")
        .eq("user_id", user_id)
        .execute()
    )
    if not items_r.data:
        return []

    item_map  = {item["id"]: item["canonical_name"] for item in items_r.data}
    item_ids  = list(item_map.keys())

    # Single batch query instead of N+1
    li_r = (
        db.table("line_items")
        .select("item_id, amount, expense_entries(created_at)")
        .in_("item_id", item_ids)
        .execute()
    )

    by_item: dict[str, list[dict]] = {}
    for row in li_r.data:
        entry = row.get("expense_entries")
        if not entry:
            continue
        by_item.setdefault(row["item_id"], []).append({
            "date":   entry["created_at"][:10],
            "amount": float(row["amount"]),
        })

    results = []
    for item_id, entries in by_item.items():
        entries = sorted(entries, key=lambda x: x["date"])
        if len(entries) < 2:
            continue
        first, last = entries[0], entries[-1]
        if first["amount"] == 0:
            continue
        change_pct = ((last["amount"] - first["amount"]) / first["amount"]) * 100
        results.append({
            "name":         item_map[item_id],
            "first_amount": first["amount"],
            "last_amount":  last["amount"],
            "first_date":   first["date"],
            "last_date":    last["date"],
            "change_pct":   change_pct,
            "count":        len(entries),
        })

    return sorted(results, key=lambda x: abs(x["change_pct"]), reverse=True)[:top_n]
