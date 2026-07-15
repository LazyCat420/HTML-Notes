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

def render_data_card(widget_id: str, config: dict) -> str:
    """Universal server-rendered data widget (the reliable path for news,
    recipes, weather, search results — any structured data). The data is baked
    into the HTML at render time, so nothing depends on client-side fetching.

    Contract: {title, subtitle?, icon?, image?, content?, items?: [
        {title, description?, image?, url?, badge?, meta?}]}
    Fallback chain: items -> content text -> raw config dump. Never blank.
    """
    title = config.get("title", "Data")
    subtitle = config.get("subtitle", "")
    icon = config.get("icon", "article")
    hero = config.get("image", "")
    content = config.get("content", "")
    items = config.get("items", []) or []
    if isinstance(items, dict):
        items = [items]

    body_parts = []

    if hero:
        body_parts.append(f"""
            <div class="hero-image w-full h-32 shrink-0 overflow-hidden relative">
                <img src="{esc(hero)}" alt="{esc(title)}" class="w-full h-full object-cover" loading="lazy">
                <div class="absolute inset-0 bg-gradient-to-t from-slate-950/80 to-transparent"></div>
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

    if rendered_items:
        body_parts.append(f"""
            <ul class="data-card-list flex flex-col gap-1 p-3 overflow-y-auto flex-grow custom-scrollbar">
                {''.join(rendered_items)}
            </ul>
        """)
    elif content:
        paragraphs = "".join(
            f'<p class="text-sm text-slate-200 leading-relaxed mb-2">{esc(p.strip())}</p>'
            for p in str(content).split("\n") if p.strip()
        )
        body_parts.append(f"""
            <div class="data-card-content p-4 overflow-y-auto flex-grow custom-scrollbar">{paragraphs}</div>
        """)
    else:
        # Last-resort fallback: show whatever config we got as key/value rows
        # instead of rendering an empty or broken card.
        rows = "".join(
            f'<div class="flex justify-between gap-3 py-1.5 border-b border-white/5"><span class="text-xs uppercase tracking-wider text-slate-400">{esc(k)}</span><span class="text-sm text-slate-200 text-right">{esc(v)}</span></div>'
            for k, v in config.items() if k not in ("title", "subtitle", "icon") and not isinstance(v, (dict, list))
        ) or '<p class="text-slate-400 text-xs italic text-center py-6">No data provided</p>'
        body_parts.append(f'<div class="data-card-content p-4 overflow-y-auto flex-grow custom-scrollbar">{rows}</div>')

    return f"""
    <div id="{widget_id}" x-data="{{}}" class="widget-container data-card col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
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
        figures = []
        for img in normalized[:4]:
            caption_html = (
                f'<figcaption class="text-[0.68rem] text-slate-300 px-2 py-1 bg-black/40 backdrop-blur-sm absolute bottom-0 inset-x-0 truncate">{esc(img["caption"])}</figcaption>'
                if img.get("caption") else ""
            )
            figures.append(f"""
                <figure class="relative flex-1 min-w-[45%] overflow-hidden rounded-xl bg-slate-950 ring-1 ring-white/10">
                    <img src="{esc(img['url'])}" alt="{esc(img.get('caption') or title)}" loading="lazy" class="w-full h-full object-cover">
                    {caption_html}
                </figure>
            """)
        body = f'<div class="image-widget-body flex flex-wrap gap-2 p-3 flex-grow overflow-hidden">{"".join(figures)}</div>'
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
    url = config.get("url", "about:blank")
    title = config.get("title", "App Window")
    icon = config.get("icon", "🌐")
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group">
        <!-- Title Bar -->
        <div class="flex items-center justify-between bg-black/30 p-3 border-b border-white/10 relative z-20">
            <div class="flex items-center gap-2">
                <span class="text-xl">{icon}</span>
                <h3 class="font-bold text-white tracking-wide truncate max-w-[250px]">{title}</h3>
            </div>
            <div class="flex items-center gap-3">
                <a href="{url}" target="_blank" class="text-white/50 hover:text-white transition-colors" title="Open Full App">
                    <span class="material-symbols-outlined text-[1.2rem]">open_in_new</span>
                </a>
                <button title="Close Widget" class="close-widget-btn text-white/50 hover:text-red-400 transition-colors">
                    <span class="material-symbols-outlined text-[1.2rem]">close</span>
                </button>
            </div>
        </div>
        <!-- Iframe Content -->
        <iframe src="{url}" class="w-full flex-grow border-none bg-slate-950" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>
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
    "chart": render_chart,
    "stock_card": render_stock_card,
}

def generate_widget_html(widget_type: str, widget_id: str, config: dict) -> str:
    """Factory function to route widget creation."""
    if not isinstance(config, dict):
        config = {}
    renderer = WIDGET_RENDERERS.get(widget_type)
    if renderer:
        return renderer(widget_id, config)
    # Fallback chain: an unknown type degrades to a data card showing whatever
    # config the model sent, instead of a dead error card.
    fallback = dict(config)
    fallback.setdefault("title", (widget_type or "Widget").replace("_", " ").title())
    return render_data_card(widget_id, fallback)
