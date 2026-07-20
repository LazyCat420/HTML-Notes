import base64
import hashlib
import html
import json
import urllib.parse
from typing import Any

def json_escape(val: Any) -> str:
    return html.escape(json.dumps(val))

def esc(val: Any) -> str:
    """HTML-escape a value for direct interpolation into widget markup."""
    return html.escape(str(val if val is not None else ""))

# Widget chrome shared by every server-rendered widget: a header bar with an
# icon, a title and a close button that works with or without Alpine.
def widget_header(title: str, icon: str = "widgets", subtitle: str = "") -> str:
    # Single row: the subtitle sits inline after the title rather than stacking a
    # second line under it, so the bar stays one line tall (~30px instead of ~44px)
    # and gives the space back to the widget body.
    subtitle_html = (
        f'<span class="text-[0.65rem] text-slate-400 tracking-wider normal-case truncate shrink">{esc(subtitle)}</span>'
        if subtitle else ""
    )
    return f"""
        <div class="widget-header flex items-center justify-between bg-black/30 px-3 py-1.5 border-b border-white/10 relative z-20 shrink-0">
            <div class="flex items-baseline gap-2 min-w-0">
                <span class="material-symbols-outlined text-[1rem] text-purple-300 self-center shrink-0">{esc(icon)}</span>
                <h3 class="font-bold text-white tracking-wide truncate text-sm leading-tight shrink-0">{esc(title)}</h3>
                {subtitle_html}
            </div>
            <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn text-white/50 hover:text-red-400 transition-colors shrink-0 ml-2 self-center">
                <span class="material-symbols-outlined text-[1.1rem]">close</span>
            </button>
        </div>
    """

def _host_of(url: str) -> str:
    """Bare hostname, for labelling a source link ('reuters.com')."""
    try:
        host = urllib.parse.urlparse(url).netloc
    except Exception:
        return "source"
    return host[4:] if host.startswith("www.") else (host or "source")


def _monogram_tile(text: str) -> str:
    """Fallback visual when an item has no image: a glowing monogram tile."""
    letter = (text or "?").strip()[:1].upper() or "?"
    return f"""
        <div class="item-thumb w-14 h-14 shrink-0 rounded-xl bg-gradient-to-tr from-slate-700 to-slate-500 flex items-center justify-center ring-1 ring-white/10 shadow-lg">
            <span class="text-xl font-bold text-white/80">{esc(letter)}</span>
        </div>
    """

import re as _re

# Inline markdown → HTML on ALREADY-ESCAPED text. The escaping (html.escape) runs
# first, so `<`/`>`/`&` are inert; markdown markers (* _ ` [ ]) survive escaping
# untouched, so we can safely wrap them here. Links are restricted to http(s) so a
# summariser can never inject a javascript:/data: URL. The whole canvas is also
# DOMPurify-sanitised on the client, so this is defense-in-depth, not the only gate.
def _md_inline(text: str) -> str:
    text = esc(text)
    text = _re.sub(r'`([^`]+)`',
                   r'<code class="px-1 py-0.5 rounded bg-white/10 text-[0.8em]">\1</code>', text)
    text = _re.sub(r'\*\*([^*]+)\*\*', r'<strong class="font-semibold text-white">\1</strong>', text)
    text = _re.sub(r'__([^_]+)__', r'<strong class="font-semibold text-white">\1</strong>', text)
    text = _re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', text)
    # [label](http…) — only http/https links survive; anything else renders as plain label.
    def _link(m):
        label, url = m.group(1), m.group(2)
        if url.lower().startswith(("http://", "https://")):
            return (f'<a href="{url}" target="_blank" rel="noopener" '
                    f'class="text-purple-300 hover:text-purple-200 hover:underline">{label}</a>')
        return label
    text = _re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', _link, text)
    return text


# A Markdown table separator row: |:---|---:|:---:| in any mix, one or more cols.
_TABLE_SEP_RE = _re.compile(r'^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$')


