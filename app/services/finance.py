import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

def _sma(values: list, window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return round(sum(values[-window:]) / window, 2)


def _rsi(values: list, period: int = 14) -> Optional[float]:
    """Wilder's RSI. <30 oversold, >70 overbought."""
    if len(values) <= period:
        return None
    gains, losses = [], []
    for prev, curr in zip(values[-(period + 1):-1], values[-period:]):
        delta = curr - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _annualized_volatility(values: list) -> Optional[float]:
    if len(values) < 20:
        return None
    returns = [(b - a) / a for a, b in zip(values[:-1], values[1:]) if a]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round((variance ** 0.5) * (252 ** 0.5) * 100, 1)


async def _yahoo_fundamentals(client: httpx.AsyncClient, symbol: str) -> dict:
    """Company fundamentals. Yahoo's quoteSummary rejects anonymous callers with
    "Invalid Crumb", so grab a cookie + crumb once and reuse it."""
    try:
        if not _yahoo_crumb["crumb"]:
            seed = await client.get("https://fc.yahoo.com", headers={"User-Agent": _YAHOO_UA})
            crumb_resp = await client.get(
                "https://query2.finance.yahoo.com/v1/test/getcrumb",
                headers={"User-Agent": _YAHOO_UA}, cookies=seed.cookies)
            if crumb_resp.status_code == 200 and crumb_resp.text.strip():
                _yahoo_crumb["crumb"] = crumb_resp.text.strip()
                _yahoo_crumb["cookies"] = seed.cookies

        if not _yahoo_crumb["crumb"]:
            return {}

        modules = "summaryDetail,defaultKeyStatistics,financialData,assetProfile"
        resp = await client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}"
            f"?modules={modules}&crumb={urllib.parse.quote(_yahoo_crumb['crumb'])}",
            headers={"User-Agent": _YAHOO_UA}, cookies=_yahoo_crumb["cookies"])
        result = (resp.json().get("quoteSummary", {}).get("result") or [None])[0]
        if not result:
            _yahoo_crumb["crumb"] = None  # expired — re-seed next call
            return {}

        detail = result.get("summaryDetail", {}) or {}
        stats = result.get("defaultKeyStatistics", {}) or {}
        financial = result.get("financialData", {}) or {}
        profile = result.get("assetProfile", {}) or {}

        def fmt(source: dict, key: str):
            value = source.get(key)
            return value.get("fmt") if isinstance(value, dict) else value

        return {
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "market_cap": fmt(detail, "marketCap"),
            "pe_ratio": fmt(detail, "trailingPE"),
            "forward_pe": fmt(detail, "forwardPE"),
            "eps": fmt(stats, "trailingEps"),
            "beta": fmt(detail, "beta"),
            "dividend_yield": fmt(detail, "dividendYield"),
            "profit_margin": fmt(financial, "profitMargins"),
            "revenue": fmt(financial, "totalRevenue"),
            "revenue_growth": fmt(financial, "revenueGrowth"),
            "analyst_target": fmt(financial, "targetMeanPrice"),
            "recommendation": financial.get("recommendationKey"),
        }
    except Exception as e:
        # Clear the crumb on ANY failure, not just the None-result branch above:
        # a 401/429/timeout here left a stale crumb cached for the process
        # lifetime, so every later fundamentals call kept failing until restart
        # (stock cards permanently missing sector/PE/margins after one blip).
        _yahoo_crumb["crumb"] = None
        logger.warning(f"fundamentals({symbol}) failed: {e}")
        return {}


async def _yahoo_chart(client: httpx.AsyncClient, symbol: str, range_: str) -> Optional[dict]:
    interval = _STOCK_INTERVALS.get(range_, "1d")
    resp = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?range={urllib.parse.quote(range_)}&interval={interval}",
        headers={"User-Agent": _YAHOO_UA})
    payload = resp.json()
    return (payload.get("chart", {}).get("result") or [None])[0]


