from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AuthenticatedKey(BaseModel):
    """Identifies the caller of an SDK/ingest request, resolved from the
    Authorization header (API key) by routers.auth.get_current_api_key."""

    api_key_id: UUID
    user_id: UUID


class AuthenticatedUser(BaseModel):
    """Identifies the caller of a dashboard request, resolved from a
    Supabase Auth JWT by routers.auth.get_current_user."""

    user_id: UUID
    email: str | None = None


class KeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class KeyInfo(BaseModel):
    id: UUID
    key_prefix: str
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    is_active: bool


class KeyCreateResponse(KeyInfo):
    key: str  # full key — shown exactly once, never retrievable again
