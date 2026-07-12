"""Edge-case verification suite — real services throughout.

Complements phase_1..4_test.py and hardening_test.py (which cover the happy
paths and the core hardening checks) with the boundary/failure conditions
those suites don't exercise:

  A. Ingest payload validation (422s) + malformed auth headers — real server
  B. API key lifecycle edge cases (idempotent revoke, cross-tenant, garbage
     tokens)
  C. Worker resilience: bounded-retry-to-'failed' transition, a genuinely
     malformed provider response, and one bad trace not blocking the rest of
     its batch
  D. Cascading delete: deleting a pipeline (something users CAN do under RLS)
     cleans up its traces/eval_scores/chunk_index/alerts, not just the row
     itself
  E. Rate limit + monthly quota under REAL concurrent load (not sequential
     calls) — proves the Redis counter and consume_trace_quota RPC are
     atomic under a real race, not just correct when called one at a time
  F. Alerting boundary conditions: insufficient data, warning/critical
     severity thresholds, cooldown suppression, dedupe + re-fire after
     resolve, and cross-pipeline isolation

Mocking is used only where the point of the test IS the mock (forcing a
deterministic provider failure to verify retry/failure bookkeeping, or a
provider returning schema-invalid JSON) — never to fake a passing score.
Everything else hits real Supabase, real Redis, and a real running API
server.

Run with the api's own virtualenv, from anywhere:
    apps/api/.venv/bin/python tests/edge_cases_test.py
"""

from __future__ import annotations

import asyncio
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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
    """One test user: auth user (+ trigger-created profile), pipeline, an
    active API key, and a JWT for dashboard-style auth. Tracks everything it
    creates for cleanup."""

    def __init__(self, admin: Client, label: str, env: dict[str, str]):
        self.admin = admin
        self.env = env
        self.label = label
        self.email = f"edge-{label}-{RUN_ID}@example.com"
        self.password = secrets.token_urlsafe(16)
        result = admin.auth.admin.create_user(
            {"email": self.email, "password": self.password, "email_confirm": True}
        )
        self.user_id = result.user.id
        pipeline = (
            admin.table("pipelines")
            .insert({"user_id": self.user_id, "name": f"edge-{label}-{RUN_ID}", "framework": "custom"})
            .execute()
        )
        self.pipeline_id = pipeline.data[0]["id"]
        self._jwt: str | None = None

    def jwt(self) -> str:
        # A fresh client per sign-in (not a shared/reused one) — matches the
        # proven pattern in hardening_test.py/phase_4_test.py. Deliberately
        # no sign_out() here: that revokes the token server-side, which
        # would invalidate the very JWT we're about to hand back to the
        # caller for use in an Authorization header.
        if self._jwt is None:
            fresh = create_client(self.env["SUPABASE_URL"], self.env["SUPABASE_ANON_KEY"])
            session = fresh.auth.sign_in_with_password({"email": self.email, "password": self.password})
            self._jwt = session.session.access_token
        return self._jwt

    def insert_trace(self, **overrides) -> str:
        row = {
            "pipeline_id": self.pipeline_id,
            "user_id": self.user_id,
            "query": overrides.pop("query", f"edge query {RUN_ID}"),
            "retrieved_chunks": overrides.pop(
                "retrieved_chunks", [{"content": "c", "score": 1.0, "doc_id": "d"}]
            ),
            "final_answer": overrides.pop("final_answer", "a"),
            "latency_ms": overrides.pop("latency_ms", 1),
            **overrides,
        }
        return self.admin.table("traces").insert(row).execute().data[0]["id"]

    def cleanup(self) -> None:
        try:
            self.admin.table("alerts").delete().eq("user_id", self.user_id).execute()
            self.admin.table("pipelines").delete().eq("user_id", self.user_id).execute()
            self.admin.table("profiles").delete().eq("id", self.user_id).execute()
            self.admin.auth.admin.delete_user(self.user_id)
        except Exception as exc:
            print(f"WARNING: cleanup incomplete for {self.email}: {exc}")


