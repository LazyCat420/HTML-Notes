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


# Tiny TTL cache. The Ethplorer freekey and the public Solana RPC are shared and
# rate-limited (429), and the canvas re-renders/re-asks the same token often, so
# caching GET results for a minute both softens the limit and makes a re-ask
# instant. Keyed by (url, sorted params). A cached miss (None) is NOT stored, so
# a transient 429 doesn't poison the next real attempt.
_CACHE: dict = {}
_CACHE_TTL = 60.0


def _cache_key(url: str, params: Optional[dict]) -> str:
    if not params:
        return url
    items = sorted((k, str(v)) for k, v in params.items() if k != "apiKey")
    return url + "?" + "&".join(f"{k}={v}" for k, v in items)


async def _get_json(url: str, params: Optional[dict] = None, timeout: float = 12.0):
    """GET → parsed JSON, or None on any error/non-200. Cached for _CACHE_TTL.
    Never raises."""
    import time
    key = _cache_key(url, params)
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, params=params,
                            headers={"User-Agent": "html-notes/1.0",
                                     "Accept": "application/json"})
            if r.status_code != 200:
                logger.info("[crypto] %s -> %s", url, r.status_code)
                return None
            data = r.json()
            _CACHE[key] = (time.time(), data)
            if len(_CACHE) > 512:               # crude bound; drop oldest half
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:256]:
                    _CACHE.pop(k, None)
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

        {coin_id, name, symbol, chain, address, image, source}

    - A 0x… string resolves via CoinGecko's contract endpoint (tries chains in
      order) so a bare address still names its token.
    - Otherwise the best search match wins (lowest market-cap rank), symbol-exact
      preferred so "eth" -> Ethereum, not a random shitcoin whose name contains
      "eth".
    Returns None when nothing resolves. `address`/`chain` may be empty for a coin
    with no single on-chain contract (e.g. native BTC)."""
    q = (query or "").strip()
    if not q:
        return None

    # Bare EVM contract address → resolve token identity across common chains.
    m = EVM_ADDR_RE.search(q)
    if m:
        addr = m.group(0).lower()
        for platform in ("ethereum", "binance-smart-chain", "polygon-pos",
                         "arbitrum-one", "base"):
            c = await cg_coin_by_contract(platform, addr)
            if c and c.get("id"):
                return _identity_from_coin(c, prefer_chain=CG_PLATFORM_TO_CHAIN.get(platform))
        # Unknown to CoinGecko but still a valid ETH address — let the caller try
        # Ethplorer directly for a raw holder graph.
        return {"coin_id": "", "name": "", "symbol": "", "chain": "ethereum",
                "address": addr, "image": "", "source": "address"}

    coins = await cg_search(q)
    if not coins:
        # A base58 string that isn't a known coin is very likely a Solana mint.
        sm = SOL_ADDR_RE.search(q)
        if sm and not EVM_ADDR_RE.search(q):
            return {"coin_id": "", "name": "", "symbol": "", "chain": "solana",
                    "address": sm.group(0), "image": "", "source": "address"}
        return None

    # Scam memecoins hijack famous names/symbols — CoinGecko search for "bitcoin"
    # returns a token whose SYMBOL is literally "BITCOIN" (rank ~5000) alongside
    # real Bitcoin (rank 1). So match by name first, then symbol, and WITHIN each
    # tier pick the best market-cap rank — the impostors all rank far lower.
    ql = q.lower()
    def _by_rank(cs):
        return sorted(cs, key=lambda c: (c.get("market_cap_rank") or 10**9))
    name_exact = [c for c in coins if (c.get("name") or "").lower() == ql]
    sym_exact = [c for c in coins if (c.get("symbol") or "").lower() == ql]
    ranked = _by_rank(name_exact) or _by_rank(sym_exact) or _by_rank(coins)
    best = ranked[0]
    full = await cg_coin(best["id"])
    return _identity_from_coin(full or best)


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
    return {
        "coin_id": coin.get("id", ""),
        "name": coin.get("name", ""),
        "symbol": (coin.get("symbol") or "").upper(),
        "chain": chain,
        "address": address,
        "image": img,
        "source": "coingecko",
    }


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


async def eth_address_info(address: str) -> dict:
    d = await _get_json(f"{ETHPLORER_BASE}/getAddressInfo/{address}",
                        {"apiKey": ETHPLORER_KEY})
    return d or {}


# ── Solana RPC: top holders of a mint ────────────────────────────────────────
async def sol_top_holders(mint: str, limit: int = 20, helius_key: str = "") -> tuple:
    """(holders, supply, decimals) for an SPL mint via RPC. Holders are
    [{address(owner), amount, share}]. Uses Helius if a key is provided (far more
    reliable than the throttled public endpoint). Returns ([], 0, 0) on failure —
    the public RPC 429s often, which is expected and handled by the caller."""
    rpc = (f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
           if helius_key else SOLANA_PUBLIC_RPC)

    supply_r = await _post_json(rpc, {"jsonrpc": "2.0", "id": 1,
                                      "method": "getTokenSupply", "params": [mint]})
    val = ((supply_r or {}).get("result") or {}).get("value") or {}
    decimals = int(val.get("decimals", 0) or 0)
    supply = float(val.get("uiAmount") or 0.0)

    largest = await _post_json(rpc, {"jsonrpc": "2.0", "id": 2,
                                     "method": "getTokenLargestAccounts",
                                     "params": [mint]})
    accts = ((largest or {}).get("result") or {}).get("value") or []
    if not accts:
        return [], supply, decimals

    # getTokenLargestAccounts returns TOKEN ACCOUNTS, not owner wallets. Resolve
    # each to its owner so the graph shows wallets, not throwaway ATAs.
    pubkeys = [a.get("address") for a in accts[:limit] if a.get("address")]
    owners = await _sol_owners(rpc, pubkeys)
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


def build_holder_graph(token: dict, holders: list, transfers: list,
                       chain: str) -> dict:
    """Holders (+ transfers, EVM only) → a cytoscape config + concentration
    metrics. Pure. Contract:

        token = {name, symbol, address}
        holders = [{address, share, balance/amount}]   (share = % of supply)
        transfers = [{from, to, value}]                (EVM; [] for Solana)

    Returns {title, chain, token, elements, metrics, note}. `elements` is a flat
    cytoscape list of node + edge {data:{…}} objects. Nodes carry kind/label/
    share for color+size; edges carry weight (transfer count between the pair)."""
    token_addr = (token.get("address") or "").lower()
    is_evm = chain != "solana"

    # ── Nodes ──
    holder_set = set()
    nodes = []
    kind_share = {}   # kind -> summed % (for the CEX/burn/DEX breakdown)
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
        nodes.append({"data": {
            "id": low, "addr": addr, "label": label, "kind": kind,
            "share": round(share, 3),
            # Node diameter: sqrt so a 40%-holder isn't 40x a 1%-holder, but the
            # eye still reads dominance. 18–90px.
            "size": round(18 + min(72, (share ** 0.5) * 12), 1),
        }})

    # ── Edges (EVM only) ── transfers where BOTH ends are top holders. That is
    # the signal: whales shuffling supply between each other (wash / coordinated
    # distribution) vs supply flowing out to fresh wallets.
    edge_weight = {}
    for t in (transfers or []):
        f = (t.get("from") or "").lower()
        to = (t.get("to") or "").lower()
        if f in holder_set and to in holder_set and f != to:
            key = (f, to)
            edge_weight[key] = edge_weight.get(key, 0) + 1
    edges = []
    for (f, to), w in edge_weight.items():
        edges.append({"data": {
            "id": f"{f[:8]}-{to[:8]}", "source": f, "target": to,
            "weight": w, "width": round(1 + min(6, w * 1.2), 1),
        }})

    # ── Metrics ── everything is over the FETCHED holders (top N), not full
    # supply — labelled as such so nobody reads top-50 HHI as whole-market.
    shares_sorted = sorted(shares, reverse=True)
    top10 = sum(shares_sorted[:10])
    cex = kind_share.get("cex", 0.0)
    burn = kind_share.get("burn", 0.0)
    dex = kind_share.get("dex", 0.0)
    contract = kind_share.get("contract", 0.0)
    # Concentration in real holders: strip custody (CEX), burned and LP/router
    # and the contract itself — those aren't a person who can dump. Top-10 of the
    # whale/holder-only list.
    real_shares = sorted(
        (n["data"]["share"] for n in nodes if n["data"]["kind"] in ("whale", "holder")),
        reverse=True)
    top10_ex = sum(real_shares[:10])
    hhi = sum(s * s for s in shares)                 # Herfindahl over fetched
    gini = _gini(shares)
    whales = sum(1 for n in nodes if n["data"]["kind"] == "whale")

    verdict, tone = _distribution_verdict(top10_ex, whales, burn, len(nodes))

    metrics = {
        "holder_count": int(token.get("holders_count") or 0),
        "fetched": len(nodes),
        "top10_share": round(top10, 2),
        "top10_share_real": round(top10_ex, 2),
        "cex_share": round(cex, 2),
        "burn_share": round(burn, 2),
        "lp_share": round(dex + contract, 2),
        "whale_count": whales,
        "hhi": round(hhi, 1),
        "gini": round(gini, 3),
        "edge_count": len(edges),
        "verdict": verdict,
        "tone": tone,
    }

    note = ""
    if not is_evm:
        note = ("Solana graph shows the top token accounts by balance and their "
                "concentration. Wallet-to-wallet transfer edges aren't drawn on "
                "Solana yet.")
    elif not edges:
        note = ("No transfers between the top holders in the recent window — "
                "supply is moving to fresh wallets, not shuffling among whales.")

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
                          n: int) -> tuple:
    """Plain-English read of the distribution, + a tone flag (bad/warn/ok) for
    the chip color. Based on real-holder top-10 concentration."""
    if n == 0:
        return "No holder data available.", "warn"
    if top10_real >= 60:
        return (f"Highly concentrated — the top real holders control "
                f"~{top10_real:.0f}% of supply. A handful of wallets could crash "
                f"the price by selling. High rug/dump risk.", "bad")
    if top10_real >= 35:
        return (f"Concentrated — top holders sit on ~{top10_real:.0f}% of supply. "
                f"Watch these {whales} whale wallets for coordinated selling.", "warn")
    if top10_real >= 18:
        return (f"Moderately distributed — top holders hold ~{top10_real:.0f}%. "
                f"Some whale presence but not dominant.", "warn")
    return (f"Fairly distributed — top real holders hold only "
            f"~{top10_real:.0f}% of supply, spread across many wallets.", "ok")
