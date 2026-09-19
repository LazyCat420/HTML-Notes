import json

CUSTOM_HTML_NOTES_CANVAS_PROMPT = """
You are the HTML-Notes intelligent agent. You help the user manage notes, create widgets, and update the canvas.
"""

CANVAS_TOOLS = [
    {
        "name": "canvas_add_widget",
        "description": "Add a new widget to the HTML-Notes canvas.",
        "parameters": {
            "type": "object",
            "properties": {
                "widget_type": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["widget_type", "content"]
        }
    },
    {
        "name": "canvas_modify_dom",
        "description": "Modify the DOM of an existing widget.",
        "parameters": {
            "type": "object",
            "properties": {
                "widget_id": {"type": "string"},
                "html": {"type": "string"}
            },
            "required": ["widget_id", "html"]
        }
    }
]

def get_html_notes_profile():
    return {
        "persona": "HTML_NOTES_ASSISTANT",
        "system_prompt": CUSTOM_HTML_NOTES_CANVAS_PROMPT,
        "tools": CANVAS_TOOLS,
        "model": "llama3"
    }
