from __future__ import annotations
import uuid
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared / root
# ---------------------------------------------------------------------------

class RootResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    db: str


class VersionResponse(BaseModel):
    version: str


# ---------------------------------------------------------------------------
# User / profile models
# ---------------------------------------------------------------------------

class UserSummary(BaseModel):
    """A minimal user record — id + username only. Used in the /users list."""
    id: str
    username: str


class LeaderboardEntry(BaseModel):
    """A player's ranking entry shown on the leaderboard."""
    id: str
    username: str
    elo: int
    wins: int
    losses: int


class RuleSetOption(BaseModel):
    id: str
    name: str


class OptionsResponse(BaseModel):
    genders:   list[str]
    games:     list[str]
    weapons:   list[str]
    shields:   list[str]
    rule_sets: list[RuleSetOption]


class UpdatePreferencesRequest(BaseModel):
    gender:           Optional[str] = None
    preferred_game:   Optional[str] = None
    preferred_weapon: Optional[str] = None
    preferred_shield: Optional[str] = None


class PreferencesResponse(BaseModel):
    gender:           Optional[str] = None
    preferred_game:   Optional[str] = None
    preferred_weapon: Optional[str] = None
    preferred_shield: Optional[str] = None


class ChangeUsernameRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=24, pattern=r'^[a-zA-Z0-9_-]+$')

    @field_validator('username', mode='before')
    @classmethod
    def strip_username(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


class ChangeUsernameResponse(BaseModel):
    username: str


class ChangeEmailRequest(BaseModel):
    email: str  # validated via regex in endpoint


class PublicUserProfile(BaseModel):
    id: str
    username: str
    elo: int
    wins: int
    losses: int
    gender: Optional[str] = None
    preferredGame: Optional[str] = None
    preferredWeapon: Optional[str] = None
    preferredShield: Optional[str] = None


class PublicUserProfileResponse(BaseModel):
    user: PublicUserProfile


class AuthUserInfo(BaseModel):
    """
    The authenticated caller's identity, returned by GET /users/me.

    Combines fields from two sources:
    - id, email  → Supabase auth user (from the JWT)
    - username, role_id → profiles table (DB query)
    """
    id: str
    email: Optional[str] = None
    username: str
    role_id: int


# ---------------------------------------------------------------------------
# Match models
# ---------------------------------------------------------------------------

class RecentMatch(BaseModel):
    """
    A confirmed match as returned by GET /matches.

    This is a lighter shape — only the fields needed for the public
    recent-matches feed. Does not include reporter/rejector fields.
    """
    id: str
    winnerId: str
    winnerName: str
    loserId: str
    loserName: str
    winnerEloBefore: int
    loserEloBefore: int
    eloChange: int
    confirmedAt: datetime
    ruleSetId: Optional[str] = None


class FullMatch(BaseModel):
    """
    A full match record — all fields. Returned by POST /matches,
    POST /matches/{id}/confirm, POST /matches/{id}/reject,
    GET /users/me/matches, and GET /users/me/matches/unconfirmed.
    """
    id: str
    winnerId: str
    winnerName: str
    loserId: str
    loserName: str
    winnerEloBefore: int
    loserEloBefore: int
    eloChange: int
    reportedAt: datetime
    reporterId: str
    reporterName: str
    confirmedAt: Optional[datetime] = None
    confirmedById: Optional[str] = None
    confirmedByName: Optional[str] = None
    rejectedAt: Optional[datetime] = None
    rejectedById: Optional[str] = None
    rejectedByName: Optional[str] = None
    ruleSetId: Optional[str] = None


# ---------------------------------------------------------------------------
# Response envelope models
# (Each endpoint wraps its payload in a top-level key, e.g. {"users": [...]})
# ---------------------------------------------------------------------------

class UsersListResponse(BaseModel):
    users: list[UserSummary]


class LeaderboardResponse(BaseModel):
    leaderboard: list[LeaderboardEntry]


class AuthUserResponse(BaseModel):
    user: AuthUserInfo


class UserMatchesResponse(BaseModel):
    confirmed: list[FullMatch]
    unconfirmed: list[FullMatch]


class UnconfirmedMatchesResponse(BaseModel):
    unconfirmed_matches: list[FullMatch]


class UserMatchHistoryResponse(BaseModel):
    matches:     list[RecentMatch]
    next_cursor: Optional[str] = None


class RecentMatchesResponse(BaseModel):
    matches: list[RecentMatch]


class MatchResponse(BaseModel):
    match: FullMatch


class PendingMatch(BaseModel):
    """A pending match row returned by GET /admin/matches/pending."""
    id: str
    winnerId: str
    winnerName: str
    loserId: str
    loserName: str
    winnerEloBefore: int
    loserEloBefore: int
    eloChange: int
    reporterId: str
    reporterName: str
    reportedAt: datetime
    confirmedAt: Optional[datetime] = None
    ruleSetId: Optional[str] = None


class PendingMatchesResponse(BaseModel):
    pending_matches: list[PendingMatch]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# Bulk match action models
# ---------------------------------------------------------------------------

class BulkMatchActionRequest(BaseModel):
    match_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)


class BulkMatchResult(BaseModel):
    match_id: str
    status: str          # "confirmed" | "rejected" | "error"
    match: Optional[FullMatch] = None
    error: Optional[str] = None


class BulkMatchActionResponse(BaseModel):
    results: list[BulkMatchResult]
    succeeded: int
    failed: int
