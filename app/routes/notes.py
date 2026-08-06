from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

router = APIRouter()

@router.post("/notes/create")
async def api_create_note(req: CreateNoteRequest):
    import uuid
    from app.agents.auditor import audit_html_fragment
    
    # Audit before manual creation
    audit_res = audit_html_fragment(req.rendered_html)
    if not audit_res["is_valid"]:
        raise HTTPException(
            status_code=400,
            detail=f"HTML content failed security audit: {', '.join(audit_res['errors'])}"
        )
        
    try:
        note_id = f"note_{uuid.uuid4().hex[:8]}"
        note = database.create_note(
            note_id=note_id,
            title=req.title,
            tags=req.tags,
            links=req.links,
            source_messages=["api-manual-create"],
            canonical_blocks=req.canonical_blocks,
            rendered_html=req.rendered_html
        )
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notes/update")
async def api_update_note(req: UpdateNoteRequest):
    if req.rendered_html is not None:
        from app.agents.auditor import audit_html_fragment
        audit_res = audit_html_fragment(req.rendered_html)
        if not audit_res["is_valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"HTML content failed security audit: {', '.join(audit_res['errors'])}"
            )
            
    try:
        note = database.update_note(
            note_id=req.note_id,
            title=req.title,
            tags=req.tags,
            links=req.links,
            canonical_blocks=req.canonical_blocks,
            rendered_html=req.rendered_html,
            source_message="api-manual-update"
        )
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notes/link")
async def api_link_notes(req: LinkNotesRequest):
    try:
        note_a = database.get_note_by_id(req.source_note_id)
        note_b = database.get_note_by_id(req.target_note_id)
        if not note_a or not note_b:
            raise HTTPException(status_code=404, detail="One or both notes not found")
            
        links = note_a.get("links", [])
        if req.target_note_id not in links:
            links.append(req.target_note_id)
            database.update_note(note_id=req.source_note_id, links=links)
            
        return {"status": "success", "detail": f"Linked {req.source_note_id} to {req.target_note_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/notes/{id}")
async def get_note(id: str):
    note = database.get_note_by_id(id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    history = database.get_note_history(id)
    return {"note": note, "history": history}


@router.post("/api/notes/save")
async def api_notes_save(req: SaveNoteRequest):
    """Write a note to the Obsidian vault as `<slug>.md` with YAML frontmatter.
    Upsert: an existing file's `created` is preserved; `updated` is bumped."""
    slug = _note_slug(req.slug or req.title)
    path = _note_path(slug)
    if path is None:
        raise HTTPException(status_code=400, detail="invalid note name")
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    created = now
    if path.exists():
        try:
            prev = _parse_frontmatter(path.read_text(encoding="utf-8"))
            created = prev.get("created") or now
        except Exception:
            pass
    meta = {"title": req.title or "Untitled", "tags": req.tags or [],
            "created": created, "updated": now}
    try:
        path.write_text(_yaml_frontmatter(meta) + (req.content or ""), encoding="utf-8")
    except Exception as e:
        logger.error(f"note save failed ({slug}): {e}")
        raise HTTPException(status_code=500, detail="could not write note")
    logger.info(f"[VAULT] saved note {path.name} ({len(req.content or '')} chars)")
    return {"ok": True, "slug": slug, "updated": now, "created": created,
            "file": path.name}


@router.get("/api/notes/list")
async def api_notes_list():
    """Every note in the vault: slug + title + tags + updated, newest first."""
    vault = pathlib.Path(OBSIDIAN_VAULT_DIR)
    out = []
    try:
        for p in vault.glob("*.md"):
            try:
                fm = _parse_frontmatter(p.read_text(encoding="utf-8"))
            except Exception:
                fm = {"title": p.stem, "tags": []}
            out.append({"slug": p.stem, "title": fm.get("title") or p.stem,
                        "tags": fm.get("tags") or [],
                        "updated": datetime.datetime.utcfromtimestamp(
                            p.stat().st_mtime).replace(microsecond=0).isoformat()})
    except Exception as e:
        logger.warning(f"note list failed: {e}")
    out.sort(key=lambda n: n["updated"], reverse=True)
    return {"notes": out, "vault": str(vault)}


@router.get("/api/notes/load")
async def api_notes_load(slug: str):
    """Load one note's body + metadata (for reopening a saved note)."""
    path = _note_path(slug)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="note not found")
    fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return {"slug": _note_slug(slug), "title": fm.get("title") or slug,
            "tags": fm.get("tags") or [], "content": fm.get("body", ""),
            "created": fm.get("created", "")}


