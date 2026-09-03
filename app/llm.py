import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

async def fast_llm_json(instruction: str, max_tokens: int = 4096) -> Optional[dict]:
    """One tool-free completion against the local vLLM, parsed as JSON.
    Rotates through available local vLLM endpoints before failing open.
    Supports both traditional and reasoning models (extracting JSON from content or reasoning).
    Ensures sufficient token headroom for reasoning models (e.g. Nemotron 3.5).
    """
    configured_urls = getattr(main, "VLLM_ENDPOINTS", None) or []
    default_pool = [
        getattr(main, "VLLM_URL", "http://10.0.0.30:8000"),
        getattr(main, "VLLM_FAST_URL", "http://10.0.0.30:8000"),
        "http://10.0.0.30:8000",
        "http://10.0.0.141:8000",
        "http://10.0.0.30:8001",
    ]
    urls = []
    for u in list(configured_urls) + default_pool:
        if u and u not in urls:
            urls.append(u)

    # Reasoning models (Nemotron 3.5, etc.) consume 1000-1800 tokens on thinking
    # before emitting content. Ensure at least 4096 tokens so completions do not truncate.
    token_budget = max(max_tokens, 4096)

    def _extract_json(src: str) -> Optional[dict]:
        if not src or not src.strip():
            return None
        matches = list(re.finditer(r'\{[\s\S]*\}', src))
        for m in reversed(matches):
            cand = m.group(0)
            try:
                return json.loads(cand)
            except Exception:
                end_pos = cand.rfind("}")
                while end_pos > 0:
                    sub = cand[:end_pos + 1]
                    try:
                        return json.loads(sub)
                    except Exception:
                        end_pos = cand.rfind("}", 0, end_pos)
        return None

    for target_url in urls:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=3.0)) as client:
                model = _fast_model.get(target_url)
                if not model:
                    resp = await client.get(f"{target_url}/v1/models")
                    if resp.status_code != 200:
                        continue
                    data = resp.json().get("data", [])
                    if not data:
                        continue
                    model = data[0]["id"]
                    _fast_model[target_url] = model

                payload = {
                    "model": model,
                    "temperature": 0.2,
                    "max_tokens": token_budget,
                    "messages": [{"role": "user", "content": instruction}],
                }
                resp = await client.post(f"{target_url}/v1/chat/completions", json=payload)
                if resp.status_code != 200:
                    logger.warning(f"fast_llm_json {target_url} returned HTTP {resp.status_code}")
                    continue

                res_json = resp.json()
                msg = (res_json.get("choices") or [{}])[0].get("message") or {}
                content = (msg.get("content") or "").strip()
                reasoning = (msg.get("reasoning") or "").strip()

                # Prioritize content, fall back to reasoning if content is empty
                parsed = _extract_json(content) if content else None
                if parsed is None and reasoning:
                    parsed = _extract_json(reasoning)

                if parsed is not None:
                    return parsed
        except Exception as e:
            if target_url in _fast_model:
                _fast_model[target_url] = None
            logger.warning(f"fast_llm_json failed on {target_url}: {type(e).__name__}: {e}")
            continue

    return None


