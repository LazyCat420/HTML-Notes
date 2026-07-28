import base64
import hashlib
import html
import json
import urllib.parse
from typing import Any, Optional

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

# A bare URL in prose. Runs to whitespace or an escaped angle bracket; trailing
# punctuation is trimmed by the autolinker rather than excluded here, since only
# the caller can tell "…/page)." from "…/page_(disambiguation)".
_URL_RE = _re.compile(r'https?://[^\s<>]+')

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
    # Anchors are PARKED as placeholders the moment they're built, so the bare-URL
    # autolinker below can't re-linkify the href of a link this pass just produced
    # (which would nest an <a> inside an attribute and corrupt the markup).
    parked: list[str] = []

    def _park(html: str) -> str:
        parked.append(html)
        return f"\x00L{len(parked) - 1}\x00"

    def _anchor(url: str, label: str) -> str:
        # `url` is already html-escaped — esc() ran over the whole string at the top
        # of this function, so quotes are &quot; and can't break out of the
        # attribute. Do NOT esc() again here; that would double-escape every & in a
        # query string into &amp;amp; and break the link.
        return (f'<a href="{url}" target="_blank" rel="noopener" '
                f'class="text-purple-300 hover:text-purple-200 hover:underline">{label}</a>')

    # [label](http…) — only http/https links survive; anything else renders as plain label.
    def _link(m):
        label, url = m.group(1), m.group(2)
        if url.lower().startswith(("http://", "https://")):
            return _park(_anchor(url, label))
        return label
    text = _re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', _link, text)

    # Bare URLs. Agents write these constantly ("source: https://example.com/x") and
    # without autolinking they rendered as inert text the reader had to select and
    # copy by hand. Markdown proper doesn't autolink either, which is exactly why
    # the agent-written output looked broken.
    def _autolink(m):
        url = m.group(0)
        # Don't swallow the sentence's punctuation into the href. A ')' is kept only
        # when the URL opened its own paren (Wikipedia's ..._(disambiguation) style),
        # otherwise it's assumed to close the surrounding prose.
        # Entities are checked BEFORE punctuation on every pass: esc() turned a
        # closing quote into "&quot;", and stripping ';' as punctuation first would
        # leave a mangled "&quot" glued to the href. `&amp;` is never stripped — it
        # is a legitimate query-string separator.
        trail = ""
        while url:
            ent = next((e for e in ("&quot;", "&#x27;", "&gt;", "&lt;")
                        if url.endswith(e)), None)
            if ent:
                url, trail = url[: -len(ent)], ent + trail
                continue
            ch = url[-1]
            if ch not in ").,;:!?":
                break
            # Balanced parens belong to the URL: en.wikipedia.org/wiki/Capsicum_(genus)
            # keeps its ')', while "(see https://x.com/b)" gives its ')' back to the prose.
            if ch == ")" and url.count("(") >= url.count(")"):
                break
            url, trail = url[:-1], ch + trail
        if not url.lower().startswith(("http://", "https://")):
            return m.group(0)
        return _park(_anchor(url, url)) + trail

    text = _URL_RE.sub(_autolink, text)

    for i, html in enumerate(parked):
        text = text.replace(f"\x00L{i}\x00", html)
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

    # The image is an ARTICLE FIGURE, not a hero band. A full-width band across the
    # top has to pick a height for a photo whose aspect ratio we don't know, and
    # every such guess either crops the subject (object-cover) or strands it in
    # letterbox slack (object-contain) — the hat shot lost its crown to the former.
    #
    # A floated infobox sidesteps the choice entirely: fix the WIDTH, let the height
    # fall out of the image's own aspect ratio (`h-auto`), and the picture is never
    # cut off because nothing is ever cropping it. Text flows alongside and then
    # under it, the way a figure sits in a Wikipedia or news article. `max-h-64`
    # is a safety stop so a tall panorama can't run the card off; `object-contain`
    # only ever engages in that rare case.
    figure_html = ""
    if hero:
        # Float/width live in index.css under a CONTAINER query, not in Tailwind
        # classes here. A float only reads as an article figure when there is room
        # for a real text column beside it; on a narrow card it squeezed the prose
        # into a two-word ribbon and overlapped list rows. Tailwind's `sm:` keys off
        # the VIEWPORT and cannot express this — the card's width comes from its
        # grid span, so the breakpoint has to be on the card itself.
        #
        # Caption only when the caller supplies a real one — defaulting it to the
        # title just printed the card header twice, once in the chrome and again
        # under the picture.
        caption = config.get("image_caption") or config.get("caption") or ""
        caption_html = (
            f'<figcaption class="px-2 py-1.5 text-[0.62rem] leading-snug text-slate-400 '
            f'border-t border-white/10 line-clamp-2">{esc(caption)}</figcaption>'
            if caption else ""
        )
        figure_html = f"""
            <figure class="data-card-figure rounded-xl overflow-hidden border border-white/10 bg-slate-950/60 shadow-lg">
                <img src="{esc(hero)}" alt="{esc(title)}" loading="lazy"
                     class="block w-full h-auto object-contain bg-slate-950/40">
                {caption_html}
            </figure>
        """

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
                <div class="data-card-sources clear-both mt-3 pt-3 border-t border-white/10">
                    <div class="flex items-center gap-1.5 mb-1.5 text-slate-400">
                        <span class="material-symbols-outlined text-[0.85rem]">link</span>
                        <span class="text-[0.65rem] font-semibold uppercase tracking-wider">Sources</span>
                    </div>
                    <ul class="flex flex-col gap-1">{''.join(rendered_items)}</ul>
                </div>
            """
        # The figure goes INSIDE .answer-prose, not beside it: a float only wraps
        # the line boxes of content in its own formatting context, so a figure
        # dropped in as a sibling of the prose div would be overlapped by it
        # rather than flowed around. The sources block below clears the float so
        # a short answer can't leave "Sources" jammed against the picture.
        body_parts.append(f"""
            <div class="data-card-answer p-4 overflow-y-auto flex-grow custom-scrollbar">
                <div class="answer-prose">{figure_html}{_render_markdown(answer)}</div>
                {sources_block}
            </div>
        """)
    elif rendered_items:
        # `space-y-1` rather than `flex flex-col`: a flex CONTAINER avoids floats as
        # one rigid block, so the whole list would be squeezed into a narrow column
        # for its entire height. As a plain block list, each <li> avoids the float
        # on its own — rows beside the figure are short, rows past it run full
        # width. That per-row reflow is what makes it read like an article.
        body_parts.append(f"""
            <div class="data-card-list-wrap p-3 overflow-y-auto flex-grow custom-scrollbar">
                {figure_html}
                <ul class="data-card-list space-y-1">{''.join(rendered_items)}</ul>
            </div>
        """)
    elif content:
        # Legacy plain-text content: render through Markdown too so a caller that
        # passes lightly-formatted text still gets lists/headings, not flat lines.
        body_parts.append(f"""
            <div class="data-card-content p-4 overflow-y-auto flex-grow custom-scrollbar">{figure_html}{_render_markdown(content)}</div>
        """)
    else:
        # Last-resort fallback: show whatever config we got as key/value rows
        # instead of rendering an empty or broken card.
        rows = "".join(
            f'<div class="flex justify-between gap-3 py-1.5 border-b border-white/5"><span class="text-xs uppercase tracking-wider text-slate-400">{esc(k)}</span><span class="text-sm text-slate-200 text-right">{esc(v)}</span></div>'
            for k, v in config.items() if k not in ("title", "subtitle", "icon") and not isinstance(v, (dict, list))
        ) or '<p class="text-slate-400 text-xs italic text-center py-6">No data provided</p>'
        body_parts.append(f'<div class="data-card-content p-4 overflow-y-auto flex-grow custom-scrollbar">{figure_html}{rows}</div>')

    # A full-width hero used to eat ~208px of vertical space, so a card carrying one
    # was given 560px to stay readable. A floated figure shares its rows with the
    # text instead of displacing them, so that compensation is mostly unnecessary
    # now — but an image still adds some height, so keep a modest bump over 380.
    card_h = "h-[460px]" if hero else "h-[380px]"
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
        # One image → a single square hero; several → a square-tile grid. Tiles are
        # object-contain, not cover: a square box still crops a portrait/landscape
        # source hard, and in a gallery the whole frame IS the content.
        grid_cls = "grid-cols-1" if len(shown) == 1 else "grid-cols-2"
        figures = []
        for img in shown:
            caption_html = (
                f'<figcaption class="text-[0.68rem] text-slate-200 px-2 py-1 bg-black/50 backdrop-blur-sm absolute bottom-0 inset-x-0 line-clamp-2">{esc(img["caption"])}</figcaption>'
                if img.get("caption") else ""
            )
            figures.append(f"""
                <figure class="relative aspect-square overflow-hidden rounded-xl bg-slate-950 ring-1 ring-white/10">
                    <img src="{esc(img['url'])}" alt="{esc(img.get('caption') or title)}" loading="lazy" class="absolute inset-0 w-full h-full object-contain">
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
                         class="absolute inset-0 w-full h-full object-contain p-2 group-hover/card:scale-105 transition-transform duration-300">
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

