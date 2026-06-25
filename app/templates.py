import html as html_module


def escape(text):
    if not isinstance(text, str):
        text = str(text)
    return html_module.escape(text)


def _calendar_widget(data):
    days_html = ""
    for d in data.get("days", []):
        cls = "cal-day has-event" if d.get("events") else "cal-day"
        events_html = ""
        for e in d.get("events", []):
            events_html += '<div class="event-dot" style="font-size: 0.8em; color: #00ff88;">' + escape(e) + '</div>'
        days_html += (
            f'<div class="{cls}" style="padding: 10px; border-radius: 8px; background: rgba(255,255,255,0.05); text-align: center;">'
            f'<span class="day-num" style="display: block; font-weight: bold; margin-bottom: 5px;">{escape(d.get("day", ""))}</span>'
            f'{events_html}'
            f'</div>'
        )
    return f"""
<div class="glass-card calendar-widget">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📅 {escape(data.get('title', 'Calendar'))}</h3>
  </div>
  <div class="calendar-grid" style="display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; margin-top: 10px;">
    {days_html}
  </div>
</div>"""


def _task_checklist(data):
    items_html = ""
    for t in data.get("tasks", []):
        checked = "checked" if t.get("done") else ""
        done_cls = "done" if t.get("done") else ""
        done_style = "text-decoration: line-through; opacity: 0.5;" if t.get("done") else ""
        due_html = ""
        if t.get("due"):
            due_html = f'<span class="status-badge warning" style="font-size: 0.7em;">{escape(t["due"])}</span>'
        items_html += (
            f'<li class="task-item {done_cls}" style="display: flex; align-items: center; gap: 10px; padding: 10px; background: rgba(255,255,255,0.02); border-radius: 6px;">'
            f'<input type="checkbox" {checked} style="accent-color: #00ff88; width: 18px; height: 18px;">'
            f'<span style="flex-grow: 1; {done_style}">{escape(t.get("text", ""))}</span>'
            f'{due_html}'
            f'</li>'
        )
    return f"""
<div class="glass-card task-checklist">
  <div class="glass-card-header">
    <h3 class="glass-card-title">✅ {escape(data.get('title', 'Tasks'))}</h3>
  </div>
  <ul class="task-list" style="list-style: none; padding: 0; margin-top: 10px; display: flex; flex-direction: column; gap: 8px;">
    {items_html}
  </ul>
</div>"""


def _reminder_banner(data):
    color = escape(data.get('color', '#00ff88'))
    return f"""
<div class="glass-card reminder-banner" style="border-left: 4px solid {color}; display: flex; align-items: center; justify-content: space-between;">
  <div>
    <h3 class="glass-card-title" style="margin: 0; font-size: 1.1em;">🔔 {escape(data.get('title', 'Reminder'))}</h3>
    <p class="text-muted" style="margin: 5px 0 0 0; font-size: 0.9em;">{escape(data.get('message', ''))}</p>
  </div>
  <div class="status-badge info">{escape(data.get('time', 'Soon'))}</div>
</div>"""


def _data_table(data):
    headers_html = ""
    for h in data.get("headers", []):
        headers_html += f'<th style="padding: 10px; color: #a3b3c8;">{escape(h)}</th>'
    rows_html = ""
    for row in data.get("rows", []):
        cells = ""
        for cell in row:
            cells += f'<td style="padding: 10px;">{escape(cell)}</td>'
        rows_html += f'<tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">{cells}</tr>'
    return f"""
<div class="glass-card data-table-container">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📊 {escape(data.get('title', 'Data Table'))}</h3>
  </div>
  <table class="data-table" style="width: 100%; border-collapse: collapse; margin-top: 10px;">
    <thead>
      <tr style="border-bottom: 1px solid rgba(255,255,255,0.1); text-align: left;">
        {headers_html}
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</div>"""


def _kanban_board(data):
    columns_html = ""
    for col in data.get("columns", []):
        cards_html = ""
        for card in col.get("cards", []):
            cards_html += f'<div class="glass-card" style="padding: 10px; margin: 0;"><div style="font-size: 0.9em;">{escape(card.get("title", ""))}</div></div>'
        columns_html += (
            f'<div class="kanban-column" style="background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px;">'
            f'<h4 style="margin: 0 0 10px 0; color: #00ff88; text-transform: uppercase; font-size: 0.8em; letter-spacing: 1px;">{escape(col.get("name", ""))}</h4>'
            f'<div style="display: flex; flex-direction: column; gap: 8px;">{cards_html}</div>'
            f'</div>'
        )
    return f"""
<div class="glass-card kanban-board">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📋 {escape(data.get('title', 'Kanban Board'))}</h3>
  </div>
  <div class="dashboard-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); align-items: start;">
    {columns_html}
  </div>
</div>"""


