from __future__ import annotations
from datetime import datetime, timezone
from db.client import get_db


def add_wallet(user_id: str, address: str, label: str | None, chain: str = "SOL") -> dict:
    r = get_db().table("tracked_wallets").insert({
        "user_id": user_id,
        "address": address,
        "chain": chain,
        "label": label,
    }).execute()
    return r.data[0]


def get_user_wallets(user_id: str) -> list[dict]:
    r = (
        get_db().table("tracked_wallets")
        .select("*")
        .eq("user_id", user_id)
        .order("added_at")
        .execute()
    )
    return r.data


def get_wallet_by_address(user_id: str, address: str) -> dict | None:
    r = (
        get_db().table("tracked_wallets")
        .select("*")
        .eq("user_id", user_id)
        .eq("address", address)
        .execute()
    )
    return r.data[0] if r.data else None


def get_all_wallets() -> list[dict]:
    """All wallets across all users, joined with users(telegram_id) for messaging."""
    r = (
        get_db().table("tracked_wallets")
        .select("*, users(telegram_id)")
        .execute()
    )
    return r.data


def update_last_synced(wallet_id: str) -> None:
    get_db().table("tracked_wallets").update({
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", wallet_id).execute()


def upsert_trade(wallet_id: str, trade: dict) -> bool:
    """Insert a trade if tx_hash not already stored. Returns True if new."""
    db = get_db()
    existing = db.table("trades").select("id").eq("tx_hash", trade["tx_hash"]).execute()
    if existing.data:
        return False
    db.table("trades").insert({"wallet_id": wallet_id, **trade}).execute()
    return True


def get_trades(
    wallet_id: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 15,
) -> list[dict]:
    q = (
        get_db().table("trades")
        .select("*")
        .eq("wallet_id", wallet_id)
        .order("traded_at", desc=True)
    )
    if start:
        q = q.gte("traded_at", start)
    if end:
        q = q.lte("traded_at", end)
    return q.limit(limit).execute().data


def get_wallet_stats(
    wallet_id: str,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    q = get_db().table("trades").select("pnl_usd, is_win").eq("wallet_id", wallet_id)
    if start:
        q = q.gte("traded_at", start)
    if end:
        q = q.lte("traded_at", end)
    rows = q.execute().data

    total = len(rows)
    wins = sum(1 for r in rows if r.get("is_win") is True)
    losses = sum(1 for r in rows if r.get("is_win") is False)
    pnl_vals = [float(r["pnl_usd"]) for r in rows if r.get("pnl_usd") is not None]
    total_pnl = sum(pnl_vals)
    biggest_win = max((p for p in pnl_vals if p > 0), default=None)
    biggest_loss = min((p for p in pnl_vals if p < 0), default=None)

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / total * 100) if total > 0 else None,
        "total_pnl": total_pnl,
        "biggest_win": biggest_win,
        "biggest_loss": biggest_loss,
        "has_pnl_data": len(pnl_vals) > 0,
    }