async def stock_snapshot(symbol: str, range_: str = "1mo") -> dict:
    """Full picture for a ticker: price series + technicals + fundamentals.

    Every tools-api market tool (get_stock, get_historical_prices,
    generate_chart) has a null endpoint in this deployment, so without this the
    model falls back to scraping the web for prices — slow, and it can't get
    clean numbers out of it anyway. Yahoo is keyless and answers in well under
    a second.

    Technicals are always computed from a 1y daily series, never from the
    displayed range: a 5-day window has no 200-day moving average, and an RSI
    taken over 5-minute candles is a different (and misleading) number from the
    daily RSI a reader expects. The two fetches run concurrently.
    """
    if range_ not in STOCK_RANGES:
        range_ = "1mo"

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            display, daily, fundamentals = await asyncio.gather(
                _yahoo_chart(client, symbol, range_),
                _yahoo_chart(client, symbol, "1y"),
                _yahoo_fundamentals(client, symbol),
                return_exceptions=True,
            )

        if not isinstance(display, dict) or not display:
            return {"error": f"No price data for '{symbol}'", "is_error": True}
        if not isinstance(fundamentals, dict):
            fundamentals = {}

        meta = display.get("meta", {})
        stamps = display.get("timestamp") or []
        quote = (display.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        # 5d is intraday too, but a bare "13:30" repeats once per day with nothing
        # to tell the days apart — it needs the weekday.
        fmt = {"1d": "%H:%M", "5d": "%a %H:%M"}.get(
            range_, "%b %Y" if range_ in ("5y", "10y", "max") else "%b %d")

        labels, values, vols = [], [], []
        for i, (stamp, close) in enumerate(zip(stamps, closes)):
            if close is None:
                continue
            moment = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
            labels.append(moment.strftime(fmt))
            values.append(round(float(close), 2))
            vols.append(volumes[i] if i < len(volumes) else None)

        if not values:
            return {"error": f"No price data for '{symbol}'", "is_error": True}

        # Technicals off the 1y daily series (falls back to the display series).
        daily_closes = values
        if isinstance(daily, dict) and daily:
            candidate = [c for c in ((daily.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
                         if c is not None]
            if candidate:
                daily_closes = [round(float(c), 2) for c in candidate]

        price = meta.get("regularMarketPrice", values[-1])
        first, last = values[0], values[-1]
        change_pct = round(((last - first) / first) * 100, 2) if first else 0.0
        sma50, sma200 = _sma(daily_closes, 50), _sma(daily_closes, 200)
        high52, low52 = meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow")

        technicals = {
            "sma_20": _sma(daily_closes, 20),
            "sma_50": sma50,
            "sma_200": sma200,
            "rsi_14": _rsi(daily_closes),
            "volatility": _annualized_volatility(daily_closes),
            "week52_high": round(high52, 2) if high52 else None,
            "week52_low": round(low52, 2) if low52 else None,
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "volume": meta.get("regularMarketVolume"),
            # What a reader actually wants to know: where does price sit relative
            # to trend, and how much of the 52-week band has it used up?
            "trend": ("bullish" if sma50 and sma200 and sma50 > sma200
                      else "bearish" if sma50 and sma200 else None),
            "vs_sma_50": (round(((price - sma50) / sma50) * 100, 1) if sma50 else None),
            "week52_position": (round(((price - low52) / (high52 - low52)) * 100)
                                if high52 and low52 and high52 > low52 else None),
        }

        return {
            "symbol": meta.get("symbol", symbol.upper()),
            "name": meta.get("longName") or meta.get("shortName") or symbol.upper(),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("fullExchangeName"),
            "price": price,
            "range": range_,
            "ranges": list(STOCK_RANGES),
            "change_pct": change_pct,
            "labels": labels,
            "values": values,
            "volumes": vols,
            "technicals": technicals,
            "fundamentals": fundamentals,
        }
    except Exception as e:
        logger.error(f"stock_snapshot({symbol}) error: {e}")
        return {"error": str(e), "is_error": True}


def _extract_compare_tickers(text: str) -> list:
    """Ticker symbols from a compare-shaped ask, in mention order.

    Explicit uppercase tokens win ("NVDA vs SPY vs TSM" — users type tickers
    uppercase). If the phrasing is compare-shaped but yields fewer than two,
    the caller falls back to name resolution per segment."""
    out = []
    for tok in re.findall(r"\$?\b[A-Z]{1,5}(?:\.[A-Z])?\b", text or ""):
        sym = tok.lstrip("$")
        if sym not in _TICKER_STOP and sym not in out:
            out.append(sym)
    return out


def _trend_kind(text: str) -> str:
    for rx, kind in _TREND_KINDS:
        if rx.search(text or ""):
            return kind
    return "trending"


def _range_from_message(text: str, default: str = "1mo") -> str:
    for rx, range_ in _RANGE_WORDS:
        if rx.search(text or ""):
            return range_
    return default


async def _trending_symbols(kind: str = "trending", limit: int = 10) -> list:
    """Real candidate tickers from Yahoo's keyless discovery feeds: trending/US
    for 'trending', the predefined screeners for gainers/losers/most-actives.
    Crypto (BTC-USD), futures (ES=F) and indices (^GSPC) are dropped — the ask
    said stocks. Empty trending falls back to most_actives; [] means the feeds
    are down and the caller should degrade."""
    if kind == "trending":
        url = "https://query1.finance.yahoo.com/v1/finance/trending/US?count=25"
    else:
        url = ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/"
               f"saved?scrIds={kind}&count=25")
    syms: list = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": _YAHOO_UA})
            result = (resp.json().get("finance", {}).get("result") or [{}])[0] or {}
            for q in result.get("quotes") or []:
                s = str((q or {}).get("symbol") or "").upper()
                if re.fullmatch(r"[A-Z][A-Z0-9]{0,4}(?:\.[A-Z])?", s) and s not in syms:
                    syms.append(s)
                if len(syms) >= limit:
                    break
    except Exception as e:
        logger.warning(f"[TRENDING] {kind} feed failed: {e}")
    if not syms and kind == "trending":
        return await _trending_symbols("most_actives", limit)
    logger.info(f"[TRENDING] {kind} -> {syms}")
    return syms


def _universe_from_message(text: str) -> str:
    """The named index a discovery ask is scoped to ('sp500'), or '' for none."""
    for rx, name in _UNIVERSE_RE:
        if rx.search(text or ""):
            return name
    return ""


async def _index_constituents(name: str) -> frozenset:
    """Cached membership set for a stock index. Empty set means 'unknown' — the
    caller must NOT filter on an empty set (that would drop every ticker), it
    should degrade to the unscoped feed and say so in the provenance."""
    name = (name or "").lower()
    url = _INDEX_SOURCES.get(name)
    if not url:
        return frozenset()
    cached = _index_cache.get(name)
    if cached and time.time() - cached[0] <= _INDEX_TTL:
        return cached[1]
    syms = set()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": _YAHOO_UA})
            for line in resp.text.splitlines()[1:]:          # skip CSV header
                sym = line.split(",", 1)[0].strip().upper()
                if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", sym):
                    syms.add(sym)
    except Exception as e:
        logger.warning(f"[INDEX] {name} constituents fetch failed: {e}")
    if syms:
        _index_cache[name] = (time.time(), frozenset(syms))
        logger.info(f"[INDEX] {name} -> {len(syms)} members")
        return _index_cache[name][1]
    # Fetch failed: keep serving a stale set rather than dropping the filter.
    return cached[1] if cached else frozenset()


async def stock_news(query: str, limit: int = 8) -> dict:
    """Stock/company news headlines + ticker matches for a free-text query.

    Yahoo's search endpoint is keyless like the chart endpoint above and
    answers both jobs stock_snapshot can't: "what's the news on X" and
    "find me stocks" (the quotes array is ticker discovery — the model can
    feed those symbols straight into html_notes_stock_history).
    """
    query = (query or "").strip()
    if not query:
        return {"error": "stock_news needs a query (ticker, company name, or topic).", "is_error": True}
    limit = max(1, min(int(limit or 8), 15))

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://query1.finance.yahoo.com/v1/finance/search"
                f"?q={urllib.parse.quote(query)}&newsCount={limit}&quotesCount=6",
                headers={"User-Agent": _YAHOO_UA})
            payload = resp.json()

        news = []
        for item in payload.get("news") or []:
            stamp = item.get("providerPublishTime")
            thumbs = (item.get("thumbnail") or {}).get("resolutions") or []
            news.append({
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "published": (datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
                              .strftime("%Y-%m-%d %H:%M UTC") if stamp else None),
                "url": item.get("link"),
                "image": (thumbs[-1].get("url") or "") if thumbs else "",
                "related_tickers": item.get("relatedTickers") or [],
            })

        matches = [
            {
                "symbol": q.get("symbol"),
                "name": q.get("longname") or q.get("shortname"),
                "exchange": q.get("exchDisp"),
                "type": q.get("quoteTypeDisp") or q.get("quoteType"),
            }
            for q in (payload.get("quotes") or [])
            if q.get("symbol")
        ]

        if not news and not matches:
            return {"news": [], "matches": [], "count": 0,
                    "message": "Nothing found. Retry with a ticker symbol or a shorter company name."}
        return {"news": news, "matches": matches, "count": len(news)}
    except Exception as e:
        logger.error(f"stock_news({query}) error: {e}")
        return {"error": str(e), "is_error": True}


async def _finnews_articles(query: str = "", tickers: Optional[list] = None,
                            limit: int = 12) -> list:
    """Multi-provider financial news via scraper-service's finnews collector.

    Yahoo's search returns only a handful of hits from one source; finnews fans
    out across ~10 keyed financial-news APIs (finnhub, marketaux, polygon,
    newsapi, ...) and returns ticker-tagged, provider-SUMMARISED articles. Pass
    `tickers` to reach the ticker-based providers (finnhub etc., the richest),
    else `query` for the keyword providers. Normalised to the stock_news item
    shape; `og_desc` is seeded from the provider summary so the editor has real
    material even when the article page won't scrape. Best-effort — [] on failure.
    """
    payload: dict = {"source": "finnews", "days_back": 7}
    if tickers:
        payload["tickers"] = [t for t in tickers if t][:5]
    elif query:
        payload["query"] = query
    else:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{SCRAPER_SERVICE_URL}/collect", json=payload)
            data = resp.json()
    except Exception as e:
        logger.warning(f"finnews fetch failed for {tickers or query!r}: {e}")
        return []
    out = []
    for a in (data.get("items") or []):
        title = (a.get("title") or "").strip()
        url = a.get("url") or ""
        if not title or not url:
            continue
        pub = a.get("published_at") or ""
        published = (pub[:16].replace("T", " ") + " UTC") if len(pub) >= 16 else None
        out.append({
            "title": title,
            "publisher": a.get("publisher") or a.get("provider") or "",
            "published": published,
            "url": url,
            "image": "",
            "related_tickers": a.get("tickers") or [],
            "og_desc": (a.get("summary") or "").strip()[:400],
        })
        if len(out) >= limit:
            break
    return out


def _merge_news(*lists: list) -> list:
    """Merge news-item lists, deduped by normalised title, preserving order
    (Yahoo first, then finnews). Prefers the first occurrence but backfills an
    empty image/og_desc from a later duplicate that has one."""
    merged, by_key = [], {}
    for lst in lists:
        for n in lst:
            key = _norm_title_key(n.get("title") or "")
            if not key:
                continue
            if key in by_key:
                keep = by_key[key]
                keep["image"] = keep.get("image") or n.get("image")
                keep["og_desc"] = keep.get("og_desc") or n.get("og_desc")
                continue
            by_key[key] = n
            merged.append(n)
    return merged


async def fetch_fx_rates(base: str) -> dict:
    """Latest FX rates for `base`, keyless via open.er-api.com, cached via the
    shared tool cache (~5m — rates barely move intraday).
    Returns {base, rates:{CODE:rate}, updated} or {} on failure."""
    base = (base or "USD").upper()
    if not re.fullmatch(r"[A-Z]{3}", base):
        return {}
    cached = get_cached_tool_result(f"fx:{base}")
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(f"https://open.er-api.com/v6/latest/{base}")
            data = r.json()
        if data.get("result") != "success" or not isinstance(data.get("rates"), dict):
            return {}
        out = {"base": base, "rates": data["rates"],
               "updated": (data.get("time_last_update_utc") or "")[:16]}
        cache_tool_result(f"fx:{base}", out)
        return out
    except Exception as e:
        logger.warning(f"fetch_fx_rates({base}) failed: {e}")
        return {}


async def _stock_video_commentary(symbol: str, company: str = "", limit: int = 2) -> list:
    """Analyst/commentary TRANSCRIPTS for a ticker via scraper-service's youtube
    collector (yt-dlp + youtube-transcript-api). This is the one path that gives
    us what a human analyst actually SAID, not just a headline — real material for
    the sentiment section of a report. Slow (yt-dlp, ~10-25s) and best-effort;
    returns [{title, channel, transcript}] or [] on failure/timeout."""
    q = f"{company or symbol} stock analysis"
    try:
        # 25s cap: transcripts are the slowest source and gate the whole report
        # (everything is gathered concurrently). If yt-dlp is slow/rate-limited the
        # report ships without the Sentiment section rather than hanging.
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post(f"{SCRAPER_SERVICE_URL}/collect", json={
                "source": "youtube", "query": q, "require_transcript": True,
                "limit": limit, "days_back": 30, "sort": "date"})
            data = r.json()
    except Exception as e:
        logger.warning(f"stock video commentary failed for {symbol!r}: {e}")
        return []
    out = []
    for v in (data.get("items") or [])[:limit]:
        tr = v.get("transcript") or ""
        if isinstance(tr, str) and len(tr) > 400:
            out.append({"title": v.get("title", ""), "channel": v.get("channel", ""),
                        "transcript": tr})
    return out


def _fundamentals_lines(snap: dict) -> str:
    """Compact, LLM-friendly rendering of the snapshot's numbers so the report
    model gets clean facts instead of a raw dict."""
    f = snap.get("fundamentals") or {}
    t = snap.get("technicals") or {}
    rows = [
        ("Price", snap.get("price")), ("Change over range %", snap.get("change_pct")),
        ("Sector", f.get("sector")), ("Industry", f.get("industry")),
        ("Market cap", f.get("market_cap")), ("P/E (trailing)", f.get("pe_ratio")),
        ("Forward P/E", f.get("forward_pe")), ("EPS", f.get("eps")),
        ("Beta", f.get("beta")), ("Dividend yield", f.get("dividend_yield")),
        ("Profit margin", f.get("profit_margin")), ("Revenue", f.get("revenue")),
        ("Revenue growth", f.get("revenue_growth")),
        ("Analyst target", f.get("analyst_target")),
        ("Recommendation", f.get("recommendation")),
        ("RSI(14)", t.get("rsi_14")), ("SMA50", t.get("sma_50")),
        ("SMA200", t.get("sma_200")), ("Trend", t.get("trend")),
        ("Price vs SMA50 %", t.get("vs_sma_50")),
        ("52wk high", t.get("week52_high")), ("52wk low", t.get("week52_low")),
        ("52wk position %", t.get("week52_position")),
        ("Annualized volatility %", t.get("volatility")),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in rows if v is not None and v != "")


async def _dexs_snapshot(ref: str, range_: str = "30d") -> dict:
    """DexScreener-sourced token (a "dexs:<chainId>:<pool>" ref) -> the same
    crypto_card payload, using DexScreener for live price/liquidity/mcap and
    GeckoTerminal for the OHLCV chart. This is the keyless path that makes ANY
    token with a live pool chartable — microcap memecoins CoinGecko never listed."""
    try:
        _, chain_id, pool = ref.split(":", 2)
    except ValueError:
        return {"is_error": True}
    gt_net = cryptolib.DEXS_CHAIN.get(chain_id, (chain_id, chain_id))[1]
    slug = cryptolib.DEXS_CHAIN.get(chain_id, (chain_id, chain_id))[0]
    pair, (labels, values) = await asyncio.gather(
        cryptolib.dexscreener_pair(chain_id, pool),
        cryptolib.gt_ohlcv(gt_net, pool, range_),
    )
    if not pair:
        return {"is_error": True}
    bt = pair.get("baseToken") or {}
    try:
        price = float(pair.get("priceUsd")) if pair.get("priceUsd") else None
    except (TypeError, ValueError):
        price = None
    chg = (pair.get("priceChange") or {}).get("h24")
    return {
        "is_error": False,
        "coin_id": ref,
        "name": bt.get("name", ""),
        "symbol": (bt.get("symbol") or "").upper(),
        "image": (pair.get("info") or {}).get("imageUrl", "") or "",
        "price": price,
        "price_str": _fmt_usd(price),
        "change_pct": round(chg, 2) if isinstance(chg, (int, float)) else None,
        "market_cap": _fmt_usd(pair.get("marketCap") or pair.get("fdv")),
        "market_cap_rank": None,
        "volume": _fmt_usd((pair.get("volume") or {}).get("h24")),
        "liquidity": _fmt_usd((pair.get("liquidity") or {}).get("usd")),
        "high_24h": "—", "low_24h": "—", "ath": "—", "ath_change_pct": None,
        "supply": None,
        "range": range_,
        "labels": labels,
        "values": values,
        "platforms": {slug: bt.get("address", "")} if bt.get("address") else {},
        "dex": pair.get("dexId", ""),
        "source": "dexscreener",
    }


async def _crypto_snapshot(coin_id: str, range_: str = "30d") -> dict:
    """Coin ref -> the crypto_card payload (price header + chart + stats +
    contracts). A "dexs:*" ref goes to DexScreener+GeckoTerminal; anything else is
    a CoinGecko coin id. {is_error:True} when it can't be loaded."""
    if coin_id.startswith("dexs:"):
        return await _dexs_snapshot(coin_id, range_)
    days = _CRYPTO_RANGES.get(range_, "30")
    coin, (labels, values) = await asyncio.gather(
        cryptolib.cg_coin(coin_id),
        cryptolib.cg_market_chart(coin_id, days),
    )
    if not coin or not coin.get("id"):
        return {"is_error": True}
    md = coin.get("market_data") or {}

    def _usd(d):
        return (d or {}).get("usd")

    platforms = {k: v for k, v in (coin.get("platforms") or {}).items() if v}
    chg = md.get("price_change_percentage_24h")
    return {
        "is_error": False,
        "coin_id": coin.get("id"),
        "name": coin.get("name", ""),
        "symbol": (coin.get("symbol") or "").upper(),
        "image": ((coin.get("image") or {}).get("large")
                  or (coin.get("image") or {}).get("small") or ""),
        "price": _usd(md.get("current_price")),
        "price_str": _fmt_usd(_usd(md.get("current_price"))),
        "change_pct": round(chg, 2) if isinstance(chg, (int, float)) else None,
        "market_cap": _fmt_usd(_usd(md.get("market_cap"))),
        "market_cap_rank": coin.get("market_cap_rank"),
        "volume": _fmt_usd(_usd(md.get("total_volume"))),
        "high_24h": _fmt_usd(_usd(md.get("high_24h"))),
        "low_24h": _fmt_usd(_usd(md.get("low_24h"))),
        "ath": _fmt_usd(_usd(md.get("ath"))),
        "ath_change_pct": round(md.get("ath_change_percentage", {}).get("usd"), 1)
            if isinstance(md.get("ath_change_percentage"), dict)
            and isinstance(md.get("ath_change_percentage", {}).get("usd"), (int, float))
            else None,
        "supply": md.get("circulating_supply"),
        "range": range_,
        "labels": labels,
        "values": values,
        "platforms": platforms,   # {chain_slug or cg-platform: address}
    }
    # "Chart no matter what": if CoinGecko's chart came back empty (rate-limited,
    # or a thinly-covered microcap) but the coin has an on-chain contract, pull
    # the price series from GeckoTerminal via its DEX pool instead.
    if not values and platforms:
        gl, gv = await _gt_chart_for_contract(platforms, range_)
        if gv:
            snap["labels"], snap["values"] = gl, gv
    return snap


async def _gt_chart_for_contract(platforms: dict, range_: str) -> tuple:
    """Given a coin's {cg-platform: address} map, find its deepest DEX pool via
    DexScreener and pull GeckoTerminal OHLCV — the keyless chart fallback for any
    token with a live pool. ([], []) if none found."""
    for cg_platform, addr in platforms.items():
        if not addr:
            continue
        try:
            best = cryptolib._best_pair(
                await cryptolib.dexscreener_by_address(addr), None)
            if not best:
                continue
            chain_id = best.get("chainId")
            gt_net = cryptolib.DEXS_CHAIN.get(chain_id, (chain_id, chain_id))[1]
            gl, gv = await cryptolib.gt_ohlcv(gt_net, best.get("pairAddress", ""), range_)
            if gv:
                return gl, gv
        except Exception as e:
            logger.info(f"[CRYPTO] GT chart fallback failed for {addr}: {e}")
    return [], []


async def _resolve_ticker(query: str) -> str:
    """Company name / free text → a ticker symbol. A bare ticker ('TSLA') passes
    through; anything else is resolved via Yahoo's search matches ('Apple' -> AAPL)."""
    q = (query or "").strip()
    if not q:
        return ""
    if re.fullmatch(r"[A-Za-z.\-]{1,6}", q) and q.upper() == q:
        return q.upper()
    data = await stock_news(q, limit=1)
    for m in (data.get("matches") or []):
        if m.get("symbol"):
            return m["symbol"]
    # Uppercase single token is very likely already a symbol ('tsla' typed lower).
    if re.fullmatch(r"[A-Za-z.\-]{1,6}", q):
        return q.upper()
    return ""


__all__ = [k for k in globals().keys() if not k.startswith('__')]