async def ground_query(message: str) -> dict:
    """Turn a raw utterance into a structured retrieval intent. ONE fast LLM pass,
    memoised per-message so the image / products / video builders of a single turn
    share it. Returns
      {subject, intent, retrieval_query, hyde, negatives[], ambiguous, clarify}.
    On any failure returns a permissive fallback (subject=message, no negatives) so
    grounding is a pure quality boost and never blocks a turn.

    `negatives` is the key field: for a word that is also a brand/place/other
    meaning it names the wrong senses ("Sandals Resorts", "beach scenery") so the
    relevance gate can reject them — this is the CLIP-negative-filter idea done with
    a vision LLM."""
    key = (message or "").strip().lower()
    if not key:
        return {"subject": "", "intent": "other", "retrieval_query": "",
                "hyde": "", "negatives": [], "ambiguous": False, "clarify": ""}
    if key in _GROUND_CACHE:
        return _GROUND_CACHE[key]
    data = await fast_llm_json(
        f"Today is {datetime.date.today().isoformat()}.\n"
        "You are the intent-grounding step for a live visual dashboard. Turn the "
        "user's ask into a precise retrieval spec so downstream image / video / web "
        "search fetches the RIGHT thing, not something merely on-theme. Return ONLY "
        "JSON, no prose, no code fence:\n"
        '{"subject": "<the concrete subject in plain words, DISAMBIGUATED — if the '
        'word is also a brand / place / other meaning, state the MOST LIKELY '
        'intended one>", '
        '"intent": "<one of: shopping, informational, media, place, data, other>", '
        '"retrieval_query": "<an expanded, unambiguous web-search query for the '
        'subject>", '
        '"hyde": "<one line describing the IDEAL result; for a product, the ideal '
        'PHOTO, e.g. \\"a product photo of open-toe leather sandals footwear\\">", '
        '"negatives": ["<up to 5 categories a WRONG match would fall in: OTHER '
        'meanings of the word, off-topic themes, ads, generic scenery>"], '
        '"freshness": "<the time constraint COPIED VERBATIM from the ask, e.g. '
        '\\"new\\", \\"this week\\", \\"yesterday\\" — empty string if the ask has '
        'no time constraint>", '
        '"ambiguous": <true only if you genuinely cannot tell what they want>, '
        '"clarify": "<if ambiguous, a one-line disambiguating question, else empty>"}\n\n'
        'Example — ASK "sandals":\n'
        '{"subject":"sandals (footwear to wear)","intent":"shopping",'
        '"retrieval_query":"best sandals to buy footwear reviews","hyde":"a product '
        'photo of sandals footwear on a plain background","negatives":["Sandals '
        'Resorts / Caribbean all-inclusive vacation","beach or ocean scenery","feet '
        'or legs lifestyle photo","advertisement"],"freshness":"",'
        '"ambiguous":false,"clarify":""}\n\n'
        f'USER: "{message}"',
        max_tokens=400,
    )
    if not isinstance(data, dict) or not str(data.get("subject") or "").strip():
        result = {"subject": (message or "").strip(), "intent": "other",
                  "retrieval_query": (message or "").strip(), "hyde": "",
                  "negatives": [], "freshness": "", "ambiguous": False, "clarify": ""}
    else:
        negs = data.get("negatives")
        result = {
            "subject": str(data.get("subject") or message).strip()[:200],
            "intent": str(data.get("intent") or "other").strip().lower(),
            "retrieval_query": str(data.get("retrieval_query") or message).strip()[:200],
            "hyde": str(data.get("hyde") or "").strip()[:300],
            "negatives": ([str(n).strip()[:80] for n in negs if str(n).strip()][:6]
                          if isinstance(negs, list) else []),
            "freshness": str(data.get("freshness") or "").strip()[:60],
            "ambiguous": bool(data.get("ambiguous")),
            "clarify": str(data.get("clarify") or "").strip()[:200],
        }
    _GROUND_CACHE[key] = result
    if len(_GROUND_CACHE) > _GROUND_CACHE_MAX:      # FIFO evict oldest
        _GROUND_CACHE.pop(next(iter(_GROUND_CACHE)), None)
    return result


