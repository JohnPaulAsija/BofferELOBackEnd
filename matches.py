import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache
from pydantic import BaseModel
from supabase import AsyncClient
from initialize import get_supabase
from helpers import resolve_user_profile, ROLE_MAP
from postgrest.exceptions import APIError
from rate_limit import limiter
from models import RecentMatchesResponse, MatchResponse, BulkMatchActionRequest, BulkMatchActionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/matches")


class ReportMatchRequest(BaseModel):
    winner_id: str
    loser_id: str
    rule_set_id: uuid.UUID


@router.get("", response_model=RecentMatchesResponse)
@cache(expire=60, namespace="matches")
async def get_recent_matches(supabase: AsyncClient = Depends(get_supabase)):
    result = (
        await supabase.from_("Matches")
        .select("id, winnerId, winnerName, loserId, loserName, winnerEloBefore, loserEloBefore, eloChange, confirmedAt, ruleSetId")
        .not_.is_("confirmedAt", "null")
        .order("confirmedAt", desc=True)
        .limit(100)  # intentionally capped — pagination not supported yet
        .execute()
    )
    return {"matches": result.data}


@router.get("/{match_id}", response_model=MatchResponse)
async def get_match(match_id: uuid.UUID, supabase: AsyncClient = Depends(get_supabase)):
    resp = await supabase.from_("Matches").select("*").eq("id", str(match_id)).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Match not found")
    return {"match": resp.data}


@limiter.limit("10/minute")
@router.post("", status_code=201, response_model=MatchResponse)
async def report_match(request: Request, body: ReportMatchRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    user_id = caller["user_id"]
    role_id = caller["role_id"]
    reporter_name = caller["username"]

    # Validate participants are not the same user
    if body.winner_id == body.loser_id:
        raise HTTPException(status_code=400, detail="winner_id and loser_id must be different users")

    # Permission check
    if role_id < ROLE_MAP["admin"] and user_id not in (body.winner_id, body.loser_id):
        logger.warning("permission denied: user_id=%s attempted to report match without participation", user_id)
        raise HTTPException(status_code=403, detail="Forbidden: you are not a participant in this match")

    # Atomically fetch profiles, calculate ELO delta, and insert match row via DB function.
    # FOR SHARE locks on both profile rows prevent a concurrent confirm from mutating
    # ELOs between our read and insert (eliminates the TOCTOU race condition).
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = await supabase.rpc("report_match", {
            "p_winner_id":     body.winner_id,
            "p_loser_id":      body.loser_id,
            "p_reporter_id":   user_id,
            "p_reporter_name": reporter_name,
            "p_reported_at":   now,
            "p_rule_set_id":   str(body.rule_set_id),
        }).execute()
    except APIError as exc:
        msg = str(exc)
        if "winner_not_found" in msg:
            logger.warning("match report failed: winner not found winner_id=%s reporter_id=%s", body.winner_id, user_id)
            raise HTTPException(status_code=404, detail="Winner profile not found")
        if "loser_not_found" in msg:
            logger.warning("match report failed: loser not found loser_id=%s reporter_id=%s", body.loser_id, user_id)
            raise HTTPException(status_code=404, detail="Loser profile not found")
        if "violates foreign key constraint" in msg and "ruleSetId" in msg:
            raise HTTPException(status_code=422, detail="Invalid rule_set_id: ruleset not found")
        logger.error("report_match RPC failed reporter_id=%s winner_id=%s loser_id=%s: %s", user_id, body.winner_id, body.loser_id, exc)
        raise HTTPException(status_code=500, detail="Match report failed")

    if not result.data:
        logger.error("report_match RPC returned no data reporter_id=%s winner_id=%s loser_id=%s", user_id, body.winner_id, body.loser_id)
        raise HTTPException(status_code=500, detail="Match report failed")
    logger.info("match reported match_id=%s reporter_id=%s winner_id=%s loser_id=%s elo_delta=%s",
                result.data[0]["id"], user_id, body.winner_id, body.loser_id, result.data[0]["eloChange"])
    return {"match": result.data[0]}


@limiter.limit("20/minute")
@router.post("/confirm", response_model=BulkMatchActionResponse)
async def confirm_matches(request: Request, body: BulkMatchActionRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    user_id = caller["user_id"]
    role_id = caller["role_id"]
    confirmer_name = caller["username"]
    now = datetime.now(timezone.utc).isoformat()

    # Batch-fetch all requested matches in one round trip
    match_resp = await supabase.from_("Matches") \
        .select("id, confirmedAt, rejectedAt, winnerId, loserId, reporterId") \
        .in_("id", [str(mid) for mid in body.match_ids]) \
        .execute()
    match_map = {m["id"]: m for m in (match_resp.data or [])}

    results = []
    any_confirmed = False

    for match_id in body.match_ids:
        match_id_str = str(match_id)

        if match_id_str not in match_map:
            logger.warning("bulk confirm: match not found match_id=%s user_id=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match not found"})
            continue

        match = match_map[match_id_str]

        if match["confirmedAt"] is not None:
            logger.warning("bulk confirm: already confirmed match_id=%s user_id=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match is already confirmed"})
            continue

        if match["rejectedAt"] is not None:
            logger.warning("bulk confirm: already rejected match_id=%s user_id=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match has been rejected"})
            continue

        is_participant = user_id == match["winnerId"] or user_id == match["loserId"]
        is_reporter    = user_id == match["reporterId"]
        if role_id < ROLE_MAP["admin"] and not (is_participant and not is_reporter):
            logger.warning("permission denied: user_id=%s attempted to confirm match_id=%s", user_id, match_id_str)
            results.append({"match_id": match_id_str, "status": "error", "error": "Forbidden"})
            continue

        try:
            result = await supabase.rpc("confirm_match_and_update_elo", {
                "p_match_id":          match_id_str,
                "p_confirmed_at":      now,
                "p_confirmed_by_id":   user_id,
                "p_confirmed_by_name": confirmer_name,
            }).execute()
        except APIError as exc:
            logger.error("confirm RPC failed match_id=%s confirmed_by=%s: %s", match_id_str, user_id, exc)
            results.append({"match_id": match_id_str, "status": "error", "error": str(exc)})
            continue

        if not result.data:
            logger.error("confirm RPC returned no data match_id=%s confirmed_by=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Confirm failed"})
            continue

        logger.info("match confirmed match_id=%s confirmed_by=%s winner_id=%s loser_id=%s",
                    match_id_str, user_id, match["winnerId"], match["loserId"])
        any_confirmed = True
        results.append({"match_id": match_id_str, "status": "confirmed", "match": result.data})

    # Invalidate caches once after all confirmations — only if at least one succeeded.
    # Wrapped in try/except so a cache-clear failure doesn't obscure the real results.
    if any_confirmed:
        try:
            await FastAPICache.clear(namespace="leaderboard")
            await FastAPICache.clear(namespace="matches")
        except Exception as exc:
            logger.warning("Cache clear failed after bulk match confirmation: %s", exc, exc_info=True)

    succeeded = sum(1 for r in results if r["status"] != "error")
    logger.info("bulk confirm completed succeeded=%d failed=%d actor=%s", succeeded, len(results) - succeeded, user_id)
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}


