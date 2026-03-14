import logging
import os
from contextlib import asynccontextmanager
import tomllib
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from initialize import init_client, get_supabase
from supabase import AsyncClient
from fastapi_cache.decorator import cache
from models import RootResponse, HealthResponse, VersionResponse, OptionsResponse
from rate_limit import limiter
from admin import router as admin_router

with open("pyproject.toml", "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]

logger = logging.getLogger(__name__)
from matches import router as matches_router
from users import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.supabase, app.state.http_client = await init_client()
    FastAPICache.init(InMemoryBackend())
    yield
    await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.include_router(admin_router)
app.include_router(matches_router)
app.include_router(users_router)

# Enable CORS
# allow_credentials is False because auth uses Authorization header (JWT), not cookies.
# Extra origins (e.g. Cloud Run URL, production web domain) are loaded from the
# CORS_ORIGINS env var as a comma-separated list, e.g.:
#   CORS_ORIGINS=https://my-service-xyz.run.app,https://my-app.com
_extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",   # Metro bundler
        "http://localhost:19006",  # Expo web
        "http://localhost:8080",   # alternative dev port
        "https://boffer-elo--zmpfbicbsr.expo.app",  # Expo web dev
        "https://boffer-elo.expo.app",
        "https://boffer-elo.firebaseapp.com",
        "https://experimental-philosophy.com",
        "https://boffer-elo.web.app",
        *_extra_origins,
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.get("/", response_model=RootResponse)
def read_root():
    """Simple endpoint that returns a JSON response"""
    return {"message": "Hello from API!"}

@app.get("/health", response_model=HealthResponse)
async def health_check(supabase: AsyncClient = Depends(get_supabase)):
    """Health check — verifies both the server and DB connection are alive."""
    try:
        await supabase.from_("profiles").select("id").limit(1).execute()
        return {"status": "ok", "db": "ok"}
    except Exception:
        logger.error("health check: DB unreachable", exc_info=True)
        raise HTTPException(status_code=503, detail={"status": "error", "db": "unreachable"})

@app.get("/version", response_model=VersionResponse)
def get_version():
    """Get the API version from pyproject.toml."""
    return {"version": VERSION}

@app.get("/options", response_model=OptionsResponse)
@cache(expire=60, namespace="options")
async def get_options(supabase: AsyncClient = Depends(get_supabase)):
    genders   = await supabase.from_("gender_options").select("name").execute()
    games     = await supabase.from_("game_types").select("name").execute()
    weapons   = await supabase.from_("weapon_types").select("name").execute()
    shields   = await supabase.from_("shield_types").select("name").execute()
    rule_sets = await supabase.from_("rule_sets").select("id, name").execute()
    return {
        "genders":   [r["name"] for r in genders.data],
        "games":     [r["name"] for r in games.data],
        "weapons":   [r["name"] for r in weapons.data],
        "shields":   [r["name"] for r in shields.data],
        "rule_sets": [{"id": r["id"], "name": r["name"]} for r in rule_sets.data],
    }
