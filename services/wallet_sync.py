import logging
from datetime import datetime, timezone, timedelta
import httpx
from config import HELIUS_API_KEY, MORALIS_API_KEY
from db.wallet_queries import upsert_trade, update_last_synced

logger = logging.getLogger(__name__)

_HELIUS_BASE   = "https://api.helius.xyz/v0"
_JUPITER_PRICE = "https://price.jup.ag/v6/price"
_SOL_MINT      = "So11111111111111111111111111111111111111112"
_MORALIS_BASE  = "https://deep-index.moralis.io/api/v2.2"

# Maps our chain labels → Moralis chain slugs
EVM_CHAINS: dict[str, str] = {
    "ETH":  "eth",
    "BASE": "base",
    "BNB":  "bsc",
    "ARB":  "arbitrum",
    "AVAX": "avalanche",
    "RHC":  "robinhood",  # Robinhood Chain (Arbitrum Orbit) — Moralis support may vary
}


# ── Solana (Helius + Jupiter) ─────────────────────────────────────────────────

async def _fetch_helius_swaps(address: str, limit: int = 100) -> list[dict]:
    url = f"{_HELIUS_BASE}/addresses/{address}/transactions"
    params = {"api-key": HELIUS_API_KEY, "type": "SWAP", "limit": limit}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _get_prices_and_symbols(mints: list[str]) -> tuple[dict, dict]:
    if not mints:
        return {}, {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_JUPITER_PRICE, params={"ids": ",".join(mints)})
            resp.raise_for_status()
            data = resp.json().get("data", {})
            prices  = {m: float(item["price"]) for m, item in data.items() if "price" in item}
            symbols = {m: item.get("mintSymbol") or m[:8] for m, item in data.items()}
            return prices, symbols
    except Exception:
        logger.warning("Jupiter price fetch failed")
        return {}, {}


def _parse_helius_swap(tx: dict) -> dict | None:
    swap = (tx.get("events") or {}).get("swap")
    sig  = tx.get("signature")
    ts   = tx.get("timestamp", 0)
    if not swap or not sig:
        return None

    in_mint, amount_in = None, 0.0
    if swap.get("nativeInput"):
        in_mint   = _SOL_MINT
        amount_in = float(swap["nativeInput"].get("amount", 0)) / 1e9
    elif swap.get("tokenInputs"):
        inp       = swap["tokenInputs"][0]
        in_mint   = inp.get("mint")
        raw       = inp.get("rawTokenAmount", {})
        dec       = int(raw.get("decimals", 0))
        amount_in = float(raw.get("tokenAmount", 0)) / (10 ** dec if dec else 1)

    out_mint, amount_out = None, 0.0
    if swap.get("nativeOutput"):
        out_mint   = _SOL_MINT
        amount_out = float(swap["nativeOutput"].get("amount", 0)) / 1e9
    elif swap.get("tokenOutputs"):
        out        = swap["tokenOutputs"][0]
        out_mint   = out.get("mint")
        raw        = out.get("rawTokenAmount", {})
        dec        = int(raw.get("decimals", 0))
        amount_out = float(raw.get("tokenAmount", 0)) / (10 ** dec if dec else 1)

    if not in_mint or not out_mint:
        return None

    return {
        "tx_hash":   sig,
        "in_mint":   in_mint,
        "out_mint":  out_mint,
        "amount_in": amount_in,
        "amount_out": amount_out,
        "traded_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
    }


