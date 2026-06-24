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
        "source": "",
        "permission": "write",
        "fallback_only": false,
        "concurrency_safe": true,
        "max_result_chars": 50000,
        "tags": [],
        "domain": "General",
        "labels": [
            "tool"
        ]
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
                    "items": {
                        "type": "string"
                    },
                    "description": "Optional updated list of tags"
                },
                "links": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "Optional updated list of target link note IDs"
                }
            },
            "required": [
                "note_id"
            ]
        },
        "tier": 0,
        "source": "",
        "permission": "write",
        "fallback_only": false,
        "concurrency_safe": true,
        "max_result_chars": 50000,
        "tags": [],
        "domain": "General",
        "labels": [
            "tool"
        ]
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
            "required": [
                "note_id"
            ]
        },
        "tier": 0,
        "source": "",
        "permission": "read_only",
        "fallback_only": false,
        "concurrency_safe": true,
        "max_result_chars": 50000,
        "tags": [],
        "domain": "General",
        "labels": [
            "tool"
        ]
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
            "required": [
                "query"
            ]
        },
        "tier": 0,
        "source": "",
        "permission": "read_only",
        "fallback_only": false,
        "concurrency_safe": true,
        "max_result_chars": 50000,
        "tags": [],
        "domain": "General",
        "labels": [
            "tool"
        ]
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
            "required": [
                "source_note_id",
                "target_note_id"
            ]
        },
        "tier": 0,
        "source": "",
        "permission": "write",
        "fallback_only": false,
        "concurrency_safe": true,
        "max_result_chars": 50000,
        "tags": [],
        "domain": "General",
        "labels": [
            "tool"
        ]
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
                    "enum": [
                        "append",
                        "prepend",
                        "insert_before",
                        "insert_after",
                        "replace"
                    ],
                    "description": "The DOM operation to perform relative to the matched element"
                },
                "html_snippet": {
                    "type": "string",
                    "description": "The raw HTML string to insert or replace with"
                }
            },
            "required": [
                "note_id",
                "css_selector",
                "action",
                "html_snippet"
            ]
        },
        "tier": 0,
        "source": "",
        "permission": "write",
        "fallback_only": false,
        "concurrency_safe": true,
        "max_result_chars": 50000,
        "tags": [],
        "domain": "General",
        "labels": [
            "tool"
        ]
    }
]
"""
HTML_NOTES_TOOLS = json.loads(HTML_NOTES_TOOLS_JSON)
