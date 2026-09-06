"""Content Quality & Source Reputation API Routes.

Provides endpoints for:
- User item upvoting / downvoting (POST /quality/vote)
- Quality profile inspection (GET /quality/profile)
- Manual source burn / unburn overrides (POST /quality/burn, POST /quality/unburn)
- Profile reset (POST /quality/reset)
"""
from fastapi import APIRouter, Request, HTTPException
from typing import Optional
from pydantic import BaseModel
from app import content_quality

router = APIRouter(prefix="/quality", tags=["quality"])


class VoteRequest(BaseModel):
    url: str
    title: Optional[str] = ""
    publisher: Optional[str] = ""
    vote: int  # +1 or -1


class BurnRequest(BaseModel):
    domain: str
    strikes: Optional[int] = None


class UnburnRequest(BaseModel):
    domain: str


@router.post("/vote")
async def cast_vote(req: VoteRequest):
    """Record an item-level vote (+1 or -1) and update source reputation."""
    if req.vote not in (1, -1):
        raise HTTPException(status_code=400, detail="Vote must be +1 or -1")
    if not req.url and not req.title:
        raise HTTPException(status_code=400, detail="url or title required")

    result = content_quality.record_vote(
        url=req.url,
        title=req.title or "",
        publisher=req.publisher or "",
        vote=req.vote
    )
    return {
        "success": True,
        "vote": req.vote,
        "reputation": result
    }


@router.get("/profile")
async def get_profile():
    """Retrieve the user's learned quality profile, trusted and burned sources."""
    profile = content_quality.get_quality_profile()
    return profile


@router.post("/burn")
async def burn_domain(req: BurnRequest):
    """Manually burn a source domain with parole sentence."""
    domain = (req.domain or "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domain required")
    rep = content_quality.burn_source(domain, strikes=req.strikes)
    return {"success": True, "source": rep}


@router.post("/unburn")
async def unburn_domain(req: UnburnRequest):
    """Remove a domain from active burn sentence."""
    domain = (req.domain or "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domain required")
    rep = content_quality.unburn_source(domain)
    return {"success": True, "source": rep}


@router.post("/reset")
async def reset_profile():
    """Reset all learned source reputations and votes."""
    content_quality.reset_quality_profile()
    return {"success": True, "message": "Quality profile reset successfully"}
