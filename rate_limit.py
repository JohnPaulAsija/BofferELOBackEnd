import base64
import json

from fastapi import Request
from slowapi import Limiter


def _user_id_key(request: Request) -> str:
    """
    Rate limit key: authenticated user's ID.
    Falls back to IP if the token is absent or cannot be decoded.
    Decodes JWT payload with base64 only — no signature verification.
    Auth is still enforced normally by the endpoint itself.
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return f"ip:{request.client.host}"
    token = authorization.removeprefix("Bearer ")
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        user_id = payload.get("sub")
        if user_id:
            return f"user:{user_id}"
    except Exception:
        pass
    return f"ip:{request.client.host}"


limiter = Limiter(key_func=_user_id_key)