def _render_markdown(md: str) -> str:
    """Small, safe Markdown-subset → HTML for the data_card answer block: headings,
    unordered/ordered lists, blockquotes, horizontal rules and paragraphs, with
    inline bold/italic/code/links. Deliberately NOT a full CommonMark parser — the
    summariser is prompted to use exactly these constructs, and a bounded renderer
    can't be surprised into emitting unsafe HTML."""
    if not md:
        return ""
    lines = str(md).replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)

    def flush_list(buf, ordered):
        if not buf:
            return
        tag = "ol" if ordered else "ul"
        cls = ("list-decimal" if ordered else "list-disc") + " list-inside space-y-1 my-2 text-sm text-slate-200"
        lis = "".join(f'<li class="leading-relaxed pl-1">{_md_inline(x)}</li>' for x in buf)
        out.append(f'<{tag} class="{cls}">{lis}</{tag}>')

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # Horizontal rule
        if _re.fullmatch(r'(-{3,}|\*{3,}|_{3,})', stripped):
            out.append('<hr class="border-white/10 my-3">')
            i += 1
            continue
        # Headings
        m = _re.match(r'(#{1,6})\s+(.*)', stripped)
        if m:
            level = len(m.group(1))
            size = {1: "text-base", 2: "text-sm", 3: "text-sm"}.get(level, "text-xs")
            out.append(f'<h4 class="{size} font-bold text-white mt-3 mb-1 tracking-wide">{_md_inline(m.group(2))}</h4>')
            i += 1
            continue
        # Unordered list block
        if _re.match(r'[-*+]\s+', stripped):
            buf = []
            while i < n and _re.match(r'\s*[-*+]\s+', lines[i]):
                buf.append(_re.sub(r'^\s*[-*+]\s+', '', lines[i]))
                i += 1
            flush_list(buf, ordered=False)
            continue
        # Ordered list block
        if _re.match(r'\d+[.)]\s+', stripped):
            buf = []
            while i < n and _re.match(r'\s*\d+[.)]\s+', lines[i]):
                buf.append(_re.sub(r'^\s*\d+[.)]\s+', '', lines[i]))
                i += 1
            flush_list(buf, ordered=True)
            continue
        # Table block: a header row, a |:---|---| separator, then body rows.
        #
        # build_answer_config's prompt tells the summariser "a comparison -> a
        # Markdown table", and until this existed nothing here could render one:
        # the rows fell through to the paragraph branch below, which joins lines
        # with " " — so a comparison arrived as a single wrapped blob of pipes
        # and dashes. The prompt asked for something the renderer couldn't draw.
        #
        # Kept to the same bounded spirit as the rest of this function: cells go
        # through _md_inline (so they escape), a row is only a row if the
        # separator line is actually there, and ragged rows are padded/truncated
        # to the header width rather than emitting uneven <td>s.
        if stripped.startswith("|") and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            def cells(row: str) -> list:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            headers = cells(stripped)
            i += 2  # header + separator
            body = []
            while i < n and lines[i].strip().startswith("|"):
                row = cells(lines[i].strip())
                row = (row + [""] * len(headers))[:len(headers)]
                body.append(row)
                i += 1
            head_html = "".join(
                f'<th class="text-left font-semibold text-white px-2 py-1 '
                f'border-b border-white/15">{_md_inline(h)}</th>' for h in headers)
            body_html = "".join(
                "<tr>" + "".join(
                    f'<td class="align-top px-2 py-1 border-b border-white/5">'
                    f'{_md_inline(c)}</td>' for c in row) + "</tr>"
                for row in body)
            # The card is a fixed-width dashboard tile, so a wide comparison has
            # to scroll inside its own box rather than stretch the widget.
            out.append(
                '<div class="overflow-x-auto my-2">'
                '<table class="w-full text-sm text-slate-200 border-collapse">'
                f'<thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody>'
                '</table></div>')
            continue
        # Blockquote
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(_re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            out.append('<blockquote class="border-l-2 border-purple-400/40 pl-3 my-2 text-slate-300 italic text-sm">'
                       + "<br>".join(_md_inline(x) for x in buf) + '</blockquote>')
            continue
        # Paragraph (gather consecutive non-block lines)
        buf = []
        while i < n and lines[i].strip() and not _re.match(r'(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|-{3,}$|\*{3,}$)', lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append(f'<p class="text-sm text-slate-200 leading-relaxed my-2">{_md_inline(" ".join(buf))}</p>')
    return "".join(out)


def render_data_card(widget_id: str, config: dict) -> str:
    """Universal server-rendered data widget (the reliable path for news,
    recipes, weather, search results — any structured data). The data is baked
    into the HTML at render time, so nothing depends on client-side fetching.

    Contract: {title, subtitle?, icon?, image?, answer?(markdown), content?,
        items?/sources?: [{title, description?, image?, url?, badge?, meta?}]}
    An `answer` is the synthesised, human-readable response (rendered as Markdown);
    when present, `items` are treated as supporting SOURCES and shown under a small
    "Sources" heading rather than as the primary content.
    Fallback chain: answer/content -> items -> raw config dump. Never blank.
    """
    title = config.get("title", "Data")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "article")
    hero = config.get("image", "")
    content = config.get("content", "")
    # `content` is the alias for `answer`. The two agent-facing documents
    # disagree: the SYSTEM_PROMPT tells the model to pass 'answer', while the
    # MCP tool schema's data_card description lists 'content' as the prose key
    # and does not mention 'answer' at all. A model following the schema wrote
    # its brief into `content`, and because the render chain below is a strict
    # `if answer / elif items / elif content`, a card with BOTH content and
    # items dropped the prose entirely — the model did the research, said "I
    # added a summary", and the card showed only headlines. Nothing repaired it
    # either: _data_card_quality_gap only inspects `answer` and `items`, so
    # `content` was invisible to the quality floor too.
    answer = (config.get("answer", "") or "") or content
    # `sources` is the semantic alias for `items` once an answer carries the content;
    # accept either so the synthesiser and the older news/search callers both work.
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]

    body_parts = []

    if hero:
        # A fixed h-32 letterbox cropped most photos to an unreadable strip — a
        # portrait shot of footwear came through as a band of ankles. Give the
        # hero a real 16/9 box so the subject survives the crop, cap it so it
        # can't eat a card whose value is the prose below it, and anchor the crop
        # at the TOP (subjects sit above centre far more often than below).
        body_parts.append(f"""
            <div class="hero-image w-full shrink-0 overflow-hidden relative aspect-video max-h-52 bg-slate-950/60">
                <img src="{esc(hero)}" alt="{esc(title)}" class="w-full h-full object-cover object-top" loading="lazy">
                <div class="absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-slate-950/85 to-transparent"></div>
            </div>
        """)

    rendered_items = []
    for item in items:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        i_title = item.get("title") or item.get("text") or item.get("name") or ""
        i_desc = item.get("description") or item.get("summary") or item.get("snippet") or ""
        i_image = item.get("image") or item.get("thumbnail") or ""
        i_url = item.get("url") or item.get("link") or ""
        i_badge = item.get("badge") or item.get("tag") or ""
        i_meta = item.get("meta") or item.get("source") or item.get("date") or ""
        # Drop fully-empty rows (a malformed config with blank items would otherwise
        # render as empty hover-boxes). Real quality-floor enrichment happens upstream
        # in the async paths; this is just the sync last-resort against blank rows.
        if not (i_title or i_desc or i_url or i_image):
            continue

        thumb = (
            f'<img src="{esc(i_image)}" alt="" loading="lazy" class="item-thumb w-14 h-14 shrink-0 rounded-xl object-cover ring-1 ring-white/10 shadow-lg">'
            if i_image else _monogram_tile(i_title)
        )
        badge_html = (
            f'<span class="item-badge text-[0.6rem] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-200 border border-purple-400/30 shrink-0">{esc(i_badge)}</span>'
            if i_badge else ""
        )
        meta_html = (
            f'<span class="item-meta text-[0.65rem] text-slate-400 tracking-wide">{esc(i_meta)}</span>'
            if i_meta else ""
        )
        # The text IS the answer — the user should be able to read the item without
        # clicking anything. Six lines rather than two, because a two-line clamp cut
        # every headline off mid-sentence and sent them to the link to find the rest.
        desc_html = (
            f'<p class="text-xs text-slate-300 leading-relaxed mt-0.5 line-clamp-6">{esc(i_desc)}</p>'
            if i_desc else ""
        )
        # The title stays plain text and the link is demoted to a small "source"
        # affordance below it. When an item arrives with no description, a linked
        # title collapses the whole card into a row of naked hyperlinks, which is
        # exactly the thing the user has to click to get the information back.
        title_html = f'<span class="text-sm font-semibold text-white leading-snug">{esc(i_title)}</span>'
        source_html = (
            f'<a href="{esc(i_url)}" target="_blank" rel="noopener" '
            f'class="item-source text-[0.65rem] text-purple-300/70 hover:text-purple-200 hover:underline truncate">'
            f'{esc(_host_of(i_url))} ↗</a>'
            if i_url else ""
        )

        rendered_items.append(f"""
            <li class="data-card-item flex items-start gap-3 p-2.5 rounded-xl hover:bg-white/5 transition-colors border border-transparent hover:border-white/10">
                {thumb}
                <div class="flex-grow min-w-0">
                    <div class="flex items-center justify-between gap-2">
                        {title_html}
                        {badge_html}
                    </div>
                    {desc_html}
                    <div class="flex items-center gap-2 mt-1 min-w-0">
                        {meta_html}
                        {source_html}
                    </div>
                </div>
            </li>
        """)

    # A synthesised answer is the primary content; sources are supporting evidence
    # shown beneath it. This is the "summary with links as sources" shape — not a
    # wall of links. Answer + sources live in ONE scroll container so they scroll
    # together as one readable document.
    if answer:
        sources_block = ""
        if rendered_items:
            sources_block = f"""
                <div class="data-card-sources mt-3 pt-3 border-t border-white/10">
                    <div class="flex items-center gap-1.5 mb-1.5 text-slate-400">
                        <span class="material-symbols-outlined text-[0.85rem]">link</span>
                        <span class="text-[0.65rem] font-semibold uppercase tracking-wider">Sources</span>
                    </div>
                    <ul class="flex flex-col gap-1">{''.join(rendered_items)}</ul>
                </div>
            """
        body_parts.append(f"""
            <div class="data-card-answer p-4 overflow-y-auto flex-grow custom-scrollbar">
                <div class="answer-prose">{_render_markdown(answer)}</div>
                {sources_block}
            </div>
        """)
    elif rendered_items:
        body_parts.append(f"""
            <ul class="data-card-list flex flex-col gap-1 p-3 overflow-y-auto flex-grow custom-scrollbar">
                {''.join(rendered_items)}
            </ul>
        """)
    elif content:
        # Legacy plain-text content: render through Markdown too so a caller that
        # passes lightly-formatted text still gets lists/headings, not flat lines.
        body_parts.append(f"""
            <div class="data-card-content p-4 overflow-y-auto flex-grow custom-scrollbar">{_render_markdown(content)}</div>
        """)
    else:
        # Last-resort fallback: show whatever config we got as key/value rows
        # instead of rendering an empty or broken card.
        rows = "".join(
            f'<div class="flex justify-between gap-3 py-1.5 border-b border-white/5"><span class="text-xs uppercase tracking-wider text-slate-400">{esc(k)}</span><span class="text-sm text-slate-200 text-right">{esc(v)}</span></div>'
            for k, v in config.items() if k not in ("title", "subtitle", "icon") and not isinstance(v, (dict, list))
        ) or '<p class="text-slate-400 text-xs italic text-center py-6">No data provided</p>'
        body_parts.append(f'<div class="data-card-content p-4 overflow-y-auto flex-grow custom-scrollbar">{rows}</div>')

    # A hero eats ~208px of a 380px card, leaving barely two lines of the answer
    # visible before the reader has to scroll — which reads as "the picture broke
    # the card". Cards carrying a photo get the height back.
    card_h = "h-[560px]" if hero else "h-[380px]"
    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container data-card col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col {card_h} group">
        {widget_header(title, icon, subtitle)}
        {''.join(body_parts)}
    </div>
    """

def render_image(widget_id: str, config: dict) -> str:
    """Image display widget. Contract: {title?, url | images:[{url, caption?}], caption?}"""
    title = config.get("title", "Image")
    images = config.get("images") or []
    if not images and config.get("url"):
        images = [{"url": config["url"], "caption": config.get("caption", "")}]
    normalized = []
    for img in images:
        if isinstance(img, str):
            normalized.append({"url": img, "caption": ""})
        elif isinstance(img, dict) and img.get("url"):
            normalized.append({"url": img["url"], "caption": img.get("caption", "")})

    if normalized:
        shown = normalized[:4]
        # One image → a single square hero; several → a square-tile grid. Either way
        # each tile is a box (aspect-square + object-cover), never a cropped letterbox.
        grid_cls = "grid-cols-1" if len(shown) == 1 else "grid-cols-2"
        figures = []
        for img in shown:
            caption_html = (
                f'<figcaption class="text-[0.68rem] text-slate-200 px-2 py-1 bg-black/50 backdrop-blur-sm absolute bottom-0 inset-x-0 line-clamp-2">{esc(img["caption"])}</figcaption>'
                if img.get("caption") else ""
            )
            figures.append(f"""
                <figure class="relative aspect-square overflow-hidden rounded-xl bg-slate-950 ring-1 ring-white/10">
                    <img src="{esc(img['url'])}" alt="{esc(img.get('caption') or title)}" loading="lazy" class="absolute inset-0 w-full h-full object-cover">
                    {caption_html}
                </figure>
            """)
        body = f'<div class="image-widget-body grid {grid_cls} gap-2 p-3 flex-grow overflow-y-auto custom-scrollbar content-start">{"".join(figures)}</div>'
    else:
        body = """
            <div class="flex flex-col items-center justify-center flex-grow text-slate-400 gap-2">
                <span class="material-symbols-outlined text-4xl opacity-40">image_not_supported</span>
                <span class="text-xs italic">No image available</span>
            </div>
        """

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container image-widget col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(title, "imagesmode")}
        {body}
    </div>
    """

