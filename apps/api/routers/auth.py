"""Auth for both caller types, plus API key management.

- SDK/ingest routes authenticate with `Authorization: Bearer kai_live_<key>`
  via get_current_api_key (Redis-cached, Supabase-backed).
- Dashboard routes authenticate with a Supabase Auth JWT via get_current_user.
- /v1/keys endpoints (dashboard-authenticated) create, list, and revoke API
  keys. Revocation deletes the Redis cache entry so it takes effect
  immediately, not after cache TTL.
"""

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db.redis_client import get_redis
from db.supabase import get_supabase
from models.user import (
    AuthenticatedKey,
    AuthenticatedUser,
    KeyCreateRequest,
    KeyCreateResponse,
    KeyInfo,
)

router = APIRouter(prefix="/v1/keys", tags=["keys"])

_bearer_scheme = HTTPBearer(auto_error=False)

_CACHE_TTL_SECONDS = 300
_NEGATIVE_CACHE_TTL_SECONDS = 60
_INVALID_SENTINEL = "invalid"

KEY_PREFIX_LEN = 12


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _cache_key(key_hash: str) -> str:
    return f"apikey:{key_hash}"


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix). The raw key is shown once."""
    raw_key = f"kai_live_{secrets.token_urlsafe(32)}"
    return raw_key, _hash_key(raw_key), raw_key[:KEY_PREFIX_LEN]


async def get_current_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedKey:
    """Validates the `Authorization: Bearer kai_live_<key>` header.

    Cache-first (Upstash Redis) with Supabase api_keys as the source of truth
    on a miss. Invalid keys are negatively cached too, so a bad key doesn't
    hammer Supabase on every retry.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    raw_key = credentials.credentials
    key_hash = _hash_key(raw_key)

    redis = get_redis()
    cached = await redis.get(_cache_key(key_hash))

    if cached == _INVALID_SENTINEL:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    if cached:
        api_key_id, user_id = cached.split(":")
        return AuthenticatedKey(api_key_id=api_key_id, user_id=user_id)

    supabase = await get_supabase()
    result = (
        await supabase.table("api_keys")
        .select("id,user_id,is_active")
        .eq("key_hash", key_hash)
        .limit(1)
        .execute()
    )
    row = result.data[0] if result.data else None

    if row is None or not row["is_active"]:
        await redis.set(_cache_key(key_hash), _INVALID_SENTINEL, ex=_NEGATIVE_CACHE_TTL_SECONDS)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    await redis.set(_cache_key(key_hash), f"{row['id']}:{row['user_id']}", ex=_CACHE_TTL_SECONDS)
    await (
        supabase.table("api_keys")
        .update({"last_used_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", row["id"])
        .execute()
    )

    return AuthenticatedKey(api_key_id=row["id"], user_id=row["user_id"])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Validates a Supabase Auth JWT (dashboard routes)."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    supabase = await get_supabase()
    try:
        result = await supabase.auth.get_user(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if result is None or result.user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return AuthenticatedUser(user_id=result.user.id, email=result.user.email)


# ---------------------------------------------------------------------------
# API key management (dashboard-authenticated)
# ---------------------------------------------------------------------------

_KEY_COLUMNS = "id,key_prefix,name,created_at,last_used_at,is_active"


@router.get("", response_model=list[KeyInfo])
async def list_keys(user: AuthenticatedUser = Depends(get_current_user)) -> list[KeyInfo]:
    supabase = await get_supabase()
    result = (
        await supabase.table("api_keys")
        .select(_KEY_COLUMNS)
        .eq("user_id", str(user.user_id))
        .order("created_at", desc=True)
        .execute()
    )
    return [KeyInfo(**row) for row in result.data]


@router.post("", response_model=KeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    request: KeyCreateRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> KeyCreateResponse:
    raw_key, key_hash, key_prefix = generate_api_key()

    supabase = await get_supabase()
    result = (
        await supabase.table("api_keys")
        .insert(
            {
                "user_id": str(user.user_id),
                "key_hash": key_hash,
                "key_prefix": key_prefix,
                "name": request.name,
            }
        )
        .execute()
    )
    row = result.data[0]
    return KeyCreateResponse(
        id=row["id"],
        key=raw_key,
        key_prefix=row["key_prefix"],
        name=row["name"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        is_active=row["is_active"],
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, user: AuthenticatedUser = Depends(get_current_user)) -> None:
    supabase = await get_supabase()
    result = (
        await supabase.table("api_keys")
        .select("id,key_hash")
        .eq("id", key_id)
        .eq("user_id", str(user.user_id))
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    key_hash = result.data[0]["key_hash"]
    await (
        supabase.table("api_keys")
        .update({"is_active": False})
        .eq("id", key_id)
        .execute()
    )
    # Invalidate the auth cache so revocation is immediate, not after TTL
    await get_redis().delete(_cache_key(key_hash))
