"""Phase 1, Week 2 verification suite.

Black-box integration test: spins up the real FastAPI app, uses the real
kairoslabs SDK (installed into apps/api/.venv, not imported from source) to
send traces over real HTTP to that server, backed by the real Supabase +
Upstash Redis services from apps/api/.env — no mocks anywhere in this file.
Also drives a real LangChain RetrievalQA chain (FakeListLLM — zero API
cost, no external LLM needed) through KairosCallbackHandler.

Run with the api's own virtualenv, from anywhere:
    apps/api/.venv/bin/python tests/phase_2_test.py

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
PORT = 8303
BASE_URL = f"http://127.0.0.1:{PORT}"

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


def wait_for_health(base_url: str, timeout: float = 12.0) -> tuple[bool, str]:
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


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    cmd = [str(VENV_PYTHON), "-m", "uvicorn", "main:app", "--port", str(port)]
    log_file = open(log_path, "w")
    return subprocess.Popen(cmd, cwd=API_DIR, stdout=log_file, stderr=subprocess.STDOUT, preexec_fn=os.setsid)


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


def wait_for_row(supabase: Client, table: str, column: str, value: str, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = supabase.table(table).select("*").eq(column, value).execute()
        if result.data:
            return result.data[0]
        time.sleep(0.3)
    return None


class Fixtures:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.user_id: str | None = None
        self.email = f"phase2-test-{RUN_ID}@example.com"
        self.pipeline_id: str | None = None
        self.other_pipeline_id: str | None = None
        self.raw_key: str | None = None

    def create(self) -> None:
        auth_result = self.supabase.auth.admin.create_user(
            {"email": self.email, "password": secrets.token_urlsafe(16), "email_confirm": True}
        )
        self.user_id = auth_result.user.id
        self.supabase.table("profiles").upsert({"id": self.user_id, "email": self.email}).execute()

        pipeline_result = (
            self.supabase.table("pipelines")
            .insert({"user_id": self.user_id, "name": f"phase-2-test-{RUN_ID}", "framework": "custom"})
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
                "name": f"phase-2-test-{RUN_ID}",
            }
        ).execute()

        # a second, unrelated user's pipeline — used to test cross-tenant rejection
        other_auth = self.supabase.auth.admin.create_user(
            {
                "email": f"phase2-other-{RUN_ID}@example.com",
                "password": secrets.token_urlsafe(16),
                "email_confirm": True,
            }
        )
        other_user_id = other_auth.user.id
        self.supabase.table("profiles").upsert(
            {"id": other_user_id, "email": f"phase2-other-{RUN_ID}@example.com"}
        ).execute()
        other_pipeline = (
            self.supabase.table("pipelines")
            .insert({"user_id": other_user_id, "name": f"phase-2-other-{RUN_ID}", "framework": "custom"})
            .execute()
        )
        self.other_pipeline_id = other_pipeline.data[0]["id"]
        self._other_user_id = other_user_id

    def cleanup(self) -> None:
        try:
            for pid in (self.pipeline_id, self.other_pipeline_id):
                if pid:
                    self.supabase.table("traces").delete().eq("pipeline_id", pid).execute()
                    self.supabase.table("pipelines").delete().eq("id", pid).execute()
            if self.user_id:
                self.supabase.table("api_keys").delete().eq("user_id", self.user_id).execute()
                self.supabase.table("profiles").delete().eq("id", self.user_id).execute()
                self.supabase.auth.admin.delete_user(self.user_id)
            if getattr(self, "_other_user_id", None):
                self.supabase.table("profiles").delete().eq("id", self._other_user_id).execute()
                self.supabase.auth.admin.delete_user(self._other_user_id)
        except Exception as exc:
            print(f"WARNING: fixture cleanup incomplete: {exc}")


# ---------------------------------------------------------------------------


def section_a_sdk_invalid_inputs(fx: Fixtures) -> None:
    print("\n== A. SDK — invalid inputs (constructor / trace() validation) ==")
    from kairos.tracer import KairosTracer

    saved_key = os.environ.pop("KAIROS_API_KEY", None)
    try:
        try:
            KairosTracer(api_url=BASE_URL)
            check("KairosTracer() with no api_key anywhere raises ValueError", False, "did not raise")
        except ValueError:
            check("KairosTracer() with no api_key anywhere raises ValueError", True)
    finally:
        if saved_key is not None:
            os.environ["KAIROS_API_KEY"] = saved_key

    saved_pid = os.environ.pop("KAIROS_PIPELINE_ID", None)
    try:
        tracer = KairosTracer(api_key=fx.raw_key, api_url=BASE_URL)
        try:
            tracer.trace(query="q")
            check("trace() with no pipeline_id anywhere raises ValueError", False, "did not raise")
        except ValueError:
            check("trace() with no pipeline_id anywhere raises ValueError", True)
    finally:
        if saved_pid is not None:
            os.environ["KAIROS_PIPELINE_ID"] = saved_pid


def section_b_manual_trace_real_roundtrip(supabase: Client, fx: Fixtures) -> None:
    print("\n== B. KairosTracer.trace() — real HTTP roundtrip to live API + Supabase ==")
    from kairos.tracer import KairosTracer

    tracer = KairosTracer(api_key=fx.raw_key, api_url=BASE_URL, pipeline_id=fx.pipeline_id)
    tag = f"phase2-manual-{RUN_ID}"

    with tracer.trace(query=f"What is the refund policy? [{tag}]") as t:
        t.log_retrieval([{"content": "Refunds within 30 days.", "score": 0.9, "doc_id": "doc-1"}])
        t.log_answer("You get a refund within 30 days.")
        t.set_metadata({"tag": tag})

    row = wait_for_row(supabase, "traces", "query", f"What is the refund policy? [{tag}]")
    check("manual trace() call did not raise in caller's code", True)
    check("trace landed in Supabase via real SDK -> real API -> real DB", row is not None, "no row appeared within timeout")
    if row:
        check("persisted final_answer correct", row["final_answer"] == "You get a refund within 30 days.", row["final_answer"])
        check("persisted metadata correct", row["metadata"] == {"tag": tag}, str(row["metadata"]))
        check(
            "persisted retrieved_chunks correct",
            row["retrieved_chunks"][0]["doc_id"] == "doc-1",
            str(row["retrieved_chunks"]),
        )
        check("pipeline association correct", row["pipeline_id"] == fx.pipeline_id, row["pipeline_id"])


def section_c_wrap_real_roundtrip(supabase: Client, fx: Fixtures) -> None:
    print("\n== C. KairosTracer.wrap() — real roundtrip ==")
    from kairos.tracer import KairosTracer

    tracer = KairosTracer(api_key=fx.raw_key, api_url=BASE_URL, pipeline_id=fx.pipeline_id)
    tag = f"phase2-wrap-{RUN_ID}"

    def fake_retriever(query: str):
        return [{"content": "Wrapped retrieval result.", "score": 0.75, "doc_id": "wrap-doc-1"}]

    wrapped = tracer.wrap(fake_retriever)

    with tracer.trace(query=f"wrapped query [{tag}]") as t:
        chunks = wrapped(f"wrapped query [{tag}]")
        assert chunks[0]["doc_id"] == "wrap-doc-1"
        t.log_answer("wrapped answer")

    row = wait_for_row(supabase, "traces", "query", f"wrapped query [{tag}]")
    check("wrap()'d retriever call returned the real result to caller", True)
    check("wrap() + trace() produced a persisted row", row is not None, "no row appeared within timeout")
    if row:
        check(
            "chunks captured automatically by wrap() are correct",
            row["retrieved_chunks"][0]["doc_id"] == "wrap-doc-1",
            str(row["retrieved_chunks"]),
        )

    # untraced usage: wrap() outside any active trace() context must not error
    # and must not create a trace.
    untraced_tag = f"phase2-wrap-untraced-{RUN_ID}"
    result = wrapped(f"untraced [{untraced_tag}]")
    check("wrap() outside a trace() context returns result untraced without error", result[0]["doc_id"] == "wrap-doc-1")
    time.sleep(1)
    row = supabase.table("traces").select("id").eq("query", f"untraced [{untraced_tag}]").execute()
    check("wrap() outside a trace() context does not create a trace row", len(row.data) == 0, str(row.data))


def section_d_sdk_error_handling(supabase: Client, fx: Fixtures) -> None:
    print("\n== D. SDK — error handling never raises into caller's code ==")
    from kairos.tracer import KairosTracer

    bad_tracer = KairosTracer(api_key="kai_live_totally_fake", api_url=BASE_URL, pipeline_id=fx.pipeline_id)
    tag = f"phase2-badkey-{RUN_ID}"
    try:
        with bad_tracer.trace(query=f"bad key query [{tag}]") as t:
            t.log_retrieval([{"content": "c", "score": 1.0, "doc_id": "d"}])
            t.log_answer("a")
        check("invalid API key does not raise from the `with` block", True)
    except Exception as exc:
        check("invalid API key does not raise from the `with` block", False, str(exc))

    time.sleep(1)
    row = supabase.table("traces").select("id").eq("query", f"bad key query [{tag}]").execute()
    check("invalid API key results in no persisted trace", len(row.data) == 0, str(row.data))

    other_tracer = KairosTracer(api_key=fx.raw_key, api_url=BASE_URL, pipeline_id=fx.other_pipeline_id)
    tag2 = f"phase2-wrongpipeline-{RUN_ID}"
    try:
        with other_tracer.trace(query=f"wrong pipeline query [{tag2}]") as t:
            t.log_retrieval([{"content": "c", "score": 1.0, "doc_id": "d"}])
            t.log_answer("a")
        check("non-owned pipeline_id does not raise from the `with` block", True)
    except Exception as exc:
        check("non-owned pipeline_id does not raise from the `with` block", False, str(exc))

    time.sleep(1)
    row = supabase.table("traces").select("id").eq("query", f"wrong pipeline query [{tag2}]").execute()
    check("non-owned pipeline_id results in no persisted trace", len(row.data) == 0, str(row.data))

    unreachable_tracer = KairosTracer(
        api_key=fx.raw_key, api_url="http://127.0.0.1:1", pipeline_id=fx.pipeline_id
    )
    try:
        with unreachable_tracer.trace(query="unreachable host query") as t:
            t.log_retrieval([{"content": "c", "score": 1.0, "doc_id": "d"}])
            t.log_answer("a")
        check("unreachable API host does not raise from the `with` block", True)
    except Exception as exc:
        check("unreachable API host does not raise from the `with` block", False, str(exc))


def section_e_langchain_real_roundtrip(supabase: Client, fx: Fixtures) -> None:
    print("\n== E. KairosCallbackHandler — real LangChain RetrievalQA chain (FakeListLLM, zero cost) ==")
    from langchain.chains import RetrievalQA
    from langchain_core.documents import Document
    from langchain_core.language_models.fake import FakeListLLM
    from langchain_core.retrievers import BaseRetriever

    from kairos.integrations.langchain import KairosCallbackHandler

    tag = f"phase2-langchain-{RUN_ID}"

    class FixedRetriever(BaseRetriever):
        def _get_relevant_documents(self, query, *, run_manager=None):
            return [
                Document(
                    page_content="LangChain refund answer source.",
                    metadata={"doc_id": "lc-doc-1", "score": 0.88},
                )
            ]

    handler = KairosCallbackHandler(api_key=fx.raw_key, pipeline_id=fx.pipeline_id, api_url=BASE_URL)
    llm = FakeListLLM(responses=["LangChain-generated refund answer."])
    qa = RetrievalQA.from_chain_type(llm=llm, retriever=FixedRetriever())

    query = f"What is the LangChain refund policy? [{tag}]"
    try:
        result = qa.invoke({"query": query}, config={"callbacks": [handler]})
        check("real RetrievalQA.invoke() ran without error", True)
    except Exception as exc:
        check("real RetrievalQA.invoke() ran without error", False, str(exc))
        return

    check("chain produced expected answer", result.get("result") == "LangChain-generated refund answer.", str(result))

    row = wait_for_row(supabase, "traces", "query", query)
    check("LangChain callback trace landed in Supabase via real API", row is not None, "no row appeared within timeout")
    if row:
        check(
            "persisted final_answer matches LLM output",
            row["final_answer"] == "LangChain-generated refund answer.",
            row["final_answer"],
        )
        check(
            "persisted retrieved_chunks match retriever output",
            row["retrieved_chunks"][0]["doc_id"] == "lc-doc-1",
            str(row["retrieved_chunks"]),
        )


def section_f_packaging() -> None:
    print("\n== F. Packaging — kairoslabs builds and installs cleanly ==")
    sdk_dir = REPO_ROOT / "packages" / "sdk-python"
    dist_dir = REPO_ROOT / "tests" / ".tmp_dist"
    dist_dir.mkdir(exist_ok=True)
    build_proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "build", "--outdir", str(dist_dir)],
        cwd=sdk_dir,
        capture_output=True,
        text=True,
    )
    check("`python -m build` succeeds for kairoslabs", build_proc.returncode == 0, build_proc.stderr[-500:])

    wheels = list(dist_dir.glob("kairoslabs-*.whl"))
    check("wheel artifact produced", len(wheels) > 0, str(list(dist_dir.iterdir())))
    if not wheels:
        return

    clean_venv = REPO_ROOT / "tests" / ".tmp_clean_venv"
    subprocess.run([sys.executable, "-m", "venv", str(clean_venv)], check=True, capture_output=True)
    install_proc = subprocess.run(
        [str(clean_venv / "bin" / "pip"), "install", "--quiet", str(wheels[0])],
        capture_output=True,
        text=True,
    )
    check("wheel installs cleanly into a fresh venv", install_proc.returncode == 0, install_proc.stderr[-500:])

    smoke_proc = subprocess.run(
        [
            str(clean_venv / "bin" / "python"),
            "-c",
            "import kairos; from kairos import KairosTracer; "
            "assert hasattr(kairos, '__version__'); print('OK')",
        ],
        capture_output=True,
        text=True,
    )
    check(
        "installed package imports and exposes public API",
        smoke_proc.returncode == 0 and "OK" in smoke_proc.stdout,
        smoke_proc.stderr[-500:],
    )

    import shutil

    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(clean_venv, ignore_errors=True)


def section_g_redis(env: dict[str, str]) -> None:
    print("\n== G. Upstash Redis reachability (shared infra, sanity check) ==")
    try:
        redis = Redis(url=env["UPSTASH_REDIS_REST_URL"], token=env["UPSTASH_REDIS_REST_TOKEN"])
        key = f"phase2-test-ping-{RUN_ID}"
        redis.set(key, "pong", ex=15)
        val = redis.get(key)
        redis.delete(key)
        check("Upstash Redis reachable (real set/get)", val == "pong", f"got {val!r}")
    except Exception as exc:
        check("Upstash Redis reachable (real set/get)", False, str(exc))


def main() -> int:
    env = load_env()
    supabase = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    fx = Fixtures(supabase)

    log_path = REPO_ROOT / "tests" / ".tmp_phase2_server.log"
    proc = start_server(PORT, log_path)
    try:
        ok, err = wait_for_health(BASE_URL)
        check("API server starts and /health responds", ok, err or f"see {log_path}")
        if not ok:
            print(log_path.read_text() if log_path.exists() else "(no log)")
            return 1

        fx.create()

        section_a_sdk_invalid_inputs(fx)
        section_b_manual_trace_real_roundtrip(supabase, fx)
        section_c_wrap_real_roundtrip(supabase, fx)
        section_d_sdk_error_handling(supabase, fx)
        section_e_langchain_real_roundtrip(supabase, fx)
        section_f_packaging()
        section_g_redis(env)

        if "Traceback (most recent call last)" in log_path.read_text():
            check("no traceback in server log", False, f"see {log_path}")
        else:
            check("no traceback in server log", True)
    finally:
        stop_server(proc)
        log_path.unlink(missing_ok=True)
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
