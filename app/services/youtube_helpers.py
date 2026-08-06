import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

def extract_youtube_query(text: str) -> str:
    text_lower = text.lower().strip()
    # Strip common trigger prefixes
    pattern = r'^(?:add|show|open|play|create|get)\s+(?:a\s+)?(?:youtube|yt)\s+(?:player\s+|widget\s+|video\s+)*(?:for\s+|with\s+|of\s+)*'
    cleaned = re.sub(pattern, '', text_lower)
    for prefix in ("youtube", "yt", "play"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    quote_match = re.search(r'["\'“]([^"\'”]+)["\'”]', cleaned)
    if quote_match:
        return quote_match.group(1).strip()
    return cleaned.strip()


def clean_video_query(text: str) -> str:
    """Strip medium words so the search hits the subject.
    "pull up a fifa news video" → "fifa news"."""
    cleaned = re.sub(r'[^\w\s]', ' ', (text or '').lower())
    kept = [w for w in cleaned.split() if w not in VIDEO_FILLER]
    return " ".join(kept).strip() or (text or "").strip()


def pick_best_video(hits: list, exclude_ids: set = None):
    """Deterministic pick: the top-ranked hit not yet shown this session.

    Replaces pick_varied_video at selection time (user decision: no
    random.choice — the ranking pipeline already ordered hits best-first, so
    take its word). Variety across repeat asks comes from `exclude_ids` (the
    session's shown-video window): the first unseen hit wins, and when EVERY
    hit was already shown we ignore the exclusion rather than return nothing —
    same exhaustion rule as pick_varied_video. Returns (chosen, other_ids) with
    other_ids in rank order so the embed-error fallback hops to the next-best."""
    if not hits:
        return None, []
    unseen = [h for h in hits if h.get("video_id") not in exclude_ids] if exclude_ids else list(hits)
    chosen = (unseen or hits)[0]
    cid = chosen.get("video_id")
    others = [v["video_id"] for v in unseen if v.get("video_id") and v.get("video_id") != cid]
    others += [v["video_id"] for v in hits
               if v.get("video_id") and v["video_id"] != cid and v["video_id"] not in others]
    return chosen, others


def pick_varied_video(hits: list, k: int = 5, exclude_ids: set = None, wide: bool = False):
    """Choose a video from the top-`k` results at random, for VARIETY.

    A broad ask ("a cookie recipe video", "funny cats") always returned the same
    #1 YouTube hit, which is boring on repeat. Picking from the top handful keeps
    the result relevant (they're all strong matches for the query) while making a
    second identical ask land on something different. Returns
    (chosen_hit, other_ids) where other_ids are the remaining results IN RELEVANCE
    ORDER — so the widget's embed-error fallback still hops to the next-best video,
    not another random one.

    `exclude_ids` drops videos already shown this session so a repeat ask ("another
    one") lands on something genuinely new; if EVERY hit was already shown we ignore
    the exclusion rather than return nothing.

    Deterministic callers (live streams, a named channel) must NOT use this: they
    want the single canonical result, so they keep indexing hits[0] directly.
    """
    if not hits:
        return None, []
    fresh_only = [h for h in hits if h.get("video_id") not in exclude_ids] if exclude_ids else list(hits)
    exhausted = not fresh_only  # every top hit already shown this session
    fresh = fresh_only or hits  # all already seen → fall back to the full list
    # Widen the sampling window when the query is broad (`wide`) or the seen-set has
    # exhausted the fresh pool — otherwise a broad/repeated ask recycles the same
    # top-k evergreen hits forever. A wider window rotates genuinely different videos.
    window = max(1, k * 2 if (wide or exhausted) else k)
    pool = fresh[:window]
    chosen = random.choice(pool)
    cid = chosen.get("video_id")
    # Fallback candidates: fresh ones first (in order), so an embed error also hops
    # to something unseen before falling back to the rest.
    others = [v["video_id"] for v in fresh if v.get("video_id") and v.get("video_id") != cid]
    others += [v["video_id"] for v in hits
               if v.get("video_id") and v.get("video_id") != cid and v["video_id"] not in others]
    return chosen, others


def _load_blocklists() -> None:
    try:
        _blocked_video_ids.update(
            m["key"] for m in database.list_agent_memory(_BLOCKED_VIDEO_CAT))
        _blocked_channels.update(
            (m["key"] or "").lower() for m in database.list_agent_memory(_BLOCKED_CHANNEL_CAT))
    except Exception as e:
        logger.warning(f"could not load video blocklists: {e}")


def block_video(video_id: str, reason: str = "") -> None:
    if not video_id:
        return
    _blocked_video_ids.add(video_id)
    database.add_agent_memory(_BLOCKED_VIDEO_CAT, video_id, reason or None)


def block_channel(channel: str, reason: str = "") -> None:
    if not channel:
        return
    _blocked_channels.add(channel.lower())
    database.add_agent_memory(_BLOCKED_CHANNEL_CAT, channel, reason or None)


def filter_blocked_videos(hits: list) -> list:
    """Drop any hit the user has permanently disliked (by video or by channel).
    If EVERY hit is blocked, returns the originals rather than nothing — a video
    the user half-dislikes still beats an empty player."""
    if not hits:
        return hits
    kept = [h for h in hits
            if h.get("video_id") not in _blocked_video_ids
            and (h.get("channel") or "").lower() not in _blocked_channels]
    return kept or hits


def _shown_video_ids(session_id: str) -> set:
    """Video ids already shown this session — pass to pick_varied_video as
    exclude_ids so a follow-up doesn't repeat one."""
    return set(_session_shown_videos.get(session_id) or ())


def _remember_current_video(session_id: str, hit_or_cfg: dict, query: str) -> None:
    if not session_id:
        return
    vid = hit_or_cfg.get("video_id")
    _session_current_video[session_id] = {
        "video_id": vid,
        "channel": hit_or_cfg.get("channel"),
        "query": query or "",
    }
    if vid:
        hist = _session_shown_videos.setdefault(session_id, deque(maxlen=_SHOWN_HISTORY))
        if vid not in hist:
            hist.append(vid)


def _split_video_subject_topic(message: str) -> tuple:
    """"fox news video newest about the stock market" → ("fox news",
    "stock market"). The subject is the channel-name guess (recency and medium
    words stripped, channel-name words KEPT); the topic is the about-clause,
    used to filter that channel's uploads. No about-clause → topic ""."""
    text = NEWEST_RE.sub(" ", message or "")
    parts = _VIDEO_TOPIC_SPLIT_RE.split(text, maxsplit=1)
    subject = clean_video_query(_SUBJECT_RECENCY_RE.sub(" ", parts[0]))
    # "a new primeagen video" left the subject as "new primeagen", which searched
    # channels for the literal words and bound nothing. The peeled form is
    # returned as a HINT rather than applied destructively: "new" is also the
    # first word of real channel names (New Rockstars), so the caller tries the
    # full subject first and only falls back to the peeled one. Peeling here
    # unconditionally turned "new rockstars video" into "rockstars".
    subject = subject.strip()
    # clean_video_query falls back to its raw input when every word was filler
    # ("newest video about X" → subject "video"). A channel GUESS made of pure
    # filler must be empty — otherwise we scrape a channel search for "video".
    if all(w in VIDEO_FILLER or _SUBJECT_RECENCY_RE.fullmatch(w)
           for w in re.findall(r"\w+", subject.lower())):
        subject = ""
    topic = clean_video_query(parts[1]) if len(parts) > 1 else ""
    return subject, topic


def _creator_evidence(subject: str, message: str = "") -> str:
    """How strongly this subject is a CREATOR rather than a topic:
    'explicit' (a possessive/from-phrase names them outright), 'weak' (contains
    generic topic vocabulary, so a bind needs corroboration), or 'plain'.

    A channel bind answers from ONE feed and skips search entirely, so a topic
    ask must not trigger it: 'cookie recipe' resolves to a real channel called
    '1,000 Cookie Recipes', which would answer every cookie question from that
    creator forever. Rather than curate a word list (brittle, and endless), the
    caller pairs 'weak' with a higher match+verified bar — evidence, not
    vocabulary."""
    subj = (subject or "").strip().lower()
    if not subj:
        return "weak"
    msg = (message or "").lower()
    if re.search(r"\b(?:from|by)\s+" + re.escape(subj[:40]), msg) \
       or re.search(re.escape(subj[:40]) + r"\s*'s\b", msg) \
       or re.search(r"\bchannel\b", msg):
        return "explicit"
    words = [w for w in re.findall(r"\w+", subj) if len(w) > 1]
    if words and any(w in _YT_TOPIC_WORDS for w in words):
        return "weak"
    # NOTE: capitalisation was tried as a "names are proper nouns" signal and
    # REJECTED — users type entirely in lowercase, so it marked 'fox news',
    # 'linus tech tips' and 'paul barron' as topics. Do not reintroduce it.
    # The plural tell below is safe because channel names are not pluralised
    # descriptions ('funny cat videos' vs 'New Rockstars').
    if len(words) > 1 and re.search(r"\b(?:videos|clips|compilations?)\b",
                                    (message or "").lower()):
        return "weak"
    return "plain"


def _topic_in_title(topic: str, title: str) -> bool:
    """At least half the topic's content words appear in the title (substring
    containment, so "stock" also matches "stocks"). Empty topic matches all."""
    toks = [w for w in re.findall(r"\w+", (topic or "").lower()) if len(w) > 2]
    if not toks:
        return True
    t = (title or "").lower()
    return sum(1 for w in toks if w in t) >= (len(toks) + 1) // 2


async def _recency_video_pick(message: str, session_id: str,
                              freshness: Optional[Freshness] = None) -> Optional[dict]:
    """Channel-verified pick for a video ask that NAMES a creator, or any ask
    carrying recency intent. Returns a youtube_player config, or None so the
    caller falls back to its generic search path.

    Order of trust:
      1. The named channel binds → its uploads RSS, strictly reverse-
         chronological and channel-verified by construction, topic-filtered
         when the ask has an about-clause. A topic with no recent upload falls
         back to the channel's newest (right channel > exact topic).
      2. No channel binds → date-ordered search when the ask wants recency;
         otherwise None, so a plain topic ask keeps relevance ranking.

    Deliberately NOT gated on a recency word by its callers: "primeagen video"
    means his LATEST, and requiring the user to say "newest" was why keyword
    search kept serving 5-, 11- and 14-day-old clips over a 3-hour-old upload.
    """
    fresh = freshness or parse_freshness(message)
    # "THE newest X" has one right answer (no seen-exclusion rotation); softer
    # recency ("new", "news", "this week") is still newest-first but rotates to
    # the newest UNSEEN hit on a repeat ask.
    want_newest = bool(NEWEST_RE.search((message or "").lower()))
    # Short vs video. The uploads feed is where this bit mattered most: Shorts
    # dominate it by volume, so "newest <creator> video" answered with a Short
    # until the feed learned to tell them apart. None here means long-form.
    form = parse_video_form(message)
    window = fresh.window_days if fresh else None
    subject, topic = _split_video_subject_topic(message)
    search_q = " ".join(x for x in (subject, topic) if x) or clean_video_query(message)

    chans = []
    if subject:
        try:
            # Creators run several channels (ThePrimeagen / The PrimeTime /
            # ...Highlights). Binding only ONE meant "newest primeagen video"
            # answered from a feed whose head was 11 days old while the same
            # creator had posted 3 hours earlier on a sibling channel. Read the
            # top matches and merge, so "newest" means newest across all of them.
            # Try the subject as written FIRST ("new rockstars" is a channel),
            # then a leading-"new" peeled variant ("new primeagen" -> the
            # creator). Order matters: peeling first renamed New Rockstars.
            variants = [subject]
            peeled = re.sub(r'^new\s+', '', subject, flags=re.I).strip()
            if peeled and peeled != subject:
                variants.append(peeled)
            # Score BOTH variants and keep the stronger bind. Stopping at the
            # first that binds anything is not enough: "new primeagen" matches
            # ThePrimeagen weakly (0.65) and misses the sibling channel holding
            # his newest upload, while the peeled "primeagen" matches at 0.90
            # and finds all three.
            best_chans = []
            for cand_subject in variants:
                got = await _resolve_youtube_channels(
                    cand_subject, limit=4,
                    evidence=_creator_evidence(cand_subject, message))
                if got and (not best_chans
                            or got[0]["match"] > best_chans[0]["match"]):
                    best_chans, subject = got, cand_subject
            chans = best_chans
            # Merge only SIBLINGS of the best match — channels within 0.3 of the
            # winner's score. Taking a fixed top-N pulls in whatever search noise
            # fills the slots (a channel coincidentally titled like the query),
            # and a stranger's uploads must never be served as this creator's
            # "newest".
            if chans:
                best = chans[0]["rank_score"]
                chans = [c for c in chans if c["rank_score"] >= best - 0.3][:3]
        except Exception as e:
            logger.warning(f"[YOUTUBE] channel resolve failed for {subject!r}: {e}")
    chan = chans[0] if chans else None
    if chan:
        feeds = await asyncio.gather(
            *[_youtube_channel_uploads(c["channel_id"], limit=12, form=form)
              for c in chans],
            return_exceptions=True)
        merged, seen_ids = [], set()
        for c, f in zip(chans, feeds):
            if isinstance(f, Exception) or not f:
                continue
            for h in f:
                if h.get("video_id") and h["video_id"] not in seen_ids:
                    seen_ids.add(h["video_id"])
                    merged.append({**h, "channel": h.get("channel") or c["title"]})
        merged.sort(key=lambda h: h["age_days"] if h.get("age_days") is not None else 1e9)
        if len(chans) > 1:
            logger.info(f"[YOUTUBE] merged {len(merged)} uploads across "
                        f"{len(chans)} channel(s) for {subject!r}; newest is "
                        f"{(merged[0]['age_days'] if merged else 0):.2f}d "
                        f"from {(merged[0].get('channel') if merged else '?')!r}")
        feed = filter_blocked_videos(merged)
        if window:
            # An explicit window ("this week") bounds even the trusted channel
            # feed; empty-after-filter falls back to the feed head (right
            # channel, newest available) rather than nothing.
            feed = filter_by_age(feed, window) or feed
        pool = [h for h in feed if _topic_in_title(topic, h.get("title"))]
        if topic and feed and not pool:
            # The feed's newest uploads don't cover the topic. Before settling
            # for the channel's newest-overall, date-search the topic and keep
            # ONLY hits whose channel matches the one we verified — this finds
            # an on-topic upload that slipped past the feed window without ever
            # reopening the door to junk channels.
            searched = filter_blocked_videos(await search_youtube_videos(
                search_q, limit=12, order="date", freshness=fresh, form=form))
            verified = [h for h in searched
                        if _yt_channel_name_match(chan["title"], h.get("channel") or "")]
            if verified:
                verified.sort(key=lambda h: h["age_days"]
                              if h.get("age_days") is not None else 1e9)
                pool = verified
                logger.info(f"[YOUTUBE] topic {topic!r} found via channel-"
                            f"verified search ({len(verified)} hits from "
                            f"{chan['title']!r})")
            else:
                logger.info(f"[YOUTUBE] {chan['title']!r} has no recent upload "
                            f"matching {topic!r} — serving its newest instead")
        pool = pool or feed
        if pool:
            if want_newest:
                # "newest" is a factual ask with one right answer — no variety,
                # no seen-exclusion; the feed head IS the latest.
                top = pool[0]
                cands = [v["video_id"] for v in pool[1:] if v.get("video_id")][:5]
            else:
                # Softer recency: newest UNSEEN upload — deterministic, rotates
                # on repeat asks via the session's shown-video window.
                top, cands = pick_best_video(
                    pool, exclude_ids=_shown_video_ids(session_id))
                cands = cands[:5]
            if top:
                age_min = (top.get("age_days") or 0) * 1440
                logger.info(f"[YOUTUBE] recency pick via channel feed: "
                            f"{top['video_id']} from {chan['title']!r} "
                            f"(~{age_min:.0f} min old, topic={topic!r})")
                _remember_current_video(session_id, top, search_q)
                return {"video_id": top["video_id"],
                        "title": top.get("title") or search_q,
                        "query": search_q,
                        "published": top.get("published"),
                        "channel": chan["title"],
                        "candidates": cands}

    # No channel bound. A plain topic ask ("a cookie recipe video") must keep
    # RELEVANCE ranking — hand it back so the caller's normal search path runs.
    # Only an ask that actually wants recency continues into the date search.
    if not fresh:
        return None

    hits = filter_blocked_videos(await search_youtube_videos(
        search_q, limit=10, order="date", strict_recency=want_newest,
        rerank=True, freshness=fresh, form=form))
    if not hits:
        hits = filter_blocked_videos(
            await search_youtube_videos(search_q, limit=10, form=form))
    if not hits:
        return None
    if want_newest:
        hits = sorted(hits, key=lambda h: h["age_days"]
                      if h.get("age_days") is not None else 1e9)
        top = hits[0]
        cands = [v["video_id"] for v in hits[1:] if v.get("video_id")][:5]
    else:
        # Hits arrive newest-first from the strict pipeline; take the newest
        # unseen one deterministically.
        top, cands = pick_best_video(hits, exclude_ids=_shown_video_ids(session_id))
        cands = cands[:5]
    if not top:
        return None
    _remember_current_video(session_id, top, search_q)
    cfg = {"video_id": top["video_id"], "title": top.get("title") or search_q,
           "query": search_q, "candidates": cands}
    if top.get("stale_fallback"):
        cfg["title"] = f'{cfg["title"]} (newest available)'
        cfg["stale_fallback"] = True
    return cfg


__all__ = [k for k in globals().keys() if not k.startswith('__')]
