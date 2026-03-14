"""
Unit tests for rate_limit._user_id_key.

Uses MagicMock to simulate FastAPI Request objects — no real HTTP involved.
"""
import base64
import json
import pytest
from unittest.mock import MagicMock

from rate_limit import _user_id_key


def _make_request(authorization: str = "", client_host: str = "127.0.0.1") -> MagicMock:
    """Build a minimal mock Request with the given Authorization header and IP."""
    request = MagicMock()
    request.headers.get = lambda key, default="": authorization if key == "Authorization" else default
    request.client.host = client_host
    return request


def _make_jwt(sub: str | None = "user-uuid-123") -> str:
    """Construct a minimal (unsigned) JWT string with the given sub claim."""
    payload = {}
    if sub is not None:
        payload["sub"] = sub
    encoded = base64.b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


# ---------------------------------------------------------------------------
# No / invalid Authorization header → fall back to IP
# ---------------------------------------------------------------------------

def test_no_authorization_header_returns_ip():
    request = _make_request(authorization="", client_host="10.0.0.1")
    key = _user_id_key(request)
    assert key == "ip:10.0.0.1"


def test_non_bearer_authorization_returns_ip():
    request = _make_request(authorization="Basic dXNlcjpwYXNz", client_host="10.0.0.2")
    key = _user_id_key(request)
    assert key == "ip:10.0.0.2"


# ---------------------------------------------------------------------------
# Valid JWT with sub → keyed by user ID
# ---------------------------------------------------------------------------

def test_valid_jwt_returns_user_key():
    token = _make_jwt(sub="abc-123")
    request = _make_request(authorization=f"Bearer {token}")
    key = _user_id_key(request)
    assert key == "user:abc-123"


# ---------------------------------------------------------------------------
# Malformed JWT → fall back to IP
# ---------------------------------------------------------------------------

def test_malformed_jwt_not_enough_parts_falls_back_to_ip():
    request = _make_request(authorization="Bearer notajwt", client_host="192.168.1.1")
    key = _user_id_key(request)
    assert key == "ip:192.168.1.1"


def test_malformed_jwt_bad_base64_falls_back_to_ip():
    request = _make_request(authorization="Bearer header.!!!.signature", client_host="192.168.1.2")
    key = _user_id_key(request)
    assert key == "ip:192.168.1.2"


def test_jwt_missing_sub_claim_falls_back_to_ip():
    token = _make_jwt(sub=None)  # payload has no 'sub'
    request = _make_request(authorization=f"Bearer {token}", client_host="192.168.1.3")
    key = _user_id_key(request)
    assert key == "ip:192.168.1.3"
