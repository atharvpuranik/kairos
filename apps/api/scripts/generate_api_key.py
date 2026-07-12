"""Dev helper: mints a Kairos API key for local testing.

The real `POST /v1/keys` dashboard endpoint isn't built until later — this
script exists so Week 1 can be tested end to end without it. Requires a
profiles row to already exist (created automatically by Supabase Auth on
signup) and a pipelines row owned by that user, since /v1/traces checks
pipeline ownership.

Usage:
    poetry run python scripts/generate_api_key.py <user_id> "My test key"
"""

import hashlib
import secrets
import sys

from supabase import create_client

sys.path.insert(0, ".")
from config import get_settings  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: generate_api_key.py <user_id> ["key name"]', file=sys.stderr)
        raise SystemExit(1)

    user_id = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Local dev key"

    raw_key = f"kai_live_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    key_prefix = raw_key[:12]

    settings = get_settings()
    supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)

    supabase.table("api_keys").insert(
        {
            "user_id": user_id,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "name": name,
        }
    ).execute()

    print("API key created. Save it now — it will not be shown again:")
    print(raw_key)


if __name__ == "__main__":
    main()
