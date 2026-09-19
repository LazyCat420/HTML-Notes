from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

from app.domain.notes.service import notes_service

router = APIRouter()

@router.post("/notes/create")
async def api_create_note(req: CreateNoteRequest):
    if not req.session_id or not req.session_id.strip():
        raise HTTPException(status_code=401, detail="session_id is required to create a note")
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
            rendered_html=req.rendered_html,
            session_id=req.session_id
        )
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notes/update")
async def api_update_note(req: UpdateNoteRequest):
    if not req.session_id:
        raise HTTPException(status_code=401, detail="Unauthorized: session_id is required to update a note")

    res = notes_service.update_note(
        note_id=req.note_id,
        session_id=req.session_id,
        title=req.title,
        tags=req.tags,
        links=req.links,
        canonical_blocks=req.canonical_blocks,
        rendered_html=req.rendered_html,
        source_message="api-manual-update"
    )
    if res.get("is_error"):
        code = res.get("code")
        if code in ("NOTE_UNCLAIMED", "NOTE_SESSION_MISMATCH"):
            raise HTTPException(status_code=403, detail=res.get("error"))
        if code == "SESSION_REQUIRED":
            raise HTTPException(status_code=401, detail=res.get("error"))
        if "not found" in res.get("error", "").lower():
            raise HTTPException(status_code=404, detail=res.get("error"))
        raise HTTPException(status_code=400, detail=res.get("error"))

    note = database.get_note_by_id(req.note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.post("/notes/link")
async def api_link_notes(req: LinkNotesRequest):
    if not req.session_id:
        raise HTTPException(status_code=401, detail="Unauthorized: session_id is required to link notes")

    res = notes_service.link_notes(
        source_note_id=req.source_note_id,
        target_note_id=req.target_note_id,
        session_id=req.session_id
    )
    if res.get("is_error"):
        code = res.get("code")
        if code in ("NOTE_UNCLAIMED", "NOTE_SESSION_MISMATCH"):
            raise HTTPException(status_code=403, detail=res.get("error"))
        if code == "SESSION_REQUIRED":
            raise HTTPException(status_code=401, detail=res.get("error"))
        if "not found" in res.get("error", "").lower():
            raise HTTPException(status_code=404, detail=res.get("error"))
        raise HTTPException(status_code=400, detail=res.get("error"))

    return {"status": "success", "detail": f"Linked {req.source_note_id} to {req.target_note_id}"}


@router.post("/notes/claim")
async def api_claim_note(req: ClaimNoteRequest):
    if not req.session_id:
        raise HTTPException(status_code=401, detail="Unauthorized: session_id is required to claim a note")

    res = notes_service.claim_note(
        note_id=req.note_id,
        session_id=req.session_id,
        owner_id=req.owner_id
    )
    if res.get("is_error"):
        code = res.get("code")
        if code == "NOTE_ALREADY_CLAIMED":
            raise HTTPException(status_code=409, detail=res.get("error"))
        if code == "SESSION_REQUIRED":
            raise HTTPException(status_code=401, detail=res.get("error"))
        if "not found" in res.get("error", "").lower():
            raise HTTPException(status_code=404, detail=res.get("error"))
        raise HTTPException(status_code=400, detail=res.get("error"))

    return res


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