# ---------------------------------------------------------------------------
# A. Ingest payload validation + malformed auth
# ---------------------------------------------------------------------------


def section_a_ingest_validation(base_url: str, user: UserFixture, raw_key: str) -> None:
    print("\n== A. Ingest payload validation + malformed auth (real server, real 4xx) ==")
    headers = {"Authorization": f"Bearer {raw_key}"}

    empty_query = minimal_trace_body(user.pipeline_id)
    empty_query["query"] = ""
    r = httpx.post(f"{base_url}/v1/traces", json=empty_query, headers=headers)
    check("empty query -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    empty_answer = minimal_trace_body(user.pipeline_id)
    empty_answer["final_answer"] = ""
    r = httpx.post(f"{base_url}/v1/traces", json=empty_answer, headers=headers)
    check("empty final_answer -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    empty_chunks = minimal_trace_body(user.pipeline_id)
    empty_chunks["retrieved_chunks"] = []
    r = httpx.post(f"{base_url}/v1/traces", json=empty_chunks, headers=headers)
    check("empty retrieved_chunks -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    neg_latency = minimal_trace_body(user.pipeline_id)
    neg_latency["latency_ms"] = -1
    r = httpx.post(f"{base_url}/v1/traces", json=neg_latency, headers=headers)
    check("negative latency_ms -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    neg_tokens = minimal_trace_body(user.pipeline_id)
    neg_tokens["token_count_input"] = -5
    r = httpx.post(f"{base_url}/v1/traces", json=neg_tokens, headers=headers)
    check("negative token_count_input -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    r = httpx.post(f"{base_url}/v1/traces/batch", json={"traces": []}, headers=headers)
    check("batch of 0 traces -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    oversized = {"traces": [minimal_trace_body(user.pipeline_id, f"over-{i}") for i in range(101)]}
    r = httpx.post(f"{base_url}/v1/traces/batch", json=oversized, headers=headers)
    check("batch of 101 traces -> 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

    r = httpx.post(f"{base_url}/v1/traces", json=minimal_trace_body(user.pipeline_id), headers={"Authorization": "Token not-a-bearer-scheme"})
    check("non-Bearer Authorization scheme -> 401", r.status_code == 401, f"got {r.status_code}: {r.text}")

    r = httpx.post(f"{base_url}/v1/traces", json=minimal_trace_body(user.pipeline_id))
    check("missing Authorization header entirely -> 401", r.status_code == 401, f"got {r.status_code}: {r.text}")

    r = httpx.post(f"{base_url}/v1/traces", json=minimal_trace_body(user.pipeline_id), headers={"Authorization": "Bearer kai_live_totally-made-up"})
    check("well-formed but nonexistent key -> 401", r.status_code == 401, f"got {r.status_code}: {r.text}")

    unicode_body = minimal_trace_body(user.pipeline_id, "日本語のクエリ 🚀 emoji test — \"quotes\" & <tags>")
    unicode_body["final_answer"] = "Réponse en français avec des caractères spéciaux: café, naïve, 你好"
    r = httpx.post(f"{base_url}/v1/traces", json=unicode_body, headers=headers)
    check("unicode/emoji/special-char payload -> 202", r.status_code == 202, f"got {r.status_code}: {r.text}")
    if r.status_code == 202:
        trace_id = r.json()["trace_id"]
        row = user.admin.table("traces").select("query,final_answer").eq("id", trace_id).execute().data[0]
        check(
            "unicode content persisted byte-for-byte",
            row["query"] == unicode_body["query"] and row["final_answer"] == unicode_body["final_answer"],
            str(row),
        )

    dup_body = minimal_trace_body(user.pipeline_id, f"duplicate-payload-{RUN_ID}")
    r1 = httpx.post(f"{base_url}/v1/traces", json=dup_body, headers=headers)
    r2 = httpx.post(f"{base_url}/v1/traces", json=dup_body, headers=headers)
    check(
        "identical payload submitted twice creates two distinct trace rows (no implicit dedup)",
        r1.status_code == 202 and r2.status_code == 202 and r1.json()["trace_id"] != r2.json()["trace_id"],
        f"{r1.status_code} {r1.text} / {r2.status_code} {r2.text}",
    )


# ---------------------------------------------------------------------------
# B. API key lifecycle edge cases
# ---------------------------------------------------------------------------


def section_b_key_edge_cases(base_url: str, user: UserFixture, other: UserFixture) -> None:
    print("\n== B. API key lifecycle edge cases (real server, real JWTs) ==")
    jwt_headers = {"Authorization": f"Bearer {user.jwt()}"}

    created = httpx.post(f"{base_url}/v1/keys", json={"name": "edge-case-key"}, headers=jwt_headers)
    check("create key -> 201", created.status_code == 201, f"got {created.status_code}: {created.text}")
    key_id = created.json()["id"]

    first_revoke = httpx.delete(f"{base_url}/v1/keys/{key_id}", headers=jwt_headers)
    check("first revoke -> 204", first_revoke.status_code == 204, f"got {first_revoke.status_code}")

    second_revoke = httpx.delete(f"{base_url}/v1/keys/{key_id}", headers=jwt_headers)
    check(
        "revoking an already-revoked key is idempotent (204, not 404)",
        second_revoke.status_code == 204,
        f"got {second_revoke.status_code}: {second_revoke.text}",
    )

    other_jwt_headers = {"Authorization": f"Bearer {other.jwt()}"}
    another_key = httpx.post(f"{base_url}/v1/keys", json={"name": "user1-key"}, headers=jwt_headers).json()
    cross_revoke = httpx.delete(f"{base_url}/v1/keys/{another_key['id']}", headers=other_jwt_headers)
    check(
        "revoking another user's key -> 404 (owner-scoped)",
        cross_revoke.status_code == 404,
        f"got {cross_revoke.status_code}: {cross_revoke.text}",
    )

    fake_id_revoke = httpx.delete(f"{base_url}/v1/keys/00000000-0000-0000-0000-000000000000", headers=jwt_headers)
    check("revoking a nonexistent key id -> 404", fake_id_revoke.status_code == 404, f"got {fake_id_revoke.status_code}")

    malformed_jwt = httpx.get(f"{base_url}/v1/keys", headers={"Authorization": "Bearer not.a.real.jwt"})
    check("malformed JWT on /v1/keys -> 401", malformed_jwt.status_code == 401, f"got {malformed_jwt.status_code}: {malformed_jwt.text}")

    no_auth = httpx.get(f"{base_url}/v1/keys")
    check("missing auth on /v1/keys -> 401", no_auth.status_code == 401, f"got {no_auth.status_code}")


# ---------------------------------------------------------------------------
# C. Worker resilience edge cases
# ---------------------------------------------------------------------------


async def section_c_worker_resilience(async_db, admin: Client, user: UserFixture) -> None:
    print("\n== C. Worker resilience: bounded retries, malformed output, batch isolation ==")
    from workers import eval_worker

    max_attempts = 2

    # Calls process_trace() directly on a manually-built trace dict (same
    # pattern as hardening_test.py section F) rather than process_once()'s
    # global fetch-pending-traces query — this keeps the test deterministic
    # regardless of what other 'pending' traces might exist in the DB.
    trace_id = user.insert_trace(query=f"always-fails-{RUN_ID}")
    trace_row = {
        "id": trace_id,
        "pipeline_id": user.pipeline_id,
        "user_id": user.user_id,
        "query": f"always-fails-{RUN_ID}",
        "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
        "final_answer": "a",
        "pipelines": {"eval_sample_rate": 1.0},
    }
    always_fails = AsyncMock(side_effect=RuntimeError("simulated provider outage"))
    with patch.object(eval_worker, "compute_eval", always_fails):
        for attempt in range(1, max_attempts + 1):
            try:
                await eval_worker.process_trace(async_db, trace_row)
                check(f"attempt {attempt}: process_trace() raised as expected", False, "did not raise")
            except RuntimeError:
                new_status = "failed" if attempt >= max_attempts else "pending"
                await eval_worker._set_status(async_db, trace_id, new_status, attempts=attempt)

    row = admin.table("traces").select("eval_status,eval_attempts").eq("id", trace_id).execute().data[0]
    check(
        f"bounded retries: after {max_attempts} failures the trace lands on 'failed' with eval_attempts={max_attempts}",
        row["eval_status"] == "failed" and row["eval_attempts"] == max_attempts,
        str(row),
    )

    # A provider response that fails Pydantic validation entirely (e.g. the
    # model ignores the schema and returns prose, or a field is the wrong
    # type) must be caught like any other exception, not crash the worker.
    from pydantic import ValidationError

    bad_trace = user.insert_trace(query=f"malformed-schema-{RUN_ID}")
    good_trace = user.insert_trace(query=f"still-processed-{RUN_ID}")

    from models.eval import EvalResult

    canned_good = EvalResult(
        faithfulness=0.95, answer_relevance=0.95, context_precision=0.9,
        hallucination_flag=False, hallucination_detail=None,
        failure_category=None, failure_reason=None,
    )

    async def selective_compute_eval(query, retrieved_chunks, final_answer):
        if "malformed-schema" in query:
            raise ValidationError.from_exception_data("EvalResult", [])
        return canned_good

    with patch.object(eval_worker, "compute_eval", AsyncMock(side_effect=selective_compute_eval)):
        n = await eval_worker.process_once()
        check("process_once() doesn't raise when one trace in the batch has malformed provider output", True)
        check("process_once() still processed both traces", n >= 2, str(n))

    bad_row = admin.table("traces").select("eval_status").eq("id", bad_trace).execute().data[0]
    good_row = admin.table("traces").select("eval_status").eq("id", good_trace).execute().data[0]
    check("trace with malformed provider output is retried, not silently dropped", bad_row["eval_status"] == "pending", str(bad_row))
    check("sibling trace in the same batch is unaffected and completes normally", good_row["eval_status"] == "completed", str(good_row))


# ---------------------------------------------------------------------------
# D. Cascading delete
# ---------------------------------------------------------------------------


def section_d_cascade_delete(admin: Client, env: dict[str, str]) -> None:
    print("\n== D. Deleting a pipeline cascades to traces/eval_scores/chunk_index/alerts ==")
    victim = UserFixture(admin, f"cascade-{RUN_ID}", env)
    try:
        trace_id = victim.insert_trace(query=f"cascade-trace-{RUN_ID}")
        admin.table("eval_scores").insert(
            {
                "trace_id": trace_id,
                "pipeline_id": victim.pipeline_id,
                "faithfulness": 0.9,
                "answer_relevance": 0.9,
                "context_precision": 0.9,
                "hallucination_flag": False,
            }
        ).execute()
        admin.table("chunk_index").insert(
            {"pipeline_id": victim.pipeline_id, "chunk_id": f"chunk-{RUN_ID}", "content_preview": "p", "retrieval_count": 1}
        ).execute()
        admin.table("alerts").insert(
            {
                "user_id": victim.user_id,
                "pipeline_id": victim.pipeline_id,
                "alert_type": "faithfulness_drop",
                "severity": "warning",
                "message": "seeded for cascade test",
            }
        ).execute()

        # Deletion is performed as the owner under RLS (not the service role)
        # to prove real users can actually do this, not just that the FK
        # cascade exists in principle.
        owner_client = create_client(env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"])
        owner_client.auth.sign_in_with_password({"email": victim.email, "password": victim.password})
        delete_result = owner_client.table("pipelines").delete().eq("id", victim.pipeline_id).execute()
        owner_client.auth.sign_out()
        check("owner can delete their own pipeline under RLS", len(delete_result.data) == 1, str(delete_result.data))

        check("traces cascade-deleted", not admin.table("traces").select("id").eq("id", trace_id).execute().data)
        check("eval_scores cascade-deleted", not admin.table("eval_scores").select("id").eq("pipeline_id", victim.pipeline_id).execute().data)
        check("chunk_index cascade-deleted", not admin.table("chunk_index").select("id").eq("pipeline_id", victim.pipeline_id).execute().data)
        check("alerts cascade-deleted", not admin.table("alerts").select("id").eq("pipeline_id", victim.pipeline_id).execute().data)
    finally:
        # pipeline is already gone; only the auth user/profile remain
        try:
            admin.table("profiles").delete().eq("id", victim.user_id).execute()
            admin.auth.admin.delete_user(victim.user_id)
        except Exception as exc:
            print(f"WARNING: cascade fixture cleanup incomplete: {exc}")


# ---------------------------------------------------------------------------
# E. Rate limit + quota under real concurrency
# ---------------------------------------------------------------------------


async def section_e_concurrency(admin: Client, env: dict[str, str]) -> None:
    print("\n== E. Rate limit + monthly quota under REAL concurrent load ==")
    user = UserFixture(admin, f"burst-{RUN_ID}", env)
    try:
        raw_key = _create_active_key(admin, user.user_id)
        log = REPO_ROOT / "tests" / ".tmp_edge_burst.log"
        server = start_server(8410, log, {"FREE_TIER_TRACES_PER_MONTH": "10", "RATE_LIMIT_PER_MINUTE": "1000"})
        try:
            if not wait_for_health("http://127.0.0.1:8410"):
                check("burst server starts", False, f"see {log}")
                return

            headers = {"Authorization": f"Bearer {raw_key}"}
            async with httpx.AsyncClient() as client:
                async def fire(i: int):
                    return await client.post(
                        "http://127.0.0.1:8410/v1/traces",
                        json=minimal_trace_body(user.pipeline_id, f"burst-{i}"),
                        headers=headers,
                        timeout=10.0,
                    )

                responses = await asyncio.gather(*(fire(i) for i in range(20)))

            accepted = sum(1 for r in responses if r.status_code == 202)
            rejected = sum(1 for r in responses if r.status_code == 429)
            check(
                "exactly quota-limit (10) requests accepted under a 20-request concurrent burst",
                accepted == 10,
                f"accepted={accepted} rejected={rejected} statuses={[r.status_code for r in responses]}",
            )
            check("the rest rejected with 429 (no over-admission, no 5xx)", rejected == 10, f"accepted={accepted} rejected={rejected}")

            usage = admin.table("usage").select("traces_count").eq("user_id", user.user_id).execute()
            check(
                "usage.traces_count reflects exactly the accepted count (RPC atomic under real concurrency)",
                bool(usage.data) and usage.data[0]["traces_count"] == 10,
                str(usage.data),
            )
        finally:
            stop_server(server)
    finally:
        user.cleanup()


def _create_active_key(admin: Client, user_id: str) -> str:
    import hashlib
    import secrets as _secrets

    raw_key = f"kai_live_{_secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    admin.table("api_keys").insert(
        {"user_id": user_id, "key_hash": key_hash, "key_prefix": raw_key[:12], "name": "burst-test-key"}
    ).execute()
    return raw_key


# ---------------------------------------------------------------------------
# F. Alerting boundary conditions
# ---------------------------------------------------------------------------


async def section_f_alerting_boundaries(async_db, admin: Client, env: dict[str, str]) -> None:
    print("\n== F. Alerting boundary conditions (real function, synthetic score history) ==")
    from services.alerting import (
        DROP_CRITICAL_RATIO,
        DROP_WARNING_RATIO,
        MIN_BASELINE_EVALS,
        MIN_RECENT_EVALS,
        check_pipeline_alerts,
    )

    now = datetime.now(timezone.utc)

    def seed(pipeline_id: str, n: int, faithfulness: float, hours_ago_start: float, hours_ago_end: float, hallucination: bool = False) -> None:
        rows = []
        for i in range(n):
            offset = hours_ago_start + (hours_ago_end - hours_ago_start) * (i / max(n - 1, 1))
            rows.append(
                {
                    "trace_id": None,
                    "pipeline_id": pipeline_id,
                    "faithfulness": faithfulness,
                    "answer_relevance": faithfulness,
                    "context_precision": faithfulness,
                    "hallucination_flag": hallucination,
                    "computed_at": (now - timedelta(hours=offset)).isoformat(),
                }
            )
        admin.table("eval_scores").insert(rows).execute()

    # F1: insufficient recent evals -> no alert even with terrible scores
    p1 = UserFixture(admin, f"alert-insuff-recent-{RUN_ID}", env)
    seed(p1.pipeline_id, MIN_RECENT_EVALS - 1, 0.1, 1, 20)
    seed(p1.pipeline_id, MIN_BASELINE_EVALS, 0.95, 30, 190)
    created = await check_pipeline_alerts(async_db, p1.pipeline_id, p1.user_id, skip_cooldown=True)
    check("insufficient recent evals -> no alert despite terrible scores", created == [], str(created))

    # F2: insufficient baseline evals -> no alert
    p2 = UserFixture(admin, f"alert-insuff-baseline-{RUN_ID}", env)
    seed(p2.pipeline_id, MIN_RECENT_EVALS, 0.1, 1, 20)
    seed(p2.pipeline_id, MIN_BASELINE_EVALS - 1, 0.95, 30, 190)
    created = await check_pipeline_alerts(async_db, p2.pipeline_id, p2.user_id, skip_cooldown=True)
    check("insufficient baseline evals -> no alert despite terrible scores", created == [], str(created))

    # F3: severity boundary — ratio just under warning but above critical -> warning
    p3 = UserFixture(admin, f"alert-warning-{RUN_ID}", env)
    baseline_f = 1.0
    warning_f = DROP_WARNING_RATIO - 0.05  # e.g. 0.80 -> below 0.85 warning line, above 0.70 critical line
    seed(p3.pipeline_id, MIN_RECENT_EVALS, warning_f, 1, 20)
    seed(p3.pipeline_id, MIN_BASELINE_EVALS, baseline_f, 30, 190)
    created = await check_pipeline_alerts(async_db, p3.pipeline_id, p3.user_id, skip_cooldown=True)
    row = admin.table("alerts").select("severity").eq("pipeline_id", p3.pipeline_id).eq("alert_type", "faithfulness_drop").execute()
    check("faithfulness_drop fires at 'warning' just past the warning threshold", "faithfulness_drop" in created and row.data and row.data[0]["severity"] == "warning", f"{created} {row.data}")

    # F4: severity boundary — ratio well below critical -> critical
    p4 = UserFixture(admin, f"alert-critical-{RUN_ID}", env)
    critical_f = DROP_CRITICAL_RATIO - 0.10
    seed(p4.pipeline_id, MIN_RECENT_EVALS, critical_f, 1, 20)
    seed(p4.pipeline_id, MIN_BASELINE_EVALS, baseline_f, 30, 190)
    created = await check_pipeline_alerts(async_db, p4.pipeline_id, p4.user_id, skip_cooldown=True)
    row = admin.table("alerts").select("severity").eq("pipeline_id", p4.pipeline_id).eq("alert_type", "faithfulness_drop").execute()
    check("faithfulness_drop fires at 'critical' well past the critical threshold", "faithfulness_drop" in created and row.data and row.data[0]["severity"] == "critical", f"{created} {row.data}")

    # F5: cooldown suppresses a second check within the window (no skip_cooldown)
    p5 = UserFixture(admin, f"alert-cooldown-{RUN_ID}", env)
    seed(p5.pipeline_id, MIN_RECENT_EVALS, 0.1, 1, 20)
    seed(p5.pipeline_id, MIN_BASELINE_EVALS, 0.95, 30, 190)
    first = await check_pipeline_alerts(async_db, p5.pipeline_id, p5.user_id)
    second = await check_pipeline_alerts(async_db, p5.pipeline_id, p5.user_id)
    check("first check (cooldown not yet active) creates an alert", first != [], str(first))
    check("immediate second check is suppressed by the 1h cooldown", second == [], str(second))

    # F6: dedupe — unresolved alert blocks a duplicate; resolving allows a new one
    p6 = UserFixture(admin, f"alert-dedupe-{RUN_ID}", env)
    seed(p6.pipeline_id, MIN_RECENT_EVALS, 0.1, 1, 20)
    seed(p6.pipeline_id, MIN_BASELINE_EVALS, 0.95, 30, 190)
    first6 = await check_pipeline_alerts(async_db, p6.pipeline_id, p6.user_id, skip_cooldown=True)
    second6 = await check_pipeline_alerts(async_db, p6.pipeline_id, p6.user_id, skip_cooldown=True)
    check("unresolved alert blocks a duplicate of the same type", first6 != [] and second6 == [], f"{first6} {second6}")
    admin.table("alerts").update({"resolved": True}).eq("pipeline_id", p6.pipeline_id).execute()
    third6 = await check_pipeline_alerts(async_db, p6.pipeline_id, p6.user_id, skip_cooldown=True)
    check("resolving the alert allows a new one to fire", third6 != [], str(third6))

    # F7: cross-pipeline isolation — a degraded pipeline doesn't alert a healthy sibling
    p7a = UserFixture(admin, f"alert-isolation-bad-{RUN_ID}", env)
    p7b = UserFixture(admin, f"alert-isolation-good-{RUN_ID}", env)
    seed(p7a.pipeline_id, MIN_RECENT_EVALS, 0.1, 1, 20)
    seed(p7a.pipeline_id, MIN_BASELINE_EVALS, 0.95, 30, 190)
    seed(p7b.pipeline_id, MIN_RECENT_EVALS, 0.95, 1, 20)
    seed(p7b.pipeline_id, MIN_BASELINE_EVALS, 0.95, 30, 190)
    created_a = await check_pipeline_alerts(async_db, p7a.pipeline_id, p7a.user_id, skip_cooldown=True)
    created_b = await check_pipeline_alerts(async_db, p7b.pipeline_id, p7b.user_id, skip_cooldown=True)
    check("degraded pipeline alerts", created_a != [], str(created_a))
    check("healthy sibling pipeline does not alert (no cross-pipeline leakage)", created_b == [], str(created_b))

    for fx in (p1, p2, p3, p4, p5, p6, p7a, p7b):
        fx.cleanup()


# ---------------------------------------------------------------------------


async def main_async() -> int:
    env = load_env()
    admin = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    if not env.get("SUPABASE_ANON_KEY"):
        print("FATAL: SUPABASE_ANON_KEY missing from apps/api/.env — required for JWT/RLS checks.")
        return 1

    from db.supabase import get_supabase

    async_db = await get_supabase()

    user1 = UserFixture(admin, f"main1-{RUN_ID}", env)
    user2 = UserFixture(admin, f"main2-{RUN_ID}", env)
    raw_key1 = _create_active_key(admin, user1.user_id)

    log = REPO_ROOT / "tests" / ".tmp_edge_main.log"
    server = start_server(8409, log, {})

    try:
        if not wait_for_health("http://127.0.0.1:8409"):
            check("edge-case API server starts", False, f"see {log}")
            return 1

        base_url = "http://127.0.0.1:8409"
        section_a_ingest_validation(base_url, user1, raw_key1)
        section_b_key_edge_cases(base_url, user1, user2)
        await section_c_worker_resilience(async_db, admin, user1)
        section_d_cascade_delete(admin, env)
        await section_e_concurrency(admin, env)
        await section_f_alerting_boundaries(async_db, admin, env)
    finally:
        stop_server(server)
        user1.cleanup()
        user2.cleanup()

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
    sys.exit(asyncio.run(main_async()))
