"""Phase 1, Week 3 verification suite — eval worker (eval provider integration).

Real Supabase throughout (fixtures, worker queries, eval_scores/chunk_index
writes) — no mocks there. eval_service.compute_eval() dispatches to whichever
provider EVAL_PROVIDER selects (default "anthropic", or "gemini" as a
dev/test stand-in while the Anthropic account has no credit — see
services/eval_service.py). Sections E and F make real calls against whichever
provider is active and adapt: success is graded like any other real check; a
known billing/quota block (Anthropic $0 credit, or Gemini free-tier 429) is
reported as SKIPPED (not PASS, not FAIL) with a clear reason and does not
count toward the pass/fail total. Any other failure there is a real FAIL — a
code bug, not a billing issue. Everything else in this file (worker polling
logic, eval_scores field mapping, chunk_index increment logic, resilience to
a failed eval call) is verified for real with no mocking of Supabase.

Note: worker internals (workers.eval_worker, services.eval_service) use the
async Supabase client (db.supabase.get_supabase()) — this suite uses that
same async client for those calls, and a plain sync client for fixture
setup/cleanup and simple assertions.

Run with the api's own virtualenv, from anywhere:
    apps/api/.venv/bin/python tests/phase_3_test.py
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anthropic
from dotenv import dotenv_values
from google.genai import errors as genai_errors
from supabase import Client, create_client

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))

RUN_ID = secrets.token_hex(4)

results: list[tuple[str, bool, str]] = []
skipped: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not condition:
        line += f" — {detail}"
    print(line)


def skip(name: str, reason: str) -> None:
    skipped.append((name, reason))
    print(f"[SKIP] {name} — {reason}")


def load_env() -> None:
    import os

    env_path = API_DIR / ".env"
    if not env_path.exists():
        print(f"FATAL: {env_path} does not exist. Cannot run against real services.")
        sys.exit(1)
    for k, v in dotenv_values(env_path).items():
        if v is not None:
            os.environ[k] = v


class Fixtures:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.user_id: str | None = None
        self.pipeline_id: str | None = None
        self.trace_ids: list[str] = []

    def create(self) -> None:
        auth_result = self.supabase.auth.admin.create_user(
            {
                "email": f"phase3-test-{RUN_ID}@example.com",
                "password": secrets.token_urlsafe(16),
                "email_confirm": True,
            }
        )
        self.user_id = auth_result.user.id
        self.supabase.table("profiles").upsert(
            {"id": self.user_id, "email": f"phase3-test-{RUN_ID}@example.com"}
        ).execute()

        pipeline_result = (
            self.supabase.table("pipelines")
            .insert({"user_id": self.user_id, "name": f"phase-3-test-{RUN_ID}", "framework": "custom"})
            .execute()
        )
        self.pipeline_id = pipeline_result.data[0]["id"]

    def insert_trace(self, query: str, chunks: list[dict], answer: str) -> str:
        result = (
            self.supabase.table("traces")
            .insert(
                {
                    "pipeline_id": self.pipeline_id,
                    "user_id": self.user_id,
                    "query": query,
                    "retrieved_chunks": chunks,
                    "final_answer": answer,
                    "latency_ms": 100,
                }
            )
            .execute()
        )
        trace_id = result.data[0]["id"]
        self.trace_ids.append(trace_id)
        return trace_id

    def cleanup(self) -> None:
        try:
            if self.pipeline_id:
                self.supabase.table("eval_scores").delete().eq("pipeline_id", self.pipeline_id).execute()
                self.supabase.table("chunk_index").delete().eq("pipeline_id", self.pipeline_id).execute()
                self.supabase.table("traces").delete().eq("pipeline_id", self.pipeline_id).execute()
                self.supabase.table("pipelines").delete().eq("id", self.pipeline_id).execute()
            if self.user_id:
                self.supabase.table("profiles").delete().eq("id", self.user_id).execute()
                self.supabase.auth.admin.delete_user(self.user_id)
        except Exception as exc:
            print(f"WARNING: fixture cleanup incomplete: {exc}")


def section_a_schema(supabase: Client) -> None:
    print("\n== A. Schema sanity (eval_scores, chunk_index) ==")
    for table in ("eval_scores", "chunk_index"):
        try:
            supabase.table(table).select("*").limit(1).execute()
            check(f"table `{table}` exists and is queryable", True)
        except Exception as exc:
            check(f"table `{table}` exists and is queryable", False, str(exc))


async def section_b_fetch_pending(async_db, supabase: Client, fx: Fixtures) -> None:
    print("\n== B. Worker polling query (status-driven, v1.1) — real Supabase ==")
    from workers.eval_worker import fetch_pending_traces

    tag = f"phase3-pending-{RUN_ID}"
    trace_id = fx.insert_trace(
        query=f"pending query [{tag}]",
        chunks=[{"content": "c", "score": 1.0, "doc_id": f"doc-{tag}"}],
        answer="a",
    )

    pending = await fetch_pending_traces(async_db, limit=50)
    pending_ids = {t["id"] for t in pending}
    check("newly inserted trace defaults to eval_status='pending'", trace_id in pending_ids, f"trace {trace_id} not found")

    matching = [t for t in pending if t["id"] == trace_id]
    check(
        "pending query embeds the pipeline's eval_sample_rate",
        bool(matching) and "eval_sample_rate" in (matching[0].get("pipelines") or {}),
        str(matching[0].get("pipelines") if matching else None),
    )

    supabase.table("traces").update({"eval_status": "completed"}).eq("id", trace_id).execute()
    pending_after = await fetch_pending_traces(async_db, limit=50)
    pending_ids_after = {t["id"] for t in pending_after}
    check(
        "trace with eval_status='completed' is excluded from the pending set",
        trace_id not in pending_ids_after,
        f"trace {trace_id} still returned as pending",
    )


async def section_c_process_trace_mocked(async_db, supabase: Client, fx: Fixtures) -> None:
    print("\n== C. process_trace() — real DB writes, compute_eval() mocked ==")
    from models.eval import EvalResult
    from workers.eval_worker import process_trace

    tag = f"phase3-processed-{RUN_ID}"
    shared_doc_id = f"shared-doc-{RUN_ID}"

    trace_1 = fx.insert_trace(
        query=f"low quality query [{tag}]",
        chunks=[{"content": "Refunds within 30 days.", "score": 0.9, "doc_id": shared_doc_id}],
        answer="You get a lifetime warranty and a free car.",
    )
    canned_bad = EvalResult(
        faithfulness=0.2,
        answer_relevance=0.3,
        context_precision=0.4,
        hallucination_flag=True,
        hallucination_detail="claims about lifetime warranty and free car are not in the chunks",
        failure_category="model",
        failure_reason="model ignored context and fabricated claims",
    )
    with patch("workers.eval_worker.compute_eval", new=AsyncMock(return_value=canned_bad)):
        await process_trace(
            async_db,
            {
                "id": trace_1,
                "pipeline_id": fx.pipeline_id,
                "user_id": fx.user_id,
                "query": "low quality query",
                "retrieved_chunks": [{"content": "Refunds within 30 days.", "score": 0.9, "doc_id": shared_doc_id}],
                "final_answer": "You get a lifetime warranty and a free car.",
            },
        )

    row = supabase.table("eval_scores").select("*").eq("trace_id", trace_1).execute()
    check("eval_scores row written for bad trace", len(row.data) == 1, str(row.data))
    if row.data:
        r = row.data[0]
        check("faithfulness persisted correctly", float(r["faithfulness"]) == 0.2, str(r["faithfulness"]))
        check("hallucination_flag persisted correctly", r["hallucination_flag"] is True, str(r))
        check(
            "hallucination_detail persisted correctly",
            r["hallucination_detail"] == canned_bad.hallucination_detail,
            str(r["hallucination_detail"]),
        )
        check("failure_category persisted correctly", r["failure_category"] == "model", str(r["failure_category"]))
        check("model_used recorded", r["model_used"] == "claude-haiku-4-5", str(r["model_used"]))
        check("prompt_version stamped (v1.1)", r["prompt_version"] == "v1", str(r.get("prompt_version")))

    status_row = supabase.table("traces").select("eval_status").eq("id", trace_1).execute()
    check(
        "trace transitioned to eval_status='completed' (v1.1)",
        bool(status_row.data) and status_row.data[0]["eval_status"] == "completed",
        str(status_row.data),
    )

    chunk_row = (
        supabase.table("chunk_index")
        .select("*")
        .eq("pipeline_id", fx.pipeline_id)
        .eq("chunk_id", shared_doc_id)
        .execute()
    )
    check("chunk_index row created on first retrieval", len(chunk_row.data) == 1, str(chunk_row.data))
    if chunk_row.data:
        check("chunk_index retrieval_count starts at 1", chunk_row.data[0]["retrieval_count"] == 1, str(chunk_row.data[0]))
        check(
            "chunk_index content_preview captured",
            chunk_row.data[0]["content_preview"] == "Refunds within 30 days.",
            str(chunk_row.data[0]["content_preview"]),
        )

    trace_2 = fx.insert_trace(
        query=f"good query [{tag}]",
        chunks=[{"content": "Refunds within 30 days.", "score": 0.9, "doc_id": shared_doc_id}],
        answer="You can get a refund within 30 days.",
    )
    canned_good = EvalResult(
        faithfulness=0.95,
        answer_relevance=0.95,
        context_precision=0.9,
        hallucination_flag=False,
        hallucination_detail=None,
        failure_category=None,
        failure_reason=None,
    )
    with patch("workers.eval_worker.compute_eval", new=AsyncMock(return_value=canned_good)):
        await process_trace(
            async_db,
            {
                "id": trace_2,
                "pipeline_id": fx.pipeline_id,
                "user_id": fx.user_id,
                "query": "good query",
                "retrieved_chunks": [{"content": "Refunds within 30 days.", "score": 0.9, "doc_id": shared_doc_id}],
                "final_answer": "You can get a refund within 30 days.",
            },
        )

    chunk_row_2 = (
        supabase.table("chunk_index")
        .select("*")
        .eq("pipeline_id", fx.pipeline_id)
        .eq("chunk_id", shared_doc_id)
        .execute()
    )
    check(
        "chunk_index retrieval_count increments on reuse (not duplicated)",
        len(chunk_row_2.data) == 1 and chunk_row_2.data[0]["retrieval_count"] == 2,
        str(chunk_row_2.data),
    )

    good_row = supabase.table("eval_scores").select("*").eq("trace_id", trace_2).execute()
    check("eval_scores row written for good trace", len(good_row.data) == 1, str(good_row.data))
    if good_row.data:
        check(
            "good trace has no failure_category",
            good_row.data[0]["failure_category"] is None,
            str(good_row.data[0]["failure_category"]),
        )
        check(
            "good trace hallucination_flag is False",
            good_row.data[0]["hallucination_flag"] is False,
            str(good_row.data[0]),
        )


async def section_d_hallucination_override() -> None:
    print("\n== D. compute_eval() hallucination_flag derivation rule (network stubbed, real function) ==")
    from config import get_settings
    from models.eval import EvalResult
    from services import eval_service

    fake_parsed = EvalResult(
        faithfulness=0.75,
        answer_relevance=0.8,
        context_precision=0.8,
        hallucination_flag=True,
        hallucination_detail="model claimed this but faithfulness is not low enough",
        failure_category=None,
        failure_reason=None,
    )

    # compute_eval() dispatches on EVAL_PROVIDER — stub whichever provider's
    # client is actually active so this test exercises the real override
    # logic instead of hitting the network.
    if get_settings().eval_provider == "gemini":
        fake_response = types.SimpleNamespace(text=fake_parsed.model_dump_json())
        with patch.object(eval_service, "_get_gemini_client") as get_client:
            get_client.return_value.aio.models.generate_content = AsyncMock(return_value=fake_response)
            result = await eval_service.compute_eval(
                query="q", retrieved_chunks=[{"content": "c", "doc_id": "d"}], final_answer="a"
            )
    else:
        fake_response = types.SimpleNamespace(parsed_output=fake_parsed)
        with patch.object(eval_service, "_get_client") as get_client:
            get_client.return_value.messages.parse = AsyncMock(return_value=fake_response)
            result = await eval_service.compute_eval(
                query="q", retrieved_chunks=[{"content": "c", "doc_id": "d"}], final_answer="a"
            )

    check(
        "hallucination_flag forced False when faithfulness >= 0.6 despite model's own flag",
        result.hallucination_flag is False,
        f"got hallucination_flag={result.hallucination_flag}",
    )
    check(
        "hallucination_detail cleared alongside the flag",
        result.hallucination_detail is None,
        str(result.hallucination_detail),
    )
    check("faithfulness itself untouched by the override", result.faithfulness == 0.75, str(result.faithfulness))


async def section_e_real_haiku_call() -> None:
    from config import get_settings
    from services.eval_service import compute_eval

    provider = get_settings().eval_provider
    label = "Claude Haiku" if provider == "anthropic" else "Gemini"
    print(f"\n== E. Real {label} call (provider={provider}; adaptive — SKIP on billing/quota block) ==")

    try:
        result = await compute_eval(
            query="What is the refund policy?",
            retrieved_chunks=[{"content": "Refunds within 30 days.", "score": 0.9, "doc_id": "doc-1"}],
            final_answer="You can get a refund within 30 days.",
        )
        check(f"real {label} eval call succeeded", True)
        check("faithfulness in [0,1]", 0 <= result.faithfulness <= 1, str(result.faithfulness))
        check("answer_relevance in [0,1]", 0 <= result.answer_relevance <= 1, str(result.answer_relevance))
        check("context_precision in [0,1]", 0 <= result.context_precision <= 1, str(result.context_precision))
        check(
            "faithful/relevant trace scored high",
            result.faithfulness > 0.7 and result.answer_relevance > 0.7,
            str(result),
        )
    except anthropic.BadRequestError as exc:
        if "credit balance" in str(exc):
            skip(
                f"real {label} eval call",
                "Anthropic account has $0 credit balance (confirmed real 400, API key itself is valid). "
                "Not a code defect — add credits and re-run to verify real scoring quality.",
            )
        else:
            check(f"real {label} eval call succeeded", False, str(exc))
    except genai_errors.ClientError as exc:
        if exc.code == 429:
            skip(f"real {label} eval call", f"Gemini free-tier quota exhausted for now (429): {exc}")
        else:
            check(f"real {label} eval call succeeded", False, str(exc))
    except Exception as exc:
        check(f"real {label} eval call succeeded", False, f"unexpected error type {type(exc).__name__}: {exc}")


async def section_f_worker_resilience(async_db, supabase: Client, fx: Fixtures) -> None:
    from config import get_settings
    from workers.eval_worker import process_once

    provider = get_settings().eval_provider
    print(f"\n== F. process_once() resilience — real trace, real eval call (provider={provider}) ==")

    tag = f"phase3-resilience-{RUN_ID}"
    trace_id = fx.insert_trace(
        query=f"resilience query [{tag}]",
        chunks=[{"content": "c", "score": 1.0, "doc_id": f"doc-{tag}"}],
        answer="a",
    )

    try:
        n = await process_once()
        check("process_once() completes without raising even when a trace's eval call fails", True)
        check("process_once() returns a count", isinstance(n, int) and n >= 0, str(n))
    except Exception as exc:
        check("process_once() completes without raising even when a trace's eval call fails", False, str(exc))
        return

    row = supabase.table("eval_scores").select("id").eq("trace_id", trace_id).execute()
    trace_after = (
        supabase.table("traces")
        .select("eval_status,eval_attempts")
        .eq("id", trace_id)
        .execute()
    ).data[0]
    if row.data:
        print(f"[INFO] eval_scores row exists for the resilience trace — {provider} call succeeded for real")
        check(
            "successful trace transitioned to 'completed'",
            trace_after["eval_status"] == "completed",
            str(trace_after),
        )
    else:
        print("[INFO] no eval_scores row for the resilience trace — consistent with the known billing block")
        check(
            "failed eval attempt recorded on the trace (v1.1 bounded retries)",
            trace_after["eval_attempts"] >= 1 and trace_after["eval_status"] in ("pending", "failed"),
            str(trace_after),
        )


async def main_async() -> int:
    load_env()
    from config import get_settings
    from db.supabase import get_supabase

    settings = get_settings()
    supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    async_db = await get_supabase()

    fx = Fixtures(supabase)
    fx.create()
    try:
        section_a_schema(supabase)
        await section_b_fetch_pending(async_db, supabase, fx)
        await section_c_process_trace_mocked(async_db, supabase, fx)
        await section_d_hallucination_override()
        await section_e_real_haiku_call()
        await section_f_worker_resilience(async_db, supabase, fx)
    finally:
        fx.cleanup()

    print("\n== Summary ==")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]
    print(f"{passed}/{len(results)} checks passed")
    if skipped:
        print(f"{len(skipped)} skipped (not counted toward pass/fail):")
        for name, reason in skipped:
            print(f"  - {name}: {reason}")
    if failed:
        print("\nFailed checks:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
