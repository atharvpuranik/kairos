-- Kairos scheduled jobs (pg_cron). Run AFTER 0002_hardening.sql, as a separate
-- execution: if pg_cron isn't enabled yet, this file fails without rolling
-- back the core hardening migration.
--
-- If `CREATE EXTENSION` errors, enable pg_cron first in the Supabase dashboard:
-- Database -> Extensions -> pg_cron -> enable, then re-run this file.

CREATE EXTENSION IF NOT EXISTS pg_cron;

-- ---------------------------------------------------------------------------
-- 1. 30-day trace retention (free-tier promise; keeps the 500MB DB alive).
--    eval_scores cascades via FK.
-- ---------------------------------------------------------------------------
SELECT cron.schedule(
  'kairos-purge-old-traces',
  '0 3 * * *',   -- daily 03:00 UTC
  $$ DELETE FROM traces WHERE created_at < now() - interval '30 days' $$
);

-- ---------------------------------------------------------------------------
-- 2. Nightly pipeline health rollup for yesterday (UTC).
--    Formula per ARCHITECTURE.md section 10: ((f*0.35)+(r*0.35)+(p*0.30))*100
-- ---------------------------------------------------------------------------
SELECT cron.schedule(
  'kairos-pipeline-health-rollup',
  '30 2 * * *',  -- daily 02:30 UTC
  $$
  INSERT INTO pipeline_health_daily (
    pipeline_id, date, health_score,
    avg_faithfulness, avg_answer_relevance, avg_context_precision,
    eval_count, hallucination_count
  )
  SELECT
    e.pipeline_id,
    (now() - interval '1 day')::date,
    (avg(e.faithfulness) * 0.35 + avg(e.answer_relevance) * 0.35 + avg(e.context_precision) * 0.30) * 100,
    avg(e.faithfulness),
    avg(e.answer_relevance),
    avg(e.context_precision),
    count(*),
    count(*) FILTER (WHERE e.hallucination_flag)
  FROM eval_scores e
  WHERE e.computed_at >= (now() - interval '1 day')::date
    AND e.computed_at < now()::date
  GROUP BY e.pipeline_id
  ON CONFLICT (pipeline_id, date) DO UPDATE SET
    health_score = EXCLUDED.health_score,
    avg_faithfulness = EXCLUDED.avg_faithfulness,
    avg_answer_relevance = EXCLUDED.avg_answer_relevance,
    avg_context_precision = EXCLUDED.avg_context_precision,
    eval_count = EXCLUDED.eval_count,
    hallucination_count = EXCLUDED.hallucination_count
  $$
);
