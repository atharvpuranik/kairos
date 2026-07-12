import asyncio

from supabase import AsyncClient, acreate_client

from config import get_settings

_client: AsyncClient | None = None
_client_lock = asyncio.Lock()


async def get_supabase() -> AsyncClient:
    """Returns a lazily-initialized, process-wide async Supabase client.

    Uses the service role key — server-side only, bypasses RLS. Never expose
    this client or its credentials to the frontend.
    """
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                settings = get_settings()
                _client = await acreate_client(
                    settings.supabase_url, settings.supabase_service_role_key
                )
    return _client