# Series palette for multi-series charts. Values mirror main.py's
# _COMPARE_COLORS (factory must not import from main — main imports us).
_SERIES_COLORS = ["#4fc3f7", "#f472b6", "#a3e635", "#fbbf24",
                  "#c084fc", "#fb923c", "#34d399", "#f87171"]


def _normalize_series(config: dict) -> Optional[dict]:
    """A generic multi-series contract → a full Chart.js config, or None.

    Contract: {labels: [str], series: [{label, values, color?}], type?,
    normalize?: bool, unit?: str}. This is the non-stock counterpart of
    build_stock_compare_config: 'compare rainfall in Seattle vs Portland' or
    'GDP of US vs China' has matched series but no ticker, so the stock-only
    compare path could never draw it and the agent had to hand-author raw
    Chart.js JSON it was never shown."""
    labels = config.get("labels") or []
    series = config.get("series") or []
    if not (isinstance(labels, list) and isinstance(series, list) and labels and series):
        return None
    datasets = []
    for i, s in enumerate(series[:8]):
        if not isinstance(s, dict):
            continue
        vals = s.get("values") or s.get("data") or []
        if not isinstance(vals, list) or not vals:
            continue
        # Pad/truncate to the label count so a ragged series can't skew the axis.
        vals = (list(vals) + [None] * len(labels))[:len(labels)]
        if config.get("normalize"):
            base = next((v for v in vals if isinstance(v, (int, float)) and v), None)
            if base:
                vals = [round((v / base - 1) * 100, 2) if isinstance(v, (int, float)) else None
                        for v in vals]
        color = str(s.get("color") or _SERIES_COLORS[i % len(_SERIES_COLORS)])
        if not _re.fullmatch(r'#[0-9a-fA-F]{3,8}', color):
            color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
        datasets.append({
            "label": str(s.get("label") or s.get("name") or f"Series {i + 1}")[:60],
            "data": vals,
            "borderColor": color,
            "backgroundColor": color,
            "fill": False,
            "tension": 0.3,
            "pointRadius": 0,
            "borderWidth": 2,
        })
    if len(datasets) < 1:
        return None
    unit = str(config.get("unit") or ("%" if config.get("normalize") else ""))
    return {
        "type": config.get("type", "line"),
        "data": {"labels": [str(l) for l in labels], "datasets": datasets},
        "options": {
            "responsive": True, "maintainAspectRatio": False,
            "interaction": {"mode": "index", "intersect": False},
            "plugins": {"legend": {"display": True, "labels": {"boxWidth": 12}}},
            "scales": {
                "x": {"ticks": {"maxTicksLimit": 8}},
                "y": {"title": {"display": bool(unit), "text": unit}},
            },
        },
    }


def render_chart(widget_id: str, config: dict) -> str:
    """Chart widget. Contract: {title?, chart: <full Chart.js config>} or
    {title?, labels: [str], series: [{label, values}]} (multi-series, any data)
    or {title?, type?, labels: [str], values: [num]} — normalized here, baked
    as a language-chart code block that the frontend converts to a Chart.js
    canvas. Falls back to a data_card of label/value rows if the data is
    unusable. Also registered as 'multi_chart' so a comparison ask can never be
    coerced into a stock_card (coerce_widget_type only hijacks 'chart')."""
    title = config.get("title", "Chart")
    chart_config = config.get("chart")
    if not isinstance(chart_config, dict) or "data" not in chart_config:
        chart_config = _normalize_series(config)
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
                    <input type="checkbox" x-model="item.done" @change="persist()" class="rounded border-white/10 text-purple-600 focus:ring-purple-500 w-4 h-4 cursor-pointer">
                    <span :class="{{'line-through opacity-50': item.done}}" x-text="item.text" class="text-sm flex-grow cursor-pointer" @click="toggleTask(idx)"></span>
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

