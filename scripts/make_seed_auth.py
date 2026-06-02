#!/usr/bin/env python3
"""Deterministically generate seed/.storage/auth with a BAKED, PUBLIC long-lived
access token, and emit the matching token string.

The harness HA is a disposable fake container. Its admin token controls nothing
real, so we commit a fixed, well-known token on purpose (see README "Token").
Re-running this script reproduces byte-identical auth + the same token.

The HA long-lived access token is an HS256 JWT:
    base64url(header) . base64url(payload) . base64url(HMAC-SHA256(jwt_key, signing_input))
where payload = {"iss": <refresh_token_id>, "iat": <past>, "exp": <far future>}
and the key is the refresh token's jwt_key (utf-8 bytes). This matches HA's
homeassistant.auth.jwt_wrapper exactly (verified against a live 2026.5.4 HA).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

SEED_STORAGE = Path(__file__).resolve().parent.parent / "seed" / ".storage"

# --- deterministic, clearly-synthetic identifiers (NOT secret) -----------------
def _det_hex(label: str, nbytes: int) -> str:
    """Stretch a label into nbytes of deterministic hex via SHA-256."""
    out = b""
    counter = 0
    while len(out) < nbytes:
        out += hashlib.sha256(f"{label}/{counter}".encode()).digest()
        counter += 1
    return out[:nbytes].hex()


# Live, fixed identities lifted from the reference demo (disposable container —
# safe to commit). Keeping these ids stable keeps device/entity registries
# (which reference user_id / config_entry_id) internally consistent.
CONTENT_USER_ID = "64f08e6c96b945da9adb5c6a636dd3db"
OWNER_USER_ID = "9a8e57f292e54cbcbdfd4021c64319c7"
CREDENTIAL_ID = "81dabdec47a94e228ec7ecfcf7215b02"

# System refresh token for the content user (serves /local files). Lifted verbatim.
SYSTEM_RT = {
    "id": "4c7accdd4d4f493daec127c26bf00869",
    "user_id": CONTENT_USER_ID,
    "client_id": None,
    "client_name": None,
    "client_icon": None,
    "token_type": "system",
    "created_at": "2024-06-01T00:00:00.000000+00:00",
    "access_token_expiration": 1800.0,
    "token": "0684d0e5ef1226d31452b7991c66a615e6c686b7982d28dc222cabfb59ffb4756abd75f78dcb52d105363b3d3d286d2a02a9166f76dca1527f194adad012295d",
    "jwt_key": "a8f371086b5bbb075daba75afa14c450da649b6d20dfed9d45a4b1e097d86288785333e3fb5529e3598be5bb8b58f8aeed08474785de0ff278a21faff76be858",
    "last_used_at": None,
    "last_used_ip": None,
    "expire_at": None,
    "credential_id": None,
    "version": "2026.5.4",
}

# BAKED public long-lived access token (the whole point of this file).
LLAT_ID = _det_hex("ha-demo-harness/llat/id/v1", 16)            # 32 hex chars
LLAT_TOKEN = _det_hex("ha-demo-harness/llat/token/v1", 64)      # 128 hex chars
LLAT_JWT_KEY = _det_hex("ha-demo-harness/llat/jwtkey/v1", 64)   # 128 hex chars
IAT = 1717200000          # 2024-06-01T00:00:00Z  (safely in the past)
EXP = 4070908800          # 2099-01-01T00:00:00Z  (effectively never expires)

LLAT_RT = {
    "id": LLAT_ID,
    "user_id": OWNER_USER_ID,
    "client_id": None,
    "client_name": "ha-demo-harness (public token)",
    "client_icon": None,
    "token_type": "long_lived_access_token",
    "created_at": "2024-06-01T00:00:00.000000+00:00",
    "access_token_expiration": float(EXP - IAT),
    "token": LLAT_TOKEN,
    "jwt_key": LLAT_JWT_KEY,
    "last_used_at": None,
    "last_used_ip": None,
    "expire_at": None,
    "credential_id": None,
    "version": "2026.5.4",
}

AUTH = {
    "version": 1,
    "minor_version": 1,
    "key": "auth",
    "data": {
        "users": [
            {
                "id": CONTENT_USER_ID,
                "group_ids": ["system-read-only"],
                "is_owner": False,
                "is_active": True,
                "name": "Home Assistant Content",
                "system_generated": True,
                "local_only": False,
            },
            {
                "id": OWNER_USER_ID,
                "group_ids": ["system-admin"],
                "is_owner": True,
                "is_active": True,
                "name": "Demo User",
                "system_generated": False,
                "local_only": False,
            },
        ],
        "groups": [
            {"id": "system-admin", "name": "Administrators"},
            {"id": "system-users", "name": "Users"},
            {"id": "system-read-only", "name": "Read Only"},
        ],
        "credentials": [
            {
                "id": CREDENTIAL_ID,
                "user_id": OWNER_USER_ID,
                "auth_provider_type": "homeassistant",
                "auth_provider_id": None,
                "data": {"username": "demo"},
            }
        ],
        "refresh_tokens": [SYSTEM_RT, LLAT_RT],
    },
}


def _b64u(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def make_jwt(rt_id: str, jwt_key: str, iat: int, exp: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": rt_id, "iat": iat, "exp": exp}
    seg = (
        _b64u(json.dumps(header, separators=(",", ":")).encode())
        + b"."
        + _b64u(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = hmac.new(jwt_key.encode("utf-8"), seg, hashlib.sha256).digest()
    return (seg + b"." + _b64u(sig)).decode()


def main() -> None:
    token = make_jwt(LLAT_ID, LLAT_JWT_KEY, IAT, EXP)
    SEED_STORAGE.mkdir(parents=True, exist_ok=True)
    (SEED_STORAGE / "auth").write_text(json.dumps(AUTH, indent=4))
    (SEED_STORAGE / ".PUBLIC_TOKEN").write_text(token + "\n")
    print("Wrote", SEED_STORAGE / "auth")
    print("Wrote", SEED_STORAGE / ".PUBLIC_TOKEN")
    print()
    print("PUBLIC LONG-LIVED ACCESS TOKEN (intentionally non-secret):")
    print(token)


if __name__ == "__main__":
    main()
