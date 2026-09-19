from fastapi import APIRouter, Request, HTTPException, Response
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)
from app import database
from app.domain.notes.service import notes_service

router = APIRouter()


class ClaimNoteRequest(BaseModel):
    note_id: str
    session_id: Optional[str] = None
    owner_id: Optional[str] = None


def _extract_session_id(request: Request, body_session_id: Optional[str] = None) -> Optional[str]:
    if body_session_id and str(body_session_id).strip():
        return str(body_session_id).strip()
    if request is not None:
        hdr = request.headers.get("x-session-id") or request.headers.get("X-Session-ID")
        if hdr and str(hdr).strip():
            return str(hdr).strip()
        qp = request.query_params.get("session_id")
        if qp and str(qp).strip():
            return str(qp).strip()
    return None


@router.post("/notes/create")
async def api_create_note(req: CreateNoteRequest, request: Request = None):
    session_id = _extract_session_id(request, getattr(req, "session_id", None))
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    res = notes_service.create_note(
        title=req.title,
        rendered_html=req.rendered_html,
        tags=req.tags,
        links=req.links,
        session_id=session_id
    )
    if res.get("is_error"):
        raise HTTPException(status_code=400, detail=res.get("error"))

    note = database.get_note_by_id(res["note_id"])
    if not note:
        raise HTTPException(status_code=500, detail="Failed to retrieve created note")
    return note


@router.post("/notes/update")
async def api_update_note(req: UpdateNoteRequest, request: Request = None):
    session_id = _extract_session_id(request, getattr(req, "session_id", None))
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    res = notes_service.update_note(
        note_id=req.note_id,
        session_id=session_id,
        title=req.title,
        tags=req.tags,
        links=req.links,
        canonical_blocks=req.canonical_blocks,
        rendered_html=req.rendered_html,
        source_message="api-manual-update"
    )
    if res.get("is_error"):
        code = res.get("code")
        if code == "NOTE_NOT_FOUND":
            raise HTTPException(status_code=404, detail=res.get("error"))
        elif code in ("NOTE_SESSION_MISMATCH", "NOTE_UNCLAIMED"):
            raise HTTPException(status_code=403, detail=res.get("error"))
        else:
            raise HTTPException(status_code=400, detail=res.get("error"))

    note = database.get_note_by_id(req.note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.post("/notes/link")
async def api_link_notes(req: LinkNotesRequest, request: Request = None):
    session_id = _extract_session_id(request, getattr(req, "session_id", None))
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    res = notes_service.link_notes(
        source_note_id=req.source_note_id,
        target_note_id=req.target_note_id,
        session_id=session_id
    )
    if res.get("is_error"):
        code = res.get("code")
        if code in ("SOURCE_NOTE_NOT_FOUND", "TARGET_NOTE_NOT_FOUND"):
            raise HTTPException(status_code=404, detail=res.get("error"))
        elif code in ("NOTE_SESSION_MISMATCH", "NOTE_UNCLAIMED"):
            raise HTTPException(status_code=403, detail=res.get("error"))
        else:
            raise HTTPException(status_code=400, detail=res.get("error"))

    return {"status": "success", "detail": f"Linked {req.source_note_id} to {req.target_note_id}"}


@router.post("/notes/claim")
@router.post("/api/notes/claim")
async def api_claim_note(req: ClaimNoteRequest, request: Request = None):
    session_id = _extract_session_id(request, req.session_id)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required to claim a note")

    res = notes_service.claim_note(
        note_id=req.note_id,
        session_id=session_id,
        owner_id=req.owner_id
    )
    if res.get("is_error"):
        code = res.get("code")
        if code == "NOTE_NOT_FOUND":
            raise HTTPException(status_code=404, detail=res.get("error"))
        elif code == "NOTE_ALREADY_CLAIMED":
            raise HTTPException(status_code=409, detail=res.get("error"))
        else:
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


