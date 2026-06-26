import json

def render_checklist(widget_id: str, config: dict) -> str:
    title = config.get("title", "Checklist")
    items = config.get("items", [])
    # Safely dump items for Alpine.js injection
    items_json = json.dumps(items).replace('"', '&quot;')
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 glass-card p-4 rounded-xl shadow-lg bg-slate-800/80" x-data="checklistWidget('{title}', {items_json})">
        <h3 class="text-lg font-bold mb-2 text-white" x-text="title"></h3>
        <div class="flex gap-2 mb-3">
            <input x-model="newItem" @keydown.enter="addTask" type="text" placeholder="Add task..." class="px-2 py-1 rounded bg-slate-700 text-white flex-grow border border-slate-600 focus:outline-none focus:border-blue-500">
            <button @click="addTask" class="px-3 py-1 bg-blue-500 hover:bg-blue-600 transition-colors rounded text-white font-bold shadow">+</button>
        </div>
        <ul class="space-y-2">
            <template x-for="(item, idx) in items" :key="idx">
                <li class="flex items-center gap-3 p-2 hover:bg-slate-700/50 rounded transition-colors group">
                    <input type="checkbox" x-model="item.done" class="rounded text-blue-500 w-4 h-4 cursor-pointer">
                    <span :class="{{'line-through opacity-50': item.done}}" x-text="item.text" class="text-slate-200 flex-grow cursor-pointer" @click="item.done = !item.done"></span>
                    <button @click="removeTask(idx)" class="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-300 transition-opacity">×</button>
                </li>
            </template>
            <li x-show="items.length === 0" class="text-slate-400 text-sm italic text-center py-2">No tasks yet</li>
        </ul>
    </div>
    """

def render_clock(widget_id: str, config: dict) -> str:
    timezone = config.get("timezone", "local")
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 glass-card p-6 rounded-xl shadow-lg bg-slate-800/80 flex flex-col items-center justify-center" x-data="clockWidget('{timezone}')">
        <div class="text-4xl font-light text-white tracking-widest" x-text="time">--:--:--</div>
        <div class="text-sm text-slate-400 uppercase tracking-wider mt-1" x-text="date">---</div>
    </div>
    """

def render_notes(widget_id: str, config: dict) -> str:
    title = config.get("title", "Quick Notes")
    content = config.get("content", "")
    content_json = json.dumps(content).replace('"', '&quot;')
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 glass-card p-4 rounded-xl shadow-lg bg-slate-800/80 flex flex-col" x-data="notesWidget('{title}', {content_json})">
        <h3 class="text-lg font-bold mb-2 text-white" x-text="title"></h3>
        <textarea x-model="content" class="w-full h-32 bg-slate-700/50 text-slate-200 p-3 rounded-lg border border-slate-600 focus:outline-none focus:border-blue-500 resize-none flex-grow shadow-inner" placeholder="Type your notes here..."></textarea>
    </div>
    """

def render_iframe_app(widget_id: str, config: dict) -> str:
    url = config.get("url", "about:blank")
    title = config.get("title", "App Window")
    icon = config.get("icon", "🌐")
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 glass-card rounded-xl shadow-lg bg-slate-800/80 flex flex-col overflow-hidden h-[500px]" x-data>
        <!-- Title Bar -->
        <div class="flex items-center justify-between bg-slate-900/80 p-3 border-b border-slate-700">
            <div class="flex items-center gap-2">
                <span class="text-xl">{icon}</span>
                <h3 class="font-bold text-white tracking-wide">{title}</h3>
            </div>
            <div class="flex items-center gap-3">
                <a href="{url}" target="_blank" class="text-slate-400 hover:text-blue-400 transition-colors" title="Open Full App">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                </a>
                <button @click="$el.closest('.widget-container').remove()" class="text-slate-400 hover:text-red-400 transition-colors" title="Close Widget">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
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
    <div id="{widget_id}" class="widget-container col-span-1 glass-card p-5 rounded-2xl shadow-xl bg-slate-800/90 backdrop-blur-md flex flex-col gap-4 border border-slate-700/50" x-data="musicPlayerWidget('{genre}', {autoplay})">
        <!-- Title Bar -->
        <div class="flex items-center justify-between">
            <div class="flex items-center gap-2 text-indigo-400">
                <span class="material-symbols-outlined text-lg">music_note</span>
                <span class="text-sm font-semibold tracking-wider uppercase">Mini Player</span>
            </div>
            <button @click="destroy(); $el.closest('.widget-container').remove()" class="text-slate-500 hover:text-red-400 transition-colors">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
            </button>
        </div>

        <!-- Track Info -->
        <div class="flex flex-col items-center text-center mt-2">
            <div class="w-16 h-16 rounded-full bg-gradient-to-tr from-indigo-500 to-purple-500 mb-3 flex items-center justify-center shadow-lg" :class="{{'animate-pulse': isPlaying}}">
                <span class="material-symbols-outlined text-3xl text-white">music_cast</span>
            </div>
            <h4 class="text-lg font-bold text-white leading-tight truncate w-full px-2" x-text="currentTrack ? currentTrack.title : 'Loading Tracks...'"></h4>
            <p class="text-sm text-slate-400 truncate w-full px-2 mt-1" x-text="currentTrack ? currentTrack.artist : 'Please wait'"></p>
            <p class="text-xs text-indigo-400 mt-1 font-mono" x-show="genreFilter">Filter: <span x-text="genreFilter"></span></p>
        </div>

        <!-- Controls -->
        <div class="flex items-center justify-center gap-4 mt-2">
            <button @click="playPause()" class="w-12 h-12 rounded-full bg-indigo-500 hover:bg-indigo-400 text-white flex items-center justify-center shadow-md transition-all active:scale-95 disabled:opacity-50" :disabled="!currentTrack">
                <span class="material-symbols-outlined text-2xl" x-text="isPlaying ? 'pause' : 'play_arrow'"></span>
            </button>
            <button @click="nextTrack()" class="w-10 h-10 rounded-full bg-slate-700 hover:bg-slate-600 text-white flex items-center justify-center shadow-md transition-all active:scale-95 disabled:opacity-50" :disabled="!currentTrack">
                <span class="material-symbols-outlined text-xl">skip_next</span>
            </button>
        </div>
        
        <!-- Error State -->
        <div x-show="error" class="text-xs text-red-400 text-center mt-2" x-text="error"></div>
    </div>
    """

def generate_widget_html(widget_type: str, widget_id: str, config: dict) -> str:
    """Factory function to route widget creation."""
    if widget_type == "checklist":
        return render_checklist(widget_id, config)
    elif widget_type == "clock":
        return render_clock(widget_id, config)
    elif widget_type == "notes":
        return render_notes(widget_id, config)
    elif widget_type == "iframe_app":
        return render_iframe_app(widget_id, config)
    elif widget_type == "mini_music_player":
        return render_mini_music_player(widget_id, config)
    else:
        # Fallback for unknown widgets
        return f'<div id="{widget_id}" class="widget-container glass-card p-4"><p class="text-red-400">Unknown widget type: {widget_type}</p></div>'
