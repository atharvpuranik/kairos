"""Phase 1, Week 1 verification suite.

Black-box integration test: spins up the real FastAPI app against the real
Supabase + Upstash Redis + Anthropic services configured in apps/api/.env
(no mocks), exercises every endpoint built this phase, verifies persisted
data by reading it back, and cleans up everything it creates.

Run with the api's own virtualenv, from anywhere:
    apps/api/.venv/bin/python tests/phase_1_test.py

Exits 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from supabase import Client, create_client
from upstash_redis import Redis

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
VENV_PYTHON = API_DIR / ".venv" / "bin" / "python"
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
        print(f"FATAL: {env_path} does not exist. Cannot run against real services.")
        sys.exit(1)
    values = dotenv_values(env_path)
    return {k: v for k, v in values.items() if v is not None}


def wait_for_health(base_url: str, timeout: float = 10.0) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                return True, ""
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.3)
    return False, last_error


def start_server(port: int, reload: bool, log_path: Path) -> subprocess.Popen:
    cmd = [str(VENV_PYTHON), "-m", "uvicorn", "main:app", "--port", str(port)]
    if reload:
        cmd.append("--reload")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        cwd=API_DIR,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    return proc


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


def log_has_traceback(log_path: Path) -> bool:
    if not log_path.exists():
        return False
    return "Traceback (most recent call last)" in log_path.read_text()


# ---------------------------------------------------------------------------
# Section A — server startup
# ---------------------------------------------------------------------------

def section_a_server_startup() -> None:
    print("\n== A. FastAPI server startup ==")

    reload_log = REPO_ROOT / "tests" / ".tmp_reload_server.log"
    proc = start_server(port=8301, reload=True, log_path=reload_log)
    try:
        ok, err = wait_for_health("http://127.0.0.1:8301", timeout=12)
        check(
            "uvicorn main:app --reload starts and /health responds",
            ok,
            err or f"see {reload_log}",
        )
        check(
            "no traceback in startup log (--reload)",
            not log_has_traceback(reload_log),
            f"see {reload_log}",
        )
    finally:
        stop_server(proc)
        reload_log.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Section setup — fixtures via admin client (not the app under test)
# ---------------------------------------------------------------------------

class Fixtures:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.user_id: str | None = None
        self.email = f"phase1-test-{RUN_ID}@example.com"
        self.pipeline_id: str | None = None
        self.raw_key: str | None = None
        self.inactive_raw_key: str | None = None
        self._inactive_key_id: str | None = None

    def create(self) -> None:
        auth_result = self.supabase.auth.admin.create_user(
            {"email": self.email, "password": secrets.token_urlsafe(16), "email_confirm": True}
        )
        self.user_id = auth_result.user.id

        self.supabase.table("profiles").upsert(
            {"id": self.user_id, "email": self.email}
        ).execute()

        pipeline_result = (
            self.supabase.table("pipelines")
            .insert(
                {
                    "user_id": self.user_id,
                    "name": f"phase-1-test-{RUN_ID}",
                    "framework": "custom",
                }
            )
            .execute()
        )
        self.pipeline_id = pipeline_result.data[0]["id"]

        self.raw_key = f"kai_live_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(self.raw_key.encode()).hexdigest()
        self.supabase.table("api_keys").insert(
            {
                "user_id": self.user_id,
                "key_hash": key_hash,
                "key_prefix": self.raw_key[:12],
                "name": f"phase-1-test-active-{RUN_ID}",
            }
        ).execute()

        self.inactive_raw_key = f"kai_live_{secrets.token_urlsafe(32)}"
        inactive_hash = hashlib.sha256(self.inactive_raw_key.encode()).hexdigest()
        inactive_result = (
            self.supabase.table("api_keys")
            .insert(
                {
                    "user_id": self.user_id,
                    "key_hash": inactive_hash,
                    "key_prefix": self.inactive_raw_key[:12],
                    "name": f"phase-1-test-inactive-{RUN_ID}",
                    "is_active": False,
                }
            )
            .execute()
        )
        self._inactive_key_id = inactive_result.data[0]["id"]

    def cleanup(self) -> None:
        try:
            if self.pipeline_id:
                self.supabase.table("traces").delete().eq("pipeline_id", self.pipeline_id).execute()
                self.supabase.table("pipelines").delete().eq("id", self.pipeline_id).execute()
            if self.user_id:
                self.supabase.table("api_keys").delete().eq("user_id", self.user_id).execute()
                self.supabase.table("profiles").delete().eq("id", self.user_id).execute()
                self.supabase.auth.admin.delete_user(self.user_id)
        except Exception as exc:
            print(f"WARNING: fixture cleanup incomplete: {exc}")


# ---------------------------------------------------------------------------
# Section B — schema sanity (tables from section 7 all exist and are queryable)
# ---------------------------------------------------------------------------

def section_b_schema(supabase: Client) -> None:
    print("\n== B. Supabase schema sanity ==")
    tables = [
        "profiles",
        "api_keys",
        "pipelines",
        "traces",
        "eval_scores",
        "chunk_index",
        "agent_projects",
        "agent_test_runs",
        "agent_simulations",
        "alerts",
        "usage",
    ]
    for table in tables:
        try:
            supabase.table(table).select("*").limit(1).execute()
            check(f"table `{table}` exists and is queryable", True)
        except Exception as exc:
            check(f"table `{table}` exists and is queryable", False, str(exc))


# ---------------------------------------------------------------------------
# Section C — health endpoint
# ---------------------------------------------------------------------------

def section_c_health(client: httpx.Client) -> None:
    print("\n== C. GET /health ==")
    resp = client.get("/health")
    check("GET /health -> 200", resp.status_code == 200, f"got {resp.status_code}")
    check("GET /health body == {'status': 'ok'}", resp.json() == {"status": "ok"}, str(resp.text))


# ---------------------------------------------------------------------------
# Section D — ingest happy path + persistence
# ---------------------------------------------------------------------------

def section_d_ingest_happy_path(client: httpx.Client, supabase: Client, fx: Fixtures) -> str | None:
    print("\n== D. POST /v1/traces — happy path + persistence ==")

    payload = {
        "pipeline_id": fx.pipeline_id,
        "query": "What is the refund policy?",
        "retrieved_chunks": [
            {"content": "Refunds within 30 days.", "score": 0.92, "doc_id": "doc-1"}
        ],
        "final_answer": "You can get a refund within 30 days.",
        "latency_ms": 245,
        "token_count_input": 120,
        "token_count_output": 18,
        "estimated_cost_usd": 0.000842,
        "metadata": {"source": f"phase1-test-{RUN_ID}"},
    }
    resp = client.post(
        "/v1/traces", json=payload, headers={"Authorization": f"Bearer {fx.raw_key}"}
    )
    check("valid ingest -> 202", resp.status_code == 202, f"got {resp.status_code}: {resp.text}")

    body = resp.json() if resp.status_code == 202 else {}
    check("response has status == 'queued'", body.get("status") == "queued", str(body))
    trace_id = body.get("trace_id")
    check("response includes a trace_id", bool(trace_id), str(body))

    if not trace_id:
        return None

    row_result = supabase.table("traces").select("*").eq("id", trace_id).execute()
    check("trace row exists in Supabase", len(row_result.data) == 1, str(row_result.data))
    if not row_result.data:
        return trace_id

    row = row_result.data[0]
    check("persisted query matches", row["query"] == payload["query"], row["query"])
    check("persisted final_answer matches", row["final_answer"] == payload["final_answer"], row["final_answer"])
    check("persisted latency_ms matches", row["latency_ms"] == payload["latency_ms"], str(row["latency_ms"]))
    check(
        "persisted token_count_input matches",
        row["token_count_input"] == payload["token_count_input"],
        str(row["token_count_input"]),
    )
    check(
        "persisted token_count_output matches",
        row["token_count_output"] == payload["token_count_output"],
        str(row["token_count_output"]),
    )
    check(
        "persisted estimated_cost_usd matches",
        abs(float(row["estimated_cost_usd"]) - payload["estimated_cost_usd"]) < 1e-6,
        str(row["estimated_cost_usd"]),
    )
    check("persisted metadata matches", row["metadata"] == payload["metadata"], str(row["metadata"]))
    chunk = row["retrieved_chunks"][0] if row["retrieved_chunks"] else {}
    check(
        "persisted retrieved_chunks matches",
        chunk.get("content") == payload["retrieved_chunks"][0]["content"]
        and chunk.get("doc_id") == payload["retrieved_chunks"][0]["doc_id"]
        and chunk.get("score") == payload["retrieved_chunks"][0]["score"],
        str(row["retrieved_chunks"]),
    )
    check("pipeline_id association correct", row["pipeline_id"] == fx.pipeline_id, row["pipeline_id"])
    check("user_id association correct", row["user_id"] == fx.user_id, row["user_id"])

    key_row = (
        supabase.table("api_keys")
        .select("last_used_at")
        .eq("user_id", fx.user_id)
        .eq("name", f"phase-1-test-active-{RUN_ID}")
        .execute()
    )
    check(
        "api_keys.last_used_at updated after successful auth",
        bool(key_row.data and key_row.data[0]["last_used_at"]),
        str(key_row.data),
    )

    return trace_id


# ---------------------------------------------------------------------------
# Section E — auth & validation error handling
# ---------------------------------------------------------------------------

def section_e_error_handling(client: httpx.Client, fx: Fixtures) -> None:
    print("\n== E. Auth & validation error handling ==")

    minimal_valid_body = {
        "pipeline_id": fx.pipeline_id,
        "query": "x",
        "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
        "final_answer": "a",
        "latency_ms": 1,
    }

    resp = client.post("/v1/traces", json=minimal_valid_body)
    check("missing Authorization header -> 401", resp.status_code == 401, f"got {resp.status_code}")

    bad_key = "kai_live_totally_fake_key"
    resp1 = client.post(
        "/v1/traces", json=minimal_valid_body, headers={"Authorization": f"Bearer {bad_key}"}
    )
    resp2 = client.post(
        "/v1/traces", json=minimal_valid_body, headers={"Authorization": f"Bearer {bad_key}"}
    )
    check("invalid API key -> 401 (cold)", resp1.status_code == 401, f"got {resp1.status_code}")
    check("invalid API key -> 401 (cached)", resp2.status_code == 401, f"got {resp2.status_code}")

    resp = client.post(
        "/v1/traces",
        json=minimal_valid_body,
        headers={"Authorization": f"Bearer {fx.inactive_raw_key}"},
    )
    check("deactivated API key -> 401", resp.status_code == 401, f"got {resp.status_code}")

    fake_pipeline = "00000000-0000-0000-0000-000000000000"
    body = dict(minimal_valid_body, pipeline_id=fake_pipeline)
    resp = client.post("/v1/traces", json=body, headers={"Authorization": f"Bearer {fx.raw_key}"})
    check(
        "valid key, non-owned/nonexistent pipeline -> 404",
        resp.status_code == 404,
        f"got {resp.status_code}: {resp.text}",
    )

    missing_field_body = dict(minimal_valid_body)
    del missing_field_body["final_answer"]
    resp = client.post(
        "/v1/traces", json=missing_field_body, headers={"Authorization": f"Bearer {fx.raw_key}"}
    )
    check("missing required field -> 422", resp.status_code == 422, f"got {resp.status_code}")

    negative_latency_body = dict(minimal_valid_body, latency_ms=-5)
    resp = client.post(
        "/v1/traces",
        json=negative_latency_body,
        headers={"Authorization": f"Bearer {fx.raw_key}"},
    )
    check("negative latency_ms -> 422", resp.status_code == 422, f"got {resp.status_code}")

    empty_chunks_body = dict(minimal_valid_body, retrieved_chunks=[])
    resp = client.post(
        "/v1/traces", json=empty_chunks_body, headers={"Authorization": f"Bearer {fx.raw_key}"}
    )
    check("empty retrieved_chunks -> 422", resp.status_code == 422, f"got {resp.status_code}")

    malformed_uuid_body = dict(minimal_valid_body, pipeline_id="not-a-uuid")
    resp = client.post(
        "/v1/traces", json=malformed_uuid_body, headers={"Authorization": f"Bearer {fx.raw_key}"}
    )
    check("malformed pipeline_id UUID -> 422", resp.status_code == 422, f"got {resp.status_code}")

    wrong_type_body = dict(minimal_valid_body, latency_ms="not-a-number")
    resp = client.post(
        "/v1/traces", json=wrong_type_body, headers={"Authorization": f"Bearer {fx.raw_key}"}
    )
    check("wrong type for latency_ms -> 422", resp.status_code == 422, f"got {resp.status_code}")


# ---------------------------------------------------------------------------
# Section F — Upstash Redis: reachability + real cache side effects
# ---------------------------------------------------------------------------

def section_f_redis(env: dict[str, str], fx: Fixtures) -> None:
    print("\n== F. Upstash Redis — reachability + cache behavior ==")
    redis = Redis(url=env["UPSTASH_REDIS_REST_URL"], token=env["UPSTASH_REDIS_REST_TOKEN"])

    scratch_key = f"phase1-test-ping-{RUN_ID}"
    try:
        redis.set(scratch_key, "pong", ex=30)
        val = redis.get(scratch_key)
        check("Upstash Redis real set/get roundtrip", val == "pong", f"got {val!r}")
    except Exception as exc:
        check("Upstash Redis real set/get roundtrip", False, str(exc))
    finally:
        try:
            redis.delete(scratch_key)
        except Exception:
            pass

    valid_hash = hashlib.sha256(fx.raw_key.encode()).hexdigest()
    cached_valid = redis.get(f"apikey:{valid_hash}")
    check(
        "valid key cached in Redis after auth",
        bool(cached_valid) and cached_valid != "invalid",
        f"got {cached_valid!r}",
    )

    invalid_hash = hashlib.sha256(b"kai_live_totally_fake_key").hexdigest()
    cached_invalid = redis.get(f"apikey:{invalid_hash}")
    check(
        "invalid key negatively cached in Redis",
        cached_invalid == "invalid",
        f"got {cached_invalid!r}",
    )

    for key in (f"apikey:{valid_hash}", f"apikey:{invalid_hash}"):
        try:
            redis.delete(key)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Section G — Anthropic reachability (not yet wired into app; key exists for Week 3)
# ---------------------------------------------------------------------------

def section_g_anthropic(env: dict[str, str]) -> None:
    print("\n== G. Anthropic API reachability (not used by app logic until Week 3) ==")
    api_key = env.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        check("Anthropic API key configured", False, "ANTHROPIC_API_KEY is empty in .env")
        return
    try:
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
        check(
            "Anthropic API reachable with configured key",
            resp.status_code == 200,
            f"got {resp.status_code}: {resp.text[:200]}",
        )
    except httpx.HTTPError as exc:
        check("Anthropic API reachable with configured key", False, str(exc))


# ---------------------------------------------------------------------------
# Section H — services explicitly out of scope this phase
# ---------------------------------------------------------------------------

def section_h_out_of_scope() -> None:
    print("\n== H. Out of scope this phase (informational, not scored) ==")
    print("[N/A] Qdrant — no client/config exists yet; not part of Phase 1 Week 1")
    print("[N/A] Inngest — keys present in .env but no enqueue code exists yet; deferred to Week 3 per agreed plan")


# ---------------------------------------------------------------------------


def main() -> int:
    env = load_env()

    section_a_server_startup()

    supabase = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    section_b_schema(supabase)

    fx = Fixtures(supabase)
    server_log = REPO_ROOT / "tests" / ".tmp_main_server.log"
    proc = start_server(port=8302, reload=False, log_path=server_log)
    try:
        ok, err = wait_for_health("http://127.0.0.1:8302", timeout=12)
        check("main test server starts and /health responds", ok, err or f"see {server_log}")
        if not ok:
            print(server_log.read_text() if server_log.exists() else "(no log)")
            return 1

        fx.create()

        with httpx.Client(base_url="http://127.0.0.1:8302", timeout=10) as client:
            section_c_health(client)
            section_d_ingest_happy_path(client, supabase, fx)
            section_e_error_handling(client, fx)

        section_f_redis(env, fx)
        section_g_anthropic(env)
        section_h_out_of_scope()

        check("no traceback in main server log", not log_has_traceback(server_log), f"see {server_log}")
    finally:
        stop_server(proc)
        server_log.unlink(missing_ok=True)
        fx.cleanup()

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
