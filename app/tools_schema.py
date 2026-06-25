import json

HTML_NOTES_TOOLS_JSON = """
[
    {
        "name": "html_notes_create_note",
        "description": "Create a new note in the HTML Notes database with a title, sanitized HTML fragment, tags, and optional backlinks.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the note"
                },
                "rendered_html": {
                    "type": "string",
                    "description": "HTML note content using approved subset tags: article, section, p, ul, ol, li, blockquote, hr, strong, em, code, pre, a"
                },
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "List of tags to categorize the note"
                },
                "links": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "Optional list of note IDs to link to"
                }
            },
            "required": [
                "title",
                "rendered_html"
            ]
        },
        "tier": 0,
        "permission": "write",
        "concurrency_safe": true,
        "domain": "General",
        "labels": ["tool"]
    },
    {
        "name": "html_notes_update_note",
        "description": "Update the content, title, tags, or links of an existing note in the HTML Notes database.",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The unique note ID to update (e.g., 'note_123')"
                },
                "title": {
                    "type": "string",
                    "description": "Optional updated title"
                },
                "rendered_html": {
                    "type": "string",
                    "description": "Optional updated HTML content fragment"
                },
                "tags": {
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "Optional updated list of tags"
                },
                "links": {
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "Optional updated list of target link note IDs"
                }
            },
            "required": ["note_id"]
        },
        "tier": 0,
        "permission": "write",
        "concurrency_safe": true,
        "domain": "General",
        "labels": ["tool"]
    },
    {
        "name": "html_notes_get_note",
        "description": "Retrieve the details and full HTML content of a specific note by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The note ID to fetch"
                }
            },
            "required": ["note_id"]
        },
        "tier": 0,
        "permission": "read_only",
        "concurrency_safe": true,
        "domain": "General",
        "labels": ["tool"]
    },
    {
        "name": "html_notes_search_notes",
        "description": "Search notes in the HTML Notes database matching a specific query term.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword or tag name"
                }
            },
            "required": ["query"]
        },
        "tier": 0,
        "permission": "read_only",
        "concurrency_safe": true,
        "domain": "General",
        "labels": ["tool"]
    },
    {
        "name": "html_notes_link_notes",
        "description": "Establish a hyperlink connection between a source note and a target note.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_note_id": {
                    "type": "string",
                    "description": "ID of the source note"
                },
                "target_note_id": {
                    "type": "string",
                    "description": "ID of the target note to connect to"
                }
            },
            "required": ["source_note_id", "target_note_id"]
        },
        "tier": 0,
        "permission": "write",
        "concurrency_safe": true,
        "domain": "General",
        "labels": ["tool"]
    },
    {
        "name": "html_notes_modify_dom",
        "description": "Precisely modify a specific part of a note's HTML using CSS selectors. Use this to insert tables, sidebars, or other components relative to existing elements.",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "The ID of the note to modify"
                },
                "css_selector": {
                    "type": "string",
                    "description": "A valid CSS selector to locate the target element (e.g., '#main-content', '.sidebar', 'table:nth-of-type(1)')"
                },
                "action": {
                    "type": "string",
                    "enum": ["append", "prepend", "insert_before", "insert_after", "replace"],
                    "description": "The DOM operation to perform relative to the matched element"
                },
                "html_snippet": {
                    "type": "string",
                    "description": "The raw HTML string to insert or replace with"
                }
            },
            "required": ["note_id", "css_selector", "action", "html_snippet"]
        },
        "tier": 0,
        "permission": "write",
        "concurrency_safe": true,
        "domain": "General",
        "labels": ["tool"]
    },
    {
        "name": "render_component",
        "description": "Render structured data as a styled HTML/CSS component on the canvas. Pass data matching the component_type format. For arbitrary HTML use component_type='custom_html' with rendered_html field.",
        "parameters": {
            "type": "object",
            "properties": {
                "component_type": {
                    "type": "string",
                    "enum": ["calendar_widget", "task_checklist", "reminder_banner", "kanban_board", "habit_tracker", "data_card", "data_table", "summary_panel", "alert_banner", "custom_html"],
                    "description": "The visual layout template to use."
                },
                "title": {
                    "type": "string",
                    "description": "Heading label for the component"
                },
                "data": {
                    "type": "object",
                    "description": "Structured data for the component. Format by type: calendar_widget={days:[{day:str, events:[str]}]}. task_checklist={tasks:[{text:str, done:bool, due?:str}]}. reminder_banner={title:str, message:str, time?:str, color?:str}. data_table={headers:[str], rows:[[str]]}. kanban_board={columns:[{name:str, cards:[{title:str}]}]}. habit_tracker={habits:[{name:str, history:[bool]}]}. data_card={value:str, label:str, icon?:str, badge?:str, progress?:int, description?:str}. summary_panel={sections:[{heading:str, content:str}]}. alert_banner={severity:'info'|'success'|'warning'|'danger', message:str, action?:str}."
                },
                "rendered_html": {
                    "type": "string",
                    "description": "Raw HTML string. Only used when component_type is 'custom_html'."
                }
            },
            "required": ["component_type", "title"]
        },
        "tier": 0,
        "permission": "read_only",
        "concurrency_safe": true,
        "domain": "Rendering",
        "labels": ["tool"]
    },
    {
        "name": "canvas_read_dom",
        "description": "Read the current live canvas DOM and return a structured summary of all visible elements. Use this to understand what is currently displayed before making modifications. Returns element count, component types found, text content summaries, and optionally the HTML of a specific element matched by CSS selector.",
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_html": {
                    "type": "string",
                    "description": "The current canvas HTML content (provided automatically by the system)."
                },
                "css_selector": {
                    "type": "string",
                    "description": "Optional CSS selector to read a specific element. If omitted, returns full canvas summary."
                }
            },
            "required": ["canvas_html"]
        },
        "tier": 0,
        "permission": "read_only",
        "concurrency_safe": true,
        "domain": "Rendering",
        "labels": ["tool"]
    },
    {
        "name": "canvas_modify_dom",
        "description": "Modify an element on the live canvas using a CSS selector. Use this to update, append to, or remove existing components without replacing the entire canvas. Returns the modified canvas HTML.",
        "parameters": {
            "type": "object",
            "properties": {
                "canvas_html": {
                    "type": "string",
                    "description": "The current canvas HTML content (provided automatically by the system)."
                },
                "css_selector": {
                    "type": "string",
                    "description": "CSS selector to locate the target element (e.g., '.task-checklist', '.glass-card:first-child', 'h3')"
                },
                "action": {
                    "type": "string",
                    "enum": ["append", "prepend", "replace", "remove", "insert_after", "insert_before"],
                    "description": "The DOM operation: append/prepend add inside the element, replace swaps it, remove deletes it, insert_before/after add as siblings."
                },
                "html_snippet": {
                    "type": "string",
                    "description": "The HTML to insert. Not required for 'remove' action."
                }
            },
            "required": ["canvas_html", "css_selector", "action"]
        },
        "tier": 0,
        "permission": "write",
        "concurrency_safe": true,
        "domain": "Rendering",
        "labels": ["tool"]
    }
]
"""
HTML_NOTES_TOOLS = json.loads(HTML_NOTES_TOOLS_JSON)
