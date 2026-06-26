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

def generate_widget_html(widget_type: str, widget_id: str, config: dict) -> str:
    """Factory function to route widget creation."""
    if widget_type == "checklist":
        return render_checklist(widget_id, config)
    elif widget_type == "clock":
        return render_clock(widget_id, config)
    elif widget_type == "notes":
        return render_notes(widget_id, config)
    else:
        # Fallback for unknown widgets
        return f'<div id="{widget_id}" class="widget-container glass-card p-4"><p class="text-red-400">Unknown widget type: {widget_type}</p></div>'
