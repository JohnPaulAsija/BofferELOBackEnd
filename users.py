import logging
import os
import re
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi_cache.decorator import cache
from postgrest.exceptions import APIError
from supabase import AsyncClient
from initialize import get_supabase
import uuid
from helpers import resolve_token, resolve_user_profile, ROLE_MAP, DELETED_USER_SENTINEL_ID
from rate_limit import limiter
from models import (
    UsersListResponse, LeaderboardResponse, AuthUserResponse,
    UserMatchesResponse, UnconfirmedMatchesResponse,
    UpdatePreferencesRequest, PreferencesResponse,
    UserMatchHistoryResponse, PublicUserProfileResponse,
    ChangeUsernameRequest, ChangeUsernameResponse, ChangeEmailRequest,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


async def _reassign_matches_to_sentinel(supabase: AsyncClient, user_id: str):
    """Reassign all match FK columns referencing user_id to the deleted-user sentinel.

    Called before deleting an auth user so that match history is preserved.
    GoTrue's admin delete_user does not reliably cascade through the
    profiles FK in a way that fires row-level triggers.
    """
    sentinel = DELETED_USER_SENTINEL_ID
    for col in ("winnerId", "loserId", "reporterId", "confirmedById", "rejectedById"):
        await supabase.from_("Matches").update({col: sentinel}).eq(col, user_id).execute()


router = APIRouter(prefix="/users")

MATCH_COLUMNS = "id, winnerId, winnerName, loserId, loserName, winnerEloBefore, loserEloBefore, eloChange, confirmedAt, confirmedById, confirmedByName, reportedAt, reporterId, reporterName, rejectedAt, rejectedById, rejectedByName, ruleSetId"


@router.get("", response_model=UsersListResponse)
async def list_users(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = user.user.id
    result = (
        await supabase.from_("profiles")
        .select("id, username")
        .neq("id", user_id)
        .not_.is_("username", "null")
        .order("username", desc=False)
        .execute()
    )
    return {"users": result.data}


@router.get("/top", response_model=LeaderboardResponse)
@cache(expire=60, namespace="leaderboard")
async def get_leaderboard(supabase: AsyncClient = Depends(get_supabase)):
    result = (
        await supabase.from_("profiles")
        .select("id, username, elo, wins, losses")
        .not_.is_("username", "null")
        .neq("id", DELETED_USER_SENTINEL_ID)
        .order("elo", desc=True)
        .limit(100)  # intentionally capped — pagination not supported yet
        .execute()
    )
    return {"leaderboard": result.data}


@router.get("/me", response_model=AuthUserResponse)
async def retrieve_user(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    auth_user = await resolve_token(authorization, supabase)
    user_id = auth_user.user.id
    profile_resp = (
        await supabase.from_("profiles")
        .select("username, role_id")
        .eq("id", user_id)
        .single()
        .execute()
    )
    if not profile_resp.data:
        logger.warning("user profile not found user_id=%s", user_id)
        raise HTTPException(status_code=404, detail="User profile not found")
    return {
        "user": {
            "id": user_id,
            "email": auth_user.user.email,
            "username": profile_resp.data["username"],
            "role_id": profile_resp.data["role_id"],
        }
    }


@router.get("/me/matches", response_model=UserMatchesResponse)
async def get_matches(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = str(user.user.id)
    result = await supabase.rpc("get_user_matches", {"p_user_id": user_id}).execute()
    # supabase-py may return the JSONB result as a dict (unwrapped) or as
    # [{"get_user_matches": {...}}] depending on the PostgREST version.
    raw = result.data
    if isinstance(raw, list):
        data = raw[0].get("get_user_matches", raw[0]) if raw else {"confirmed": [], "unconfirmed": []}
    elif isinstance(raw, dict):
        data = raw
    else:
        data = {"confirmed": [], "unconfirmed": []}
    return {"confirmed": data.get("confirmed", []), "unconfirmed": data.get("unconfirmed", [])}


@router.get("/me/matches/unconfirmed", response_model=UnconfirmedMatchesResponse)
async def get_unconfirmed_matches(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = user.user.id
    matches = (
        await supabase.from_("Matches")
        .select(MATCH_COLUMNS)
        .is_("confirmedAt", "null")
        .is_("rejectedAt", "null")
        .or_(f"winnerId.eq.{user_id},loserId.eq.{user_id}")
        .execute()
    )
    return {"unconfirmed_matches": matches.data}


async def _validate_option(supabase: AsyncClient, table: str, value: str, field: str):
    result = await supabase.from_(table).select("name").eq("name", value).execute()
    if not result.data:
        raise HTTPException(status_code=422, detail=f"Invalid value for {field}: '{value}'")


async def _apply_preferences(
    supabase: AsyncClient,
    target_user_id: str,
    body: UpdatePreferencesRequest,
) -> PreferencesResponse:
    if body.gender is not None:
        await _validate_option(supabase, "gender_options", body.gender, "gender")
    if body.preferred_game is not None:
        await _validate_option(supabase, "game_types", body.preferred_game, "preferred_game")
    if body.preferred_weapon is not None:
        await _validate_option(supabase, "weapon_types", body.preferred_weapon, "preferred_weapon")
    if body.preferred_shield is not None:
        await _validate_option(supabase, "shield_types", body.preferred_shield, "preferred_shield")

    await supabase.from_("profiles").update({
        "gender":          body.gender,
        "preferredGame":   body.preferred_game,
        "preferredWeapon": body.preferred_weapon,
        "preferredShield": body.preferred_shield,
    }).eq("id", target_user_id).execute()

    result = await supabase.from_("profiles") \
        .select("gender, preferredGame, preferredWeapon, preferredShield") \
        .eq("id", target_user_id).single().execute()
    row = result.data
    return PreferencesResponse(
        gender=row["gender"],
        preferred_game=row["preferredGame"],
        preferred_weapon=row["preferredWeapon"],
        preferred_shield=row["preferredShield"],
    )


@router.patch("/me/preferences", response_model=PreferencesResponse)
async def update_my_preferences(
    body: UpdatePreferencesRequest,
    authorization: str = Header(...),
    supabase: AsyncClient = Depends(get_supabase),
):
    user = await resolve_token(authorization, supabase)
    return await _apply_preferences(supabase, user.user.id, body)


@router.patch("/me/username", response_model=ChangeUsernameResponse)
@limiter.limit("5/minute")
async def change_my_username(request: Request, body: ChangeUsernameRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = user.user.id
    try:
        await supabase.from_("profiles").update({"username": body.username}).eq("id", user_id).execute()
    except APIError:
        raise HTTPException(status_code=409, detail="Username already taken")
    return {"username": body.username}


@router.patch("/me/email", status_code=200)
@limiter.limit("5/minute")
async def change_my_email(request: Request, body: ChangeEmailRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = str(user.user.id)
    if not _is_valid_email(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    await supabase.auth.admin.update_user_by_id(user_id, {"email": body.email, "email_confirm": False})
    return {"message": f"Confirmation email sent to {body.email}"}


@router.delete("/me", status_code=200)
@limiter.limit("5/minute")
async def delete_own_account(request: Request, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = str(user.user.id)
    await _reassign_matches_to_sentinel(supabase, user_id)
    await supabase.auth.admin.delete_user(user_id)
    logger.info("account deleted: user_id=%s (self)", user_id)
    return {"deleted": user_id}


@router.patch("/{user_id}/preferences", response_model=PreferencesResponse)
async def update_user_preferences(
    user_id: uuid.UUID,
    body: UpdatePreferencesRequest,
    authorization: str = Header(...),
    supabase: AsyncClient = Depends(get_supabase),
):
    caller = await resolve_user_profile(authorization, supabase)
    if caller["role_id"] < ROLE_MAP["superAdmin"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    target = await supabase.from_("profiles").select("id").eq("id", str(user_id)).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="User not found")

    return await _apply_preferences(supabase, str(user_id), body)


@router.patch("/{user_id}/username", response_model=ChangeUsernameResponse)
async def change_user_username(user_id: uuid.UUID, body: ChangeUsernameRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    if caller["role_id"] < ROLE_MAP["superAdmin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    target = await supabase.from_("profiles").select("id").eq("id", str(user_id)).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        await supabase.from_("profiles").update({"username": body.username}).eq("id", str(user_id)).execute()
    except APIError:
        raise HTTPException(status_code=409, detail="Username already taken")
    logger.info("username changed: user_id=%s new=%s actor_id=%s", user_id, body.username, caller["user_id"])
    return {"username": body.username}


@router.patch("/{user_id}/email", status_code=200)
async def change_user_email(user_id: uuid.UUID, body: ChangeEmailRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    if caller["role_id"] < ROLE_MAP["superAdmin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not _is_valid_email(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    target_id = str(user_id)
    try:
        await supabase.auth.admin.update_user_by_id(target_id, {"email": body.email, "email_confirm": True})
    except Exception:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("email changed: user_id=%s actor_id=%s", target_id, caller["user_id"])
    return {"email": body.email}


@router.delete("/{user_id}", status_code=200)
async def delete_user(user_id: uuid.UUID, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    if caller["role_id"] < ROLE_MAP["superAdmin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    target_id = str(user_id)
    if target_id == DELETED_USER_SENTINEL_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the system sentinel user")
    bootstrap_email = os.environ.get("SUPER_ADMIN_EMAIL", "").lower()
    try:
        target_user = await supabase.auth.admin.get_user_by_id(target_id)
    except Exception:
        raise HTTPException(status_code=404, detail="User not found")
    if (target_user.user.email or "").lower() == bootstrap_email:
        raise HTTPException(status_code=400, detail="Cannot delete the bootstrap superAdmin")
    await _reassign_matches_to_sentinel(supabase, target_id)
    await supabase.auth.admin.delete_user(target_id)
    logger.info("account deleted: user_id=%s actor_id=%s", target_id, caller["user_id"])
    return {"deleted": target_id}


@router.get("/{user_id}", response_model=PublicUserProfileResponse)
async def get_user_profile(user_id: uuid.UUID, supabase: AsyncClient = Depends(get_supabase)):
    try:
        resp = (
            await supabase.from_("profiles")
            .select("id, username, elo, wins, losses, gender, preferredGame, preferredWeapon, preferredShield")
            .eq("id", str(user_id))
            .single()
            .execute()
        )
    except APIError:
        raise HTTPException(status_code=404, detail="User not found")
    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": resp.data}


@router.get("/{user_id}/matches", response_model=UserMatchHistoryResponse)
async def get_user_match_history(
    user_id: uuid.UUID,
    supabase: AsyncClient = Depends(get_supabase),
    limit: int = Query(default=20, ge=1, le=100),
    before: str = Query(default=None),
):
    try:
        profile_resp = await supabase.from_("profiles").select("id").eq("id", str(user_id)).single().execute()
    except APIError:
        raise HTTPException(status_code=404, detail="User not found")
    if not profile_resp.data:
        raise HTTPException(status_code=404, detail="User not found")

    HISTORY_COLUMNS = "id, winnerId, winnerName, loserId, loserName, winnerEloBefore, loserEloBefore, eloChange, confirmedAt"
    query = (
        supabase.from_("Matches")
        .select(HISTORY_COLUMNS)
        .not_.is_("confirmedAt", "null")
        .or_(f"winnerId.eq.{user_id},loserId.eq.{user_id}")
        .order("confirmedAt", desc=True)
        .limit(limit + 1)
    )
    if before:
        query = query.lt("confirmedAt", before)

    result = await query.execute()
    matches = result.data

    if len(matches) > limit:
        matches = matches[:limit]
        next_cursor = matches[-1]["confirmedAt"]
    else:
        next_cursor = None

    return {"matches": matches, "next_cursor": next_cursor}
