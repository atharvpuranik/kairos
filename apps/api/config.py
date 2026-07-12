from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_service_role_key: str

    # Upstash Redis
    upstash_redis_rest_url: str
    upstash_redis_rest_token: str

    # Anthropic (eval computation — wired up Week 3)
    anthropic_api_key: str = ""

    # Eval provider — "anthropic" (documented default, ARCHITECTURE.md section 10)
    # or "gemini" (dev/test stand-in while the Anthropic account has no credit).
    eval_provider: str = "anthropic"
    gemini_api_key: str = ""

    # Inngest (background jobs — wired up Week 3)
    inngest_event_key: str = ""
    inngest_signing_key: str = ""

    # App
    app_env: str = "development"
    api_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    # Supabase anon key — used by the dashboard and by RLS verification tests;
    # never used for server-side privileged access
    supabase_anon_key: str = ""

    # Limits / hardening
    rate_limit_per_minute: int = 120            # per API key, ingest routes
    free_tier_traces_per_month: int = 10_000    # per user, enforced at ingest
    eval_max_attempts: int = 3                  # then trace is marked 'failed'
    eval_daily_cap_per_user: int = 2_000        # Haiku spend backstop; over-cap traces are 'skipped'


@lru_cache
def get_settings() -> Settings:
    return Settings()