def render_settings(widget_id: str, config: dict) -> str:
    """Appearance + preferences panel the agent pops up ("open settings",
    "change the theme"). The theme swatches come from the server's THEME_CATALOG
    (config['themes']); clicking one applies + persists the palette client-side
    via window.HN. `active` is the current theme; `apply` (when set) is a theme
    the agent wants applied on render — the widget applies it in init()."""
    themes = config.get("themes") or []
    active = config.get("active") or "hud"
    apply = config.get("apply") or ""
    cfg_js = (f"{{ themes: {json_escape(themes)}, active: {json_escape(active)}, "
              f"apply: {json_escape(apply)} }}")
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col group"
         x-data="settingsWidget({cfg_js})">
        {widget_header("Settings", "settings")}
        <div class="p-4 flex flex-col gap-4 overflow-y-auto">
            <!-- Appearance / themes -->
            <div>
                <div class="text-[0.62rem] uppercase tracking-wider text-slate-500 mb-2">Appearance</div>
                <div class="grid grid-cols-2 gap-2">
                    <template x-for="t in themes" :key="t.name">
                        <button @click="setTheme(t.name)"
                                class="flex items-center gap-2 px-2.5 py-2 rounded-xl border transition-colors text-left min-w-0"
                                :class="t.name === active ? 'border-white/40 bg-white/10' : 'border-white/10 hover:bg-white/5'">
                            <span class="flex shrink-0 rounded-md overflow-hidden border border-white/10" style="width:36px;height:22px">
                                <span class="h-full" :style="{{ background: t.swatch[0], width: '34%' }}"></span>
                                <span class="h-full" :style="{{ background: t.swatch[1], width: '33%' }}"></span>
                                <span class="h-full" :style="{{ background: t.swatch[2], width: '33%' }}"></span>
                            </span>
                            <span class="text-xs truncate" x-text="t.label"></span>
                            <span x-show="t.name === active" class="material-symbols-outlined text-[0.95rem] ml-auto shrink-0">check</span>
                        </button>
                    </template>
                </div>
            </div>
            <!-- Preferences -->
            <div class="flex flex-col gap-2 border-t border-white/10 pt-3">
                <div class="flex items-center justify-between">
                    <span class="text-xs text-slate-300">Voice replies</span>
                    <button @click="toggleMute()"
                            class="px-3 py-1 rounded-lg text-xs border transition-colors"
                            :class="muted ? 'border-white/10 text-slate-400 hover:bg-white/5' : 'border-white/30 bg-white/10 text-white'"
                            x-text="muted ? 'Off' : 'On'">On</button>
                </div>
                <button @click="resetLayout()" class="text-xs text-slate-400 hover:text-white transition-colors text-left">Reset widget sizes &amp; order</button>
            </div>
        </div>
    </div>
    """

def render_converter(widget_id: str, config: dict) -> str:
    """Calculator + unit/currency converter. Interactive client-side (no agent
    round-trip per calculation); the server only seeds the initial tab + input
    from the user's phrasing. `seed` is the raw ask ("40% of 1250", "5 mi in km",
    "20 usd to eur"); `tab` is the server's guess (calc / units / currency)."""
    seed = config.get("seed", "")
    tab = config.get("tab", "calc")
    cfg_js = f"{{ seed: {json_escape(seed)}, tab: {json_escape(tab)} }}"
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col group"
         x-data="converterWidget({cfg_js})">
        {widget_header("Converter", "calculate")}
        <div class="p-4 flex flex-col gap-3 overflow-y-auto">
            <!-- Tabs -->
            <div class="flex gap-1">
                <template x-for="t in ['calc','units','currency']" :key="t">
                    <button @click="tab = t" class="px-3 py-1 rounded-lg text-xs font-semibold uppercase tracking-wide transition-colors"
                            :class="tab === t ? 'bg-white/15 text-white' : 'text-slate-400 hover:text-white hover:bg-white/5'"
                            x-text="t === 'calc' ? 'Calc' : (t === 'units' ? 'Units' : 'Currency')"></button>
                </template>
            </div>

            <!-- CALCULATOR -->
            <div x-show="tab === 'calc'" class="flex flex-col gap-2">
                <input x-model="expr" @input="calc()" @keydown.enter="calc()" spellcheck="false"
                       class="w-full bg-black/30 text-slate-100 font-mono text-sm rounded-xl border border-white/10 px-3 py-2 focus:outline-none focus:border-purple-500"
                       placeholder="e.g. 40% of 1250, (3+4)*2, 15^2">
                <div class="text-right text-2xl font-mono tabular-nums px-1" :class="calcErr ? 'text-rose-400 text-sm' : 'text-emerald-300'"
                     x-text="calcErr || calcResult"></div>
            </div>

            <!-- UNITS -->
            <div x-show="tab === 'units'" class="flex flex-col gap-2">
                <select x-model="uCat" @change="onCatChange()"
                        class="bg-black/30 text-slate-200 text-xs rounded-xl border border-white/10 px-3 py-2 focus:outline-none focus:border-purple-500 appearance-none cursor-pointer">
                    <template x-for="c in Object.keys(units)" :key="c"><option :value="c" x-text="c"></option></template>
                </select>
                <div class="flex items-center gap-2">
                    <input x-model.number="uVal" @input="conv()" type="number"
                           class="flex-1 min-w-0 bg-black/30 text-slate-100 font-mono text-sm rounded-xl border border-white/10 px-3 py-2 focus:outline-none focus:border-purple-500">
                    <select x-model="uFrom" @change="conv()" class="bg-black/30 text-slate-200 text-xs rounded-xl border border-white/10 px-2 py-2 appearance-none cursor-pointer max-w-[7rem]">
                        <template x-for="u in Object.keys(units[uCat])" :key="u"><option :value="u" x-text="u"></option></template>
                    </select>
                </div>
                <div class="flex items-center gap-2">
                    <div class="flex-1 min-w-0 text-right text-lg font-mono tabular-nums text-emerald-300 px-1" x-text="uResult"></div>
                    <select x-model="uTo" @change="conv()" class="bg-black/30 text-slate-200 text-xs rounded-xl border border-white/10 px-2 py-2 appearance-none cursor-pointer max-w-[7rem]">
                        <template x-for="u in Object.keys(units[uCat])" :key="u"><option :value="u" x-text="u"></option></template>
                    </select>
                </div>
            </div>

            <!-- CURRENCY -->
            <div x-show="tab === 'currency'" class="flex flex-col gap-2">
                <div class="flex items-center gap-2">
                    <input x-model.number="cVal" @input="fxConv()" type="number"
                           class="flex-1 min-w-0 bg-black/30 text-slate-100 font-mono text-sm rounded-xl border border-white/10 px-3 py-2 focus:outline-none focus:border-purple-500">
                    <select x-model="cFrom" @change="loadFx()" class="bg-black/30 text-slate-200 text-xs rounded-xl border border-white/10 px-2 py-2 appearance-none cursor-pointer max-w-[7rem]">
                        <template x-for="c in currencies" :key="c"><option :value="c" x-text="c"></option></template>
                    </select>
                </div>
                <div class="flex items-center gap-2">
                    <div class="flex-1 min-w-0 text-right text-lg font-mono tabular-nums text-emerald-300 px-1" x-text="cResult"></div>
                    <select x-model="cTo" @change="fxConv()" class="bg-black/30 text-slate-200 text-xs rounded-xl border border-white/10 px-2 py-2 appearance-none cursor-pointer max-w-[7rem]">
                        <template x-for="c in currencies" :key="c"><option :value="c" x-text="c"></option></template>
                    </select>
                </div>
                <div class="text-[0.62rem] text-slate-500 px-1" x-text="fxNote"></div>
            </div>
        </div>
    </div>
    """


def render_reminder(widget_id: str, config: dict) -> str:
    """A reminder/alarm: counts down to a target time, then fires a browser
    notification + a beep. Client-side (works while the tab is open); the target
    persists in localStorage so a reload restores the countdown."""
    label = config.get("label", "Reminder")
    cfg_js = (f"{{ label: {json_escape(label)}, "
              f"offset_seconds: {int(config.get('offset_seconds', 0) or 0)}, "
              f"at_time: {json_escape(config.get('at_time', ''))}, "
              f"tomorrow: {str(bool(config.get('tomorrow'))).lower()} }}")
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col justify-between h-[280px] group"
         x-data="reminderWidget({cfg_js})">
        {widget_header("Reminder", "alarm")}
        <div class="flex-grow flex flex-col items-center justify-center gap-2 p-4 text-center">
            <div class="text-xs uppercase tracking-widest text-slate-400 px-2 truncate max-w-full" x-text="label"></div>
            <div class="text-4xl font-mono tabular-nums" :class="done ? 'text-amber-300 animate-pulse' : 'text-white'"
                 x-text="done ? 'Done!' : remaining">--:--</div>
            <div class="text-xs text-slate-500" x-text="done ? 'was set for ' + targetLabel() : 'at ' + targetLabel()"></div>
            <div x-show="permHint" class="text-[0.6rem] text-amber-400/70" x-text="permHint" style="display:none"></div>
        </div>
        <div class="flex gap-2 justify-center p-3">
            <button @click="snooze(5)" class="px-3 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20 border border-white/10 transition-colors" x-text="done ? '+5 min' : 'Snooze 5'"></button>
            <button @click="snooze(10)" class="px-3 py-1 rounded-lg text-xs bg-white/10 hover:bg-white/20 border border-white/10 transition-colors">+10 min</button>
            <button x-show="done" @click="dismiss()" class="px-3 py-1 rounded-lg text-xs bg-amber-500/25 hover:bg-amber-500/35 border border-amber-400/30 transition-colors" style="display:none">Dismiss</button>
        </div>
    </div>
    """


