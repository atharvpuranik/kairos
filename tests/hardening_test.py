"""Hardening verification suite (2026-07 architecture review, 🔴+🟠 tier).

Requires migrations 0002_hardening.sql (+ optionally 0003_cron_jobs.sql) to be
applied, and SUPABASE_ANON_KEY present in apps/api/.env. Everything runs
against the real Supabase + Upstash + live API server processes — the only
mocks are compute_eval() stubs in the worker-policy section, where the point
is precisely that no LLM call should happen.

Covers:
  A. profiles auto-creation trigger on auth signup
  B. Row Level Security — anon denied, owner sees own rows, cross-tenant denied
  C. /v1/keys lifecycle over JWT auth: create -> use -> list -> revoke,
     with revocation taking effect immediately (Redis cache invalidated)
  D. per-key rate limiting on ingest (429)
  E. monthly quota enforcement + batch ingest endpoint
  F. worker eval policy: sampling skip + daily cap skip (no Haiku call made)
  G. RPC correctness: consume_trace_quota atomic metering

Run with the api's own virtualenv, from anywhere:
    apps/api/.venv/bin/python tests/hardening_test.py
"""

from __future__ import annotations

import asyncio
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from dotenv import dotenv_values
from supabase import Client, create_client

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
VENV_PYTHON = API_DIR / ".venv" / "bin" / "python"
sys.path.insert(0, str(API_DIR))

RUN_ID = secrets.token_hex(4)

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not condition:
        line += f" — {detail}"
    print(line)


def load_env() -> dict[str, str]:
    env_path = API_DIR / ".env"
    if not env_path.exists():
        print(f"FATAL: {env_path} does not exist.")
        sys.exit(1)
    values = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    for k, v in values.items():
        os.environ[k] = v
    return values


