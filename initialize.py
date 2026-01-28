import supabase
import os
from dotenv import load_dotenv

load_dotenv()

def create_client() -> supabase.Client:
    api_url = os.getenv("API_URL", "default_api_url")
    api_key_s = os.getenv("API_KEY_s", "default_api_key")
    api_key_p = os.getenv("API_KEY_p", "default_api_key")
    print(f"API URL: {api_url}")
    print(f"API Key s: {api_key_s}")
    print(f"API Key p: {api_key_p}")
    client = supabase.create_client(api_url, api_key_s)
    return client
