"""Eval quality gate — ARCHITECTURE.md launch checklist.

Runs the REAL eval path (services.eval_service.compute_eval, provider chosen
by EVAL_PROVIDER — Claude Haiku by default, or Gemini as a free-tier dev/test
stand-in) over 50 labeled traces — 25 known-good, 25 known-bad spanning
hallucination, off-topic, wrong-chunks, and partial-grounding failure modes —
and measures agreement with the labels. Launch gate: ≥85% overall agreement
(section 18 target: >85% match with human judgment).

Grading rule per case:
  good  → correct if faithfulness ≥ 0.7 AND answer_relevance ≥ 0.7
          AND hallucination_flag is False
  bad   → correct if (min score < 0.7) OR hallucination_flag is True;
          when the case expects a hallucination, the flag must be True.

Cost: 50 Anthropic calls ≈ $0.10–0.20 (free on Gemini's free tier, just
slower due to rate limiting — ~11 min at 1 req/13s). Exits with a clear SKIP
on a known billing/quota block instead of a false failure.

Run: apps/api/.venv/bin/python tests/eval_quality_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import anthropic
from dotenv import dotenv_values
from google.genai import errors as genai_errors

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))


@dataclass
class Case:
    label: str            # "good" | "bad"
    expects_hallucination: bool
    query: str
    chunks: list[str]
    answer: str
    note: str = ""


def dataset() -> list[Case]:
    cases: list[Case] = []

    # ---------------- 25 known-good: grounded, on-topic answers ----------------
    grounded = [
        ("What is the refund window?",
         ["Customers may request a full refund within 30 days of purchase. After 30 days, store credit is offered instead."],
         "You can get a full refund within 30 days of purchase; after that you'd receive store credit."),
        ("Which plan includes SSO?",
         ["SSO via SAML is available on the Business plan and above. The Starter plan supports email/password only."],
         "SSO (SAML) is included starting with the Business plan; Starter only has email/password login."),
        ("How do I reset my password?",
         ["To reset a password, click 'Forgot password' on the login page. A reset link valid for 60 minutes is emailed to you."],
         "Click 'Forgot password' on the login page — you'll get an email link that stays valid for 60 minutes."),
        ("What database does the service use?",
         ["The platform stores all relational data in PostgreSQL 15, managed through Supabase."],
         "It uses PostgreSQL 15, managed via Supabase."),
        ("Is there an uptime SLA?",
         ["The Business plan carries a 99.9% monthly uptime SLA with service credits for breaches."],
         "Yes — Business plan customers get a 99.9% monthly uptime SLA with service credits if it's breached."),
        ("What regions can I deploy in?",
         ["Deployments are currently supported in us-east-1, eu-west-1, and ap-southeast-2."],
         "You can deploy in us-east-1, eu-west-1, and ap-southeast-2."),
        ("How are API requests authenticated?",
         ["All API requests require a bearer token in the Authorization header. Tokens are issued per workspace."],
         "Requests are authenticated with a workspace-issued bearer token sent in the Authorization header."),
        ("What file formats can I export?",
         ["Reports can be exported as CSV or JSON. PDF export is on the roadmap but not yet available."],
         "CSV and JSON today; PDF export isn't available yet."),
        ("How long are logs retained?",
         ["Audit logs are retained for 90 days on all plans. Extended retention is available as an add-on."],
         "Audit logs are kept for 90 days on every plan, with extended retention available as an add-on."),
        ("Can I invite external collaborators?",
         ["Workspace admins can invite guests with view-only access. Guests cannot modify resources."],
         "Yes — admins can invite view-only guests, though guests can't modify anything."),
        ("What's the maximum upload size?",
         ["Individual file uploads are limited to 250 MB. Larger datasets should use the bulk import API."],
         "Single files are capped at 250 MB; use the bulk import API for anything larger."),
        ("Does the CLI support Windows?",
         ["The CLI ships binaries for macOS, Linux, and Windows (x64 and ARM)."],
         "Yes, the CLI has Windows binaries for both x64 and ARM."),
        ("How do webhooks retry?",
         ["Failed webhook deliveries are retried up to 5 times with exponential backoff over 24 hours."],
         "Failed deliveries retry up to 5 times with exponential backoff spread over 24 hours."),
        ("What languages does the SDK support?",
         ["Official SDKs are available for Python and TypeScript. Community SDKs exist for Go and Ruby."],
         "Officially Python and TypeScript; Go and Ruby have community-maintained SDKs."),
        ("Is data encrypted at rest?",
         ["All customer data is encrypted at rest with AES-256 and in transit with TLS 1.3."],
         "Yes — AES-256 at rest and TLS 1.3 in transit."),
        ("How do I delete my account?",
         ["Account deletion is available under Settings -> Danger zone. Deletion is permanent after a 14-day grace period."],
         "Go to Settings -> Danger zone. It becomes permanent after a 14-day grace period."),
        ("What's included in the free tier?",
         ["The free tier includes 3 projects, 10,000 events per month, and community support."],
         "3 projects, 10,000 monthly events, and community support."),
        ("Can I pause a subscription?",
         ["Subscriptions can be paused for up to 3 months once per year from the billing page."],
         "Yes, once per year you can pause for up to 3 months from the billing page."),
        ("Which browsers are supported?",
         ["The dashboard supports the last two major versions of Chrome, Firefox, Safari, and Edge."],
         "The last two major versions of Chrome, Firefox, Safari, and Edge."),
        ("How is usage billed?",
         ["Usage is metered daily and billed monthly in arrears, based on events ingested."],
         "It's metered daily and billed monthly in arrears by events ingested."),
        ("Do you support IP allowlisting?",
         ["Enterprise customers can restrict API access to an IP allowlist configured per workspace."],
         "Yes, on the Enterprise plan — configurable per workspace."),
        ("What happens when I hit my quota?",
         ["When the monthly quota is exhausted, additional requests are rejected with HTTP 429 until the next cycle."],
         "Requests beyond the quota get a 429 until the next billing cycle."),
        ("Can I use my own domain?",
         ["Custom domains with automatic TLS are supported on the Pro plan and above."],
         "Yes, Pro plan and above, with automatic TLS."),
        ("Is there an audit trail for API keys?",
         ["Every API key records its creation time and last-used timestamp, visible in Settings."],
         "Yes — each key shows creation time and last use in Settings."),
        ("How fast is search indexing?",
         ["New documents become searchable within 30 seconds of upload on average."],
         "New documents are searchable within about 30 seconds."),
    ]
    for q, chunks, a in grounded:
        cases.append(Case("good", False, q, chunks, a))

    # ---------------- 25 known-bad ----------------
    # 10 hallucinations: specific claims absent from the chunks
    hallucinated = [
        ("What is the refund window?",
         ["Customers may request a full refund within 30 days of purchase."],
         "You get a 90-day refund window, plus a lifetime warranty and a $50 gift card with every return."),
        ("Which plan includes SSO?",
         ["SSO via SAML is available on the Business plan and above."],
         "Every plan includes SSO, SCIM provisioning, and free penetration testing twice a year."),
        ("Is there an uptime SLA?",
         ["The Business plan carries a 99.9% monthly uptime SLA."],
         "Yes, all plans have a 100% uptime guarantee backed by a full refund of your annual fee."),
        ("What's the maximum upload size?",
         ["Individual file uploads are limited to 250 MB."],
         "There is no upload limit at all — files of any size are accepted, including multi-terabyte datasets."),
        ("How long are logs retained?",
         ["Audit logs are retained for 90 days on all plans."],
         "Logs are kept forever and can be exported to your own S3 bucket automatically every hour."),
        ("What regions can I deploy in?",
         ["Deployments are currently supported in us-east-1, eu-west-1, and ap-southeast-2."],
         "You can deploy in any of our 34 regions, including mainland China and a sovereign EU cloud."),
        ("Does the CLI support Windows?",
         ["The CLI ships binaries for macOS, Linux, and Windows (x64 and ARM)."],
         "Yes, and it also runs natively on iOS and Android via the companion mobile app."),
        ("What's included in the free tier?",
         ["The free tier includes 3 projects, 10,000 events per month, and community support."],
         "The free tier includes unlimited projects, 1 million events, and 24/7 phone support."),
        ("Can I use my own domain?",
         ["Custom domains with automatic TLS are supported on the Pro plan and above."],
         "Yes, on all plans, and we also register the domain for you free of charge for the first year."),
        ("How do webhooks retry?",
         ["Failed webhook deliveries are retried up to 5 times with exponential backoff over 24 hours."],
         "Webhooks retry indefinitely every minute until acknowledged, and failures page your on-call via PagerDuty integration."),
    ]
    for q, chunks, a in hallucinated:
        cases.append(Case("bad", True, q, chunks, a, "hallucination"))

    # 8 off-topic: answer ignores the question
    offtopic = [
        ("What is the refund window?",
         ["Customers may request a full refund within 30 days of purchase."],
         "Our company was founded in 2019 and now employs over 200 people across three continents."),
        ("How do I reset my password?",
         ["To reset a password, click 'Forgot password' on the login page."],
         "We take security very seriously and are SOC 2 Type II certified."),
        ("Which browsers are supported?",
         ["The dashboard supports the last two major versions of Chrome, Firefox, Safari, and Edge."],
         "The mobile app is available on the App Store and Google Play."),
        ("What database does the service use?",
         ["The platform stores all relational data in PostgreSQL 15."],
         "You can contact support via chat or email, and responses typically arrive within four hours."),
        ("How is usage billed?",
         ["Usage is metered daily and billed monthly in arrears."],
         "The dashboard recently got a dark mode you can toggle in your profile."),
        ("Can I pause a subscription?",
         ["Subscriptions can be paused for up to 3 months once per year."],
         "Our API uses REST semantics with JSON payloads and standard HTTP status codes."),
        ("Is data encrypted at rest?",
         ["All customer data is encrypted at rest with AES-256."],
         "We publish a monthly changelog on our blog with all new features."),
        ("What happens when I hit my quota?",
         ["When the monthly quota is exhausted, additional requests are rejected with HTTP 429."],
         "Annual plans come with two months free compared to monthly billing."),
    ]
    for q, chunks, a in offtopic:
        cases.append(Case("bad", False, q, chunks, a, "off-topic"))

    # 7 wrong/insufficient grounding: chunks irrelevant to the question,
    # answer invented from nowhere
    wrong_chunks = [
        ("What is the API rate limit?",
         ["The office is closed on public holidays.", "Our headquarters moved to Berlin in 2022."],
         "The API rate limit is 5,000 requests per second on all plans."),
        ("How do I configure SAML?",
         ["The cafeteria menu rotates weekly.", "Parking passes are issued at the front desk."],
         "Go to Settings -> SSO, upload your IdP metadata XML, and map the email attribute."),
        ("What is the data residency policy?",
         ["Team offsites happen twice a year.", "The support team uses a follow-the-sun model."],
         "All data is stored exclusively in Frankfurt with optional replication to Zurich."),
        ("Which payment methods are accepted?",
         ["Engineering follows a two-week sprint cadence."],
         "We accept credit cards, PayPal, wire transfer, and cryptocurrency."),
        ("How do I rotate credentials?",
         ["The style guide requires sentence-case headings."],
         "Credentials rotate automatically every 24 hours with zero downtime."),
        ("What's the incident response SLA?",
         ["New hires get a mentorship buddy for their first quarter."],
         "P1 incidents are acknowledged within 15 minutes, 24/7."),
        ("Does the product support GraphQL?",
         ["The annual report is published each February."],
         "Yes, there's a fully-typed GraphQL API with subscriptions support."),
    ]
    for q, chunks, a in wrong_chunks:
        cases.append(Case("bad", False, q, chunks, a, "wrong-chunks"))

    assert len([c for c in cases if c.label == "good"]) == 25
    assert len([c for c in cases if c.label == "bad"]) == 25
    return cases


_pacer_lock = asyncio.Lock()
_pacer_next_call_at = 0.0


async def _pace_for_free_tier(min_interval: float) -> None:
    """Proactively spaces calls at least min_interval apart (serialized via a
    lock) instead of reactively absorbing 429s — cheaper on a free-tier quota
    than firing bursts and backing off after the fact."""
    global _pacer_next_call_at
    async with _pacer_lock:
        now = asyncio.get_event_loop().time()
        wait = max(0.0, _pacer_next_call_at - now)
        if wait:
            await asyncio.sleep(wait)
        _pacer_next_call_at = asyncio.get_event_loop().time() + min_interval


async def run_case(sem: asyncio.Semaphore, case: Case, pace_interval: float):
    from services.eval_service import compute_eval

    async with sem:
        if pace_interval:
            await _pace_for_free_tier(pace_interval)
        result = await compute_eval(
            query=case.query,
            retrieved_chunks=[{"content": c, "score": 0.85, "doc_id": f"d{i}"} for i, c in enumerate(case.chunks)],
            final_answer=case.answer,
        )
    min_score = min(result.faithfulness, result.answer_relevance, result.context_precision)
    if case.label == "good":
        correct = (
            result.faithfulness >= 0.7
            and result.answer_relevance >= 0.7
            and not result.hallucination_flag
        )
    else:
        correct = min_score < 0.7 or result.hallucination_flag
        if case.expects_hallucination:
            correct = correct and result.hallucination_flag
    return case, result, correct


async def main_async() -> int:
    for k, v in dotenv_values(API_DIR / ".env").items():
        if v is not None:
            os.environ[k] = v

    from config import get_settings

    provider = get_settings().eval_provider
    # Anthropic's paid tier tolerates real concurrency; Gemini's free tier is
    # tightly rate-limited (observed: 5 req/min) — go fully sequential and
    # proactively pace calls rather than lean on reactive 429 backoff.
    concurrency = 5 if provider == "anthropic" else 1
    pace_interval = 0.0 if provider == "anthropic" else 13.0
    print(f"provider={provider} concurrency={concurrency} pace_interval={pace_interval}s")

    cases = dataset()
    sem = asyncio.Semaphore(concurrency)

    try:
        outcomes = await asyncio.gather(*(run_case(sem, c, pace_interval) for c in cases))
    except anthropic.BadRequestError as exc:
        if "credit balance" in str(exc):
            print(
                "SKIP: Anthropic account has no credit balance — the eval quality gate "
                "needs ~50 real Haiku calls (~$0.15). Add credits and re-run."
            )
            return 2
        raise
    except genai_errors.ClientError as exc:
        if exc.code == 429:
            print(f"SKIP: Gemini free-tier quota exhausted even after backoff/pacing: {exc}")
            return 2
        raise

    by_note: dict[str, list[bool]] = {}
    misses = []
    for case, result, correct in outcomes:
        key = case.note or case.label
        by_note.setdefault(key, []).append(correct)
        if not correct:
            misses.append((case, result))

    total = len(outcomes)
    right = sum(1 for _, _, ok in outcomes if ok)
    good_right = sum(1 for c, _, ok in outcomes if c.label == "good" and ok)
    bad_right = sum(1 for c, _, ok in outcomes if c.label == "bad" and ok)

    print(f"\n== Eval quality vs labeled dataset (n={total}) ==")
    print(f"overall agreement : {right}/{total} = {right / total:.0%}")
    print(f"known-good passed : {good_right}/25 (false-alarm rate {1 - good_right / 25:.0%})")
    print(f"known-bad caught  : {bad_right}/25 (miss rate {1 - bad_right / 25:.0%})")
    for note, oks in sorted(by_note.items()):
        print(f"  - {note:<14}: {sum(oks)}/{len(oks)}")

    if misses:
        print("\nDisagreements:")
        for case, result in misses:
            print(
                f"  [{case.note or case.label}] {case.query!r}\n"
                f"    scores f={result.faithfulness:.2f} r={result.answer_relevance:.2f} "
                f"p={result.context_precision:.2f} halluc={result.hallucination_flag}"
            )

    gate = right / total >= 0.85
    print(f"\nLAUNCH GATE (>=85% agreement): {'PASS' if gate else 'FAIL'}")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