async def _sync_sol_wallet(wallet: dict) -> int:
    if not HELIUS_API_KEY:
        logger.warning("HELIUS_API_KEY not set — SOL wallet sync skipped")
        return 0

    try:
        raw_txs = await _fetch_helius_swaps(wallet["address"])
    except Exception as e:
        logger.error("Helius fetch failed for %s: %s", wallet["address"], e)
        return 0

    parsed = [_parse_helius_swap(tx) for tx in raw_txs]
    parsed = [p for p in parsed if p]

    if not parsed:
        update_last_synced(wallet["id"])
        return 0

    all_mints = list({p["in_mint"] for p in parsed} | {p["out_mint"] for p in parsed})
    prices, symbols = await _get_prices_and_symbols(all_mints)

    new_count = 0
    for swap in parsed:
        in_sym  = symbols.get(swap["in_mint"])  or swap["in_mint"][:8]
        out_sym = symbols.get(swap["out_mint"]) or swap["out_mint"][:8]
        price_in  = prices.get(swap["in_mint"])
        price_out = prices.get(swap["out_mint"])
        val_in    = swap["amount_in"]  * price_in  if price_in  is not None else None
        val_out   = swap["amount_out"] * price_out if price_out is not None else None
        pnl       = (val_out - val_in) if (val_in is not None and val_out is not None) else None

        added = upsert_trade(wallet["id"], {
            "tx_hash":      swap["tx_hash"],
            "token_in":     in_sym,
            "token_out":    out_sym,
            "amount_in":    swap["amount_in"],
            "amount_out":   swap["amount_out"],
            "value_usd_in": val_in,
            "value_usd_out": val_out,
            "pnl_usd":      pnl,
            "is_win":       (pnl > 0) if pnl is not None else None,
            "traded_at":    swap["traded_at"],
        })
        if added:
            new_count += 1

    update_last_synced(wallet["id"])
    return new_count


# ── EVM chains (Moralis) ──────────────────────────────────────────────────────

async def _fetch_moralis_swaps(address: str, chain_slug: str, limit: int = 100) -> list[dict]:
    url     = f"{_MORALIS_BASE}/wallets/{address}/swaps"
    headers = {"X-API-Key": MORALIS_API_KEY}
    params  = {"chain": chain_slug, "limit": limit, "order": "DESC"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json().get("result", [])


def _parse_moralis_swap(tx: dict) -> dict | None:
    tx_hash = tx.get("transactionHash")
    ts      = tx.get("blockTimestamp")
    sold    = tx.get("tokenSold") or {}
    bought  = tx.get("tokenBought") or {}

    if not tx_hash or not sold or not bought:
        return None

    traded_at  = ts if ts else datetime.now(timezone.utc).isoformat()
    amount_in  = float(sold.get("amount")   or 0)
    amount_out = float(bought.get("amount") or 0)
    val_in_raw  = sold.get("usdAmount")
    val_out_raw = bought.get("usdAmount")
    val_in      = float(val_in_raw)  if val_in_raw  else None
    val_out     = float(val_out_raw) if val_out_raw else None
    pnl         = (val_out - val_in) if (val_in is not None and val_out is not None) else None

    return {
        "tx_hash":       tx_hash,
        "token_in":      sold.get("tokenSymbol")   or "?",
        "token_out":     bought.get("tokenSymbol") or "?",
        "amount_in":     amount_in,
        "amount_out":    amount_out,
        "value_usd_in":  val_in,
        "value_usd_out": val_out,
        "pnl_usd":       pnl,
        "is_win":        (pnl > 0) if pnl is not None else None,
        "traded_at":     traded_at,
    }


async def _sync_evm_wallet(wallet: dict) -> int:
    if not MORALIS_API_KEY:
        logger.warning("MORALIS_API_KEY not set — EVM wallet sync skipped")
        return 0

    chain      = wallet.get("chain", "ETH")
    chain_slug = EVM_CHAINS.get(chain, chain.lower())

    try:
        raw_txs = await _fetch_moralis_swaps(wallet["address"], chain_slug)
    except Exception as e:
        logger.error("Moralis fetch failed for %s (%s): %s", wallet["address"], chain, e)
        return 0

    new_count = 0
    for tx in raw_txs:
        parsed = _parse_moralis_swap(tx)
        if not parsed:
            continue
        if upsert_trade(wallet["id"], parsed):
            new_count += 1

    update_last_synced(wallet["id"])
    return new_count


# ── Dispatcher ────────────────────────────────────────────────────────────────

_SYNC_COOLDOWN = timedelta(minutes=5)


async def sync_wallet(wallet: dict) -> int:
    last_synced = wallet.get("last_synced_at")
    if last_synced:
        try:
            last_dt = datetime.fromisoformat(last_synced.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last_dt < _SYNC_COOLDOWN:
                return 0  # synced recently — skip API call
        except (ValueError, TypeError):
            pass

    if wallet.get("chain", "SOL") == "SOL":
        return await _sync_sol_wallet(wallet)
    return await _sync_evm_wallet(wallet)
