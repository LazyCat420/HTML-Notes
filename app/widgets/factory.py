import html
import json
from typing import Any

def json_escape(val: Any) -> str:
    return html.escape(json.dumps(val))

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
    return f"""
    <div id="{widget_id}" class="widget-container col-span-1 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white p-5 flex flex-col h-[280px] justify-between group" x-data="clockWidget({json_escape(timezone)})">
        <!-- Close Button -->
        <button title="Close Widget" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
            <span class="material-symbols-outlined text-[1.2rem]">close</span>
        </button>
        
        <div class="flex-grow flex flex-col items-center justify-center">
            <div class="text-4xl font-light text-white tracking-widest font-mono" x-text="time">--:--:--</div>
            <div class="text-xs text-purple-300 uppercase tracking-widest mt-2 font-semibold" x-text="date">---</div>
        </div>
        
        <div class="opacity-0 group-hover:opacity-100 transition-opacity w-full mt-2">
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
    
    return f"""
    <div id="{widget_id}" class="widget-container col-span-2 relative overflow-hidden rounded-[2rem] shadow-2xl bg-slate-900/60 backdrop-blur-xl border border-white/10 text-white flex flex-col h-[380px] group" x-data="youtubePlayerWidget({json_escape(video_id)}, {json_escape(title)})">
        <!-- Title Bar -->
        <div class="flex items-center justify-between bg-black/30 p-3 border-b border-white/10 relative z-20">
            <div class="flex items-center gap-2">
                <span class="text-xl text-red-500">📺</span>
                <h3 class="font-bold text-white tracking-wide truncate max-w-[250px]" x-text="title"></h3>
                <span x-show="isLoading" class="text-xs text-slate-400 italic animate-pulse">Resolving stream...</span>
            </div>
            <button title="Close Widget" class="close-widget-btn text-white/50 hover:text-red-400 transition-colors">
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
            <div x-show="error" class="absolute inset-0 bg-slate-950/90 flex flex-col items-center justify-center p-4 text-center z-10">
                <span class="material-symbols-outlined text-4xl text-red-500 mb-2">error</span>
                <span class="text-sm text-slate-200" x-text="error"></span>
            </div>
            <!-- Iframe player -->
            <template x-if="embedUrl">
                <iframe :src="embedUrl" class="w-full h-full border-none" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
            </template>
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
    elif widget_type == "youtube_player":
        return render_youtube_player(widget_id, config)
    else:
        # Fallback for unknown widgets
        return f'<div id="{widget_id}" class="widget-container glass-card p-4"><p class="text-red-400">Unknown widget type: {widget_type}</p></div>'
