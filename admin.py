# Admin / seed endpoints — dev and testing use only.
# The seed functions (create_test_users, create_test_matches) are intentionally
# synchronous blocking calls. They are not wrapped in asyncio.to_thread() because
# they are not intended for use in production or under any concurrent load.
import logging
import os
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from supabase import AsyncClient
from initialize import get_supabase
from helpers import resolve_user_profile, ROLE_MAP, DELETED_USER_SENTINEL_ID
from models import PendingMatchesResponse
from seed_data import create_test_users, create_test_matches

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


class SeedUsersRequest(BaseModel):
    n_users: int = Field(default=0, ge=0, le=100)
    n_admins: int = Field(default=0, ge=0, le=100)
    n_super_admins: int = Field(default=0, ge=0, le=100)


class SeedMatchesRequest(BaseModel):
    n: int = Field(default=1, ge=0, le=100)
    confirmed: bool = False


async def _require_super_admin(authorization: str, supabase: AsyncClient) -> dict:
    caller = await resolve_user_profile(authorization, supabase)
    if caller["role_id"] < ROLE_MAP["superAdmin"]:
        logger.warning("permission denied: user_id=%s role_id=%s required superAdmin", caller["user_id"], caller["role_id"])
        raise HTTPException(status_code=403, detail="Forbidden: superAdmin access required")
    return caller


async def _require_admin(authorization: str, supabase: AsyncClient) -> dict:
    caller = await resolve_user_profile(authorization, supabase)
    if caller["role_id"] < ROLE_MAP["admin"]:
        logger.warning("permission denied: user_id=%s role_id=%s required admin", caller["user_id"], caller["role_id"])
        raise HTTPException(status_code=403, detail="Forbidden: admin access required")
    return caller


_PENDING_MATCH_COLUMNS = "id, winnerId, winnerName, loserId, loserName, winnerEloBefore, loserEloBefore, eloChange, reporterId, reporterName, reportedAt, confirmedAt, ruleSetId"


@router.get("/matches/pending", response_model=PendingMatchesResponse)
async def get_pending_matches(
    authorization: str = Header(...),
    supabase: AsyncClient = Depends(get_supabase),
    limit: int = Query(default=50, ge=1, le=100),
    before: str = Query(default=None),
):
    caller = await _require_admin(authorization, supabase)

    query = (
        supabase.from_("Matches")
        .select(_PENDING_MATCH_COLUMNS)
        .is_("confirmedAt", "null")
        .is_("rejectedAt", "null")
        .order("reportedAt", desc=True)
        .limit(limit + 1)
    )
    if before:
        query = query.lt("reportedAt", before)

    result = await query.execute()
    matches = result.data

    if len(matches) > limit:
        matches = matches[:limit]
        next_cursor = matches[-1]["reportedAt"]
    else:
        next_cursor = None

    logger.info("admin pending matches: count=%d actor_id=%s", len(matches), caller["user_id"])
    return {"pending_matches": matches, "next_cursor": next_cursor}


@router.post("/seed/users")
async def seed_users(body: SeedUsersRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await _require_super_admin(authorization, supabase)
    created = create_test_users(n_users=body.n_users, n_admins=body.n_admins, n_super_admins=body.n_super_admins)
    logger.info("admin seed: users created=%s actor_id=%s", created, caller["user_id"])
    return {"created": created}


@router.post("/seed/matches")
async def seed_matches(body: SeedMatchesRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await _require_super_admin(authorization, supabase)
    created = create_test_matches(n=body.n, confirmed=body.confirmed)
    logger.info("admin seed: matches created=%s actor_id=%s", created, caller["user_id"])
    return {"created": created}


@router.post("/reset")
async def reset_data(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    await _require_super_admin(authorization, supabase)

    # Delete all matches first — Matches rows may reference profiles via FK;
    # clearing them before the auth-user cascade avoids constraint violations.
    await supabase.from_("Matches").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

    # Delete every auth user except the bootstrap superAdmin.
    # Deleting an auth user cascades to the profiles row via the DB trigger.
    bootstrap_email = os.environ["SUPER_ADMIN_EMAIL"].lower()
    users_resp = await supabase.auth.admin.list_users()
    for u in users_resp:
        if (u.email or "").lower() != bootstrap_email and u.id != DELETED_USER_SENTINEL_ID:
            await supabase.auth.admin.delete_user(u.id)
            logger.info("admin reset: deleted user email=%s", u.email)

    logger.info("admin reset: complete")
    return {"reset": True}


@router.delete("/matches/{match_id}")
async def delete_match(match_id: str, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await _require_super_admin(authorization, supabase)
    match_resp = await supabase.from_("Matches").select("id").eq("id", match_id).single().execute()
    if not match_resp.data:
        raise HTTPException(status_code=404, detail="Match not found")
    await supabase.from_("Matches").delete().eq("id", match_id).execute()
    logger.info("admin delete: match_id=%s actor_id=%s", match_id, caller["user_id"])
    return {"deleted": match_id}