async def _fast_multimodal_json(content: list, max_tokens: int = 300,
                                temperature: float = 0.1) -> Optional[dict]:
    """fast_llm_json for a multimodal message. `content` is an OpenAI content array
    (text + image_url blocks). Same model discovery, JSON parsing and fail-open
    contract as fast_llm_json — returns None on any error."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            model = _fast_model["name"]
            if not model:
                resp = await client.get(f"{VLLM_URL}/v1/models")
                model = resp.json()["data"][0]["id"]
                _fast_model["name"] = model
            resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            })
            text = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group(0)) if match else None
    except Exception as e:
        _fast_model["name"] = None
        logger.warning(f"_fast_multimodal_json failed: {type(e).__name__}: {e}")
        return None


async def route_with_llm(message: str, context_block: str) -> Optional[dict]:
    """Classify an ask the fast lane missed into one or more server-buildable
    widgets, or a deferral. `context_block` is build_turn_context()'s bundle:
    recent turns (with a content gist) + the widgets on the canvas now — so the
    router can recognise a follow-up and target the widget it refines. Returns:
      {"widgets": [{"type", "query", "modifiers", "target"}], "reason"} to
        build+spawn (target = a canvas widget id to UPDATE in place, or omitted), or
      {"defer": true} to hand off to the full agent (removals, edits, note
      dictation, custom widgets, small talk), or
      None on any model failure — the caller then falls back to the agent, so the
    router is a pure latency/quality optimization and never a hard gate.

    The returned dict also carries `checks` — the PRE-FLIGHT answers (see
    _preflight_block). This pass already has the message AND the canvas context,
    and on the research-deferral path its whole output used to be logged and
    thrown away, so asking it a handful of yes/no questions costs no extra call
    and no extra latency. `checks` is best-effort: a missing or malformed object
    yields {} and every downstream consumer degrades to today's behaviour."""
    catalog = "\n".join(f"- {name}: {spec}" for name, (_p, spec) in ROUTER_WIDGETS.items())
    data = await fast_llm_json(
        "You are the router for a live dashboard. Choose the widget(s) that best "
        "serve the user and the search query for each. Return ONLY a JSON object, "
        "no prose, no code fence:\n"
        '{"widgets": [{"type": "<type>", "query": "<query>", "modifiers": {}, '
        '"target": "<canvas widget id or omit>"}], "reason": "<=8 words", '
        '"checks": {"is_arithmetic": <bool>, "needs_fresh_data": <bool>, '
        '"wants": "<answer|watch|listen|see|track|compute|remember|ui-control>", '
        '"subject": "<the real subject, disambiguated>", '
        '"unknowns": ["<what you would still have to look up>"]}}\n'
        "Rules:\n"
        "- Match the widgets to the ask. A NARROW single-intent ask (a forecast, a "
        "ticker, a timer) is ONE widget. A BROAD or rich ask should COMPOSE the "
        "modalities that together serve it — lead with the explanation/answer, then "
        "add supporting media that genuinely helps (an image of the subject, a video "
        "to watch, recent news if it's current, a map if it's a place). Examples: "
        '"plan my Saturday in Seattle" -> weather + map + things-to-do; "tesla stock '
        'and news" -> stock + stock_news; "tell me about black holes" -> answer + '
        "image + video. Do NOT pad with a modality that doesn't serve the subject. Max 4.\n"
        "- For a traffic ask add modifiers {\"traffic\": true}.\n"
        "- FOLLOW-UPS UPDATE, THEY DON'T STACK. Read RECENT TURNS and the canvas "
        "below. If the ask refines something you already built (\"what about the "
        "away team?\" after a scoreboard, \"tell me more\", \"and taco bell?\" after a "
        "news card, another place on an open map), set \"target\" to that widget's "
        "id so the server rewrites it in place. Only omit target when the ask opens "
        "a genuinely NEW subject. Never open a second map/weather/stock of the same kind.\n"
        "- If the ask is to REMOVE / close / clear an EXISTING widget, to "
        "take dictation into a note, to build a custom/one-off widget, to change "
        "the THEME / appearance / settings, or is small "
        'talk with no widget need, return {"defer": true} instead of widgets.\n'
        "- Never invent a type or a widget id. Use only the types listed and the "
        "ids shown below.\n"
        # PRE-FLIGHT. Kept LAST so the tuned widget rules above are untouched.
        # These are the basic questions the agent should have asked itself before
        # reaching for a tool; answering them here means the agent is handed the
        # answers instead of re-deriving intent from raw text.
        '- Then fill in "checks", about the ask as a whole:\n'
        '  "is_arithmetic": true ONLY if the ask IS a calculation or unit/currency '
        "conversion that a calculator alone can finish. A question that merely "
        "MENTIONS numbers, temperatures, weights, times or money is false — "
        '"how long until my 145F chicken hits 165 in a 400F oven" needs cooking '
        "knowledge, so it is false.\n"
        '  "needs_fresh_data": true if answering requires looking something up '
        "rather than reading it off this conversation.\n"
        '  "wants": what the user actually wants OUT of this — an answer, to watch, '
        "to listen, to see, to track, to compute, to remember, or to control the UI.\n"
        '  "subject": the real subject in plain words, disambiguated against the '
        "conversation above.\n"
        '  "unknowns": up to 3 things you would still have to look up. [] if none.\n\n'
        "WIDGET TYPES:\n" + catalog +
        (f"\n\n{context_block[:1200]}" if context_block else "") +
        f'\n\nUSER: "{message}"',
        # Raised from 400: the response now also carries the `checks` object.
        # Too small a ceiling truncates the JSON mid-object and fast_llm_json's
        # brace match then fails, which would silently turn every route into a
        # None -> agent fallback.
        max_tokens=550,
    )
    if not isinstance(data, dict):
        return None
    checks = _clean_preflight_checks(data.get("checks"))
    if data.get("defer"):
        return {"defer": True, "reason": data.get("reason", ""), "checks": checks}
    widgets = data.get("widgets")
    if not isinstance(widgets, list):
        return None
    clean = []
    for w in widgets[:4]:
        if not isinstance(w, dict):
            continue
        wtype = str(w.get("type", "")).strip().lower()
        if wtype not in ROUTER_WIDGETS:
            continue
        tgt = w.get("target")
        clean.append({"type": wtype,
                      "query": str(w.get("query", "") or "").strip(),
                      "modifiers": w.get("modifiers") if isinstance(w.get("modifiers"), dict) else {},
                      "target": str(tgt).lstrip("#").strip() if tgt else None})
    if not clean:
        return None
    # The prompt says "never open a second stock of the same kind", but prose
    # can't outvote sampling: "XLP vs SPY" reliably came back as TWO stock
    # specs and rendered two separate cards. A comparison is one question —
    # collapse same-type stock specs into a single spec whose query joins the
    # tickers; the stock builder renders it as one multi-series chart.
    stock_specs = [w for w in clean if w["type"] == "stock"]
    if len(stock_specs) > 1:
        joined = " vs ".join(w["query"] for w in stock_specs if w["query"])
        stock_specs[0]["query"] = joined
        clean = [w for w in clean if w["type"] != "stock" or w is stock_specs[0]]
        logger.info(f"[ROUTER] collapsed {len(stock_specs)} stock specs into "
                    f"one compare: {joined!r}")
    # A converter pick is the one classification with no net underneath it (see
    # build_router_widget), and `checks.is_arithmetic` is the model's own answer
    # to "is this actually a calculation?" — when it says no, believe it.
    if checks.get("is_arithmetic") is False:
        for w in clean:
            if w["type"] == "converter":
                logger.info(f"[ROUTER] demoted converter -> answer "
                            f"(checks.is_arithmetic=false): {message[:70]!r}")
                w["type"] = "answer"
                w["query"] = w["query"] or message
    return {"widgets": clean, "reason": str(data.get("reason", ""))[:80],
            "checks": checks}


__all__ = [k for k in globals().keys() if not k.startswith('__')]