def render_notes(widget_id: str, config: dict) -> str:
    """Markdown notes: edit ⇄ preview, interactive checklists, tables, tags, and
    Save-to-vault (writes a .md with frontmatter to the Obsidian vault). Typing
    autosaves to localStorage so it survives a reload; Save makes it durable +
    portable to Obsidian. `slug` is set when the note came from the vault."""
    cfg_js = (f"{{ title: {json_escape(config.get('title', 'Quick Notes'))}, "
              f"content: {json_escape(config.get('content', ''))}, "
              f"tags: {json_escape(config.get('tags', []))}, "
              f"slug: {json_escape(config.get('slug', ''))} }}")
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[360px] group"
         x-data="notesWidget({cfg_js})">
        {widget_header("Notes", "edit_note")}
        <div class="flex flex-col flex-grow min-h-0 p-3 gap-2">
            <!-- Title + mode -->
            <div class="flex items-center gap-2">
                <input x-model="title" @input="autosave()" placeholder="Untitled"
                       class="flex-grow min-w-0 bg-transparent text-sm font-semibold text-white focus:outline-none border-b border-transparent focus:border-white/20 pb-0.5">
                <button @click="mode = (mode === 'edit' ? 'preview' : 'edit')"
                        class="shrink-0 px-2.5 py-1 rounded-lg text-[0.7rem] font-semibold uppercase tracking-wide bg-white/10 hover:bg-white/20 border border-white/10 transition-colors"
                        x-text="mode === 'edit' ? 'Preview' : 'Edit'"></button>
            </div>

            <!-- Tags -->
            <div class="flex items-center gap-1.5 flex-wrap">
                <template x-for="(t, i) in tags" :key="i">
                    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[0.65rem] bg-purple-500/20 text-purple-200 border border-purple-400/20">
                        <span x-text="t"></span>
                        <button @click="removeTag(i)" class="hover:text-white leading-none">×</button>
                    </span>
                </template>
                <input x-model="tagInput" @keydown.enter.prevent="addTag()" @keydown.comma.prevent="addTag()"
                       placeholder="+ tag" class="w-16 bg-transparent text-[0.7rem] text-slate-300 focus:outline-none placeholder:text-slate-600">
            </div>

            <!-- EDIT -->
            <div x-show="mode === 'edit'" class="flex flex-col flex-grow min-h-0 gap-1.5">
                <div class="flex gap-1">
                    <button @click="insert('- [ ] ')" title="Checklist item" class="px-2 py-0.5 rounded text-xs bg-white/5 hover:bg-white/10 border border-white/10">☑</button>
                    <button @click="insert('| Col A | Col B |\\n| --- | --- |\\n| 1 | 2 |\\n')" title="Table" class="px-2 py-0.5 rounded text-xs bg-white/5 hover:bg-white/10 border border-white/10">▦</button>
                    <button @click="insert('## ')" title="Heading" class="px-2 py-0.5 rounded text-xs bg-white/5 hover:bg-white/10 border border-white/10">H</button>
                </div>
                <textarea x-ref="ta" x-model="content" @input="autosave()" spellcheck="true"
                          class="w-full flex-grow bg-black/20 text-slate-200 p-3 rounded-xl border border-white/10 focus:outline-none focus:border-purple-500 resize-none shadow-inner text-sm leading-relaxed font-mono"
                          placeholder="Markdown — # headings, - [ ] checklists, | tables |, **bold**…"></textarea>
            </div>

            <!-- PREVIEW -->
            <div x-show="mode === 'preview'" @click="onPreviewClick($event)"
                 class="notes-preview flex-grow min-h-0 overflow-y-auto bg-black/20 rounded-xl border border-white/10 p-3 text-sm leading-relaxed"
                 x-html="rendered()"></div>

            <!-- Save row -->
            <div class="flex items-center gap-2 shrink-0">
                <button @click="save()" :disabled="saving"
                        class="px-3 py-1 rounded-lg text-xs font-semibold bg-purple-600/50 hover:bg-purple-500/60 border border-white/10 transition-colors disabled:opacity-50"
                        x-text="saving ? 'Saving…' : 'Save to vault'"></button>
                <span class="text-[0.65rem] text-slate-400" x-text="saved"></span>
                <span x-show="dirty && !saved" class="text-[0.6rem] text-amber-400/60">unsaved</span>
            </div>
        </div>
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
        # Match against netloc+path, NOT the whole URL: a substring test over the
        # full string let `https://evil.com/?x=youtube.com/embed` frame directly.
        try:
            parsed = urllib.parse.urlparse(url)
            frame_target = f"{parsed.netloc}{parsed.path}".lower()
            # "output=embed" is a QUERY marker (classic keyless Google Maps embed)
            # — only honor it when the host really is Google Maps.
            frame_query = parsed.query.lower() if "google." in parsed.netloc.lower() else ""
        except Exception:
            frame_target, frame_query = "", ""
        embeddable = any(h in frame_target or h in frame_query for h in _FRAME_OK)
        src = url if embeddable else f"/widgets/embed?u={urllib.parse.quote(url, safe='')}"
        # No allow-same-origin: the non-embeddable branch frames OUR origin
        # (/widgets/embed), and scripts + same-origin in a same-origin frame is a
        # sandbox no-op — any script in that document would get the parent's
        # cookies/localStorage/fetch. The reader renders bounded markdown and
        # needs no same-origin capability; direct embeds (YouTube etc.) are
        # cross-origin players that work sandboxed the same way.
        body = (
            f'<iframe src="{esc(src)}" class="w-full flex-grow border-none bg-slate-950" '
            f'sandbox="allow-scripts allow-forms allow-popups"></iframe>'
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
                <span class="text-xl">{esc(icon)}</span>
                <h3 class="font-bold text-white tracking-wide truncate max-w-[250px]">{esc(title)}</h3>
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
    # NOTE: this template has a hand-maintained twin in app/static/index.js
    # (the self-heal rehydration for music widgets that lost their Alpine
    # attrs). Structural changes here must be mirrored there.
    from app.config import MUSIC_PLAYER_URL
    genre = config.get("genre", "")
    # 'genre' | 'artist' | '' — routing's guess at what the query names.
    # The widget tries the genre pipeline first when unset; a miss fails over
    # to the artist mix client-side, so a wrong guess self-corrects.
    kind = config.get("kind", "")
    autoplay = str(config.get("autoplay", False)).lower()
    cfg_js = (f'{{ genre: {json_escape(genre)}, kind: {json_escape(kind)}, '
              f'autoplay: {autoplay}, base: {json_escape(MUSIC_PLAYER_URL)} }}')

    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-gradient-to-br from-purple-950/70 via-indigo-950/60 to-slate-950/70 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col justify-between group transition-all duration-300" :class="showQueue ? 'h-[420px]' : 'h-[280px]'" x-data="musicPlayerWidget({cfg_js})">
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
                <p class="text-xs text-purple-200 truncate mt-0.5 drop-shadow-sm font-medium" x-text="currentTrack ? currentTrack.artist : (streamStatus || 'Please wait')"></p>
            </div>
        </div>

        <!-- Queue Panel (toggled by the queue_music button below) -->
        <div x-show="showQueue" x-transition.opacity class="relative z-10 flex-grow min-h-0 overflow-y-auto rounded-xl bg-black/30 backdrop-blur-md border border-white/10 mt-2 divide-y divide-white/5" style="display: none;">
            <template x-for="item in upcoming" :key="item.t.id">
                <div class="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-white/5 cursor-pointer group/row" @click="playAt(item.i)">
                    <span class="material-symbols-outlined text-[0.9rem] text-purple-300/60 shrink-0">music_note</span>
                    <div class="min-w-0 flex-grow">
                        <div class="truncate text-white/90" x-text="item.t.title"></div>
                        <div class="truncate text-purple-300/70 text-[10px]" x-text="item.t.artist"></div>
                    </div>
                    <button @click.stop="removeAt(item.i)" title="Remove from queue" class="opacity-0 group-hover/row:opacity-100 text-white/40 hover:text-red-400 transition-opacity shrink-0">
                        <span class="material-symbols-outlined text-[0.9rem]">close</span>
                    </button>
                </div>
            </template>
            <div x-show="!upcoming.length" class="px-3 py-2 text-xs text-white/40">Queue empty — more on the way…</div>
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

            <button @click="showQueue = !showQueue" class="transition-colors p-1.5 rounded-lg" :class="{{'text-purple-300 font-bold bg-white/5': showQueue, 'text-white/50 hover:text-white': !showQueue}}" title="Queue">
                <span class="material-symbols-outlined text-lg">queue_music</span>
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


def render_crypto_card(widget_id: str, config: dict) -> str:
    """A cryptocurrency's price header + chart + key stats + contract addresses.

    Mirrors the stock card: the CoinGecko snapshot is baked in server-side so it
    paints immediately; the range tabs re-fetch /api/crypto/<coin_id> directly
    (no agent turn). Degrades to a readable 'couldn't load' card if the snapshot
    is missing its price (a rare unresolved coin that slipped through)."""
    if not config.get("price") and not config.get("values"):
        return render_data_card(widget_id, {
            "title": config.get("name") or config.get("symbol") or "Token",
            "icon": "currency_bitcoin",
            "content": "Couldn't load live data for this token right now."})

    snapshot = {
        "coin_id": config.get("coin_id", ""),
        "symbol": config.get("symbol") or "—",
        "name": config.get("name") or config.get("symbol") or "",
        "image": config.get("image", ""),
        "price_str": config.get("price_str", "—"),
        "change_pct": config.get("change_pct"),
        "range": config.get("range", "30d"),
        "labels": config.get("labels") or [],
        "values": config.get("values") or [],
        "market_cap": config.get("market_cap", "—"),
        "market_cap_rank": config.get("market_cap_rank"),
        "volume": config.get("volume", "—"),
        "high_24h": config.get("high_24h", "—"),
        "low_24h": config.get("low_24h", "—"),
        "ath": config.get("ath", "—"),
        "ath_change_pct": config.get("ath_change_pct"),
        "platforms": config.get("platforms") or {},
    }
    # Contract-address chips: each chain's address, copy-to-clipboard on click.
    chip_rows = ""
    for chain, addr in list(snapshot["platforms"].items())[:6]:
        short = (addr[:8] + "…" + addr[-6:]) if len(addr) > 18 else addr
        chip_rows += (
            f'<button onclick="navigator.clipboard.writeText(\'{esc(addr)}\');'
            f'this.querySelector(\'.copy-lbl\').textContent=\'copied!\';'
            f'setTimeout(()=>this.querySelector(\'.copy-lbl\')&&'
            f'(this.querySelector(\'.copy-lbl\').textContent=\'{esc(short)}\'),1200)" '
            f'class="flex items-center justify-between gap-2 w-full px-2.5 py-1.5 '
            f'rounded-lg bg-white/5 hover:bg-white/10 transition-colors text-left">'
            f'<span class="text-[0.62rem] uppercase tracking-wider text-slate-400 shrink-0">{esc(chain)}</span>'
            f'<span class="copy-lbl text-[0.68rem] font-mono text-slate-200 truncate">{esc(short)}</span>'
            f'<span class="material-symbols-outlined text-[0.85rem] text-slate-500 shrink-0">content_copy</span>'
            f'</button>')

    logo = (f'<img src="{esc(snapshot["image"])}" class="w-8 h-8 rounded-full shrink-0" '
            f'onerror="this.style.display=\'none\'">' if snapshot["image"] else "")

    return f"""
    <div id="{widget_id}" class="widget-container crypto-card col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[560px] group"
         x-data="cryptoCardWidget({json_escape(snapshot)})">
        <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
            <span class="material-symbols-outlined text-[1.2rem]">close</span>
        </button>

        <!-- Header -->
        <div class="flex items-center gap-3 pr-8 shrink-0">
            {logo}
            <div class="min-w-0 flex-grow">
                <div class="flex items-baseline gap-2">
                    <h3 class="text-xl font-bold tracking-tight" x-text="snapshot.symbol"></h3>
                    <span class="text-xs text-slate-400 truncate" x-text="snapshot.name"></span>
                    <span x-show="snapshot.market_cap_rank" class="text-[0.6rem] px-1.5 py-0.5 rounded bg-white/10 text-slate-300 shrink-0" x-text="'#' + snapshot.market_cap_rank"></span>
                </div>
                <div class="flex items-baseline gap-2 mt-1">
                    <span class="text-3xl font-semibold tabular-nums" x-text="snapshot.price_str"></span>
                    <span class="text-sm font-semibold tabular-nums"
                          :class="(snapshot.change_pct ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'"
                          x-show="snapshot.change_pct !== null"
                          x-text="((snapshot.change_pct ?? 0) >= 0 ? '+' : '') + snapshot.change_pct + '% 24h'"></span>
                </div>
            </div>
        </div>

        <!-- Range tabs -->
        <div class="flex gap-1 mt-3 shrink-0">
            <template x-for="r in ranges" :key="r">
                <button @click="setRange(r)"
                        class="px-2.5 py-1 rounded-lg text-[0.7rem] font-semibold uppercase tracking-wide transition-colors"
                        :class="r === snapshot.range ? 'bg-white/15 text-white' : 'text-slate-400 hover:text-white hover:bg-white/5'"
                        x-text="r"></button>
            </template>
            <span x-show="loading" class="ml-2 self-center text-[0.7rem] text-slate-400">loading…</span>
        </div>

        <!-- Chart -->
        <div class="relative mt-2 h-[150px] shrink-0">
            <canvas x-ref="canvas"></canvas>
        </div>

        <!-- Stats -->
        <div class="grid grid-cols-3 gap-x-4 gap-y-2 mt-4 text-[0.72rem] shrink-0">
            <template x-for="row in statRows()" :key="row.label">
                <div>
                    <div class="text-[0.6rem] uppercase tracking-wider text-slate-500" x-text="row.label"></div>
                    <div class="tabular-nums font-medium" x-text="row.value"></div>
                </div>
            </template>
        </div>

        <!-- Contract addresses -->
        <div class="mt-4 flex-grow overflow-y-auto min-h-0" x-show="Object.keys(snapshot.platforms||{{}}).length">
            <div class="text-[0.6rem] uppercase tracking-wider text-slate-500 mb-1.5">Contracts — tap to copy</div>
            <div class="flex flex-col gap-1.5">
                {chip_rows}
            </div>
        </div>
    </div>
    """


# Node-kind → color. One palette shared by the server legend and the client
# cytoscape stylesheet (kept in sync by eye — small + stable).
_GRAPH_KIND_COLORS = {
    "whale": "#f97316",      # orange — a big unlabeled holder (the ones to watch)
    "holder": "#38bdf8",     # cyan — ordinary holder
    "cex": "#a78bfa",        # purple — exchange custody (not one person)
    "dex": "#22d3ee",        # teal — DEX router / LP / market maker
    "burn": "#64748b",       # slate — burned / dead
    "contract": "#fbbf24",   # amber — the token contract itself
    "source": "#ef4444",     # red — a wallet that funded ≥2 whales (coordination)
}
_GRAPH_KIND_LABEL = {
    "whale": "Whale (>1%)", "holder": "Holder", "cex": "Exchange",
    "dex": "DEX / LP", "burn": "Burn / dead", "contract": "Token contract",
    "source": "Shared source ⚠",
}


def render_wallet_graph(widget_id: str, config: dict) -> str:
    """Holder-network graph: top wallets as nodes (sized by % of supply) with
    transfer edges between them, plus a concentration read.

    Contract: the output of crypto.build_holder_graph — {title, chain, token,
    elements, metrics, note}. The cytoscape `elements` are baked as a hidden
    language-graph code block that index.js hydrates into an interactive graph
    (same pattern as language-chart → Chart.js), so the config survives the
    client-serialize → server-adopt round trip. Degrades to a data_card if there
    are no elements."""
    elements = config.get("elements") or []
    metrics = config.get("metrics") or {}
    token = config.get("token") or {}
    if not elements:
        return render_data_card(widget_id, {
            "title": config.get("title") or "Holder graph",
            "icon": "hub",
            "content": config.get("note") or "No holder data available for this token."})

    sym = token.get("symbol") or "Token"
    chain = config.get("chain", "")
    title = config.get("title") or f"{sym} — Holder Network"

    tone = metrics.get("tone", "warn")
    tone_bg = {"bad": "bg-rose-500/20 text-rose-300 border-rose-500/40",
               "warn": "bg-amber-500/15 text-amber-300 border-amber-500/40",
               "ok": "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
               }.get(tone, "bg-white/10 text-slate-300 border-white/20")

    def chip(label, value):
        return (f'<div class="flex flex-col px-2.5 py-1.5 rounded-lg bg-white/5 shrink-0">'
                f'<span class="text-[0.55rem] uppercase tracking-wider text-slate-500">{esc(label)}</span>'
                f'<span class="text-sm font-semibold tabular-nums text-white">{esc(value)}</span></div>')

    chip_list = [
        chip("Top-10 real", f'{metrics.get("top10_share_real", 0)}%'),
        chip("Exchanges", f'{metrics.get("cex_share", 0)}%'),
        chip("Burned", f'{metrics.get("burn_share", 0)}%'),
        chip("Whales", str(metrics.get("whale_count", 0))),
        chip("Flows", str(metrics.get("edge_count", 0))),
    ]
    # Only show the coordination chip when there IS coordination — a red flag that
    # earns its space (whales linked through a shared funder wallet).
    if metrics.get("clustered_whales"):
        chip_list.append(
            f'<div class="flex flex-col px-2.5 py-1.5 rounded-lg bg-rose-500/20 '
            f'border border-rose-500/40 shrink-0">'
            f'<span class="text-[0.55rem] uppercase tracking-wider text-rose-300">Linked ⚠</span>'
            f'<span class="text-sm font-semibold tabular-nums text-white">'
            f'{metrics.get("clustered_whales")}</span></div>')
    else:
        chip_list.append(chip("Gini", str(metrics.get("gini", "—"))))
    chips = "".join(chip_list)

    # Legend from the kinds actually present.
    present = []
    seen = set()
    for el in elements:
        k = (el.get("data") or {}).get("kind")
        if k and k not in seen and "source" not in (el.get("data") or {}):
            seen.add(k)
            present.append(k)
    legend = "".join(
        f'<span class="inline-flex items-center gap-1 text-[0.62rem] text-slate-400">'
        f'<span style="width:9px;height:9px;border-radius:50%;background:{_GRAPH_KIND_COLORS.get(k,"#888")}"></span>'
        f'{esc(_GRAPH_KIND_LABEL.get(k, k))}</span>'
        for k in present)

    graph_payload = {
        "elements": elements,
        "colors": _GRAPH_KIND_COLORS,
        "chain": chain,
    }
    note = config.get("note") or ""
    note_html = (f'<div class="text-[0.62rem] text-slate-400 italic mt-1.5 shrink-0">{esc(note)}</div>'
                 if note else "")
    token_addr = token.get("address", "")
    explorer = ""
    if token_addr:
        base = ("https://solscan.io/token/" if chain == "solana"
                else "https://etherscan.io/token/")
        explorer = (f'<a href="{esc(base)}{esc(token_addr)}" target="_blank" '
                    f'class="text-[0.62rem] text-sky-400 hover:underline shrink-0">explorer ↗</a>')

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container wallet-graph-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[600px] group">
        {widget_header(title, "hub", subtitle=chain.upper())}
        <div class="p-3 flex flex-col gap-2 min-h-0 flex-grow">
            <!-- Verdict banner -->
            <div class="rounded-xl border px-3 py-2 text-[0.78rem] leading-snug shrink-0 {tone_bg}">
                {esc(metrics.get("verdict", ""))}
            </div>
            <!-- Metric chips -->
            <div class="flex flex-wrap gap-1.5 shrink-0">{chips}</div>
            <!-- The graph. index.js finds the hidden config block and mounts an
                 interactive cytoscape canvas as its sibling. -->
            <div class="graph-mount relative flex-grow min-h-0 rounded-xl bg-black/20 border border-white/5 overflow-hidden">
                <pre class="graph-config-block" style="display:none"><code class="language-graph">{esc(json.dumps(graph_payload))}</code></pre>
            </div>
            <div class="flex items-center justify-between gap-2 shrink-0">
                <div class="flex flex-wrap gap-x-3 gap-y-1">{legend}</div>
                {explorer}
            </div>
            {note_html}
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


def _spark_svg(values, color: str = "#4fc3f7", w: int = 100, h: int = 28) -> str:
    """A tiny inline-SVG sparkline. Static markup — unlike the Chart.js path it
    needs no <canvas> or script revival, so it survives innerHTML sanitization
    (SVG/polyline are in DOMPurify's default allowlist)."""
    nums = [v for v in (values or []) if isinstance(v, (int, float))]
    if len(nums) < 2:
        return ""
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1
    pts = " ".join(
        f"{(i / (len(nums) - 1)) * w:.1f},{h - 2 - ((v - lo) / span) * (h - 4):.1f}"
        for i, v in enumerate(nums))
    if not _re.fullmatch(r'#[0-9a-fA-F]{3,8}', color or ""):
        color = "#4fc3f7"
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="w-20 h-6 shrink-0">'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/></svg>')