def render_products(widget_id: str, config: dict) -> str:
    """Product / shopping-recommendation grid. Each item is a big REFERENCE PHOTO
    that is itself a link to the source page, so the user sees what the thing looks
    like and clicks the picture to buy/read more — the shape asked for by "help me
    find good outdoor shoes": pictures as reference, click-through to the source.

    Contract: {title, subtitle?, icon?, items:[{title|name, description?, image?,
        url?, price?, badge?, meta?}]}. An item with no image degrades to a
    monogram tile so the grid never shows a broken frame.
    """
    title = config.get("title", "Recommendations")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "shopping_bag")
    items = config.get("items") or config.get("sources") or config.get("products") or []
    if isinstance(items, dict):
        items = [items]

    cards = []
    for item in items[:8]:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        i_url = item.get("url") or item.get("link") or ""
        # Contract: every card carries a NAME and a context line — never a naked
        # image tile. Fall back through name→title→source host so a card is always
        # labelled, and let the description degrade to the host when nothing better
        # exists, so the user always knows what they're looking at / clicking.
        i_title = (item.get("title") or item.get("name") or item.get("text")
                   or (_host_of(i_url) if i_url else "") or "Result")
        i_meta = item.get("meta") or item.get("source") or (_host_of(i_url) if i_url else "")
        i_desc = (item.get("description") or item.get("summary") or item.get("snippet")
                  or (f"From {i_meta}" if i_meta else ""))
        i_image = item.get("image") or item.get("thumbnail") or ""
        i_price = item.get("price") or ""
        i_badge = item.get("badge") or item.get("tag") or ""

        # The whole card is the link when there's a url — image, name and all —
        # so clicking the picture opens the source, exactly as requested.
        tag, href = ("a", f'href="{esc(i_url)}" target="_blank" rel="noopener"') if i_url else ("div", "")

        if i_image:
            media = f"""
                <div class="product-media relative w-full aspect-square overflow-hidden bg-slate-950 shrink-0">
                    <img src="{esc(i_image)}" alt="{esc(i_title)}" loading="lazy"
                         class="absolute inset-0 w-full h-full object-cover group-hover/card:scale-105 transition-transform duration-300">
                    {f'<span class="absolute top-2 right-2 text-xs font-bold px-2 py-0.5 rounded-lg bg-black/70 text-emerald-300 backdrop-blur-sm">{esc(i_price)}</span>' if i_price else ''}
                    {f'<span class="absolute top-2 left-2 text-[0.6rem] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-purple-500/70 text-white backdrop-blur-sm">{esc(i_badge)}</span>' if i_badge else ''}
                </div>
            """
        else:
            media = f"""
                <div class="product-media relative w-full aspect-square overflow-hidden bg-gradient-to-br from-slate-700 to-slate-800 flex items-center justify-center shrink-0">
                    <span class="text-5xl font-bold text-white/40">{esc((i_title or '?')[:1].upper())}</span>
                    {f'<span class="absolute top-2 right-2 text-xs font-bold px-2 py-0.5 rounded-lg bg-black/70 text-emerald-300">{esc(i_price)}</span>' if i_price else ''}
                </div>
            """

        cards.append(f"""
            <{tag} {href} class="product-card group/card flex flex-col rounded-2xl overflow-hidden bg-white/5 hover:bg-white/10 ring-1 ring-white/10 hover:ring-purple-400/40 transition-all no-underline">
                {media}
                <div class="p-2.5 flex flex-col gap-1 flex-grow">
                    <span class="text-sm font-semibold text-white leading-snug line-clamp-2">{esc(i_title)}</span>
                    {f'<p class="text-xs text-slate-300 leading-relaxed line-clamp-3">{esc(i_desc)}</p>' if i_desc else ''}
                    {f'<span class="mt-auto pt-1 text-[0.65rem] text-purple-300/70 truncate">{esc(i_meta)} ↗</span>' if i_meta else ''}
                </div>
            </{tag}>
        """)

    if cards:
        body = f"""
            <div class="products-grid grid grid-cols-2 gap-3 p-3 overflow-y-auto flex-grow custom-scrollbar">
                {''.join(cards)}
            </div>
        """
    else:
        body = """
            <div class="flex flex-col items-center justify-center flex-grow text-slate-400 gap-2">
                <span class="material-symbols-outlined text-4xl opacity-40">shopping_bag</span>
                <span class="text-xs italic">No recommendations found</span>
            </div>
        """

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container products-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(title, icon, subtitle)}
        {body}
    </div>
    """

def render_chart(widget_id: str, config: dict) -> str:
    """Chart widget. Contract: {title?, chart: <full Chart.js config>} or
    {title?, type?, labels: [str], values: [num]} — normalized here, baked as a
    language-chart code block that the frontend converts to a Chart.js canvas.
    Falls back to a data_card of label/value rows if the data is unusable."""
    title = config.get("title", "Chart")
    chart_config = config.get("chart")
    if not isinstance(chart_config, dict) or "data" not in chart_config:
        labels = config.get("labels") or []
        values = config.get("values") or config.get("data") or []
        if isinstance(values, dict):
            labels, values = list(values.keys()), list(values.values())
        if not (isinstance(labels, list) and isinstance(values, list) and labels and values):
            # Fallback chain: unusable chart data renders as a data card.
            items = [{"title": str(l), "meta": str(v)} for l, v in zip(labels or [], values or [])]
            return render_data_card(widget_id, {"title": title, "icon": "monitoring", "items": items,
                                                "content": config.get("content", "")})
        chart_config = {
            "type": config.get("type", "line"),
            "data": {
                "labels": labels,
                "datasets": [{
                    "label": config.get("label", title),
                    "data": values,
                    "borderColor": "#4fc3f7",
                    "backgroundColor": "rgba(79, 195, 247, 0.18)",
                    "fill": True,
                    "tension": 0.35,
                }],
            },
            "options": {"responsive": True, "maintainAspectRatio": False},
        }

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container chart-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(title, "monitoring")}
        <div class="chart-body flex-grow p-3 min-h-0">
            <pre class="chart-config-block" style="display:none"><code class="language-chart">{esc(json.dumps(chart_config))}</code></pre>
        </div>
    </div>
    """

