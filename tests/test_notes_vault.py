"""Notes v2 + Obsidian vault: slug safety, frontmatter round-trip, save/list/
load endpoints, and the widget render. The markdown editor / checklist toggling
is client-side (covered by the live E2E)."""
import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")
# Point the vault at a temp dir BEFORE importing main (config reads it at import).
_VAULT = tempfile.mkdtemp()
os.environ["OBSIDIAN_VAULT_DIR"] = _VAULT

from app import main as m
from app.widgets.factory import generate_widget_html

# main may have been imported (with the default vault) by an earlier test in the
# suite, so config's env override wouldn't have taken. Force main's vault dir to
# our temp dir directly — the endpoints read this module global.
m.OBSIDIAN_VAULT_DIR = _VAULT


def test_slug_is_filesystem_safe():
    assert m._note_slug("My Great Note!") == "my-great-note"
    assert m._note_slug("  spaces  and--dashes ") == "spaces-and-dashes"
    assert m._note_slug("") == "note"
    # No slug can contain a path separator or escape the vault.
    for bad in ["../etc/passwd", "a/b/c", "..\\..\\x", "./x"]:
        assert "/" not in m._note_slug(bad) and "\\" not in m._note_slug(bad)


def test_note_path_stays_in_vault():
    import pathlib
    vault = pathlib.Path(_VAULT).resolve()
    for name in ["note", "../../evil", "a/b", "..."]:
        p = m._note_path(name)
        assert p is None or str(p).startswith(str(vault))


def test_frontmatter_round_trips_title_tags_created():
    fm = m._yaml_frontmatter({"title": 'A "quoted" title', "tags": ["work", "ideas"],
                              "created": "2026-07-21T00:00:00+00:00", "updated": "x"})
    assert fm.startswith("---\n") and "source: html-notes" in fm
    parsed = m._parse_frontmatter(fm + "the body")
    assert parsed["title"] == 'A "quoted" title'
    assert parsed["tags"] == ["work", "ideas"]
    assert parsed["created"] == "2026-07-21T00:00:00+00:00"
    assert parsed["body"] == "the body"


def test_parse_frontmatter_tolerates_no_block():
    assert m._parse_frontmatter("just text")["body"] == "just text"


@pytest.mark.asyncio
async def test_save_list_load_and_upsert_preserves_created():
    body = "- [ ] milk\n- [x] eggs\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    r = await m.api_notes_save(m.SaveNoteRequest(title="Grocery Run", content=body, tags=["shopping"]))
    assert r["ok"] and r["slug"] == "grocery-run" and r["file"] == "grocery-run.md"

    lst = await m.api_notes_list()
    row = next(n for n in lst["notes"] if n["slug"] == "grocery-run")
    assert row["title"] == "Grocery Run" and row["tags"] == ["shopping"]

    loaded = await m.api_notes_load(slug="grocery-run")
    assert loaded["content"] == body and loaded["tags"] == ["shopping"]

    # Re-save preserves created, bumps nothing that would lose the body.
    await asyncio.sleep(0.01)
    r2 = await m.api_notes_save(m.SaveNoteRequest(title="Grocery Run", content="new body",
                                                  tags=["shopping", "done"], slug="grocery-run"))
    assert r2["created"] == r["created"]
    reloaded = await m.api_notes_load(slug="grocery-run")
    assert reloaded["content"] == "new body" and set(reloaded["tags"]) == {"shopping", "done"}


@pytest.mark.asyncio
async def test_save_rejects_traversal_and_writes_inside_vault():
    import pathlib
    r = await m.api_notes_save(m.SaveNoteRequest(title="../../escape", content="x"))
    # slug sanitizes to a safe name; the file lands in the vault, not outside.
    p = pathlib.Path(_VAULT) / f"{r['slug']}.md"
    assert p.exists()
    assert pathlib.Path(_VAULT).resolve() in p.resolve().parents


@pytest.mark.asyncio
async def test_load_missing_note_404s():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await m.api_notes_load(slug="does-not-exist-xyz")
    assert ei.value.status_code == 404


def test_notes_widget_renders_editor():
    html = generate_widget_html("notes", "note-1",
                                {"title": "Ideas", "content": "- [ ] one", "tags": ["x"], "slug": ""})
    assert "notesWidget(" in html
    for hook in ["onPreviewClick", "Save to vault", 'x-html="rendered()"', "addTag()", "mode ="]:
        assert hook in html, hook
    assert html.count("{{") == 0
    # The interactive-checklist + markdown logic lives in the component.
    import pathlib
    js = pathlib.Path(m.__file__).parent.joinpath("static/js/widgets.js").read_text()
    for fn in ["toggleTask(", "rendered(", "onPreviewClick(", "DOMPurify.sanitize", "marked.parse"]:
        assert fn in js, fn


def test_notes_widget_legacy_positional_still_works():
    # Old serialized canvases call notesWidget('title','content'); the component
    # shim must accept that. Pin the shim exists in source.
    import pathlib
    js = pathlib.Path(m.__file__).parent.joinpath("static/js/widgets.js").read_text()
    assert "typeof cfgOrTitle === 'object'" in js