# Micro-label + value cell, the weather stat-cell idiom shared by the stat-grid
# widgets below (kpi_row, versus_card, profile_card).
def _stat_cell(label: str, value_html: str) -> str:
    return (f'<div class="flex flex-col min-w-0">'
            f'<span class="text-[0.6rem] uppercase tracking-wider text-slate-400 truncate">{esc(label)}</span>'
            f'{value_html}</div>')


def render_table(widget_id: str, config: dict) -> str:
    """Real data table: typed columns, right-aligned tabular numerics, row cap.
    The markdown-table branch in _render_markdown is text-only (no alignment, no
    formatting, no cap) — this is the widget for structured/ranked rows.

    Contract: {title, subtitle?, icon?, columns:[{key, label, align?('right'),
    format?('number'|'currency'|'percent'|'text')}], rows:[{<key>: value}] or
    [[v1, v2…]], sort?: {key, dir:'asc'|'desc'}, max_rows?: int}.
    Also accepts the legacy {headers:[str], rows:[[str]]} shape."""
    title = config.get("title", "Table")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "table_chart")
    columns = config.get("columns") or []
    rows = config.get("rows") or []

    # Legacy render_component shape: headers + positional rows.
    if not columns and config.get("headers"):
        columns = [{"key": str(i), "label": str(hdr)} for i, hdr in enumerate(config["headers"])]
        rows = [{str(i): c for i, c in enumerate(r)} for r in rows if isinstance(r, (list, tuple))]

    cols = []
    for i, c in enumerate(columns[:8]):
        if isinstance(c, str):
            c = {"key": c, "label": c}
        if not isinstance(c, dict):
            continue
        key = str(c.get("key") or c.get("label") or i)
        fmt = str(c.get("format") or "text")
        cols.append({"key": key, "label": str(c.get("label") or key),
                     "numeric": fmt in ("number", "currency", "percent") or c.get("align") == "right",
                     "fmt": fmt})
    norm_rows = []
    for r in rows:
        if isinstance(r, (list, tuple)):
            r = {c["key"]: v for c, v in zip(cols, r)}
        if isinstance(r, dict):
            norm_rows.append(r)
    if not (cols and norm_rows):
        return render_data_card(widget_id, {"title": title, "icon": icon,
                                            "content": config.get("content", "") or "No table data provided."})

    sort = config.get("sort") or {}
    if isinstance(sort, dict) and sort.get("key"):
        def _sort_val(row):
            v = row.get(sort["key"])
            try:
                return (0, float(_re.sub(r'[^\d.\-]', '', str(v))))
            except (TypeError, ValueError):
                return (1, str(v))
        try:
            norm_rows.sort(key=_sort_val, reverse=str(sort.get("dir", "asc")).lower() == "desc")
        except Exception:
            pass

    try:
        max_rows = max(1, min(100, int(config.get("max_rows") or 50)))
    except (TypeError, ValueError):
        max_rows = 50
    shown, dropped = norm_rows[:max_rows], max(0, len(norm_rows) - max_rows)

    def _cell(col, val):
        if val is None:
            return '<span class="text-slate-500">—</span>'
        s = str(val)
        if col["fmt"] in ("number", "currency", "percent"):
            try:
                n = float(_re.sub(r'[^\d.\-]', '', s))
                if col["fmt"] == "currency":
                    s = f"${n:,.2f}".rstrip("0").rstrip(".") if "." in f"{n}" else f"${n:,.0f}"
                elif col["fmt"] == "percent":
                    s = f"{n:,.2f}".rstrip("0").rstrip(".") + "%"
                else:
                    s = f"{n:,.2f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                pass
            return esc(s)
        return _md_inline(s)

    head = "".join(
        f'<th class="{"text-right" if c["numeric"] else "text-left"} font-semibold text-white px-2.5 py-1.5 '
        f'border-b border-white/15 whitespace-nowrap">{esc(c["label"])}</th>' for c in cols)
    body = "".join(
        "<tr class='hover:bg-white/5 transition-colors'>" + "".join(
            f'<td class="px-2.5 py-1.5 border-b border-white/5 align-top text-sm '
            f'{"text-right tabular-nums" if c["numeric"] else "text-left"}">{_cell(c, r.get(c["key"]))}</td>'
            for c in cols) + "</tr>"
        for r in shown)
    more = (f'<div class="text-[0.65rem] text-slate-500 px-3 py-1.5">+{dropped} more rows not shown</div>'
            if dropped else "")

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container table-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(title, icon, subtitle)}
        <div class="flex-grow overflow-y-auto custom-scrollbar min-h-0">
            <div class="overflow-x-auto">
                <table class="w-full text-slate-200 border-collapse">
                    <thead class="sticky top-0 bg-slate-900/95 backdrop-blur-sm z-10"><tr>{head}</tr></thead>
                    <tbody>{body}</tbody>
                </table>
            </div>
            {more}
        </div>
    </div>
    """


def render_kpi_row(widget_id: str, config: dict) -> str:
    """A row of big-number metric tiles with signed deltas and optional
    sparklines — the 'how is X doing right now' shape nothing else rendered
    (data_card buries numbers in prose; a chart is overkill for 3 values).

    Contract: {title, subtitle?, icon?, metrics:[{label, value, unit?, delta?,
    direction?('up'|'down'), good?('up'|'down'), spark?:[num]}], note?}."""
    title = config.get("title", "Metrics")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "monitoring")
    metrics = [m for m in (config.get("metrics") or config.get("stats") or [])
               if isinstance(m, dict)][:8]
    if not metrics:
        return render_data_card(widget_id, {"title": title, "icon": icon,
                                            "content": config.get("note", "") or "No metrics provided."})

    tiles = []
    for m in metrics:
        label = str(m.get("label") or m.get("name") or "")
        value = m.get("value")
        unit = str(m.get("unit") or "")
        delta = str(m.get("delta") or "")
        direction = str(m.get("direction") or ("down" if delta.strip().startswith("-") else
                                               "up" if delta.strip().startswith("+") else ""))
        good = str(m.get("good") or "up")
        if direction:
            positive = (direction == good)
            tone = "text-emerald-400" if positive else "text-rose-400"
            arrow = "trending_up" if direction == "up" else "trending_down"
        else:
            tone, arrow = "text-slate-400", ""
        delta_html = (
            f'<span class="flex items-center gap-0.5 text-[0.7rem] font-semibold tabular-nums {tone}">'
            + (f'<span class="material-symbols-outlined text-[0.85rem]">{arrow}</span>' if arrow else "")
            + f'{esc(delta)}</span>'
            if delta else "")
        spark = _spark_svg(m.get("spark"), "#34d399" if (not direction or direction == good) else "#f87171")
        tiles.append(f"""
            <div class="flex flex-col gap-1 px-3.5 py-3 rounded-2xl bg-white/5 border border-white/10 min-w-0">
                <span class="text-[0.62rem] uppercase tracking-wider text-slate-400 truncate">{esc(label)}</span>
                <span class="text-2xl font-semibold tabular-nums leading-none text-white">{esc(value if value is not None else '—')}<span class="text-sm text-slate-400 ml-0.5">{esc(unit)}</span></span>
                <div class="flex items-center justify-between gap-2 min-h-[1.25rem]">{delta_html}{spark}</div>
            </div>
        """)

    grid_cols = {1: "grid-cols-1", 2: "grid-cols-2", 3: "grid-cols-3"}.get(len(tiles), "grid-cols-4")
    note = config.get("note") or ""
    note_html = (f'<div class="px-4 pb-3 text-[0.68rem] text-slate-400 leading-snug">{_md_inline(str(note))}</div>'
                 if note else "")

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container kpi-row-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col group">
        {widget_header(title, icon, subtitle)}
        <div class="grid {grid_cols} gap-2.5 p-3 flex-grow content-start overflow-y-auto custom-scrollbar">{''.join(tiles)}</div>
        {note_html}
    </div>
    """


