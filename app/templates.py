import html

def escape(text):
    if not isinstance(text, str):
        text = str(text)
    return html.escape(text)

TEMPLATES = {
    "calendar_widget": lambda data: f"""
<div class="glass-card calendar-widget">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📅 {escape(data.get('title', 'Calendar'))}</h3>
  </div>
  <div class="calendar-grid" style="display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; margin-top: 10px;">
    {''.join(
        f'<div class="cal-day {"has-event" if d.get("events") else ""}" style="padding: 10px; border-radius: 8px; background: rgba(255,255,255,0.05); text-align: center;">'
        f'<span class="day-num" style="display: block; font-weight: bold; margin-bottom: 5px;">{escape(d.get("day", ""))}</span>'
        f'{"".join(f"<div class=\\"event-dot\\" style=\\"font-size: 0.8em; color: #00ff88;\\">{escape(e)}</div>" for e in d.get("events", []))}'
        f'</div>'
        for d in data.get("days", [])
    )}
  </div>
</div>""",

    "task_checklist": lambda data: f"""
<div class="glass-card task-checklist">
  <div class="glass-card-header">
    <h3 class="glass-card-title">✅ {escape(data.get('title', 'Tasks'))}</h3>
  </div>
  <ul class="task-list" style="list-style: none; padding: 0; margin-top: 10px; display: flex; flex-direction: column; gap: 8px;">
    {''.join(
        f'<li class="task-item {"done" if t.get("done") else ""}" style="display: flex; align-items: center; gap: 10px; padding: 10px; background: rgba(255,255,255,0.02); border-radius: 6px;">'
        f'<input type="checkbox" {"checked" if t.get("done") else ""} style="accent-color: #00ff88; width: 18px; height: 18px;">'
        f'<span style="flex-grow: 1; {"text-decoration: line-through; opacity: 0.5;" if t.get("done") else ""}">{escape(t.get("text", ""))}</span>'
        f'{"<span class=\\"status-badge warning\\" style=\\"font-size: 0.7em;\\">" + escape(t["due"]) + "</span>" if t.get("due") else ""}'
        f'</li>'
        for t in data.get("tasks", [])
    )}
  </ul>
</div>""",

    "reminder_banner": lambda data: f"""
<div class="glass-card reminder-banner" style="border-left: 4px solid {escape(data.get('color', '#00ff88'))}; display: flex; align-items: center; justify-content: space-between;">
  <div>
    <h3 class="glass-card-title" style="margin: 0; font-size: 1.1em;">🔔 {escape(data.get('title', 'Reminder'))}</h3>
    <p class="text-muted" style="margin: 5px 0 0 0; font-size: 0.9em;">{escape(data.get('message', ''))}</p>
  </div>
  <div class="status-badge info">{escape(data.get('time', 'Soon'))}</div>
</div>""",

    "data_table": lambda data: f"""
<div class="glass-card data-table-container">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📊 {escape(data.get('title', 'Data Table'))}</h3>
  </div>
  <table class="data-table" style="width: 100%; border-collapse: collapse; margin-top: 10px;">
    <thead>
      <tr style="border-bottom: 1px solid rgba(255,255,255,0.1); text-align: left;">
        {''.join(f'<th style="padding: 10px; color: #a3b3c8;">{escape(h)}</th>' for h in data.get("headers", []))}
      </tr>
    </thead>
    <tbody>
      {''.join(
          f'<tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">' +
          ''.join(f'<td style="padding: 10px;">{escape(cell)}</td>' for cell in row) +
          f'</tr>'
          for row in data.get("rows", [])
      )}
    </tbody>
  </table>
</div>""",

    "kanban_board": lambda data: f"""
<div class="glass-card kanban-board">
  <div class="glass-card-header">
    <h3 class="glass-card-title">📋 {escape(data.get('title', 'Kanban Board'))}</h3>
  </div>
  <div class="dashboard-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); align-items: start;">
    {''.join(
        f'<div class="kanban-column" style="background: rgba(0,0,0,0.2); padding: 10px; border-radius: 8px;">'
        f'<h4 style="margin: 0 0 10px 0; color: #00ff88; text-transform: uppercase; font-size: 0.8em; letter-spacing: 1px;">{escape(col.get("name", ""))}</h4>'
        f'<div style="display: flex; flex-direction: column; gap: 8px;">' +
        ''.join(f'<div class="glass-card" style="padding: 10px; margin: 0;"><div style="font-size: 0.9em;">{escape(card.get("title", ""))}</div></div>' for card in col.get("cards", [])) +
        f'</div></div>'
        for col in data.get("columns", [])
    )}
  </div>
</div>""",

    "habit_tracker": lambda data: f"""
<div class="glass-card habit-tracker">
  <div class="glass-card-header">
    <h3 class="glass-card-title">🌱 {escape(data.get('title', 'Habits'))}</h3>
  </div>
  <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 15px;">
    {''.join(
        f'<div style="display: flex; align-items: center; justify-content: space-between;">'
        f'<span style="font-weight: 500; width: 120px;">{escape(habit.get("name", ""))}</span>'
        f'<div style="display: flex; gap: 4px;">' +
        ''.join(f'<div style="width: 20px; height: 20px; border-radius: 4px; background: {"#00ff88" if done else "rgba(255,255,255,0.1)"};"></div>' for done in habit.get("history", [])) +
        f'</div></div>'
        for habit in data.get("habits", [])
    )}
  </div>
</div>"""
}
