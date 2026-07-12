"""Phase 1, Week 4 verification suite — Next.js dashboard.

Real end-to-end: production `next build`, real `next start` + real FastAPI
server, real Supabase (RLS-scoped reads through the browser), real Upstash,
and a real Chromium browser (Playwright) driving the UI: login, overview,
pipeline creation, trace explorer + detail, API key lifecycle from the
settings page, alert resolve flow, sign-out. The alert rows themselves are
produced by the real services/alerting.py logic running over seeded eval
history (no Haiku needed — alerting reads eval_scores).

Run with the api's own virtualenv, from anywhere:
    apps/api/.venv/bin/python tests/phase_4_test.py
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

import httpx
from dotenv import dotenv_values
from playwright.sync_api import expect, sync_playwright
from supabase import Client, create_client

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
DASH_DIR = REPO_ROOT / "apps" / "dashboard"
VENV_PYTHON = API_DIR / ".venv" / "bin" / "python"
sys.path.insert(0, str(API_DIR))

RUN_ID = secrets.token_hex(4)
API_PORT = 8000  # NEXT_PUBLIC_API_URL is baked into the build as localhost:8000
DASH_PORT = 3000  # must match the API's FRONTEND_URL for CORS
API_URL = f"http://127.0.0.1:{API_PORT}"
DASH_URL = f"http://localhost:{DASH_PORT}"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not condition:
        line += f" — {detail}"
    print(line)


def load_env() -> dict[str, str]:
    values = {k: v for k, v in dotenv_values(API_DIR / ".env").items() if v is not None}
    for k, v in values.items():
        os.environ[k] = v
    return values


def start_process(cmd: list[str], cwd: Path, log_path: Path, env: dict | None = None) -> subprocess.Popen:
    log_file = open(log_path, "w")
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, **(env or {})},
        preexec_fn=os.setsid,
    )


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=8)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def wait_for_http(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0, follow_redirects=False).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


class Fixtures:
    def __init__(self, admin: Client):
        self.admin = admin
        self.email = f"phase4-{RUN_ID}@example.com"
        self.password = f"pw-{secrets.token_urlsafe(12)}"
        result = admin.auth.admin.create_user(
            {"email": self.email, "password": self.password, "email_confirm": True}
        )
        self.user_id = result.user.id

        pipeline = (
            admin.table("pipelines")
            .insert({"user_id": self.user_id, "name": f"seeded-pipeline-{RUN_ID}", "framework": "langchain"})
            .execute()
        )
        self.pipeline_id = pipeline.data[0]["id"]
        self.pipeline_name = f"seeded-pipeline-{RUN_ID}"
        self.good_trace_id: str | None = None
        self.bad_trace_id: str | None = None

    def seed_traces_and_scores(self) -> None:
        """Seeds a good and a bad (hallucinated) scored trace for UI checks,
        plus enough history for the alerting baseline/recent windows."""
        now = datetime.now(timezone.utc)

        def trace_row(query: str, answer: str, created: datetime) -> dict:
            return {
                "pipeline_id": self.pipeline_id,
                "user_id": self.user_id,
                "query": query,
                "retrieved_chunks": [
                    {"content": "Refunds are issued within 30 days.", "score": 0.91, "doc_id": "kb-refunds-1", "metadata": None}
                ],
                "final_answer": answer,
                "latency_ms": 240,
                "token_count_input": 100,
                "token_count_output": 20,
                "eval_status": "completed",
                "created_at": created.isoformat(),
            }

        # 12 baseline traces (2-7 days old, healthy) + 6 recent bad ones (last 12h)
        baseline_rows = [
            trace_row(f"baseline question {i} [{RUN_ID}]", "Refunds within 30 days.", now - timedelta(days=2 + (i % 6), hours=i))
            for i in range(12)
        ]
        recent_rows = [
            trace_row(f"recent degraded question {i} [{RUN_ID}]", "You get a free yacht with every order.", now - timedelta(hours=1 + i))
            for i in range(6)
        ]
        inserted = self.admin.table("traces").insert(baseline_rows + recent_rows).execute()
        trace_ids = [r["id"] for r in inserted.data]
        baseline_ids, recent_ids = trace_ids[:12], trace_ids[12:]
        self.good_trace_id = baseline_ids[0]
        self.bad_trace_id = recent_ids[0]

        eval_rows = []
        for i, tid in enumerate(baseline_ids):
            eval_rows.append(
                {
                    "trace_id": tid,
                    "pipeline_id": self.pipeline_id,
                    "faithfulness": 0.92,
                    "answer_relevance": 0.9,
                    "context_precision": 0.88,
                    "hallucination_flag": False,
                    "computed_at": (now - timedelta(days=2 + (i % 6), hours=i)).isoformat(),
                }
            )
        for i, tid in enumerate(recent_ids):
            eval_rows.append(
                {
                    "trace_id": tid,
                    "pipeline_id": self.pipeline_id,
                    "faithfulness": 0.35,
                    "answer_relevance": 0.4,
                    "context_precision": 0.5,
                    "hallucination_flag": True,
                    "hallucination_detail": "The yacht claim is not supported by any retrieved chunk.",
                    "failure_category": "model",
                    "failure_reason": "Model fabricated a promotion not present in the context.",
                    "computed_at": (now - timedelta(hours=1 + i)).isoformat(),
                }
            )
        self.admin.table("eval_scores").insert(eval_rows).execute()

    def cleanup(self) -> None:
        try:
            # alerts first: pre-0004 schemas have no ON DELETE CASCADE on alerts.pipeline_id
            self.admin.table("alerts").delete().eq("user_id", self.user_id).execute()
            self.admin.table("pipelines").delete().eq("user_id", self.user_id).execute()
            self.admin.table("profiles").delete().eq("id", self.user_id).execute()
            self.admin.auth.admin.delete_user(self.user_id)
        except Exception as exc:
            print(f"WARNING: cleanup incomplete: {exc}")


def run_alerting_over_seeded_history(fx: Fixtures) -> tuple[list[str], list[str]]:
    """Runs the REAL alerting service twice (create + dedupe pass) inside one
    event loop — the API's async Supabase client is cached per-loop, so both
    calls must share the loop."""
    from db.supabase import get_supabase
    from services.alerting import check_pipeline_alerts

    async def run() -> tuple[list[str], list[str]]:
        supabase = await get_supabase()
        first = await check_pipeline_alerts(supabase, fx.pipeline_id, fx.user_id, skip_cooldown=True)
        second = await check_pipeline_alerts(supabase, fx.pipeline_id, fx.user_id, skip_cooldown=True)
        return first, second

    return asyncio.run(run())


def main() -> int:  # noqa: PLR0915
    env = load_env()
    admin = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

    # -- production build ---------------------------------------------------
    print("== Build ==")
    build = subprocess.run(
        ["npm", "run", "build"], cwd=DASH_DIR, capture_output=True, text=True, timeout=600
    )
    check("`next build` (production) succeeds", build.returncode == 0, build.stdout[-800:] + build.stderr[-400:])
    if build.returncode != 0:
        return 1

    fx = Fixtures(admin)
    fx.seed_traces_and_scores()

    api_log = REPO_ROOT / "tests" / ".tmp_p4_api.log"
    dash_log = REPO_ROOT / "tests" / ".tmp_p4_dash.log"
    api_proc = start_process(
        [str(VENV_PYTHON), "-m", "uvicorn", "main:app", "--port", str(API_PORT)], API_DIR, api_log
    )
    dash_proc = start_process(["npm", "run", "start", "--", "-p", str(DASH_PORT)], DASH_DIR, dash_log)

    try:
        check("FastAPI server up", wait_for_http(f"{API_URL}/health"), f"see {api_log}")
        check("Next.js production server up", wait_for_http(f"{DASH_URL}/login"), f"see {dash_log}")

        # -- alerting over seeded history (real service logic) ---------------
        print("\n== Alerting service over seeded history ==")
        created, dedupe = run_alerting_over_seeded_history(fx)
        check("faithfulness_drop alert created", "faithfulness_drop" in created, str(created))
        check("hallucination_spike alert created", "hallucination_spike" in created, str(created))
        check("re-running creates no duplicate alerts (unresolved dedupe)", dedupe == [], str(dedupe))

        # -- browser E2E ------------------------------------------------------
        print("\n== Browser E2E (Chromium) ==")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(20_000)

            # unauthenticated -> redirected to /login
            page.goto(DASH_URL)
            page.wait_for_url("**/login")
            check("unauthenticated / redirects to /login", "/login" in page.url, page.url)

            # login with real credentials
            page.fill('input[name="email"]', fx.email)
            page.fill('input[name="password"]', fx.password)
            page.click('button[type="submit"]')
            page.wait_for_url(DASH_URL + "/")
            expect(page.get_by_role("heading", name="Overview")).to_be_visible()
            check("login lands on Overview", True)

            # overview: stats + alerts + recent traces
            expect(page.get_by_text("Traces this month")).to_be_visible()
            has_alert = page.get_by_text("Faithfulness dropped", exact=False).first.is_visible()
            check("overview shows the faithfulness_drop alert", has_alert)
            check(
                "overview lists a seeded recent trace",
                page.get_by_text(f"recent degraded question", exact=False).first.is_visible(),
            )

            # resolve the hallucination_spike alert via UI (RLS-scoped update)
            spike_item = page.locator("li", has=page.get_by_text("Hallucination rate spiked", exact=False))
            spike_item.get_by_role("button", name="Resolve").click()
            expect(page.get_by_text("Hallucination rate spiked", exact=False)).to_have_count(0)
            check("alert resolve via UI removes it from the list", True)
            resolved_row = (
                admin.table("alerts")
                .select("resolved")
                .eq("pipeline_id", fx.pipeline_id)
                .eq("alert_type", "hallucination_spike")
                .execute()
            )
            check(
                "alert resolve persisted to DB through RLS",
                bool(resolved_row.data) and resolved_row.data[0]["resolved"] is True,
                str(resolved_row.data),
            )

            # pipelines: create via UI
            page.goto(f"{DASH_URL}/pipelines")
            new_name = f"ui-created-{RUN_ID}"
            page.fill('input[name="pipeline-name"]', new_name)
            page.click('button:has-text("Create pipeline")')
            expect(page.get_by_text(new_name)).to_be_visible()
            check("pipeline created through the UI (direct Supabase insert under RLS)", True)
            ui_pipeline = (
                admin.table("pipelines").select("user_id").eq("name", new_name).execute()
            )
            check(
                "UI-created pipeline persisted with correct owner",
                bool(ui_pipeline.data) and ui_pipeline.data[0]["user_id"] == fx.user_id,
                str(ui_pipeline.data),
            )

            # pipeline detail: summary + charts sections render with data
            page.get_by_role("link", name=fx.pipeline_name).click()
            page.wait_for_url("**/pipelines/**")
            expect(page.get_by_text("Faithfulness (recent)")).to_be_visible()
            expect(page.get_by_text("Eval scores (last 100 traces)")).to_be_visible()
            check("pipeline detail renders summary + score timeline", True)
            check(
                "pipeline detail shows failure category breakdown",
                page.get_by_text("model:", exact=False).first.is_visible(),
            )

            # traces explorer: seeded rows + status filter
            page.goto(f"{DASH_URL}/traces")
            expect(page.get_by_text(f"baseline question 0 [{RUN_ID}]")).to_be_visible()
            check("trace explorer lists seeded traces", True)
            page.get_by_role("link", name="completed", exact=True).click()
            page.wait_for_url("**status=completed**")
            check("status filter updates the URL/query", "status=completed" in page.url, page.url)
            check(
                "hallucination badge shown on degraded trace rows",
                page.get_by_text("hallucination", exact=True).first.is_visible(),
            )

            # trace detail: full content for the bad trace
            page.goto(f"{DASH_URL}/traces/{fx.bad_trace_id}")
            expect(page.get_by_text("Hallucination detected")).to_be_visible()
            expect(page.get_by_text("The yacht claim is not supported", exact=False)).to_be_visible()
            expect(page.get_by_text("Root cause: model")).to_be_visible()
            expect(page.get_by_text("Refunds are issued within 30 days.")).to_be_visible()
            check("trace detail shows scores, hallucination detail, root cause, and chunks", True)

            # settings: API key lifecycle through the UI -> real API -> real ingest
            page.goto(f"{DASH_URL}/settings")
            page.fill('input[name="key-name"]', "e2e key")
            page.click('button:has-text("Create key")')
            fresh_key = page.get_by_test_id("fresh-key").inner_text().strip()
            check("settings creates a key via FastAPI (JWT auth)", fresh_key.startswith("kai_live_"), fresh_key[:16])

            ingest = httpx.post(
                f"{API_URL}/v1/traces",
                json={
                    "pipeline_id": fx.pipeline_id,
                    "query": f"ui key ingest [{RUN_ID}]",
                    "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
                    "final_answer": "a",
                    "latency_ms": 5,
                },
                headers={"Authorization": f"Bearer {fresh_key}"},
            )
            check("UI-created key ingests a real trace -> 202", ingest.status_code == 202, f"{ingest.status_code}: {ingest.text}")

            page.get_by_role("button", name="Revoke").first.click()
            expect(page.get_by_text("revoked").first).to_be_visible()
            after = httpx.post(
                f"{API_URL}/v1/traces",
                json={
                    "pipeline_id": fx.pipeline_id,
                    "query": "post revoke",
                    "retrieved_chunks": [{"content": "c", "score": 1.0, "doc_id": "d"}],
                    "final_answer": "a",
                    "latency_ms": 5,
                },
                headers={"Authorization": f"Bearer {fresh_key}"},
            )
            check("revoked-from-UI key immediately rejected", after.status_code == 401, str(after.status_code))

            # sign out
            page.get_by_role("button", name="Sign out").click()
            page.wait_for_url("**/login")
            check("sign out returns to /login", "/login" in page.url, page.url)

            # signed-out access blocked again
            page.goto(f"{DASH_URL}/traces")
            page.wait_for_url("**/login")
            check("signed-out user cannot reach /traces", "/login" in page.url, page.url)

            browser.close()

        for log in (api_log, dash_log):
            content = log.read_text()
            check(
                f"no traceback in {log.name}",
                "Traceback (most recent call last)" not in content,
                f"see {log}",
            )
    finally:
        stop_process(api_proc)
        stop_process(dash_proc)
        api_log.unlink(missing_ok=True)
        dash_log.unlink(missing_ok=True)
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