def render_checklist(widget_id: str, config: dict) -> str:
    title = config.get("title", "Checklist")
    items = config.get("items", [])
    
    # Normalize items to objects {text: str, done: bool} if LLM passed list of strings
    normalized_items = []
    for item in items:
        if isinstance(item, str):
            normalized_items.append({"text": item, "done": False})
        elif isinstance(item, dict):
            normalized_items.append({
                "text": item.get("text", ""),
                "done": item.get("done", False)
            })
    items_json = json_escape(normalized_items)
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[280px] group" x-data="checklistWidget({json_escape(title)}, {items_json})">
        <!-- Close Button -->
        <button title="Close Widget" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
            <span class="material-symbols-outlined text-[1.2rem]">close</span>
        </button>
        
        <h3 class="text-lg font-bold mb-3 text-white truncate pr-6" x-text="title"></h3>
        <div class="flex gap-2 mb-3">
            <input x-model="newItem" @keydown.enter="addTask" type="text" placeholder="Add task..." class="px-3 py-1.5 rounded-xl bg-white/5 text-white flex-grow border border-white/10 focus:outline-none focus:border-purple-500 text-sm">
            <button @click="addTask" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 transition-colors rounded-xl text-white font-bold shadow">+</button>
        </div>
        <ul class="space-y-2 overflow-y-auto flex-grow pr-1 custom-scrollbar">
            <template x-for="(item, idx) in items" :key="idx">
                <li class="flex items-center gap-3 p-2 rounded-xl transition-all duration-300 group/item border border-transparent"
                    :class="{{'bg-green-500/10 border-green-500/20 text-green-300': item.done, 'hover:bg-white/5': !item.done}}">
                    <input type="checkbox" x-model="item.done" class="rounded border-white/10 text-purple-600 focus:ring-purple-500 w-4 h-4 cursor-pointer">
                    <span :class="{{'line-through opacity-50': item.done}}" x-text="item.text" class="text-sm flex-grow cursor-pointer" @click="item.done = !item.done"></span>
                    <button @click="removeTask(idx)" class="opacity-0 group-hover/item:opacity-100 text-red-400 hover:text-red-300 transition-opacity">×</button>
                </li>
            </template>
            <li x-show="items.length === 0" class="text-slate-400 text-xs italic text-center py-4">No tasks yet</li>
        </ul>
    </div>
    """

def render_clock(widget_id: str, config: dict) -> str:
    timezone = config.get("timezone") or "local"
    mode = config.get("mode") or "clock"
    duration = config.get("duration_seconds") or config.get("duration") or 0
    try:
        duration = int(float(duration))
    except (TypeError, ValueError):
        duration = 0
    # A duration with no explicit mode means the user asked for a timer.
    if mode == "clock" and duration > 0:
        mode = "countdown"
    if mode not in ("clock", "stopwatch", "countdown"):
        mode = "clock"
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[280px] justify-between group" x-data="clockWidget({json_escape(timezone)}, {json_escape(mode)}, {duration})">
        <!-- Close Button -->
        <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
            <span class="material-symbols-outlined text-[1.2rem]">close</span>
        </button>
        
        <div class="flex-grow flex flex-col items-center justify-center">
            <div class="text-4xl font-light text-white tracking-widest font-mono" :class="finished ? 'text-red-400 animate-pulse' : ''" x-text="time">--:--:--</div>
            <div class="text-xs text-purple-300 uppercase tracking-widest mt-2 font-semibold" x-text="date">---</div>
        </div>

        <div x-show="mode !== 'clock'" class="w-full mt-2 flex gap-2 justify-center">
            <button @click="toggle()" class="px-4 py-1.5 rounded-xl text-xs font-semibold bg-purple-600/60 hover:bg-purple-500/70 border border-white/10 transition-colors"
                x-text="finished ? 'Restart' : (running ? 'Pause' : 'Start')">Start</button>
            <button @click="reset()" class="px-4 py-1.5 rounded-xl text-xs font-semibold bg-black/30 hover:bg-black/50 text-slate-300 border border-white/10 transition-colors">Reset</button>
        </div>

        <div x-show="mode === 'clock'" class="opacity-0 group-hover:opacity-100 transition-opacity w-full mt-2">
            <select x-model="selectedTimezone" class="w-full bg-black/30 text-slate-300 text-xs rounded-xl border border-white/10 px-3 py-2 focus:outline-none focus:border-purple-500 transition-colors cursor-pointer appearance-none text-center">
                <option value="local">Local Time</option>
                <option value="UTC">UTC</option>
                <option value="America/New_York">New York (EST/EDT)</option>
                <option value="America/Chicago">Chicago (CST/CDT)</option>
                <option value="America/Los_Angeles">Los Angeles (PST/PDT)</option>
                <option value="Europe/London">London (GMT/BST)</option>
                <option value="Europe/Paris">Paris (CET/CEST)</option>
                <option value="Asia/Tokyo">Tokyo (JST)</option>
                <option value="Asia/Shanghai">Shanghai (CST)</option>
                <option value="Australia/Sydney">Sydney (AEST/AEDT)</option>
            </select>
        </div>
    </div>
    """

