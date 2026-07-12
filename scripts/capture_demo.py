"""Captures README screenshots + demo GIF from the real dashboard.

Seeds a realistic demo workspace (pipeline, traces, scores, health history,
an alert), drives the production dashboard in Chromium, saves PNGs + a GIF
into docs/assets/, then cleans everything up.

Requires: the API venv (playwright installed), ffmpeg, and built dashboard.
Run: apps/api/.venv/bin/python scripts/capture_demo.py
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright
from supabase import create_client

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
DASH_DIR = REPO_ROOT / "apps" / "dashboard"
ASSETS = REPO_ROOT / "docs" / "assets"
VENV_PYTHON = API_DIR / ".venv" / "bin" / "python"

RUN_ID = secrets.token_hex(3)

QA_PAIRS = [
    ("How do I rotate my API keys?", "Go to Settings -> API keys, create a new key, then revoke the old one. Revocation takes effect immediately.", 0.95, False),
    ("What's the free tier trace limit?", "The free tier includes 10,000 traced queries per month per workspace.", 0.93, False),
    ("Does the SDK add latency to my pipeline?", "No — traces are buffered and sent asynchronously in the background, so your pipeline is never blocked.", 0.96, False),
    ("How long are traces retained?", "Traces are retained for 30 days on the free tier, then purged automatically.", 0.94, False),
    ("Can I self-host Kairos?", "Yes, a Docker Compose stack ships with the repo — see the self-hosting guide.", 0.91, False),
    ("Which frameworks are supported?", "LangChain today via the callback handler, plus any custom retriever through tracer.wrap(). LlamaIndex is coming in Phase 2.", 0.9, False),
    ("How is faithfulness scored?", "Claude Haiku compares your answer against the retrieved chunks and scores how grounded each claim is, from 0 to 1.", 0.92, False),
    ("What triggers a degradation alert?", "A drop of more than 15% in faithfulness versus your 7-day baseline, or a hallucination-rate spike.", 0.95, False),
    ("Do you support workspace seats?", "Every plan includes unlimited seats, SSO, priority support and a dedicated account manager.", 0.3, True),
    ("Is there an on-prem enterprise version?", "Yes, the enterprise tier ships with air-gapped deployment and 24/7 phone support starting at $99.", 0.25, True),
]


def start(cmd, cwd, log):
    return subprocess.Popen(cmd, cwd=cwd, stdout=open(log, "w"), stderr=subprocess.STDOUT,
                            env=os.environ.copy(), preexec_fn=os.setsid)


def stop(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=8)
    except Exception:
        pass


def main() -> int:
    env = {k: v for k, v in dotenv_values(API_DIR / ".env").items() if v}
    os.environ.update(env)
    admin = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    ASSETS.mkdir(parents=True, exist_ok=True)

    email = f"demo-{RUN_ID}@kairos.dev"
    password = secrets.token_urlsafe(14)
    user = admin.auth.admin.create_user({"email": email, "password": password, "email_confirm": True}).user
    now = datetime.now(timezone.utc)

    pipeline = admin.table("pipelines").insert(
        {"user_id": user.id, "name": "docs-assistant-prod", "framework": "langchain"}
    ).execute().data[0]["id"]

    trace_rows = []
    for i, (q, a, _, _) in enumerate(QA_PAIRS):
        trace_rows.append({
            "pipeline_id": pipeline, "user_id": user.id, "query": q,
            "retrieved_chunks": [
                {"content": "Kairos retains traces for 30 days on the free tier. API keys are managed from Settings and revocation is immediate.", "score": 0.89, "doc_id": f"docs/faq.md#{i}", "metadata": None},
                {"content": "The SDK buffers traces and flushes them asynchronously; ingestion is rate limited per key.", "score": 0.83, "doc_id": "docs/sdk.md#batching", "metadata": None},
            ],
            "final_answer": a, "latency_ms": 180 + 17 * i, "token_count_input": 640 + 12 * i,
            "token_count_output": 60 + 5 * i, "eval_status": "completed",
            "created_at": (now - timedelta(hours=2 * i + 1)).isoformat(),
        })
    trace_ids = [r["id"] for r in admin.table("traces").insert(trace_rows).execute().data]

    eval_rows = []
    for tid, (q, a, faith, halluc) in zip(trace_ids, QA_PAIRS):
        eval_rows.append({
            "trace_id": tid, "pipeline_id": pipeline,
            "faithfulness": faith, "answer_relevance": min(0.97, faith + 0.03),
            "context_precision": max(0.4, faith - 0.05),
            "hallucination_flag": halluc,
            "hallucination_detail": "Claims about pricing tiers, SSO and phone support are not present in any retrieved chunk." if halluc else None,
            "failure_category": "model" if halluc else None,
            "failure_reason": "The model invented enterprise plan details instead of using the retrieved context." if halluc else None,
            "computed_at": now.isoformat(),
        })
    admin.table("eval_scores").insert(eval_rows).execute()

    health_rows = []
    for d in range(14, 0, -1):
        score = 91 - (6 if d < 3 else 0) + (d % 3)
        health_rows.append({
            "pipeline_id": pipeline, "date": (now - timedelta(days=d)).date().isoformat(),
            "health_score": score, "avg_faithfulness": round(score / 100, 2),
            "avg_answer_relevance": round(score / 100 + 0.02, 2),
            "avg_context_precision": round(score / 100 - 0.03, 2),
            "eval_count": 40 + d, "hallucination_count": 2 if d < 3 else 0,
        })
    admin.table("pipeline_health_daily").insert(health_rows).execute()

    admin.table("alerts").insert({
        "user_id": user.id, "pipeline_id": pipeline, "alert_type": "hallucination_spike",
        "severity": "critical",
        "message": "Hallucination rate spiked to 20% over the last 24h (baseline 2%).",
        "metric_before": 0.02, "metric_after": 0.2,
    }).execute()

    api_log, dash_log = "/tmp/demo_api.log", "/tmp/demo_dash.log"
    api = start([str(VENV_PYTHON), "-m", "uvicorn", "main:app", "--port", "8000"], API_DIR, api_log)
    dash = start(["npm", "run", "start", "--", "-p", "3000"], DASH_DIR, dash_log)
    time.sleep(6)

    video_dir = Path("/tmp/demo_video")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                      record_video_dir=str(video_dir),
                                      record_video_size={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.set_default_timeout(20000)

            page.goto("http://localhost:3000/login")
            page.wait_for_load_state("networkidle")
            page.fill('input[name="email"]', email)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_url("http://localhost:3000/")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(ASSETS / "overview.png"))

            page.goto("http://localhost:3000/traces")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            page.screenshot(path=str(ASSETS / "traces.png"))

            bad_trace = trace_ids[8]
            page.goto(f"http://localhost:3000/traces/{bad_trace}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            page.screenshot(path=str(ASSETS / "trace-detail.png"))

            page.goto(f"http://localhost:3000/pipelines/{pipeline}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(ASSETS / "pipeline-health.png"))

            page.wait_for_timeout(500)
            ctx.close()
            video_path = list(video_dir.glob("*.webm"))[0]
            browser.close()

        gif = ASSETS / "demo.gif"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            "-loop", "0", str(gif),
        ], check=True, capture_output=True)
        print("assets written:", sorted(p.name for p in ASSETS.iterdir()))
    finally:
        stop(api)
        stop(dash)
        admin.table("alerts").delete().eq("user_id", user.id).execute()
        admin.table("pipelines").delete().eq("user_id", user.id).execute()
        admin.table("profiles").delete().eq("id", user.id).execute()
        admin.auth.admin.delete_user(user.id)
        print("demo data cleaned up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
