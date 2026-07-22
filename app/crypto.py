"""Crypto data sources + holder-graph analysis for the canvas.

Self-contained (no app.main imports) so it stays testable and import-cheap. Three
keyless-first data sources, each degrading to ``{}``/``[]`` on any failure so a
builder can always render *something* honest:

  - CoinGecko  — token search/lookup by name/symbol/contract, price, market cap,
                 price chart. Works for every chain, no key.
  - Ethplorer  — Ethereum ERC-20 top holders (with % share), transfers between
                 them (graph edges) and single-address holdings. The public
                 ``freekey`` is rate-limited but needs no signup.
  - Solana RPC — getTokenLargestAccounts for the top holders of an SPL mint. The
                 public endpoint is heavily throttled; a Helius key (passed in)
                 is used when available. No transfer edges on Solana v1 — the RPC
                 cost is too high keyless — so the Solana graph is nodes +
                 concentration only, and says so.

The graph itself (build_holder_graph) is a pure function over already-fetched
holders/transfers, so it unit-tests without the network.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("html-notes.crypto")

CG_BASE = "https://api.coingecko.com/api/v3"
ETHPLORER_BASE = "https://api.ethplorer.io"
ETHPLORER_KEY = "freekey"  # public shared key; rate-limited but no signup
SOLANA_PUBLIC_RPC = "https://api.mainnet-beta.solana.com"

# CoinGecko "platform" id -> the short chain slug we use everywhere else. Only
# EVM chains Ethplorer/Blockscout-style holder data could conceivably cover, plus
# Solana which has its own RPC path. A token on a chain not here still gets a
# price card; its holder graph degrades to "unsupported chain".
CG_PLATFORM_TO_CHAIN = {
    "ethereum": "ethereum",
    "binance-smart-chain": "bsc",
    "polygon-pos": "polygon",
    "arbitrum-one": "arbitrum",
    "base": "base",
    "optimistic-ethereum": "optimism",
    "avalanche": "avalanche",
    "solana": "solana",
}

# Chains whose holder graph we can actually build today. Ethereum via Ethplorer
# (holders + edges); Solana via RPC (holders only). Everything else: price card
# only, with an honest note.
GRAPH_CHAINS = {"ethereum", "solana"}

EVM_ADDR_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
# Base58, 32-44 chars, excluding an all-hex-looking 0x string. Solana mints/
# wallets. The word-boundary + charset keeps it from matching English words.
SOL_ADDR_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

# ── Known Ethereum addresses (lowercased) ────────────────────────────────────
# Labelling the biggest holders is what turns a blob of hashes into a story: a
# token whose top 3 "holders" are Binance/Coinbase hot wallets is custodial, not
# whale-controlled. Calling getAddressInfo per holder would blow the freekey rate
# limit, so we classify from this curated map instead. Kind drives node color.
BURN = "burn"
CEX = "cex"
DEX = "dex"
KNOWN_EVM = {
    "0x0000000000000000000000000000000000000000": (BURN, "Null / burn"),
    "0x000000000000000000000000000000000000dead": (BURN, "Dead / burn"),
    # Binance
    "0xf977814e90da44bfa03b6295a0616a897441acec": (CEX, "Binance"),
    "0x28c6c06298d514db089934071355e5743bf21d60": (CEX, "Binance"),
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": (CEX, "Binance"),
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": (CEX, "Binance"),
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": (CEX, "Binance"),
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": (CEX, "Binance"),
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": (CEX, "Binance"),
    "0x4976a4a02f38326660d17bf34b431dc6e2eb2327": (CEX, "Binance"),
    "0xd551234ae421e3bcba99a0da6d736074f22192ff": (CEX, "Binance"),
    # Coinbase
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": (CEX, "Coinbase"),
    "0x503828976d22510aad0201ac7ec88293211d23da": (CEX, "Coinbase"),
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": (CEX, "Coinbase"),
    "0x3cd751e6b0078be393132286c442345e5dc49699": (CEX, "Coinbase"),
    "0xb5d85cbf7cb3ee0d56b3bb207d5fc4b82f43f511": (CEX, "Coinbase"),
    "0xeb2629a2734e272bcc07bda959863f316f4bd4cf": (CEX, "Coinbase"),
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": (CEX, "Coinbase"),
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": (CEX, "Kraken"),
    "0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13": (CEX, "Kraken"),
    "0xe853c56864a2ebe4576a807d26fdc4a0ada51919": (CEX, "Kraken"),
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": (CEX, "Kraken"),
    # OKX / Gate / Bitfinex / others
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": (CEX, "OKX"),
    "0xa7efae728d2936e78bda97dc267687568dd593f3": (CEX, "OKX"),
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": (CEX, "Gate.io"),
    "0x876eabf441b2ee5b5b0554fd502a8e0600950cfa": (CEX, "Bitfinex"),
    "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f": (CEX, "Bitfinex"),
    "0x66f820a414680b5bcda5eeca5dea238543f42054": (CEX, "AAVE / staking"),
    "0x40b38765696e3d5d8d9d834d8aad4bb6e418e489": (CEX, "Robinhood"),
    # DEX routers / infra
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": (DEX, "Uniswap V2 router"),
    "0xe592427a0aece92de3edee1f18e0157c05861564": (DEX, "Uniswap V3 router"),
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": (DEX, "Uniswap router"),
    "0x881d40237659c251811cec9c364ef91dc08d300c": (DEX, "Metamask router"),
    "0x1111111254eeb25477b68fb85ed929f73a960582": (DEX, "1inch router"),
    # Market makers
    "0x0000006daea1723962647b7e189d311d757fb793": (DEX, "Wintermute"),
    "0x4f3a120e72c76c22ae802d129f599bfdbc31cb81": (DEX, "Wintermute"),
}


# Two-layer TTL cache: an in-memory hot layer + a PERSISTENT sqlite layer that
# survives restarts. The keyless sources (Ethplorer freekey, public Solana RPC,
# CoinGecko free) are all shared and rate-limited (429). Persisting responses
# means a popular token, a re-ask, or a range-tab switch never re-spends the rate
# budget — which is what lets us sample DEEPER (100 holders + per-holder flows)
# without 429-ing. Per-URL TTLs: holder/flow/token data is slow-moving (15 min),
# prices are fresh (90s). A miss (None) is never cached, so a transient 429
# doesn't poison the next real attempt.
import os as _os
import sqlite3 as _sqlite3
import threading as _threading
import time as _time

_CACHE: dict = {}
_CACHE_LOCK = _threading.Lock()
_DB_PATH = _os.path.join(
    _os.path.dirname(_os.getenv("DATABASE_URL", "data/notes.db")) or "data",
    "crypto_cache.db")
_db_conn = None


def _cache_db():
    global _db_conn
    if _db_conn is None:
        try:
            _os.makedirs(_os.path.dirname(_DB_PATH) or ".", exist_ok=True)
            _db_conn = _sqlite3.connect(_DB_PATH, check_same_thread=False)
            _db_conn.execute(
                "CREATE TABLE IF NOT EXISTS crypto_cache "
                "(k TEXT PRIMARY KEY, v TEXT, exp REAL)")
            _db_conn.commit()
        except Exception as e:
            logger.warning("[crypto] cache db init failed (%s) — memory only", e)
            _db_conn = False   # sentinel: don't retry every call
    return _db_conn or None


def _ttl_for(url: str) -> float:
    """Per-source freshness. On-chain holder/flow/token facts move slowly; prices
    must stay live."""
    u = url.lower()
    if ("market_chart" in u or "/ohlcv/" in u or "/search" in u
            or "dexscreener" in u and "/pairs/" in u):
        return 90.0
    if ("gettoptokenholders" in u or "getaddresshistory" in u
            or "gettokeninfo" in u or "gettokenhistory" in u
            or "getaddressinfo" in u):
        return 900.0        # 15 min — the expensive holder/flow calls
    if "/coins/" in u:
        return 300.0
    return 120.0


def _cache_key(url: str, params: Optional[dict]) -> str:
    if not params:
        return url
    items = sorted((k, str(v)) for k, v in params.items() if k != "apiKey")
    return url + "?" + "&".join(f"{k}={v}" for k, v in items)


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if hit and _time.time() < hit[0]:
        return hit[1], True
    db = _cache_db()
    if db is not None:
        try:
            with _CACHE_LOCK:
                row = db.execute("SELECT v, exp FROM crypto_cache WHERE k=?",
                                 (key,)).fetchone()
            if row and _time.time() < row[1]:
                import json as _json
                data = _json.loads(row[0])
                _CACHE[key] = (row[1], data)   # promote to hot layer
                return data, True
        except Exception:
            pass
    return None, False


def _cache_put(key: str, data, ttl: float):
    exp = _time.time() + ttl
    _CACHE[key] = (exp, data)
    if len(_CACHE) > 512:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:256]:
            _CACHE.pop(k, None)
    db = _cache_db()
    if db is not None:
        try:
            import json as _json
            with _CACHE_LOCK:
                db.execute(
                    "INSERT OR REPLACE INTO crypto_cache (k, v, exp) VALUES (?,?,?)",
                    (key, _json.dumps(data), exp))
                db.commit()
        except Exception:
            pass


async def _get_json(url: str, params: Optional[dict] = None, timeout: float = 12.0):
    """GET → parsed JSON, or None on any error/non-200. Two-layer TTL cached.
    Never raises."""
    key = _cache_key(url, params)
    cached, ok = _cache_get(key)
    if ok:
        return cached
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, params=params,
                            headers={"User-Agent": "html-notes/1.0",
                                     "Accept": "application/json"})
            # CoinGecko/Ethplorer free tiers rate-limit (429) under bursts; one
            # short backoff usually clears it.
            if r.status_code == 429:
                await asyncio.sleep(1.5)
                r = await c.get(url, params=params,
                                headers={"User-Agent": "html-notes/1.0",
                                         "Accept": "application/json"})
            if r.status_code != 200:
                logger.info("[crypto] %s -> %s", url, r.status_code)
                return None
            data = r.json()
            _cache_put(key, data, _ttl_for(url))
            return data
    except Exception as e:
        logger.info("[crypto] GET %s failed: %s", url, e)
        return None


async def _post_json(url: str, payload: dict, timeout: float = 12.0):
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        logger.info("[crypto] POST %s failed: %s", url, e)
        return None


# ── CoinGecko: identity, price, chart ────────────────────────────────────────
async def cg_search(query: str) -> list:
    """Free-text → CoinGecko coin matches [{id, name, symbol, market_cap_rank}]."""
    d = await _get_json(f"{CG_BASE}/search", {"query": query})
    return (d or {}).get("coins", []) or []


async def cg_coin(coin_id: str) -> dict:
    """Full coin record: platforms (contract per chain), market_data, links."""
    d = await _get_json(f"{CG_BASE}/coins/{coin_id}", {
        "localization": "false", "tickers": "false", "market_data": "true",
        "community_data": "false", "developer_data": "false", "sparkline": "false"})
    return d or {}


async def cg_coin_by_contract(platform: str, address: str) -> dict:
    d = await _get_json(f"{CG_BASE}/coins/{platform}/contract/{address}")
    return d or {}


async def cg_market_chart(coin_id: str, days: str = "30") -> tuple:
    """(labels, values) price series for the chart. Down-samples long ranges so
    the payload and canvas stay light. ([], []) on failure."""
    d = await _get_json(f"{CG_BASE}/coins/{coin_id}/market_chart",
                        {"vs_currency": "usd", "days": days})
    prices = (d or {}).get("prices", []) or []
    if not prices:
        return [], []
    # Cap to ~120 points; keep first + last.
    step = max(1, len(prices) // 120)
    pts = prices[::step]
    if pts and pts[-1] is not prices[-1]:
        pts.append(prices[-1])
    labels, values = [], []
    for ts, price in pts:
        labels.append(_fmt_ts_label(ts / 1000.0, days))
        values.append(price)
    return labels, values


def _fmt_ts_label(epoch: float, days: str) -> str:
    import datetime
    dt = datetime.datetime.utcfromtimestamp(epoch)
    try:
        n = float(days)
    except (TypeError, ValueError):
        n = 30
    if n <= 1:
        return dt.strftime("%H:%M")
    if n <= 90:
        return dt.strftime("%b %d")
    return dt.strftime("%b %y")


async def resolve_crypto(query: str) -> Optional[dict]:
    """Name / symbol / contract-address → a normalized identity we can build on:

        {coin_id, ref, name, symbol, chain, address, pool, gt_network, image, source}

    Resolution strategy — CoinGecko for CANONICAL coins, DexScreener for the long
    tail (microcap memecoins CoinGecko never lists, e.g. "jimothy"):
    - An address resolves via CoinGecko's contract endpoint first (canonical),
      then DexScreener by-address (any chain).
    - A name/symbol takes a CoinGecko name/symbol-EXACT match if one exists
      (canonical, real multi-year history), else falls back to DexScreener search
      (finds anything with a live DEX pool), else CoinGecko's best fuzzy match.

    `ref` is the routable id the card/API use: a CoinGecko coin id, or
    "dexs:<chainId>:<pool>" for a DexScreener-sourced token. Returns None when
    nothing resolves anywhere."""
    q = (query or "").strip()
    if not q:
        return None

    def _by_rank(cs):
        return sorted(cs, key=lambda c: (c.get("market_cap_rank") or 10**9))

    # ── Address ──
    m = EVM_ADDR_RE.search(q)
    if m:
        addr = m.group(0).lower()
        for platform in ("ethereum", "binance-smart-chain", "polygon-pos",
                         "arbitrum-one", "base"):
            c = await cg_coin_by_contract(platform, addr)
            if c and c.get("id"):
                return _identity_from_coin(c, prefer_chain=CG_PLATFORM_TO_CHAIN.get(platform))
        di = await resolve_dexscreener_address(addr)
        if di:
            return di
        # Unknown to both but a valid ETH address — let the holder-graph path try
        # Ethplorer directly on the raw address.
        return {"coin_id": "", "ref": "", "name": "", "symbol": "",
                "chain": "ethereum", "address": addr, "pool": "", "gt_network": "eth",
                "image": "", "source": "address"}

    sm = SOL_ADDR_RE.search(q)
    if sm and not m and _looks_like_solana_addr(q):
        di = await resolve_dexscreener_address(sm.group(0))
        if di:
            return di
        return {"coin_id": "", "ref": "", "name": "", "symbol": "",
                "chain": "solana", "address": sm.group(0), "pool": "",
                "gt_network": "solana", "image": "", "source": "address"}

    # ── Name / symbol ── strip filler ("price of X", "who holds X") to the
    # token words before searching.
    subject = _clean_crypto_query(q)
    ql = subject.lower()

    # Majors short-circuit — canonical id, no search, no impostor risk.
    if ql in MAJORS:
        full = await cg_coin(MAJORS[ql])
        if full and full.get("id"):
            return _identity_from_coin(full)

    coins = await cg_search(subject)
    # Scam memecoins hijack famous names/symbols — CoinGecko search for "bitcoin"
    # returns a token whose SYMBOL is literally "BITCOIN" (rank ~5000) next to
    # real Bitcoin (rank 1). Take a name-exact then symbol-exact CoinGecko match,
    # best-rank within tier — those are the canonical coins with real history.
    name_exact = [c for c in coins if (c.get("name") or "").lower() == ql]
    sym_exact = [c for c in coins if (c.get("symbol") or "").lower() == ql]
    strong = _by_rank(name_exact) or _by_rank(sym_exact)
    if strong:
        full = await cg_coin(strong[0]["id"])
        return _identity_from_coin(full or strong[0])

    # No canonical match — this is where microcaps live. DexScreener finds
    # anything with a live pool ("jimothy" -> the pump.fun raccoon token).
    di = await resolve_dexscreener(subject)
    if di:
        return di

    # Last resort: CoinGecko's best fuzzy match (a loose name hit).
    if coins:
        full = await cg_coin(_by_rank(coins)[0]["id"])
        return _identity_from_coin(full or coins[0])
    return None


def _looks_like_solana_addr(q: str) -> bool:
    """A base58 32-44 string that is the whole query (or nearly) — not an English
    sentence that happens to contain a long word. Guards the Solana-address path."""
    toks = [t for t in re.split(r"\s+", q.strip()) if t]
    return len(toks) == 1 and bool(SOL_ADDR_RE.fullmatch(toks[0]))


def _identity_from_coin(coin: dict, prefer_chain: Optional[str] = None) -> dict:
    platforms = coin.get("platforms") or {}
    chain, address = "", ""
    # Prefer the chain the address was found on; else first EVM/known platform.
    order = []
    if prefer_chain:
        for p, ch in CG_PLATFORM_TO_CHAIN.items():
            if ch == prefer_chain:
                order.append(p)
    order += list(CG_PLATFORM_TO_CHAIN.keys())
    for p in order:
        a = platforms.get(p)
        if a:
            chain, address = CG_PLATFORM_TO_CHAIN[p], a
            break
    img = ((coin.get("image") or {}).get("large")
           or (coin.get("image") or {}).get("small") or "")
    cid = coin.get("id", "")
    return {
        "coin_id": cid,
        "ref": cid,
        "name": coin.get("name", ""),
        "symbol": (coin.get("symbol") or "").upper(),
        "chain": chain,
        "address": address,
        "pool": "",
        "gt_network": "",
        "image": img,
        "source": "coingecko",
    }


# ── DexScreener + GeckoTerminal: the keyless long-tail (microcaps) ────────────
# DexScreener indexes every DEX PAIR across chains (price/liquidity/mcap/24h) and
# has a name search — so it resolves "jimothy" the moment a pool exists, where
# CoinGecko (which lists canonical coins) returns nothing. GeckoTerminal (also
# keyless, CoinGecko's on-chain arm) then supplies OHLCV candles for that pool so
# even an unlisted memecoin gets a real price chart. Neither needs a key.
DEXS_BASE = "https://api.dexscreener.com/latest/dex"
GT_BASE = "https://api.geckoterminal.com/api/v2"

# DexScreener chainId -> (our chain slug, GeckoTerminal network id). The GT
# network ids differ from the chain names ("eth", not "ethereum").
DEXS_CHAIN = {
    "ethereum": ("ethereum", "eth"),
    "solana": ("solana", "solana"),
    "bsc": ("bsc", "bsc"),
    "base": ("base", "base"),
    "polygon": ("polygon", "polygon_pos"),
    "arbitrum": ("arbitrum", "arbitrum"),
    "avalanche": ("avalanche", "avax"),
    "optimism": ("optimism", "optimism"),
}

# Range -> (GeckoTerminal timeframe, aggregate, candle limit).
_GT_RANGE = {
    "1d": ("minute", 15, 96),
    "7d": ("hour", 1, 168),
    "30d": ("hour", 4, 180),
    "90d": ("day", 1, 90),
    "1y": ("day", 1, 365),
    "max": ("day", 1, 1000),
}
_RANGE_DAYS = {"1d": "1", "7d": "7", "30d": "30", "90d": "90", "1y": "365", "max": "max"}


async def dexscreener_search(query: str) -> list:
    d = await _get_json(f"{DEXS_BASE}/search", {"q": query})
    return (d or {}).get("pairs", []) or []


async def dexscreener_by_address(address: str) -> list:
    d = await _get_json(f"{DEXS_BASE}/tokens/{address}")
    return (d or {}).get("pairs", []) or []


async def dexscreener_pair(chain_id: str, pool: str) -> dict:
    d = await _get_json(f"{DEXS_BASE}/pairs/{chain_id}/{pool}")
    if not d:
        return {}
    ps = d.get("pairs") or ([d["pair"]] if d.get("pair") else [])
    return ps[0] if ps else {}


def _liq(p: dict) -> float:
    return float((p.get("liquidity") or {}).get("usd") or 0.0)


# Filler stripped before a name/symbol search so "price of jimothy the raccoon"
# searches "jimothy raccoon", not the whole sentence (which name-matches nothing).
_CRYPTO_FILLER = {
    "the", "a", "an", "of", "for", "me", "please", "show", "get", "pull", "up",
    "price", "prices", "chart", "charts", "graph", "token", "tokens", "coin",
    "coins", "crypto", "cryptocurrency", "cryptocurrencies", "value", "worth",
    "holders", "holder", "holds", "hold", "holding", "whales", "whale",
    "distribution", "distributed", "wallet", "wallets", "connected", "connections",
    "network", "map", "fair", "launch", "pump", "dump", "rug", "scam", "safe",
    "legit", "deep", "dive", "dd", "due", "diligence", "analyze", "analysis",
    "research", "who", "owns", "own", "top", "report", "on", "about", "what",
    "whats", "is", "it", "how", "much", "current", "live", "now", "today", "buy",
    "should", "i", "and", "or", "vs", "to", "this", "that", "in",
}

# Top coins short-circuit the search entirely: name/symbol -> canonical CoinGecko
# id. This makes the majors bulletproof and instant — they never depend on a
# CoinGecko search call (which can rate-limit) and so can NEVER resolve to a
# DexScreener impostor named "Ethereum" with a $75k pool.
MAJORS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
    "cardano": "cardano", "ada": "cardano",
    "litecoin": "litecoin", "ltc": "litecoin",
    "polkadot": "polkadot", "dot": "polkadot",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "tron": "tron", "trx": "tron",
    "polygon": "matic-network", "matic": "matic-network",
    "shiba": "shiba-inu", "shib": "shiba-inu", "shiba inu": "shiba-inu",
    "pepe": "pepe",
    "uniswap": "uniswap", "uni": "uniswap",
    "tether": "tether", "usdt": "tether",
    "usdc": "usd-coin",
    "dai": "dai",
    "monero": "monero", "xmr": "monero",
    "stellar": "stellar", "xlm": "stellar",
    "cosmos": "cosmos", "atom": "cosmos",
    "bonk": "bonk",
    "dogwifhat": "dogwifhat", "wif": "dogwifhat",
    "near": "near",
    "aptos": "aptos", "apt": "aptos",
    "arbitrum": "arbitrum", "arb": "arbitrum",
    "optimism": "optimism", "op": "optimism",
    "bnb": "binancecoin", "binance coin": "binancecoin",
    "cronos": "crypto-com-chain", "cro": "crypto-com-chain",
    "toncoin": "the-open-network", "ton": "the-open-network",
    "sui": "sui", "sei": "sei-network", "hbar": "hedera-hashgraph",
    "floki": "floki", "mog": "mog-coin", "brett": "based-brett",
}


def _clean_crypto_query(query: str) -> str:
    """Strip filler so a name/symbol search sees just the token words. Keeps
    $CASHTAGs and addresses intact (caller handles those before this)."""
    words = re.split(r"[^A-Za-z0-9$]+", query or "")
    kept = [w for w in words if w and w.lower() not in _CRYPTO_FILLER]
    return " ".join(kept).strip() or (query or "").strip()


def _best_pair(pairs: list, query: Optional[str]) -> Optional[dict]:
    """Pick the token to show from a DexScreener result set. With a query, SCORE
    each pair by how well its base token name/symbol matches the query words
    (symbol-exact > symbol-substring > name-word > name-substring), then break
    ties by deepest liquidity — so a scam clone with a $50 pool can't win, and a
    high-liquidity but UNRELATED token can't hijack a no-match query (that returns
    None instead, so the caller degrades honestly). Without a query (address
    lookup) the deepest-liquidity pool wins outright."""
    cands = [p for p in pairs if (p.get("baseToken") or {}).get("address")
             and p.get("chainId") in DEXS_CHAIN]
    if not cands:
        return None
    if not query:
        return max(cands, key=_liq)

    words = [w for w in re.split(r"[^a-z0-9]+", query.lower())
             if len(w) >= 2 and w not in _CRYPTO_FILLER]
    if not words:
        return max(cands, key=_liq)

    def _score(p):
        bt = p.get("baseToken") or {}
        sym = (bt.get("symbol") or "").lower()
        name = (bt.get("name") or "").lower()
        name_words = set(re.split(r"[^a-z0-9]+", name))
        s = 0
        for w in words:
            if w == sym:
                s += 4
            elif w in sym:
                s += 2
            elif w in name_words:
                s += 3
            elif w in name:
                s += 1
        return s

    scored = [(_score(p), _liq(p), p) for p in cands]
    best = max(s for s, _, _ in scored)
    if best <= 0:
        return None   # query given but nothing matched — don't grab a whale
    return max((t for t in scored if t[0] == best), key=lambda t: t[1])[2]


def _identity_from_pair(p: dict) -> Optional[dict]:
    chain_id = p.get("chainId")
    if chain_id not in DEXS_CHAIN:
        return None
    slug, gt = DEXS_CHAIN[chain_id]
    bt = p.get("baseToken") or {}
    pool = p.get("pairAddress", "")
    return {
        "coin_id": f"dexs:{chain_id}:{pool}",
        "ref": f"dexs:{chain_id}:{pool}",
        "name": bt.get("name", ""),
        "symbol": (bt.get("symbol") or "").upper(),
        "chain": slug,
        "address": bt.get("address", ""),
        "pool": pool,
        "gt_network": gt,
        "dexs_chain": chain_id,
        "image": (p.get("info") or {}).get("imageUrl", "") or "",
        "source": "dexscreener",
    }


async def resolve_dexscreener(query: str) -> Optional[dict]:
    best = _best_pair(await dexscreener_search(query), query)
    return _identity_from_pair(best) if best else None


async def resolve_dexscreener_address(address: str) -> Optional[dict]:
    best = _best_pair(await dexscreener_by_address(address), None)
    return _identity_from_pair(best) if best else None


async def gt_ohlcv(network: str, pool: str, range_: str = "30d") -> tuple:
    """(labels, values) close-price series for a DEX pool via GeckoTerminal.
    Keyless. ([], []) on failure. GT returns newest-first; we flip to oldest-first
    so the chart reads left→right in time."""
    tf, agg, lim = _GT_RANGE.get(range_, ("hour", 4, 180))
    d = await _get_json(f"{GT_BASE}/networks/{network}/pools/{pool}/ohlcv/{tf}",
                        {"aggregate": agg, "limit": lim, "currency": "usd"})
    ol = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    ol = list(reversed(ol))
    days = _RANGE_DAYS.get(range_, "30")
    labels, values = [], []
    for row in ol:
        if len(row) < 5:
            continue
        ts, _o, _h, _l, close = row[0], row[1], row[2], row[3], row[4]
        labels.append(_fmt_ts_label(float(ts), days))
        values.append(close)
    return labels, values


# ── Ethplorer: Ethereum holders / transfers / address ────────────────────────
async def eth_token_info(address: str) -> dict:
    d = await _get_json(f"{ETHPLORER_BASE}/getTokenInfo/{address}",
                        {"apiKey": ETHPLORER_KEY})
    return d or {}


async def eth_top_holders(address: str, limit: int = 50) -> list:
    """[{address, share, balance}] — share is already % of supply, biggest first."""
    d = await _get_json(f"{ETHPLORER_BASE}/getTopTokenHolders/{address}",
                        {"apiKey": ETHPLORER_KEY, "limit": limit})
    return (d or {}).get("holders", []) or []


async def eth_transfers(address: str, limit: int = 100) -> list:
    """Recent token transfers [{from, to, value}] — the raw material for edges."""
    d = await _get_json(f"{ETHPLORER_BASE}/getTokenHistory/{address}",
                        {"apiKey": ETHPLORER_KEY, "type": "transfer", "limit": limit})
    return (d or {}).get("operations", []) or []


async def eth_address_token_history(address: str, token: str, limit: int = 30) -> list:
    """ONE holder's transfers of a SPECIFIC token [{from, to, value, timestamp}].
    This is what makes the flow graph real: for each top holder we see who it
    actually sent to / received from — including counterparties that aren't
    themselves top holders (a shared funder that seeded several whale wallets)."""
    d = await _get_json(f"{ETHPLORER_BASE}/getAddressHistory/{address}",
                        {"apiKey": ETHPLORER_KEY, "token": token,
                         "type": "transfer", "limit": limit})
    return (d or {}).get("operations", []) or []


async def eth_holder_flows(token: str, holder_addrs: list, per_holder: int = 30,
                           concurrency: int = 4) -> list:
    """Fetch each holder's transfers of `token` and return the flattened union —
    the edge material for the flow graph. Bounded concurrency keeps the shared
    Ethplorer freekey from 429-ing on a burst; results are TTL-cached so a re-ask
    is instant. Returns [{from,to,value,timestamp}, ...]."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(addr):
        async with sem:
            return await eth_address_token_history(addr, token, per_holder)

    batches = await asyncio.gather(*[_one(a) for a in holder_addrs],
                                   return_exceptions=True)
    flows = []
    for b in batches:
        if isinstance(b, list):
            flows.extend(b)
    return flows


