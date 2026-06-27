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
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-gradient-to-br from-purple-900 via-indigo-900 to-slate-900 text-white border border-white/10 group" x-data="musicPlayerWidget('{genre}', {autoplay})">
        <!-- Background Blur/Glow effect -->
        <div class="absolute inset-0 bg-cover bg-center opacity-30 mix-blend-overlay" style="background-image: url('https://images.unsplash.com/photo-1514525253161-7a46d19cd819?q=80&w=600&auto=format&fit=crop')"></div>
        <div class="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-900/60 to-transparent"></div>
        
        <!-- Content Container -->
        <div class="relative z-10 p-5 flex flex-col h-full justify-between">
            
            <!-- Top Bar: Genre / Icon -->
            <div class="flex justify-between items-start mb-2">
                <div class="bg-black/40 backdrop-blur-md px-3 py-1 rounded-full border border-white/10 flex items-center gap-1.5 shadow-sm">
                    <span class="material-symbols-outlined text-[1rem] text-purple-300">graphic_eq</span>
                    <span class="text-xs font-semibold tracking-wider text-purple-200 uppercase" x-text="genreFilter || 'Radio'"></span>
                </div>
                <button @click="destroy(); window.WidgetManager.dismiss($el.closest('.widget-container'))" class="text-white/50 hover:text-white bg-black/20 hover:bg-black/40 rounded-full p-1.5 backdrop-blur-sm transition-all shadow-sm">
                    <span class="material-symbols-outlined text-[1rem]">close</span>
                </button>
            </div>
            
            <!-- Track Info -->
            <div class="flex items-center gap-4 mt-2">
                <!-- Album Art Mock -->
                <div class="w-16 h-16 shrink-0 rounded-2xl bg-gradient-to-tr from-fuchsia-500 to-orange-500 shadow-lg flex items-center justify-center relative overflow-hidden ring-2 ring-white/10">
                    <div class="absolute inset-0 bg-black/20 transition-opacity" :class="{{'opacity-0': !isPlaying, 'animate-pulse': isPlaying}}"></div>
                    <span class="material-symbols-outlined text-3xl text-white relative z-10">album</span>
                </div>
                
                <div class="flex-grow min-w-0 flex flex-col justify-center">
                    <h4 class="text-lg font-bold text-white truncate leading-tight drop-shadow-md" x-text="currentTrack ? currentTrack.title : 'Searching signals...'"></h4>
                    <p class="text-sm text-purple-200 truncate mt-0.5 drop-shadow-sm font-medium" x-text="currentTrack ? currentTrack.artist : 'Please wait'"></p>
                </div>
            </div>

            <!-- Progress Bar -->
            <div class="w-full mt-5 relative group-hover:opacity-100 opacity-80 transition-opacity">
                <div class="h-1.5 w-full bg-white/10 rounded-full overflow-hidden backdrop-blur-sm shadow-inner">
                    <div class="h-full bg-gradient-to-r from-purple-400 to-fuchsia-400 rounded-full relative shadow-[0_0_10px_rgba(216,180,254,0.5)] transition-all duration-300" :class="{{'w-1/3 animate-[slideRight_10s_linear_infinite]': isPlaying, 'w-0': !isPlaying}}"></div>
                </div>
            </div>
            
            <!-- Controls -->
            <div class="flex items-center justify-between mt-5 px-1">
                <button class="text-white/50 hover:text-white transition-colors p-2" title="Shuffle">
                    <span class="material-symbols-outlined text-xl">shuffle</span>
                </button>
                
                <div class="flex items-center gap-4">
                    <button @click="nextTrack()" class="w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                        <span class="material-symbols-outlined">skip_previous</span>
                    </button>
                    
                    <button @click="playPause()" class="w-16 h-16 rounded-[2rem] bg-purple-300 hover:bg-purple-200 text-slate-900 flex items-center justify-center shadow-[0_0_20px_rgba(216,180,254,0.2)] hover:shadow-[0_0_25px_rgba(216,180,254,0.4)] transition-all active:scale-95" :disabled="!currentTrack">
                        <span class="material-symbols-outlined text-4xl" x-text="isPlaying ? 'pause' : 'play_arrow'" style="font-variation-settings: 'FILL' 1;">play_arrow</span>
                    </button>
                    
                    <button @click="nextTrack()" class="w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                        <span class="material-symbols-outlined">skip_next</span>
                    </button>
                </div>
                
                <button class="text-white/50 hover:text-white transition-colors p-2" title="Repeat">
                    <span class="material-symbols-outlined text-xl">repeat</span>
                </button>
            </div>
            
            <!-- Error State -->
            <div x-show="error" x-transition class="absolute bottom-2 left-1/2 -translate-x-1/2 bg-red-500/90 text-white text-xs px-3 py-1 rounded-full backdrop-blur-md whitespace-nowrap shadow-lg" x-text="error" style="display: none;"></div>
        </div>
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
