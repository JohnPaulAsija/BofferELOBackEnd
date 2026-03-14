import logging

from fastapi import HTTPException
from supabase import AsyncClient, AuthApiError

logger = logging.getLogger(__name__)

ROLE_MAP = {
    "user":       1,
    "admin":      2,
    "superAdmin": 3,
}

DELETED_USER_SENTINEL_ID = "00000000-0000-0000-0000-000000000002"


async def resolve_token(authorization: str, supabase: AsyncClient):
    if not authorization.startswith("Bearer "):
        logger.warning("auth failed: missing or invalid Authorization header")
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        return await supabase.auth.get_user(token)
    except AuthApiError:
        logger.warning("auth failed: invalid or expired token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception:
        logger.exception("Unexpected error during token resolution")
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def resolve_user_profile(authorization: str, supabase: AsyncClient) -> dict:
    """
    Resolve JWT and fetch the user's profile.

    Returns dict with keys: user_id, role_id, username.
    Raises 401 for invalid token, 404 for missing profile, 403 for incomplete profile (NULL username — setup not done).
    """
    user = await resolve_token(authorization, supabase)
    user_id = user.user.id
    profile = await supabase.from_("profiles").select("role_id, username").eq("id", user_id).single().execute()
    if not profile.data:
        raise HTTPException(status_code=404, detail="User profile not found")
    if profile.data["username"] is None:
        raise HTTPException(
            status_code=403,
            detail="Profile setup not complete — please complete account setup",
        )
    return {
        "user_id": user_id,
        "role_id": profile.data["role_id"],
        "username": profile.data["username"],
    }