async def eth_address_info(address: str) -> dict:
    d = await _get_json(f"{ETHPLORER_BASE}/getAddressInfo/{address}",
                        {"apiKey": ETHPLORER_KEY})
    return d or {}


# ── Solana RPC: top holders of a mint ────────────────────────────────────────
# Keyless public RPCs, tried in order until one answers (each is heavily throttled
# on its own, so a rotation catches more successes). A Helius key, when present,
# jumps the queue — it's the only reliable option. Honest reality: keyless Solana
# holder data OFTEN fails; the caller degrades to the price card.
_SOLANA_RPCS = [
    SOLANA_PUBLIC_RPC,
    "https://solana-rpc.publicnode.com",
    "https://api.mainnet-beta.solana.com",
]


async def _sol_rpc_call(rpcs: list, payload: dict):
    """POST `payload` to each RPC until one returns a non-error result."""
    for rpc in rpcs:
        r = await _post_json(rpc, payload)
        if r and not r.get("error") and r.get("result") is not None:
            return r, rpc
    return None, None


async def sol_top_holders(mint: str, limit: int = 20, helius_key: str = "") -> tuple:
    """(holders, supply, decimals) for an SPL mint via RPC. Holders are
    [{address(owner), amount, share}]. A Helius key (when present) is tried first;
    otherwise a rotation of public endpoints. Returns ([], 0, 0) on failure — the
    public RPCs 429 often, which is expected and handled by the caller."""
    rpcs = ([f"https://mainnet.helius-rpc.com/?api-key={helius_key}"]
            if helius_key else []) + _SOLANA_RPCS

    supply_r, rpc = await _sol_rpc_call(
        rpcs, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply",
               "params": [mint]})
    val = ((supply_r or {}).get("result") or {}).get("value") or {}
    decimals = int(val.get("decimals", 0) or 0)
    supply = float(val.get("uiAmount") or 0.0)

    # Prefer the RPC that just worked for the (rate-limited) largest-accounts call.
    largest, rpc = await _sol_rpc_call(
        ([rpc] if rpc else []) + rpcs,
        {"jsonrpc": "2.0", "id": 2, "method": "getTokenLargestAccounts",
         "params": [mint]})
    accts = ((largest or {}).get("result") or {}).get("value") or []
    if not accts:
        return [], supply, decimals

    # getTokenLargestAccounts returns TOKEN ACCOUNTS, not owner wallets. Resolve
    # each to its owner so the graph shows wallets, not throwaway ATAs.
    pubkeys = [a.get("address") for a in accts[:limit] if a.get("address")]
    owners = await _sol_owners(rpc or rpcs[0], pubkeys)
    holders = []
    for a in accts[:limit]:
        amt = float((a.get("uiAmount") or 0) or 0)
        share = (amt / supply * 100.0) if supply else 0.0
        owner = owners.get(a.get("address")) or a.get("address")
        holders.append({"address": owner, "amount": amt, "share": share})
    return holders, supply, decimals