def render_timeline(widget_id: str, config: dict) -> str:
    """Chronology on a vertical rail: date chips, per-event summaries, optional
    thumbnails and source links. News items always carry dates; nothing could
    lay them on a time axis before this.

    Contract: {title, subtitle?, icon?, order?('asc'|'desc'), events:[{date,
    title, description?|summary?, badge?, url?, image?}]}. Events are sorted by
    parsed date server-side; unparseable dates keep emission order."""
    import datetime as _dt
    title = config.get("title", "Timeline")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "timeline")
    events = [e for e in (config.get("events") or []) if isinstance(e, dict)][:30]
    if not events:
        return render_data_card(widget_id, {"title": title, "icon": icon,
                                            "content": config.get("content", "") or "No events provided."})

    def _parse_date(s):
        s = str(s or "").strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return _dt.datetime.strptime(s[:len(_dt.datetime.now().strftime(fmt))], fmt)
            except ValueError:
                continue
        try:
            return _dt.datetime.fromisoformat(s)
        except ValueError:
            return None

    parsed = [(_parse_date(e.get("date")), i, e) for i, e in enumerate(events)]
    if all(p[0] is not None for p in parsed):
        parsed.sort(key=lambda p: (p[0], p[1]),
                    reverse=str(config.get("order", "desc")).lower() == "desc")

    lis = []
    for dt, _i, e in parsed:
        e_title = str(e.get("title") or e.get("text") or "")
        e_desc = str(e.get("description") or e.get("summary") or "")
        e_badge = str(e.get("badge") or "")
        e_url = str(e.get("url") or e.get("link") or "")
        e_img = str(e.get("image") or e.get("thumbnail") or "")
        date_label = dt.strftime("%b %d, %Y") if dt else str(e.get("date") or "")
        thumb = (f'<img src="{esc(e_img)}" alt="" loading="lazy" class="item-thumb w-12 h-12 shrink-0 rounded-lg object-cover ring-1 ring-white/10">'
                 if e_img else "")
        badge_html = (f'<span class="item-badge text-[0.58rem] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-purple-500/20 text-purple-200 border border-purple-400/30 shrink-0">{esc(e_badge)}</span>'
                      if e_badge else "")
        source_html = (f'<a href="{esc(e_url)}" target="_blank" rel="noopener" class="text-[0.62rem] text-purple-300/70 hover:text-purple-200 hover:underline">{esc(_host_of(e_url))} ↗</a>'
                       if e_url and e_url.lower().startswith(("http://", "https://")) else "")
        desc_html = (f'<p class="text-xs text-slate-300 leading-relaxed mt-0.5 line-clamp-4">{_md_inline(e_desc)}</p>'
                     if e_desc else "")
        lis.append(f"""
            <li class="relative pl-5 pb-4">
                <span class="absolute left-[-5px] top-1.5 w-2 h-2 rounded-full bg-purple-400 ring-4 ring-purple-400/15"></span>
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-[0.62rem] font-semibold uppercase tracking-wider text-sky-300 font-mono">{esc(date_label)}</span>
                    {badge_html}
                    {source_html}
                </div>
                <div class="flex items-start gap-2.5 mt-1">
                    {thumb}
                    <div class="min-w-0">
                        <span class="text-sm font-semibold text-white leading-snug">{esc(e_title)}</span>
                        {desc_html}
                    </div>
                </div>
            </li>
        """)

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container timeline-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[460px] group">
        {widget_header(title, icon, subtitle)}
        <ol class="flex flex-col p-4 pl-5 ml-1.5 border-l-2 border-purple-400/30 overflow-y-auto flex-grow custom-scrollbar my-2 mr-1">
            {''.join(lis)}
        </ol>
    </div>
    """


def render_versus_card(widget_id: str, config: dict) -> str:
    """Side-by-side comparison of 2-4 entities with aligned stat rows and
    winner highlighting — the answer shape for 'X vs Y, which is better'.
    Before this, comparisons were a text-only Markdown table with no per-row
    winner marking and no verdict line.

    Contract: {title, subtitle?, icon?, entities:[{name, caption?, image?}],
    rows:[{label, values:[…], winner?: idx|null}], verdict?}."""
    title = config.get("title", "Comparison")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "compare_arrows")
    entities = [e if isinstance(e, dict) else {"name": str(e)}
                for e in (config.get("entities") or [])][:4]
    rows = [r for r in (config.get("rows") or []) if isinstance(r, dict)][:14]
    if len(entities) < 2 or not rows:
        return render_data_card(widget_id, {"title": title, "icon": icon,
                                            "answer": config.get("verdict", "") or "Not enough comparison data.",
                                            "items": config.get("items") or []})
    n = len(entities)
    grid_style = f"grid-template-columns: minmax(0,1.1fr) repeat({n}, minmax(0,1fr));"

    header_cells = ['<div></div>']
    for e in entities:
        name = str(e.get("name") or "—")
        caption = str(e.get("caption") or "")
        img = str(e.get("image") or "")
        visual = (f'<img src="{esc(img)}" alt="{esc(name)}" loading="lazy" class="w-10 h-10 rounded-xl object-contain bg-slate-950/60 ring-1 ring-white/10 mx-auto">'
                  if img else
                  f'<div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-slate-700 to-slate-500 flex items-center justify-center ring-1 ring-white/10 mx-auto"><span class="text-base font-bold text-white/80">{esc(name[:1].upper())}</span></div>')
        header_cells.append(
            f'<div class="flex flex-col items-center gap-1 text-center min-w-0">{visual}'
            f'<span class="text-sm font-semibold text-white truncate max-w-full">{esc(name)}</span>'
            + (f'<span class="text-[0.62rem] text-slate-400 truncate max-w-full">{esc(caption)}</span>' if caption else "")
            + '</div>')

    row_html = []
    for r in rows:
        vals = r.get("values") or []
        if not isinstance(vals, list):
            continue
        vals = (list(vals) + ["—"] * n)[:n]
        winner = r.get("winner")
        winner = winner if isinstance(winner, int) and 0 <= winner < n else None
        cells = [f'<div class="text-[0.7rem] text-slate-400 uppercase tracking-wide self-center truncate">{esc(str(r.get("label") or ""))}</div>']
        for i, v in enumerate(vals):
            if winner is None:
                cls = "text-slate-200"
            elif i == winner:
                cls = "text-white font-semibold bg-emerald-500/10 ring-1 ring-emerald-400/25 rounded-lg"
            else:
                cls = "text-slate-400"
            cells.append(f'<div class="text-center text-sm tabular-nums px-1.5 py-1 {cls}">{esc(str(v))}</div>')
        row_html.append(f'<div class="grid gap-x-2 items-center py-0.5 border-b border-white/5" style="{grid_style}">{"".join(cells)}</div>')

    verdict = str(config.get("verdict") or "")
    verdict_html = (
        f'<div class="shrink-0 mx-3 mb-3 px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-xs text-slate-200 leading-relaxed">'
        f'<span class="material-symbols-outlined text-[0.9rem] text-purple-300 align-middle mr-1">verified</span>{_md_inline(verdict)}</div>'
        if verdict else "")

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container versus-widget col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        {widget_header(title, icon, subtitle)}
        <div class="grid gap-x-2 px-3 pt-3 pb-2 border-b border-white/10 shrink-0" style="{grid_style}">{''.join(header_cells)}</div>
        <div class="flex-grow overflow-y-auto custom-scrollbar px-3 py-1.5">{''.join(row_html)}</div>
        {verdict_html}
    </div>
    """