def _habit_tracker(data):
    habits_html = ""
    for habit in data.get("habits", []):
        dots = ""
        for done in habit.get("history", []):
            bg = "#00ff88" if done else "rgba(255,255,255,0.1)"
            dots += f'<div style="width: 20px; height: 20px; border-radius: 4px; background: {bg};"></div>'
        habits_html += (
            f'<div style="display: flex; align-items: center; justify-content: space-between;">'
            f'<span style="font-weight: 500; width: 120px;">{escape(habit.get("name", ""))}</span>'
            f'<div style="display: flex; gap: 4px;">{dots}</div>'
            f'</div>'
        )
    return f"""
<div class="glass-card habit-tracker">
  <div class="glass-card-header">
    <h3 class="glass-card-title">🌱 {escape(data.get('title', 'Habits'))}</h3>
  </div>
  <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 15px;">
    {habits_html}
  </div>
</div>"""


def _data_card(data):
    badge_html = ""
    if data.get("badge"):
        badge_type = escape(data.get("badge_type", "info"))
        badge_html = f'<span class="status-badge {badge_type}">{escape(data["badge"])}</span>'
    progress_html = ""
    if data.get("progress") is not None:
        pct = min(int(data.get("progress", 0)), 100)
        progress_html = f'<div class="progress-container" style="margin-top: 1rem;"><div class="progress-bar" style="width: {pct}%;"></div></div>'
    desc_html = ""
    if data.get("description"):
        desc_html = f'<p class="text-muted text-sm" style="margin-top: 0.8rem;">{escape(data["description"])}</p>'
    icon = escape(data.get("icon", "📊"))
    return f"""
<div class="glass-card data-card">
  <div class="glass-card-header">
    <h3 class="glass-card-title">{icon} {escape(data.get('title', 'Metric'))}</h3>
    {badge_html}
  </div>
  <div class="metric-box">
    <span class="metric-value">{escape(data.get('value', '—'))}</span>
    <span class="metric-label">{escape(data.get('label', ''))}</span>
  </div>
  {progress_html}
  {desc_html}
</div>"""


def _summary_panel(data):
    sections_html = ""
    for section in data.get("sections", []):
        sections_html += (
            f'<div style="border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 0.8rem;">'
            f'<div style="font-weight: 600; color: #e6edf3; margin-bottom: 0.3rem;">{escape(section.get("heading", ""))}</div>'
            f'<div class="text-muted text-sm">{escape(section.get("content", ""))}</div>'
            f'</div>'
        )
    return f"""
<div class="glass-card summary-panel">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📋 {escape(data.get('title', 'Summary'))}</h3>
  </div>
  <div style="display: flex; flex-direction: column; gap: 1rem; margin-top: 0.5rem;">
    {sections_html}
  </div>
</div>"""


def _alert_banner(data):
    severity = data.get("severity", "info")
    color_map = {"info": "#58a6ff", "success": "#2ea043", "warning": "#d29922", "danger": "#f85149"}
    icon_map = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "danger": "🚨"}
    color = escape(color_map.get(severity, "#58a6ff"))
    icon = escape(data.get("icon", icon_map.get(severity, "ℹ️")))
    action_html = ""
    if data.get("action"):
        action_html = f'<div style="margin-top: 0.5rem;"><span class="status-badge {escape(severity)}">{escape(data["action"])}</span></div>'
    return f"""
<div class="glass-card alert-banner" style="border-left: 4px solid {color}; display: flex; align-items: flex-start; gap: 1rem;">
  <div style="font-size: 1.5rem; flex-shrink: 0;">{icon}</div>
  <div style="flex: 1;">
    <div style="font-weight: 600; color: #e6edf3; margin-bottom: 0.3rem;">{escape(data.get('title', 'Alert'))}</div>
    <div class="text-muted text-sm">{escape(data.get('message', ''))}</div>
    {action_html}
  </div>
</div>"""


TEMPLATES = {
    "calendar_widget": _calendar_widget,
    "task_checklist": _task_checklist,
    "reminder_banner": _reminder_banner,
    "data_table": _data_table,
    "kanban_board": _kanban_board,
    "habit_tracker": _habit_tracker,
    "data_card": _data_card,
    "summary_panel": _summary_panel,
    "alert_banner": _alert_banner,
}