async def _sol_owners(rpc: str, token_accounts: list) -> dict:
    """token-account pubkey -> owner wallet, via a single getMultipleAccounts
    (jsonParsed). {} on failure — caller falls back to the token-account key."""
    if not token_accounts:
        return {}
    r = await _post_json(rpc, {"jsonrpc": "2.0", "id": 3,
                               "method": "getMultipleAccounts",
                               "params": [token_accounts,
                                          {"encoding": "jsonParsed"}]})
    vals = ((r or {}).get("result") or {}).get("value") or []
    out = {}
    for pk, acc in zip(token_accounts, vals):
        try:
            info = (((acc or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            if info.get("owner"):
                out[pk] = info["owner"]
        except Exception:
            continue
    return out


# ── Graph + concentration metrics (pure) ─────────────────────────────────────
def _short(addr: str) -> str:
    if not addr:
        return "?"
    return addr[:6] + "…" + addr[-4:] if len(addr) > 12 else addr


def _classify_evm(addr: str, share: float, token_addr: str) -> tuple:
    """(kind, label). Kind ∈ burn|cex|dex|contract|whale|holder — drives color."""
    a = (addr or "").lower()
    if a == (token_addr or "").lower():
        return "contract", "Token contract"
    if a in KNOWN_EVM:
        return KNOWN_EVM[a]
    if share >= 1.0:
        return "whale", _short(addr)
    return "holder", _short(addr)


def _humanize_tokens(raw: float, decimals: int) -> str:
    """Raw on-chain integer amount → human token count (1.2M, 3.4B)."""
    try:
        n = float(raw) / (10 ** int(decimals or 0))
    except (TypeError, ValueError):
        return "?"
    a = abs(n)
    if a >= 1e12:
        return f"{n/1e12:.2f}T"
    if a >= 1e9:
        return f"{n/1e9:.2f}B"
    if a >= 1e6:
        return f"{n/1e6:.2f}M"
    if a >= 1e3:
        return f"{n/1e3:.1f}K"
    if 0 < a < 0.01:
        return "<0.01"
    return f"{n:.2f}"


def build_holder_graph(token: dict, holders: list, transfers: list,
                       chain: str, decimals: int = 0) -> dict:
    """Holders + their transfer flows → a cytoscape config + concentration &
    coordination metrics. Pure. Contract:

        token = {name, symbol, address}
        holders = [{address, share, balance/amount}]   (share = % of supply)
        transfers = [{from, to, value, timestamp}]     (the UNION of each top
                    holder's own transfers of this token — EVM; [] for Solana)

    Edges answer "who transferred what to where": a directed edge per (from→to)
    pair carries the summed amount + count. A counterparty that isn't itself a top
    holder but connects TWO OR MORE top holders is promoted to a red "shared
    source" node — the coordinated-seeding / pump-and-dump tell. Returns
    {title, chain, token, elements, metrics, note}."""
    token_addr = (token.get("address") or "").lower()
    is_evm = chain != "solana"

    # ── Holder nodes ──
    holder_set = set()
    nodes = []
    node_index = {}   # addr -> node dict (so we can flag it later)
    kind_share = {}
    shares = []
    for h in holders:
        addr = (h.get("address") or "").strip()
        if not addr:
            continue
        low = addr.lower()
        if low in holder_set:
            continue
        holder_set.add(low)
        share = float(h.get("share") or 0.0)
        shares.append(share)
        if is_evm:
            kind, label = _classify_evm(addr, share, token_addr)
        else:
            kind = "whale" if share >= 1.0 else "holder"
            label = _short(addr)
        kind_share[kind] = kind_share.get(kind, 0.0) + share
        node = {"data": {
            "id": low, "addr": addr, "label": label, "kind": kind,
            "share": round(share, 3),
            "size": round(18 + min(72, (share ** 0.5) * 12), 1),
        }}
        nodes.append(node)
        node_index[low] = node

    # ── Flow edges ── tally each holder's real transfers of this token.
    pair = {}                 # (from,to) -> [sum_raw, count]
    counterparties = {}       # non-holder addr -> set(holder addrs it touched)
    for t in (transfers or []):
        f = (t.get("from") or "").lower()
        to = (t.get("to") or "").lower()
        if not f or not to or f == to:
            continue
        raw = float(t.get("value") or 0)
        p = pair.setdefault((f, to), [0.0, 0])
        p[0] += raw
        p[1] += 1
        if f in holder_set and to not in holder_set:
            counterparties.setdefault(to, set()).add(f)
        elif to in holder_set and f not in holder_set:
            counterparties.setdefault(f, set()).add(to)

    # Promote a counterparty to a node when it links ≥2 top holders (a shared
    # funder/router — the coordination signal), or when it's a known entity
    # (CEX/burn/DEX). Others stay off the graph to avoid a hairball.
    promoted = {a for a, hs in counterparties.items()
                if len(hs) >= 2 or a in KNOWN_EVM or a == token_addr}
    for a in promoted:
        if a in holder_set:
            continue
        if is_evm:
            kind, label = _classify_evm(a, 0.0, token_addr)
            if kind == "holder":            # unlabeled but ties whales together
                kind, label = "source", "shared source"
        else:
            kind, label = "source", _short(a)
        node = {"data": {"id": a, "addr": a, "label": label, "kind": kind,
                         "share": 0.0, "size": 26,
                         "ties": len(counterparties.get(a, ()))}}
        nodes.append(node)
        node_index[a] = node

    node_ids = set(node_index)
    edges = []
    for (f, to), (amt, cnt) in pair.items():
        if f in node_ids and to in node_ids:
            edges.append({"data": {
                "id": f"{f[:8]}-{to[:8]}", "source": f, "target": to,
                "count": cnt, "amount": _humanize_tokens(amt, decimals),
                "amount_raw": amt,
                "width": round(1 + min(6, (cnt ** 0.5) * 1.5), 1),
            }})

    # Whales tied together through a promoted "source" node = coordination.
    source_ids = {n["data"]["id"] for n in nodes if n["data"]["kind"] == "source"}
    clustered = set()
    for a in source_ids:
        for h in counterparties.get(a, ()):
            clustered.add(h)
    clustered_whales = len(clustered)

    # ── Metrics ──
    shares_sorted = sorted(shares, reverse=True)
    top10 = sum(shares_sorted[:10])
    cex = kind_share.get("cex", 0.0)
    burn = kind_share.get("burn", 0.0)
    dex = kind_share.get("dex", 0.0)
    contract = kind_share.get("contract", 0.0)
    real_shares = sorted(
        (n["data"]["share"] for n in nodes if n["data"]["kind"] in ("whale", "holder")),
        reverse=True)
    top10_ex = sum(real_shares[:10])
    hhi = sum(s * s for s in shares)
    gini = _gini(shares)
    whales = sum(1 for n in nodes if n["data"]["kind"] == "whale")

    verdict, tone = _distribution_verdict(top10_ex, whales, burn, len(shares),
                                          clustered_whales)

    metrics = {
        "holder_count": int(token.get("holders_count") or 0),
        "fetched": len(shares),
        "top10_share": round(top10, 2),
        "top10_share_real": round(top10_ex, 2),
        "cex_share": round(cex, 2),
        "burn_share": round(burn, 2),
        "lp_share": round(dex + contract, 2),
        "whale_count": whales,
        "hhi": round(hhi, 1),
        "gini": round(gini, 3),
        "edge_count": len(edges),
        "source_count": len(source_ids),
        "clustered_whales": clustered_whales,
        "verdict": verdict,
        "tone": tone,
    }

    note = ""
    if not is_evm:
        note = ("Solana graph shows the top token accounts by balance and their "
                "concentration. Wallet-to-wallet transfer flows aren't drawn on "
                "Solana (needs an indexer key).")
    elif len(source_ids):
        note = (f"{clustered_whales} top wallets are linked through "
                f"{len(source_ids)} shared source wallet(s) (red) — possible "
                f"coordinated seeding. Tap an edge for the amount moved.")
    elif not edges:
        note = ("No transfers among the top holders in the sampled history — "
                "supply came from fresh wallets, not shuffled between whales.")
    else:
        note = "Edges show real transfers between top wallets. Tap one for the amount."

    sym = token.get("symbol") or "token"
    return {
        "title": f"{sym} — Holder Network",
        "chain": chain,
        "token": token,
        "elements": nodes + edges,
        "metrics": metrics,
        "note": note,
    }


def _gini(shares: list) -> float:
    """Gini coefficient of the fetched holders' shares (0 = equal, →1 = one
    wallet holds all). Standard sorted-cumulative formula."""
    xs = sorted(float(s) for s in shares if s and s > 0)
    n = len(xs)
    if n < 2:
        return 0.0
    total = sum(xs)
    if total <= 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(xs, 1):
        cum += i * x
    return (2 * cum) / (n * total) - (n + 1) / n


def _distribution_verdict(top10_real: float, whales: int, burn: float,
                          n: int, clustered: int = 0) -> tuple:
    """Plain-English read of the distribution + a tone flag (bad/warn/ok) for the
    chip color. Based on real-holder top-10 concentration, escalated when the flow
    graph shows several whales linked through a shared source (coordination)."""
    if n == 0:
        return "No holder data available.", "warn"
    # Coordination overrides: several whales seeded through one source is a
    # stronger pump/rug signal than concentration alone.
    coord = (f" ⚠ {clustered} of the top wallets are linked through a shared "
             f"source — looks coordinated." if clustered >= 3 else "")
    if top10_real >= 60 or clustered >= 3:
        return (f"Highly concentrated — the top real holders control "
                f"~{top10_real:.0f}% of supply.{coord} A handful of wallets could "
                f"crash the price. High rug/dump risk.", "bad")
    if top10_real >= 35:
        return (f"Concentrated — top holders sit on ~{top10_real:.0f}% of supply. "
                f"Watch these {whales} whale wallets for coordinated selling.{coord}",
                "warn")
    if top10_real >= 18:
        return (f"Moderately distributed — top holders hold ~{top10_real:.0f}%. "
                f"Some whale presence but not dominant.{coord}", "warn")
    return (f"Fairly distributed — top real holders hold only "
            f"~{top10_real:.0f}% of supply, spread across many wallets.{coord}", "ok")
