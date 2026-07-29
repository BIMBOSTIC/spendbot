import logging
from datetime import datetime, timezone
import httpx
from config import HELIUS_API_KEY
from db.wallet_queries import upsert_trade, update_last_synced

logger = logging.getLogger(__name__)

_HELIUS_BASE = "https://api.helius.xyz/v0"
_JUPITER_PRICE = "https://price.jup.ag/v6/price"
_SOL_MINT = "So11111111111111111111111111111111111111112"


async def _fetch_helius_swaps(address: str, limit: int = 100) -> list[dict]:
    url = f"{_HELIUS_BASE}/addresses/{address}/transactions"
    params = {"api-key": HELIUS_API_KEY, "type": "SWAP", "limit": limit}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _get_prices_and_symbols(mints: list[str]) -> tuple[dict, dict]:
    """Returns (prices_usd, symbols), both keyed by mint address."""
    if not mints:
        return {}, {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_JUPITER_PRICE, params={"ids": ",".join(mints)})
            resp.raise_for_status()
            data = resp.json().get("data", {})
            prices = {m: float(item["price"]) for m, item in data.items() if "price" in item}
            symbols = {m: item.get("mintSymbol") or m[:8] for m, item in data.items()}
            return prices, symbols
    except Exception:
        logger.warning("Jupiter price fetch failed")
        return {}, {}


def _parse_swap_tx(tx: dict) -> dict | None:
    """Extract in/out token info from a Helius enhanced swap transaction."""
    swap = (tx.get("events") or {}).get("swap")
    sig = tx.get("signature")
    ts = tx.get("timestamp", 0)
    if not swap or not sig:
        return None

    in_mint, amount_in = None, 0.0
    if swap.get("nativeInput"):
        in_mint = _SOL_MINT
        amount_in = int(swap["nativeInput"].get("amount", 0)) / 1e9
    elif swap.get("tokenInputs"):
        inp = swap["tokenInputs"][0]
        in_mint = inp.get("mint")
        raw = inp.get("rawTokenAmount", {})
        dec = int(raw.get("decimals", 0))
        amount_in = float(raw.get("tokenAmount", 0)) / (10 ** dec if dec else 1)

    out_mint, amount_out = None, 0.0
    if swap.get("nativeOutput"):
        out_mint = _SOL_MINT
        amount_out = int(swap["nativeOutput"].get("amount", 0)) / 1e9
    elif swap.get("tokenOutputs"):
        out = swap["tokenOutputs"][0]
        out_mint = out.get("mint")
        raw = out.get("rawTokenAmount", {})
        dec = int(raw.get("decimals", 0))
        amount_out = float(raw.get("tokenAmount", 0)) / (10 ** dec if dec else 1)

    if not in_mint or not out_mint:
        return None

    return {
        "tx_hash": sig,
        "in_mint": in_mint,
        "out_mint": out_mint,
        "amount_in": amount_in,
        "amount_out": amount_out,
        "traded_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
    }


async def sync_wallet(wallet: dict) -> int:
    """
    Fetch latest swaps from Helius, compute USD P&L via Jupiter,
    persist new trades. Returns count of new trades added.
    """
    if not HELIUS_API_KEY:
        logger.warning("HELIUS_API_KEY not set — wallet sync skipped")
        return 0

    try:
        raw_txs = await _fetch_helius_swaps(wallet["address"])
    except Exception as e:
        logger.error("Helius fetch failed for %s: %s", wallet["address"], e)
        return 0

    parsed = [_parse_swap_tx(tx) for tx in raw_txs]
    parsed = [p for p in parsed if p]

    if not parsed:
        update_last_synced(wallet["id"])
        return 0

    all_mints = list({p["in_mint"] for p in parsed} | {p["out_mint"] for p in parsed})
    prices, symbols = await _get_prices_and_symbols(all_mints)

    new_count = 0
    for swap in parsed:
        in_sym = symbols.get(swap["in_mint"]) or swap["in_mint"][:8]
        out_sym = symbols.get(swap["out_mint"]) or swap["out_mint"][:8]
        price_in = prices.get(swap["in_mint"])
        price_out = prices.get(swap["out_mint"])
        val_in = swap["amount_in"] * price_in if price_in is not None else None
        val_out = swap["amount_out"] * price_out if price_out is not None else None
        pnl = (val_out - val_in) if (val_in is not None and val_out is not None) else None
        is_win = (pnl > 0) if pnl is not None else None

        added = upsert_trade(wallet["id"], {
            "tx_hash": swap["tx_hash"],
            "token_in": in_sym,
            "token_out": out_sym,
            "amount_in": swap["amount_in"],
            "amount_out": swap["amount_out"],
            "value_usd_in": val_in,
            "value_usd_out": val_out,
            "pnl_usd": pnl,
            "is_win": is_win,
            "traded_at": swap["traded_at"],
        })
        if added:
            new_count += 1

    update_last_synced(wallet["id"])
    return new_count