def render_notes(widget_id: str, config: dict) -> str:
    title = config.get("title", "Quick Notes")
    content = config.get("content", "")
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[280px] group" x-data="notesWidget({json_escape(title)}, {json_escape(content)})">
        <!-- Close Button -->
        <button title="Close Widget" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
            <span class="material-symbols-outlined text-[1.2rem]">close</span>
        </button>
        
        <h3 class="text-lg font-bold mb-2 text-white pr-6 truncate" x-text="title"></h3>
        <textarea x-model="content" class="w-full bg-white/5 text-slate-200 p-3.5 rounded-2xl border border-white/10 focus:outline-none focus:border-purple-500 resize-none flex-grow shadow-inner text-sm leading-relaxed" placeholder="Type your notes here..."></textarea>
    </div>
    """

def render_iframe_app(widget_id: str, config: dict) -> str:
    url = (config.get("url") or "").strip()
    title = config.get("title", "App Window")
    icon = config.get("icon", "🌐")

    # A missing/blank url used to fall back to src="about:blank" over a near-black
    # background — a widget that renders as a solid black box. Show a readable
    # placeholder instead, and only offer the "open" link when there's a real url.
    if not url or url == "about:blank":
        body = (
            '<div class="flex-grow flex flex-col items-center justify-center text-center p-6 gap-3 text-slate-400">'
            '<span class="material-symbols-outlined text-4xl opacity-40">public_off</span>'
            '<p class="text-sm">No app URL was provided for this window.</p>'
            '</div>'
        )
        open_link = ""
    else:
        # Most sites send X-Frame-Options / Cloudflare bot walls and refuse to be
        # framed directly (the "Max challenge attempts exceeded" black box). Frame-
        # friendly hosts (video/map embeds, wikipedia mobile) embed as-is; everything
        # else loads through the same-origin /widgets/embed reader, which fetches the
        # page server-side so the iframe always shows real content.
        _FRAME_OK = ("youtube.com/embed", "youtube-nocookie.com", "youtu.be",
                     "openstreetmap.org/export", "google.com/maps/embed",
                     "output=embed",  # classic keyless Google Maps embed (traffic/directions)
                     "player.vimeo.com", "codepen.io", "en.m.wikipedia.org")
        lu = url.lower()
        embeddable = any(h in lu for h in _FRAME_OK)
        src = url if embeddable else f"/widgets/embed?u={urllib.parse.quote(url, safe='')}"
        body = (
            f'<iframe src="{esc(src)}" class="w-full flex-grow border-none bg-slate-950" '
            f'sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>'
        )
        open_link = (
            f'<a href="{esc(url)}" target="_blank" rel="noopener" '
            f'class="text-white/50 hover:text-white transition-colors" title="Open Full App">'
            f'<span class="material-symbols-outlined text-[1.2rem]">open_in_new</span></a>'
        )

    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        <!-- Title Bar -->
        <div class="flex items-center justify-between bg-black/30 p-3 border-b border-white/10 relative z-20">
            <div class="flex items-center gap-2">
                <span class="text-xl">{icon}</span>
                <h3 class="font-bold text-white tracking-wide truncate max-w-[250px]">{title}</h3>
            </div>
            <div class="flex items-center gap-3">
                {open_link}
                <button title="Close Widget" class="close-widget-btn text-white/50 hover:text-red-400 transition-colors">
                    <span class="material-symbols-outlined text-[1.2rem]">close</span>
                </button>
            </div>
        </div>
        <!-- Content -->
        {body}
    </div>
    """

def render_mini_music_player(widget_id: str, config: dict) -> str:
    genre = config.get("genre", "")
    autoplay = str(config.get("autoplay", False)).lower()
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-gradient-to-br from-purple-950/70 via-indigo-950/60 to-slate-950/70 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[280px] justify-between group" x-data="musicPlayerWidget({json_escape(genre)}, {autoplay})">
        <!-- Background Blur/Glow effect -->
        <div class="absolute inset-0 bg-cover bg-center opacity-20 mix-blend-overlay pointer-events-none" style="background-image: url('https://images.unsplash.com/photo-1514525253161-7a46d19cd819?q=80&w=600&auto=format&fit=crop')"></div>
        <div class="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-900/40 to-transparent pointer-events-none"></div>
        
        <!-- Top Bar: Genre / Close -->
        <div class="relative z-10 flex justify-between items-start">
            <div class="bg-black/40 backdrop-blur-md px-3 py-1 rounded-full border border-white/10 flex items-center gap-1.5 shadow-sm">
                <span class="material-symbols-outlined text-[1rem] text-purple-300">graphic_eq</span>
                <span class="text-xs font-semibold tracking-wider text-purple-200 uppercase" x-text="genreFilter || 'Radio'"></span>
            </div>
            <button title="Close Widget" class="close-widget-btn text-white/50 hover:text-white bg-black/20 hover:bg-black/40 rounded-full p-1.5 backdrop-blur-sm transition-all shadow-sm z-20">
                <span class="material-symbols-outlined text-[1rem]">close</span>
            </button>
        </div>
        
        <!-- Track Info -->
        <div class="relative z-10 flex items-center gap-4 mt-2">
            <div class="w-14 h-14 shrink-0 rounded-2xl bg-gradient-to-tr from-fuchsia-500 to-orange-500 shadow-lg flex items-center justify-center relative overflow-hidden ring-2 ring-white/10">
                <div class="absolute inset-0 bg-black/20 transition-opacity" :class="{{'opacity-0': !isPlaying, 'animate-pulse': isPlaying}}"></div>
                <span class="material-symbols-outlined text-2xl text-white relative z-10">album</span>
            </div>
            <div class="flex-grow min-w-0 flex flex-col justify-center">
                <h4 class="text-base font-bold text-white truncate leading-tight drop-shadow-md" x-text="currentTrack ? currentTrack.title : 'Searching signals...'"></h4>
                <p class="text-xs text-purple-200 truncate mt-0.5 drop-shadow-sm font-medium" x-text="currentTrack ? currentTrack.artist : 'Please wait'"></p>
            </div>
        </div>

        <!-- Progress Bar & Time -->
        <div class="relative z-10 w-full mt-2">
            <div class="w-full relative group/progress cursor-pointer py-1" @click="handleSeek($event)">
                <div class="h-1.5 w-full bg-white/10 rounded-full overflow-hidden backdrop-blur-sm shadow-inner relative">
                    <div class="h-full bg-gradient-to-r from-purple-400 to-fuchsia-400 rounded-full shadow-[0_0_10px_rgba(216,180,254,0.5)] transition-all duration-100" :style="'width: ' + progress + '%'"></div>
                </div>
            </div>
            <div class="flex justify-between text-[10px] text-purple-300 font-mono mt-1 px-0.5">
                <span x-text="formatTime(currentTime)">0:00</span>
                <span x-text="formatTime(duration)">0:00</span>
            </div>
        </div>
        
        <!-- Controls -->
        <div class="relative z-10 flex items-center justify-between px-1 mt-1">
            <button @click="toggleShuffle()" class="transition-colors p-1.5 rounded-lg" :class="{{'text-purple-300 font-bold bg-white/5': isShuffle, 'text-white/50 hover:text-white': !isShuffle}}" title="Shuffle">
                <span class="material-symbols-outlined text-lg">shuffle</span>
            </button>
            
            <!-- Volume Slider -->
            <div class="flex items-center gap-1 group/volume">
                <button @click="toggleMute()" class="text-white/50 hover:text-white transition-colors p-1" title="Mute">
                    <span class="material-symbols-outlined text-lg" x-text="isMuted ? 'volume_off' : (volume > 0.5 ? 'volume_up' : 'volume_down')">volume_up</span>
                </button>
                <input type="range" min="0" max="1" step="0.05" x-model="volume" @input="setVolume(volume)" class="w-10 h-1 bg-white/20 rounded-lg appearance-none cursor-pointer accent-purple-400 group-hover/volume:w-16 transition-all duration-200">
            </div>
            
            <div class="flex items-center gap-2">
                <button @click="prevTrack()" class="w-8 h-8 rounded-full bg-white/5 hover:bg-white/10 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                    <span class="material-symbols-outlined text-base">skip_previous</span>
                </button>
                
                <button @click="playPause()" class="w-10 h-10 rounded-2xl bg-purple-300 hover:bg-purple-200 text-slate-900 flex items-center justify-center shadow-lg transition-all active:scale-95" :disabled="!currentTrack">
                    <span class="material-symbols-outlined text-xl" x-text="isPlaying ? 'pause' : 'play_arrow'" style="font-variation-settings: 'FILL' 1;">play_arrow</span>
                </button>
                
                <button @click="nextTrack()" class="w-8 h-8 rounded-full bg-white/5 hover:bg-white/10 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                    <span class="material-symbols-outlined text-base">skip_next</span>
                </button>
            </div>
            
            <button @click="toggleRepeat()" class="transition-colors p-1.5 rounded-lg" :class="{{'text-purple-300 font-bold bg-white/5': isRepeat, 'text-white/50 hover:text-white': !isRepeat}}" title="Repeat">
                <span class="material-symbols-outlined text-lg">repeat</span>
            </button>
        </div>
        
        <div x-show="error" x-transition class="absolute bottom-2 left-1/2 -translate-x-1/2 bg-red-500/90 text-white text-xs px-3 py-1 rounded-full backdrop-blur-md whitespace-nowrap shadow-lg z-20" x-text="error" style="display: none;"></div>
    </div>
    """

def render_youtube_player(widget_id: str, config: dict) -> str:
    video_id = config.get("video_id", "")
    title = config.get("title", "YouTube Player")
    # Alternate ids to try when the primary video blocks embedding, plus the
    # search query for a client-side re-search as the last resort.
    candidates = [c for c in (config.get("candidates") or []) if isinstance(c, str)]
    query = config.get("query", "") or title

    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[456px] group" x-data="youtubePlayerWidget({json_escape(video_id)}, {json_escape(title)}, {json_escape(candidates)}, {json_escape(query)})">
        <!-- Title Bar -->
        <div class="flex items-center justify-between bg-black/30 p-3 border-b border-white/10 relative z-20">
            <div class="flex items-center gap-2">
                <span class="text-xl text-red-500">📺</span>
                <h3 class="font-bold text-white tracking-wide truncate max-w-[250px]" x-text="title"></h3>
                <span x-show="isLoading" class="text-xs text-slate-400 italic animate-pulse">Resolving stream...</span>
            </div>
            <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn text-white/50 hover:text-red-400 transition-colors">
                <span class="material-symbols-outlined text-[1.2rem]">close</span>
            </button>
        </div>
        <!-- Video Embed -->
        <div class="w-full flex-grow bg-black relative flex items-center justify-center">
            <!-- Loading state overlay -->
            <div x-show="isLoading" class="absolute inset-0 bg-slate-950/80 flex flex-col items-center justify-center z-10">
                <span class="material-symbols-outlined text-4xl text-purple-400 animate-spin mb-2">sync</span>
                <span class="text-sm text-slate-300">Searching YouTube...</span>
            </div>
            <!-- Error state overlay -->
            <div x-show="error" class="absolute inset-0 bg-slate-950/90 flex flex-col items-center justify-center p-4 text-center z-10 gap-2">
                <span class="material-symbols-outlined text-4xl text-red-500 mb-2">error</span>
                <span class="text-sm text-slate-200" x-text="error"></span>
                <a x-show="watchUrl" :href="watchUrl" target="_blank" rel="noopener" class="text-sm text-purple-300 underline hover:text-purple-200">Watch on YouTube ↗</a>
            </div>
            <!-- Iframe player -->
            <template x-if="embedUrl">
                <iframe :src="embedUrl" class="w-full h-full border-none" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
            </template>
        </div>
    </div>
    """

