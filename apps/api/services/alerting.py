"""Degradation detection + alerts — ARCHITECTURE.md section 10/12 (Week 4 basic tier).

Called by the eval worker after each completed eval (rate-limited to once per
pipeline per hour via Redis). Compares the last 24h of eval scores against the
prior 7-day baseline and writes an `alerts` row when quality degrades:

- faithfulness_drop: recent avg < 85% of baseline avg (warning),
  < 70% (critical)
- hallucination_spike: recent hallucination rate exceeds 20% AND is at least
  3x the baseline rate (critical)

Dedupe: no new alert while an unresolved alert of the same type exists for
the pipeline. The dashboard resolves alerts directly via RLS.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from db.redis_client import get_redis

logger = logging.getLogger("kairos.alerting")

MIN_RECENT_EVALS = 5
MIN_BASELINE_EVALS = 10
DROP_WARNING_RATIO = 0.85
DROP_CRITICAL_RATIO = 0.70
HALLUCINATION_RATE_FLOOR = 0.2
HALLUCINATION_MULTIPLIER = 3.0
CHECK_COOLDOWN_SECONDS = 3600


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def _cooldown_active(pipeline_id: str) -> bool:
    redis = get_redis()
    count = await redis.incr(f"alertcheck:{pipeline_id}")
    if count == 1:
        await redis.expire(f"alertcheck:{pipeline_id}", CHECK_COOLDOWN_SECONDS)
    return count > 1


async def _has_unresolved(supabase, pipeline_id: str, alert_type: str) -> bool:
    result = (
        await supabase.table("alerts")
        .select("id")
        .eq("pipeline_id", pipeline_id)
        .eq("alert_type", alert_type)
        .eq("resolved", False)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def _fetch_window(supabase, pipeline_id: str, start: datetime, end: datetime) -> list[dict]:
    result = (
        await supabase.table("eval_scores")
        .select("faithfulness,hallucination_flag")
        .eq("pipeline_id", pipeline_id)
        .gte("computed_at", start.isoformat())
        .lt("computed_at", end.isoformat())
        .execute()
    )
    return result.data


async def check_pipeline_alerts(
    supabase, pipeline_id: str, user_id: str, skip_cooldown: bool = False
) -> list[str]:
    """Runs the degradation checks; returns the list of alert types created."""
    if not skip_cooldown and await _cooldown_active(pipeline_id):
        return []

    now = datetime.now(timezone.utc)
    recent = await _fetch_window(supabase, pipeline_id, now - timedelta(hours=24), now)
    baseline = await _fetch_window(
        supabase, pipeline_id, now - timedelta(days=8), now - timedelta(hours=24)
    )
    if len(recent) < MIN_RECENT_EVALS or len(baseline) < MIN_BASELINE_EVALS:
        return []

    created: list[str] = []

    recent_f = _avg([float(r["faithfulness"]) for r in recent if r["faithfulness"] is not None])
    baseline_f = _avg([float(r["faithfulness"]) for r in baseline if r["faithfulness"] is not None])
    if recent_f is not None and baseline_f is not None and baseline_f > 0:
        ratio = recent_f / baseline_f
        if ratio < DROP_WARNING_RATIO and not await _has_unresolved(
            supabase, pipeline_id, "faithfulness_drop"
        ):
            severity = "critical" if ratio < DROP_CRITICAL_RATIO else "warning"
            await supabase.table("alerts").insert(
                {
                    "user_id": user_id,
                    "pipeline_id": pipeline_id,
                    "alert_type": "faithfulness_drop",
                    "severity": severity,
                    "message": (
                        f"Faithfulness dropped {round((1 - ratio) * 100)}% vs the 7-day baseline "
                        f"({baseline_f:.2f} -> {recent_f:.2f} over the last 24h)."
                    ),
                    "metric_before": round(baseline_f, 3),
                    "metric_after": round(recent_f, 3),
                }
            ).execute()
            created.append("faithfulness_drop")

    recent_rate = _avg([1.0 if r["hallucination_flag"] else 0.0 for r in recent])
    baseline_rate = _avg([1.0 if r["hallucination_flag"] else 0.0 for r in baseline])
    if recent_rate is not None and baseline_rate is not None:
        spiking = recent_rate > HALLUCINATION_RATE_FLOOR and recent_rate > (
            HALLUCINATION_MULTIPLIER * baseline_rate
        )
        if spiking and not await _has_unresolved(supabase, pipeline_id, "hallucination_spike"):
            await supabase.table("alerts").insert(
                {
                    "user_id": user_id,
                    "pipeline_id": pipeline_id,
                    "alert_type": "hallucination_spike",
                    "severity": "critical",
                    "message": (
                        f"Hallucination rate spiked to {round(recent_rate * 100)}% over the last 24h "
                        f"(baseline {round(baseline_rate * 100)}%)."
                    ),
                    "metric_before": round(baseline_rate, 3),
                    "metric_after": round(recent_rate, 3),
                }
            ).execute()
            created.append("hallucination_spike")

    if created:
        logger.warning("alerts created for pipeline %s: %s", pipeline_id, created)
    return created