def start_server(port: int, log_path: Path, env_overrides: dict[str, str]) -> subprocess.Popen:
    env = {**os.environ, **env_overrides}
    log_file = open(log_path, "w")
    return subprocess.Popen(
        [str(VENV_PYTHON), "-m", "uvicorn", "main:app", "--port", str(port)],
        cwd=API_DIR,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        preexec_fn=os.setsid,
    )


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def wait_for_health(base_url: str, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def minimal_trace_body(pipeline_id: str, query: str = "q") -> dict:
    return {
        "pipeline_id": pipeline_id,
        "query": query,
        "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
        "final_answer": "a",
        "latency_ms": 1,
    }


class UserFixture:
    """One test user: auth user (+ trigger-created profile), pipeline, password kept for JWT sign-in."""

    def __init__(self, admin: Client, label: str):
        self.admin = admin
        self.email = f"hardening-{label}-{RUN_ID}@example.com"
        self.password = secrets.token_urlsafe(16)
        result = admin.auth.admin.create_user(
            {"email": self.email, "password": self.password, "email_confirm": True}
        )
        self.user_id = result.user.id
        pipeline = (
            admin.table("pipelines")
            .insert({"user_id": self.user_id, "name": f"hardening-{label}-{RUN_ID}", "framework": "custom"})
            .execute()
        )
        self.pipeline_id = pipeline.data[0]["id"]

    def cleanup(self) -> None:
        try:
            self.admin.table("pipelines").delete().eq("user_id", self.user_id).execute()
            self.admin.table("profiles").delete().eq("id", self.user_id).execute()
            self.admin.auth.admin.delete_user(self.user_id)
        except Exception as exc:
            print(f"WARNING: cleanup incomplete for {self.email}: {exc}")


def section_a_profiles_trigger(admin: Client, user: UserFixture) -> None:
    print("\n== A. profiles auto-creation trigger ==")
    row = admin.table("profiles").select("id,email").eq("id", user.user_id).execute()
    check(
        "profiles row auto-created on auth signup (no manual insert)",
        len(row.data) == 1 and row.data[0]["email"] == user.email,
        str(row.data),
    )


def section_b_rls(admin: Client, env: dict, user1: UserFixture, user2: UserFixture) -> None:
    print("\n== B. Row Level Security ==")
    anon_key = env.get("SUPABASE_ANON_KEY", "")
    if not anon_key:
        check("SUPABASE_ANON_KEY present in .env (required for RLS verification)", False, "missing")
        return

    # seed a trace + api key for user1 via service role (bypasses RLS)
    trace = (
        admin.table("traces")
        .insert(
            {
                "pipeline_id": user1.pipeline_id,
                "user_id": user1.user_id,
                "query": f"rls probe {RUN_ID}",
                "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
                "final_answer": "a",
                "latency_ms": 1,
            }
        )
        .execute()
    )
    trace_id = trace.data[0]["id"]

    anon = create_client(env["SUPABASE_URL"], anon_key)
    anon_traces = anon.table("traces").select("id").execute()
    check("anon client sees zero traces", len(anon_traces.data) == 0, f"saw {len(anon_traces.data)} rows")
    anon_keys = anon.table("api_keys").select("id").execute()
    check("anon client sees zero api_keys", len(anon_keys.data) == 0, f"saw {len(anon_keys.data)} rows")
    anon_pipelines = anon.table("pipelines").select("id").execute()
    check("anon client sees zero pipelines", len(anon_pipelines.data) == 0, f"saw {len(anon_pipelines.data)} rows")

    owner = create_client(env["SUPABASE_URL"], anon_key)
    owner.auth.sign_in_with_password({"email": user1.email, "password": user1.password})
    own_traces = owner.table("traces").select("id").eq("id", trace_id).execute()
    check("owner (JWT) sees own trace under RLS", len(own_traces.data) == 1, str(own_traces.data))
    owner.auth.sign_out()

    other = create_client(env["SUPABASE_URL"], anon_key)
    other.auth.sign_in_with_password({"email": user2.email, "password": user2.password})
    cross_traces = other.table("traces").select("id").eq("id", trace_id).execute()
    check("other user (JWT) cannot see user1's trace", len(cross_traces.data) == 0, str(cross_traces.data))
    cross_pipelines = other.table("pipelines").select("id").eq("id", user1.pipeline_id).execute()
    check("other user (JWT) cannot see user1's pipeline", len(cross_pipelines.data) == 0, str(cross_pipelines.data))
    other.auth.sign_out()


def section_c_keys_lifecycle(base_url: str, env: dict, user: UserFixture) -> str | None:
    print("\n== C. /v1/keys lifecycle (JWT auth) + immediate revocation ==")
    anon = create_client(env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"])
    session = anon.auth.sign_in_with_password({"email": user.email, "password": user.password})
    jwt = session.session.access_token
    jwt_headers = {"Authorization": f"Bearer {jwt}"}

    unauth = httpx.get(f"{base_url}/v1/keys")
    check("GET /v1/keys without token -> 401", unauth.status_code == 401, f"got {unauth.status_code}")

    created = httpx.post(f"{base_url}/v1/keys", json={"name": "hardening key"}, headers=jwt_headers)
    check("POST /v1/keys with JWT -> 201", created.status_code == 201, f"got {created.status_code}: {created.text}")
    if created.status_code != 201:
        return None
    body = created.json()
    raw_key = body["key"]
    key_id = body["id"]
    check("created key uses kai_live_ prefix", raw_key.startswith("kai_live_"), raw_key[:12])
    check("key_prefix matches raw key start", raw_key.startswith(body["key_prefix"]), str(body["key_prefix"]))

    listed = httpx.get(f"{base_url}/v1/keys", headers=jwt_headers)
    listed_ids = [k["id"] for k in listed.json()]
    check("GET /v1/keys lists the new key", key_id in listed_ids, str(listed_ids))
    check("list response never contains raw key or hash", all("key" not in k or k.get("key") is None for k in listed.json()) and "key_hash" not in listed.text, "")

    ingest = httpx.post(
        f"{base_url}/v1/traces",
        json=minimal_trace_body(user.pipeline_id, f"keytest {RUN_ID}"),
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    check("API-created key works for ingest -> 202", ingest.status_code == 202, f"got {ingest.status_code}: {ingest.text}")

    revoked = httpx.delete(f"{base_url}/v1/keys/{key_id}", headers=jwt_headers)
    check("DELETE /v1/keys/{id} -> 204", revoked.status_code == 204, f"got {revoked.status_code}")

    after = httpx.post(
        f"{base_url}/v1/traces",
        json=minimal_trace_body(user.pipeline_id, "post-revoke"),
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    check(
        "revoked key rejected IMMEDIATELY (Redis cache invalidated, no TTL wait)",
        after.status_code == 401,
        f"got {after.status_code}",
    )
    anon.auth.sign_out()
    return raw_key


def section_d_rate_limit(base_url: str, env: dict, user: UserFixture) -> None:
    print("\n== D. Per-key rate limiting (server running with RATE_LIMIT_PER_MINUTE=5) ==")
    anon = create_client(env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"])
    session = anon.auth.sign_in_with_password({"email": user.email, "password": user.password})
    jwt_headers = {"Authorization": f"Bearer {session.session.access_token}"}
    created = httpx.post(f"{base_url}/v1/keys", json={"name": "ratelimit key"}, headers=jwt_headers)
    raw_key = created.json()["key"]
    anon.auth.sign_out()

    statuses = []
    for i in range(14):
        r = httpx.post(
            f"{base_url}/v1/traces",
            json=minimal_trace_body(user.pipeline_id, f"rl {i}"),
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        statuses.append(r.status_code)

    check("some requests accepted before the limit", 202 in statuses, str(statuses))
    check("requests beyond 5/min rejected with 429", 429 in statuses, str(statuses))
    check("no 5xx during rate limiting", all(s < 500 for s in statuses), str(statuses))


def section_e_quota_and_batch(base_url: str, env: dict, quota_user: UserFixture, other_pipeline: str) -> None:
    print("\n== E. Monthly quota + batch endpoint (server running with FREE_TIER_TRACES_PER_MONTH=3) ==")
    anon = create_client(env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"])
    session = anon.auth.sign_in_with_password({"email": quota_user.email, "password": quota_user.password})
    jwt_headers = {"Authorization": f"Bearer {session.session.access_token}"}
    created = httpx.post(f"{base_url}/v1/keys", json={"name": "quota key"}, headers=jwt_headers)
    raw_key = created.json()["key"]
    key_headers = {"Authorization": f"Bearer {raw_key}"}
    anon.auth.sign_out()

    cross = httpx.post(
        f"{base_url}/v1/traces/batch",
        json={"traces": [minimal_trace_body(other_pipeline, "cross-tenant batch")]},
        headers=key_headers,
    )
    check("batch with non-owned pipeline -> 404 (before any quota consumed)", cross.status_code == 404, f"got {cross.status_code}: {cross.text}")

    batch = httpx.post(
        f"{base_url}/v1/traces/batch",
        json={"traces": [minimal_trace_body(quota_user.pipeline_id, f"batch {i}") for i in range(3)]},
        headers=key_headers,
    )
    check("batch of 3 within quota -> 202", batch.status_code == 202, f"got {batch.status_code}: {batch.text}")
    if batch.status_code == 202:
        ids = batch.json()["trace_ids"]
        check("batch returns 3 trace ids", len(ids) == 3, str(ids))

    over = httpx.post(
        f"{base_url}/v1/traces",
        json=minimal_trace_body(quota_user.pipeline_id, "over quota"),
        headers=key_headers,
    )
    check("4th trace over monthly quota -> 429", over.status_code == 429, f"got {over.status_code}: {over.text}")
    check(
        "quota rejection names the limit in the message",
        over.status_code == 429 and "limit" in over.text.lower(),
        over.text,
    )


async def section_f_worker_policy(admin: Client, user: UserFixture) -> None:
    print("\n== F. Worker eval policy — sampling + daily cap skip WITHOUT calling Haiku ==")
    from db.supabase import get_supabase
    from workers import eval_worker

    async_db = await get_supabase()

    def insert_trace(query: str) -> str:
        return (
            admin.table("traces")
            .insert(
                {
                    "pipeline_id": user.pipeline_id,
                    "user_id": user.user_id,
                    "query": query,
                    "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
                    "final_answer": "a",
                    "latency_ms": 1,
                }
            )
            .execute()
        ).data[0]["id"]

    # sampling: eval_sample_rate=0 must skip without any eval call
    trace_a = insert_trace(f"sampled-out {RUN_ID}")
    eval_mock = AsyncMock()
    with patch.object(eval_worker, "compute_eval", eval_mock):
        await eval_worker.process_trace(
            async_db,
            {
                "id": trace_a,
                "pipeline_id": user.pipeline_id,
                "user_id": user.user_id,
                "query": "q",
                "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
                "final_answer": "a",
                "pipelines": {"eval_sample_rate": 0},
            },
        )
    check("sample_rate=0: compute_eval never called", eval_mock.await_count == 0, f"called {eval_mock.await_count}x")
    row = admin.table("traces").select("eval_status").eq("id", trace_a).execute()
    check("sample_rate=0: trace marked 'skipped'", row.data[0]["eval_status"] == "skipped", str(row.data))

    # daily cap 0: must skip without any eval call
    trace_b = insert_trace(f"over-cap {RUN_ID}")
    from config import Settings

    capped = Settings().model_copy(update={"eval_daily_cap_per_user": 0})
    eval_mock2 = AsyncMock()
    with patch.object(eval_worker, "compute_eval", eval_mock2), patch.object(
        eval_worker, "get_settings", lambda: capped
    ):
        await eval_worker.process_trace(
            async_db,
            {
                "id": trace_b,
                "pipeline_id": user.pipeline_id,
                "user_id": user.user_id,
                "query": "q",
                "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
                "final_answer": "a",
                "pipelines": {"eval_sample_rate": 1.0},
            },
        )
    check("daily cap exceeded: compute_eval never called", eval_mock2.await_count == 0, f"called {eval_mock2.await_count}x")
    row_b = admin.table("traces").select("eval_status").eq("id", trace_b).execute()
    check("daily cap exceeded: trace marked 'skipped'", row_b.data[0]["eval_status"] == "skipped", str(row_b.data))


def section_g_quota_rpc(admin: Client, user: UserFixture) -> None:
    print("\n== G. consume_trace_quota RPC atomic metering ==")
    args = {"p_user_id": user.user_id, "p_count": 1, "p_limit": 2}
    first = admin.rpc("consume_trace_quota", args).execute()
    second = admin.rpc("consume_trace_quota", args).execute()
    third = admin.rpc("consume_trace_quota", args).execute()
    check("consumes 1st within limit", first.data is True, str(first.data))
    check("consumes 2nd within limit", second.data is True, str(second.data))
    check("rejects 3rd over limit", third.data is False, str(third.data))
    usage = admin.table("usage").select("traces_count").eq("user_id", user.user_id).execute()
    check(
        "usage row records exactly the consumed count (rejections not counted)",
        bool(usage.data) and usage.data[0]["traces_count"] == 2,
        str(usage.data),
    )


def main() -> int:
    env = load_env()
    admin = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

    if not env.get("SUPABASE_ANON_KEY"):
        print("FATAL: SUPABASE_ANON_KEY missing from apps/api/.env — required for RLS/JWT verification.")
        return 1

    user1 = UserFixture(admin, "u1")
    user2 = UserFixture(admin, "u2")
    quota_user = UserFixture(admin, "quota")

    log_main = REPO_ROOT / "tests" / ".tmp_hardening_main.log"
    log_quota = REPO_ROOT / "tests" / ".tmp_hardening_quota.log"
    server_main = start_server(8305, log_main, {"RATE_LIMIT_PER_MINUTE": "5"})
    server_quota = start_server(8306, log_quota, {"FREE_TIER_TRACES_PER_MONTH": "3", "RATE_LIMIT_PER_MINUTE": "1000"})

    try:
        ok_main = wait_for_health("http://127.0.0.1:8305")
        ok_quota = wait_for_health("http://127.0.0.1:8306")
        check("hardened API server starts (rate-limit config)", ok_main, f"see {log_main}")
        check("hardened API server starts (quota config)", ok_quota, f"see {log_quota}")
        if not (ok_main and ok_quota):
            return 1

        section_a_profiles_trigger(admin, user1)
        section_b_rls(admin, env, user1, user2)
        section_c_keys_lifecycle("http://127.0.0.1:8305", env, user1)
        section_d_rate_limit("http://127.0.0.1:8305", env, user1)
        section_e_quota_and_batch("http://127.0.0.1:8306", env, quota_user, user1.pipeline_id)
        asyncio.run(section_f_worker_policy(admin, user2))
        section_g_quota_rpc(admin, user2)

        for log in (log_main, log_quota):
            check(
                f"no traceback in server log ({log.name})",
                "Traceback (most recent call last)" not in log.read_text(),
                f"see {log}",
            )
    finally:
        stop_server(server_main)
        stop_server(server_quota)
        log_main.unlink(missing_ok=True)
        log_quota.unlink(missing_ok=True)
        for fixture in (user1, user2, quota_user):
            fixture.cleanup()

    print("\n== Summary ==")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]
    print(f"{passed}/{len(results)} checks passed")
    if failed:
        print("\nFailed checks:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