def render_stock_card(widget_id: str, config: dict) -> str:
    """Rich ticker widget: price header, range tabs, chart, technicals, fundamentals.

    The whole snapshot is baked in at spawn time so the widget renders complete
    with no client fetch. Switching range re-fetches /api/stock/<symbol> directly
    — the agent is not involved, so 1D→10Y is instant rather than another
    minute-long agentic turn.
    """
    symbol = config.get("symbol") or config.get("title") or "—"
    snapshot = {
        "symbol": symbol,
        "name": config.get("name") or symbol,
        "currency": config.get("currency", "USD"),
        "price": config.get("price"),
        "range": config.get("range", "1mo"),
        "change_pct": config.get("change_pct", 0),
        "labels": config.get("labels") or [],
        "values": config.get("values") or [],
        "technicals": config.get("technicals") or {},
        "fundamentals": config.get("fundamentals") or {},
    }

    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[620px] group"
         x-data="stockCardWidget({json_escape(snapshot)})">
        <button title="Close Widget" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
            <span class="material-symbols-outlined text-[1.2rem]">close</span>
        </button>

        <!-- Header: symbol, name, price, change -->
        <div class="flex items-end justify-between pr-8 shrink-0">
            <div class="min-w-0">
                <div class="flex items-baseline gap-2">
                    <h3 class="text-xl font-bold tracking-tight" x-text="snapshot.symbol"></h3>
                    <span class="text-xs text-slate-400 truncate" x-text="snapshot.name"></span>
                </div>
                <div class="flex items-baseline gap-2 mt-1">
                    <span class="text-3xl font-semibold tabular-nums" x-text="fmtPrice(snapshot.price)"></span>
                    <span class="text-sm font-semibold tabular-nums"
                          :class="snapshot.change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'"
                          x-text="(snapshot.change_pct >= 0 ? '+' : '') + snapshot.change_pct + '%'"></span>
                    <span class="text-[0.65rem] text-slate-500 uppercase tracking-wider" x-text="snapshot.range"></span>
                </div>
            </div>
            <div class="text-right text-[0.7rem] text-slate-400 shrink-0" x-show="snapshot.technicals.trend">
                <div class="uppercase tracking-wider text-slate-500">Trend</div>
                <div class="font-semibold"
                     :class="snapshot.technicals.trend === 'bullish' ? 'text-emerald-400' : 'text-rose-400'"
                     x-text="snapshot.technicals.trend"></div>
            </div>
        </div>

        <!-- Range tabs -->
        <div class="flex gap-1 mt-3 shrink-0">
            <template x-for="r in ranges" :key="r">
                <button @click="setRange(r)"
                        class="px-2.5 py-1 rounded-lg text-[0.7rem] font-semibold uppercase tracking-wide transition-colors"
                        :class="r === snapshot.range
                            ? 'bg-white/15 text-white'
                            : 'text-slate-400 hover:text-white hover:bg-white/5'"
                        x-text="r"></button>
            </template>
            <span x-show="loading" class="ml-2 self-center text-[0.7rem] text-slate-400">loading…</span>
        </div>

        <!-- Chart -->
        <div class="relative mt-2 h-[150px] shrink-0">
            <canvas x-ref="canvas"></canvas>
        </div>

        <!-- Technicals + fundamentals -->
        <div class="grid grid-cols-2 gap-5 mt-4 overflow-y-auto flex-grow text-[0.72rem]">
            <div>
                <div class="text-[0.62rem] uppercase tracking-wider text-slate-500 mb-1.5">Technicals</div>
                <div class="grid grid-cols-2 gap-x-3 gap-y-1">
                    <template x-for="row in technicalRows()" :key="row.label">
                        <template x-if="row.value !== null && row.value !== undefined">
                            <div class="contents">
                                <span class="text-slate-400 truncate" x-text="row.label"></span>
                                <span class="text-right tabular-nums font-medium"
                                      :class="row.tone" x-text="row.value"></span>
                            </div>
                        </template>
                    </template>
                </div>
            </div>
            <div>
                <div class="text-[0.62rem] uppercase tracking-wider text-slate-500 mb-1.5">Fundamentals</div>
                <div class="grid grid-cols-2 gap-x-3 gap-y-1">
                    <template x-for="row in fundamentalRows()" :key="row.label">
                        <template x-if="row.value !== null && row.value !== undefined && row.value !== ''">
                            <div class="contents">
                                <span class="text-slate-400 truncate" x-text="row.label"></span>
                                <span class="text-right tabular-nums font-medium"
                                      :class="row.tone" x-text="row.value"></span>
                            </div>
                        </template>
                    </template>
                </div>
                <div x-show="!hasFundamentals()" class="text-slate-500 italic">Not available for this symbol.</div>
            </div>
        </div>
    </div>
    """


def render_scoreboard(widget_id: str, config: dict) -> str:
    """Fixtures/scores widget. Contract: the full result of html_notes_sports_scores
    — {league, title, season, events:[{home, away, state, detail, note}]}.

    Server-rendered with the data baked in: widget <script> tags never execute
    through innerHTML, so anything that fetched client-side would render a dead
    "Loading..." shell.
    """
    title = config.get("title") or config.get("league") or "Scores"
    events = config.get("events") or []

    def side_row(side: dict, live: bool, opponent_won: bool) -> str:
        name = esc(side.get("name") or "TBD")
        logo = side.get("logo") or ""
        score = side.get("score")
        record = side.get("record") or ""
        won = side.get("winner")

        # A loser in a finished game is dimmed so a scanning eye lands on the
        # winner without having to compare two numbers.
        name_cls = "text-white font-semibold" if (won or not opponent_won) else "text-slate-400"
        score_cls = ("text-white font-bold" if won or live else
                     "text-slate-400 font-semibold" if opponent_won else "text-white font-semibold")

        badge = (f'<img src="{esc(logo)}" alt="" loading="lazy" class="w-6 h-6 object-contain shrink-0">'
                 if logo else
                 f'<div class="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center shrink-0">'
                 f'<span class="text-[0.55rem] font-bold text-white/70">{esc((side.get("abbrev") or name)[:3].upper())}</span></div>')

        record_html = (f'<span class="text-[0.6rem] text-slate-500 ml-1.5">{esc(record)}</span>'
                       if record else "")

        return f"""
            <div class="flex items-center justify-between gap-2 py-0.5">
                <div class="flex items-center gap-2 min-w-0">
                    {badge}
                    <span class="text-[0.8rem] truncate {name_cls}">{name}</span>
                    {record_html}
                </div>
                <span class="text-sm tabular-nums shrink-0 {score_cls}">{esc(score if score not in (None, "") else "—")}</span>
            </div>
        """

    cards = []
    for event in events:
        home = event.get("home") or {}
        away = event.get("away") or {}
        state = event.get("state")
        live = state == "in"
        final = state == "post"
        detail = event.get("detail") or ""
        note = event.get("note") or ""

        if live:
            status_html = (f'<span class="flex items-center gap-1.5 text-[0.62rem] font-bold uppercase tracking-wider text-rose-400">'
                           f'<span class="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse"></span>{esc(detail or "Live")}</span>')
        else:
            tone = "text-slate-500" if final else "text-sky-300"
            status_html = (f'<span class="text-[0.62rem] font-semibold uppercase tracking-wider {tone}">'
                           f'{esc(detail or ("Final" if final else "Scheduled"))}</span>')

        note_html = (f'<div class="text-[0.6rem] text-slate-500 truncate mt-0.5">{esc(note)}</div>'
                     if note else "")

        cards.append(f"""
            <li class="rounded-xl border border-white/10 bg-white/[0.03] hover:bg-white/[0.06] transition-colors p-2.5">
                <div class="flex items-center justify-between mb-1.5">
                    {status_html}
                    {note_html}
                </div>
                {side_row(away, live, bool(home.get("winner")))}
                {side_row(home, live, bool(away.get("winner")))}
            </li>
        """)

    body = ("".join(cards) if cards else
            '<li class="text-slate-400 text-xs italic text-center py-6">No fixtures — likely the off-season.</li>')

    subtitle = f"{len(events)} matchup{'s' if len(events) != 1 else ''}"

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container scoreboard col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(title, "sports_soccer", subtitle)}
        <ul class="flex flex-col gap-1.5 p-3 overflow-y-auto flex-grow custom-scrollbar">
            {body}
        </ul>
    </div>
    """