def render_profile_card(widget_id: str, config: dict) -> str:
    """Wikipedia-style infobox for a person / company / place: portrait,
    label/value facts, a short bio, links. The agent emits {profile_query} and
    the server resolves the payload (image provenance: Wikipedia thumbnail or
    og:image — never a model-typed URL).

    Contract: {title, subtitle?, image?, image_caption?, facts:[{label, value}],
    answer?(markdown bio), links:[{label, url}]}."""
    title = config.get("title", "Profile")
    subtitle = config.get("subtitle", "")
    image = str(config.get("image") or "")
    caption = str(config.get("image_caption") or config.get("caption") or "")
    facts = [f for f in (config.get("facts") or []) if isinstance(f, dict)][:10]
    bio = str(config.get("answer") or config.get("bio") or config.get("content") or "")
    links = [l for l in (config.get("links") or []) if isinstance(l, dict) and
             str(l.get("url", "")).lower().startswith(("http://", "https://"))][:4]

    if image:
        visual = f"""
            <figure class="rounded-xl overflow-hidden border border-white/10 bg-slate-950/60 shadow-lg shrink-0">
                <img src="{esc(image)}" alt="{esc(title)}" loading="lazy" class="block w-full h-auto max-h-44 object-contain bg-slate-950/40">
                {f'<figcaption class="px-2 py-1 text-[0.6rem] text-slate-400 border-t border-white/10">{esc(caption)}</figcaption>' if caption else ''}
            </figure>"""
    else:
        visual = (f'<div class="h-24 rounded-xl bg-gradient-to-tr from-slate-700 to-slate-500 flex items-center justify-center ring-1 ring-white/10 shrink-0">'
                  f'<span class="text-4xl font-bold text-white/70">{esc((title or "?")[:1].upper())}</span></div>')

    fact_cells = "".join(
        _stat_cell(str(f.get("label") or ""),
                   f'<span class="text-sm font-semibold text-white leading-snug">{esc(str(f.get("value") or "—"))}</span>')
        for f in facts if f.get("label"))
    facts_html = (f'<div class="grid grid-cols-2 gap-x-3 gap-y-2">{fact_cells}</div>' if fact_cells else "")
    bio_html = (f'<div class="answer-prose text-sm">{_render_markdown(bio)}</div>' if bio else "")
    links_html = ("".join(
        f'<a href="{esc(str(l["url"]))}" target="_blank" rel="noopener" class="text-[0.68rem] text-purple-300/80 hover:text-purple-200 hover:underline mr-3">{esc(str(l.get("label") or _host_of(str(l["url"]))))} ↗</a>'
        for l in links))
    links_html = f'<div class="pt-2 border-t border-white/10">{links_html}</div>' if links_html else ""

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container profile-widget col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[460px] group">
        {widget_header(title, "person", subtitle)}
        <div class="flex flex-col gap-3 p-4 overflow-y-auto flex-grow custom-scrollbar">
            {visual}
            {facts_html}
            {bio_html}
            {links_html}
        </div>
    </div>
    """


def render_progress(widget_id: str, config: dict) -> str:
    """Labeled progress/goal bars: 'X of Y' quantities readable at a glance.
    Checklist is boolean-only and a Chart.js bar is a heavy canvas for three
    numbers — this is the bounded-quantity shape in plain divs.

    Contract: {title, icon?, items:[{label, value?, target?, pct?, unit?,
    note?}]}. pct wins when present; else pct = value/target."""
    title = config.get("title", "Progress")
    icon = config.get("icon", "flag")
    items = [i for i in (config.get("items") or []) if isinstance(i, dict)][:12]
    rows = []
    for it in items:
        label = str(it.get("label") or it.get("name") or "")
        unit = str(it.get("unit") or "")
        pct = it.get("pct")
        value, target = it.get("value"), it.get("target")
        if pct is None and isinstance(value, (int, float)) and isinstance(target, (int, float)) and target:
            pct = value / target * 100
        try:
            pct = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            continue
        if isinstance(value, (int, float)) and isinstance(target, (int, float)):
            amount = f"{unit}{value:,.10g} / {unit}{target:,.10g}"
        else:
            amount = f"{pct:.0f}%"
        note = str(it.get("note") or "")
        fill = "bg-emerald-400" if pct >= 100 else "bg-purple-500"
        rows.append(f"""
            <div class="flex flex-col gap-1">
                <div class="flex items-baseline justify-between gap-2">
                    <span class="text-sm text-white truncate">{esc(label)}</span>
                    <span class="text-xs text-slate-300 tabular-nums shrink-0">{esc(amount)}</span>
                </div>
                <div class="h-2 rounded-full bg-white/10 overflow-hidden">
                    <div class="h-full rounded-full {fill}" style="width:{pct:.0f}%"></div>
                </div>
                {f'<span class="item-meta text-[0.65rem] text-slate-400 tracking-wide">{esc(note)}</span>' if note else ''}
            </div>
        """)
    if not rows:
        return render_data_card(widget_id, {"title": title, "icon": icon,
                                            "content": "No progress data provided."})
    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container progress-widget col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[280px] group">
        {widget_header(title, icon)}
        <div class="flex flex-col gap-3 p-4 overflow-y-auto flex-grow custom-scrollbar">{''.join(rows)}</div>
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
    "crypto_card": render_crypto_card,
    "wallet_graph": render_wallet_graph,
    "weather": render_weather,
    "map": render_map,
    "settings": render_settings,
    "converter": render_converter,
    "reminder": render_reminder,
    # Widget-pack additions (2026-07-21): dense data, comparison and composite
    # display shapes the audit found inexpressible with the original set.
    "table": render_table,
    "kpi_row": render_kpi_row,
    "timeline": render_timeline,
    "versus_card": render_versus_card,
    "profile_card": render_profile_card,
    "progress": render_progress,
    # Alias with its own slug on purpose: coerce_widget_type only hijacks
    # widget_type == 'chart' into stock_card, so a non-stock multi-series
    # comparison emitted as 'multi_chart' can never be coerced away.
    "multi_chart": render_chart,
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
    #
    # Also stamp the widget's IDENTITY. Widget ids are opaque ('traffic-c20bc01b'),
    # so an agent asked to "close the san jose to sf map" saw two indistinguishable
    # traffic widgets and picked one at random — and picked wrong. The title was
    # only present as header text buried in markup. These attributes put type and
    # subject where they can be read without parsing the whole widget, and they are
    # what canvas_read_dom reports as its inventory.
    marker = f'id="{widget_id}"'
    label = esc(str(config.get("title") or "")[:120])
    subtitle = esc(str(config.get("subtitle") or "")[:120])
    attrs = (f'{marker} data-sig="{_content_sig(widget_type, config)}"'
             f' data-widget-type="{esc(widget_type)}"'
             f' data-widget-title="{label}"')
    if subtitle:
        attrs += f' data-widget-subtitle="{subtitle}"'
    # A provisional widget is real fetched data committed BEFORE the agent
    # finishes composing (e.g. news articles pushed the moment the tool
    # returns). The attribute drives a "composing…" badge client-side and is
    # stripped when the final commit re-renders without the flag — which also
    # changes the data-sig (the flag is part of the hashed config), so the
    # client reconciler is guaranteed to replace provisional with final.
    if config.get("provisional"):
        attrs += ' data-provisional="1"'
    return html_out.replace(marker, attrs, 1)
