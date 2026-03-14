import httpx
import supabase
import os
from dotenv import load_dotenv
from fastapi import Request
from supabase import acreate_client, AsyncClient, AsyncClientOptions

load_dotenv()

# --- Sync client (used by seed_data.py and admin.py) ---

def create_client() -> supabase.Client:
    api_url = os.environ["API_URL"]
    api_key_s = os.environ["API_KEY_s"]
    client = supabase.create_client(api_url, api_key_s)
    return client

client = create_client()

# --- Async client (used by all other endpoints) ---
# Created by init_client() during FastAPI lifespan startup,
# stored on app.state.supabase, and injected via get_supabase().

async def init_client() -> tuple[AsyncClient, httpx.AsyncClient]:
    url = os.environ["API_URL"]
    key = os.environ["API_KEY_s"]
    http_client = httpx.AsyncClient()
    client = await acreate_client(url, key, options=AsyncClientOptions(httpx_client=http_client))
    return client, http_client


async def get_supabase(request: Request) -> AsyncClient:
    return request.app.state.supabase