@limiter.limit("20/minute")
@router.post("/reject", response_model=BulkMatchActionResponse)
async def reject_matches(request: Request, body: BulkMatchActionRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    user_id = caller["user_id"]
    role_id = caller["role_id"]
    rejecter_name = caller["username"]
    now = datetime.now(timezone.utc).isoformat()

    # Batch-fetch all requested matches in one round trip
    match_resp = await supabase.from_("Matches") \
        .select("id, confirmedAt, rejectedAt, winnerId, loserId, reporterId") \
        .in_("id", [str(mid) for mid in body.match_ids]) \
        .execute()
    match_map = {m["id"]: m for m in (match_resp.data or [])}

    results = []

    for match_id in body.match_ids:
        match_id_str = str(match_id)

        if match_id_str not in match_map:
            logger.warning("bulk reject: match not found match_id=%s user_id=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match not found"})
            continue

        match = match_map[match_id_str]

        if match["confirmedAt"] is not None:
            logger.warning("bulk reject: already confirmed match_id=%s user_id=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match is already confirmed"})
            continue

        if match["rejectedAt"] is not None:
            logger.warning("bulk reject: already rejected match_id=%s user_id=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match is already rejected"})
            continue

        # Permission check — reporter CAN reject (unlike confirm)
        is_participant = user_id == match["winnerId"] or user_id == match["loserId"]
        is_reporter    = user_id == match["reporterId"]
        if role_id < ROLE_MAP["admin"] and not (is_participant or is_reporter):
            logger.warning("permission denied: user_id=%s attempted to reject match_id=%s", user_id, match_id_str)
            results.append({"match_id": match_id_str, "status": "error", "error": "Forbidden"})
            continue

        try:
            result = await supabase.rpc("reject_match", {
                "p_match_id":         match_id_str,
                "p_rejected_at":      now,
                "p_rejected_by_id":   user_id,
                "p_rejected_by_name": rejecter_name,
            }).execute()
        except APIError as exc:
            logger.error("reject RPC failed match_id=%s rejected_by=%s: %s", match_id_str, user_id, exc)
            results.append({"match_id": match_id_str, "status": "error", "error": str(exc)})
            continue

        if not result.data:
            logger.warning("reject RPC returned no data match_id=%s rejected_by=%s", match_id_str, user_id)
            results.append({"match_id": match_id_str, "status": "error", "error": "Match is already rejected or confirmed"})
            continue

        logger.info("match rejected match_id=%s rejected_by=%s", match_id_str, user_id)
        results.append({"match_id": match_id_str, "status": "rejected", "match": result.data[0]})

    succeeded = sum(1 for r in results if r["status"] != "error")
    logger.info("bulk reject completed succeeded=%d failed=%d actor=%s", succeeded, len(results) - succeeded, user_id)
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}