def render_weather(widget_id: str, config: dict) -> str:
    """Current conditions + 5-day forecast. Config is the get_weather() result:
    {location, unit, current:{temp,feels_like,humidity,wind,wind_unit,condition,emoji},
     daily:[{day,hi,lo,emoji}]}. Degrades to a data_card if the data is missing."""
    cur = config.get("current") or {}
    if config.get("is_error") or not cur:
        return render_data_card(widget_id, {
            "title": "Weather", "icon": "cloud",
            "content": config.get("error") or "Weather data is unavailable right now.",
        })

    location = config.get("location", "—")
    unit = config.get("unit", "°")
    temp = cur.get("temp")
    emoji = cur.get("emoji", "")
    condition = cur.get("condition", "")

    stat_cells = []
    for label, val in (
        ("Feels", f"{cur.get('feels_like')}{unit}" if cur.get("feels_like") is not None else None),
        ("Humidity", f"{cur.get('humidity')}%" if cur.get("humidity") is not None else None),
        ("Wind", f"{cur.get('wind')} {cur.get('wind_unit','')}" if cur.get("wind") is not None else None),
    ):
        if val:
            stat_cells.append(
                f'<div class="flex flex-col"><span class="text-[0.6rem] uppercase tracking-wider text-slate-400">{esc(label)}</span>'
                f'<span class="text-sm font-semibold text-white">{esc(val)}</span></div>')

    day_tiles = []
    for d in (config.get("daily") or []):
        hi = d.get("hi")
        lo = d.get("lo")
        day_tiles.append(f"""
            <div class="flex flex-col items-center gap-1 px-2.5 py-2 rounded-xl bg-white/5 border border-white/10 shrink-0 min-w-[3.5rem]">
                <span class="text-[0.65rem] uppercase tracking-wider text-slate-400">{esc(d.get('day',''))}</span>
                <span class="text-xl leading-none">{esc(d.get('emoji',''))}</span>
                <span class="text-xs font-semibold text-white">{esc(hi) if hi is not None else '–'}°</span>
                <span class="text-[0.65rem] text-slate-400">{esc(lo) if lo is not None else '–'}°</span>
            </div>
        """)

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container weather-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-gradient-to-br from-sky-900/70 via-slate-900/70 to-indigo-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(location, "partly_cloudy_day", condition)}
        <div class="flex items-center justify-between px-6 pt-5 pb-2">
            <div class="flex flex-col">
                <span class="text-6xl font-extralight tracking-tighter leading-none">{esc(temp) if temp is not None else '—'}<span class="text-3xl align-top text-slate-300 ml-0.5">{esc(unit)}</span></span>
                <span class="text-sm text-slate-300 mt-1">{esc(condition)}</span>
            </div>
            <span class="text-7xl leading-none drop-shadow-lg">{esc(emoji)}</span>
        </div>
        <div class="flex gap-6 px-6 pb-3">{''.join(stat_cells)}</div>
        <div class="mt-auto px-4 pb-4 pt-2 border-t border-white/10">
            <div class="flex gap-2 overflow-x-auto custom-scrollbar">{''.join(day_tiles)}</div>
        </div>
    </div>
    """


def map_payload(config: dict) -> dict:
    """Sanitise a map config into the minimal {center, zoom, markers} the map
    document needs. Shared by render_map (to build the iframe URL) and the
    /widgets/map endpoint (to render the page). Labels are HTML-escaped so a
    web-sourced string can't inject markup into a Leaflet popup."""
    clean = []
    for m in (config.get("markers") or []):
        if not isinstance(m, dict):
            continue
        try:
            lat, lon = float(m.get("lat")), float(m.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        color = str(m.get("color", "") or "")
        # An emoji marker reads far faster than an identical colored dot ("where's
        # the coffee" → ☕). Kept short and escaped; the map document renders it as a
        # divIcon when present, else falls back to the colored circle.
        emoji = str(m.get("emoji", "") or "").strip()[:4]
        clean.append({
            "lat": lat, "lon": lon,
            "label": esc(str(m.get("label", ""))[:90]),
            "detail": esc(str(m.get("detail", ""))[:180]),
            "color": color if color.startswith("#") and len(color) <= 9 else "",
            "emoji": esc(emoji),
        })
    center = config.get("center")
    if not (isinstance(center, dict) and "lat" in center and "lon" in center):
        if clean:
            center = {"lat": sum(c["lat"] for c in clean) / len(clean),
                      "lon": sum(c["lon"] for c in clean) / len(clean)}
        else:
            center = {"lat": 39.5, "lon": -98.35}  # continental US fallback
    try:
        zoom = int(config.get("zoom") or (5 if len(clean) > 1 else (9 if clean else 4)))
    except (TypeError, ValueError):
        zoom = 5
    return {"center": {"lat": center["lat"], "lon": center["lon"]}, "zoom": zoom,
            "markers": clean[:40], "traffic": bool(config.get("traffic"))}


def map_document_html(payload: dict, traffic_tiles_url: str = "") -> str:
    """A complete, standalone Leaflet HTML page for the map <iframe>. Rendered by
    the server (NOT sanitised by the canvas DOMPurify, since it's a separate
    document loaded via iframe src), so the map's own <script> runs normally —
    which is why the map lives in an iframe instead of inline in the canvas, where
    DOMPurify strips <script> tags. `payload` is already label-escaped by
    map_payload(); it's JSON-embedded with <-escaping to prevent </script> breakout.

    `traffic_tiles_url` + payload["traffic"] layers live traffic-flow tiles over the
    base map. It points at our OWN same-origin tile proxy (/widgets/map/traffic/...),
    NOT api.tomtom.com directly, so the TomTom key never reaches the browser and the
    referrer-restricted-key 403 (this iframe is sandboxed → Origin: null → no
    referrer) can't happen. The proxy holds the key server-side."""
    data_json = json.dumps(payload).replace("<", "\\u003c")
    traffic_js = ""
    if payload.get("traffic") and traffic_tiles_url:
        traffic_js = (
            "L.tileLayer('" + traffic_tiles_url
            + "',{maxZoom:18,opacity:0.85,attribution:'© TomTom'}).addTo(map);")
    # Plain string (not f-string): keep Leaflet's {s}/{z}/{x}/{y}{r} tile tokens and
    # the JS braces literal; only __DATA__ is substituted.
    body = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{height:100%;margin:0;background:#0f172a}.leaflet-popup-content{font:13px system-ui}
.emoji-pin{display:flex;align-items:center;justify-content:center;width:32px;height:32px;font-size:20px;line-height:1;
  background:rgba(15,23,42,0.92);border:2px solid var(--pc,#f97316);border-radius:50% 50% 50% 0;
  transform:rotate(-45deg);box-shadow:0 2px 6px rgba(0,0,0,0.5)}
.emoji-pin span{transform:rotate(45deg)}</style>
</head><body><div id="map"></div><script>
var d=__DATA__;
var map=L.map('map',{scrollWheelZoom:false,attributionControl:true}).setView([d.center.lat,d.center.lon],d.zoom);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{maxZoom:18,attribution:'© OpenStreetMap © CARTO'}).addTo(map);
__TRAFFIC_LAYER__
var pts=[];
(d.markers||[]).forEach(function(m){
  var c=m.color||'#f97316';
  var mk;
  if(m.emoji){
    // A teardrop pin carrying the category emoji — far more legible than a dot.
    var icon=L.divIcon({className:'',iconSize:[32,32],iconAnchor:[16,32],popupAnchor:[0,-30],
      html:'<div class="emoji-pin" style="--pc:'+c+'"><span>'+m.emoji+'</span></div>'});
    mk=L.marker([m.lat,m.lon],{icon:icon}).addTo(map);
  }else{
    mk=L.circleMarker([m.lat,m.lon],{radius:8,color:c,fillColor:c,fillOpacity:0.65,weight:2}).addTo(map);
  }
  mk.bindPopup('<b>'+(m.label||'')+'</b>'+(m.detail?'<br>'+m.detail:''));
  if(m.label){mk.bindTooltip(m.label,{direction:'top'});}
  pts.push([m.lat,m.lon]);
});
if(pts.length>1){try{map.fitBounds(pts,{padding:[25,25],maxZoom:12});}catch(e){}}
</script></body></html>"""
    return body.replace("__DATA__", data_json).replace("__TRAFFIC_LAYER__", traffic_js)


def render_map(widget_id: str, config: dict) -> str:
    """Interactive map widget (Leaflet + CARTO dark tiles, keyless). Server-rendered
    template: the agent supplies only DATA. The map itself is loaded in an <iframe>
    from /widgets/map (iframes are DOMPurify-allowed on the canvas; inline <script>
    is not), so the marker data rides in the iframe URL as base64url JSON.

    Contract: {title?, subtitle?, center?:{lat,lon}, zoom?, markers?:[
        {lat, lon, label?, detail?, color?(#hex)}]}"""
    title = config.get("title", "Map")
    subtitle = config.get("subtitle", "")
    payload = map_payload(config)
    token = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    empty_note = ("" if payload["markers"] else
                  '<div class="absolute inset-x-0 bottom-3 text-center text-[0.7rem] text-slate-400 z-[500] pointer-events-none">'
                  'Couldn\'t pin exact spots — showing the region.</div>')

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container map-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[420px] group">
        {widget_header(title, "map", subtitle)}
        <div class="relative flex-grow">
            <iframe src="/widgets/map?d={token}" class="absolute inset-0 w-full h-full border-0" style="background:#0f172a" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-scripts"></iframe>
            {empty_note}
        </div>
    </div>
    """


WIDGET_RENDERERS = {
    "checklist": render_checklist,
    "scoreboard": render_scoreboard,
    "clock": render_clock,
    "notes": render_notes,
    "iframe_app": render_iframe_app,
    "mini_music_player": render_mini_music_player,
    "youtube_player": render_youtube_player,
    "data_card": render_data_card,
    "image": render_image,
    "products": render_products,
    "chart": render_chart,
    "stock_card": render_stock_card,
    "weather": render_weather,
    "map": render_map,
}

def _content_sig(widget_type: str, config: dict) -> str:
    """A stable fingerprint of what a widget DISPLAYS, from its type + config.

    The client reconciler keys on this to decide "did this widget actually
    change?" — comparing rendered HTML there is unreliable, because once Alpine
    runs it rewrites the DOM (x-if inserts a real <iframe> sibling, x-for adds
    <li>s, :class merges resolved classes) so an untouched widget no longer
    matches its own pristine server template and gets needlessly replaced, which
    reloads its iframe / restarts its audio. A signature computed from the DATA
    is immune to all of that: same config in → same sig → the live node is left
    alone (and keeps its playing media). Same-typed singletons with new content
    (a new song, refreshed headlines) get a new sig and correctly re-render.
    """
    try:
        payload = json.dumps(config, sort_keys=True, default=str)
    except Exception:
        payload = repr(config)
    return hashlib.md5(f"{widget_type}\x00{payload}".encode("utf-8")).hexdigest()[:16]


def generate_widget_html(widget_type: str, widget_id: str, config: dict) -> str:
    """Factory function to route widget creation."""
    if not isinstance(config, dict):
        config = {}
    renderer = WIDGET_RENDERERS.get(widget_type)
    if renderer:
        html_out = renderer(widget_id, config)
    else:
        # Fallback chain: an unknown type degrades to a data card showing whatever
        # config the model sent, instead of a dead error card.
        fallback = dict(config)
        fallback.setdefault("title", (widget_type or "Widget").replace("_", " ").title())
        html_out = render_data_card(widget_id, fallback)
    # Stamp the content signature onto the widget root so the client can tell an
    # unchanged widget from a genuinely changed one without diffing live DOM.
    marker = f'id="{widget_id}"'
    sig_attr = f'{marker} data-sig="{_content_sig(widget_type, config)}"'
    return html_out.replace(marker, sig_attr, 1)
