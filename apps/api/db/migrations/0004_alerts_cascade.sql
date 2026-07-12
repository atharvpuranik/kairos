-- Fix: alerts referenced pipelines/agent_projects without ON DELETE CASCADE,
-- so deleting a pipeline that ever alerted failed with a FK violation
-- (users CAN delete pipelines directly under the RLS "own pipelines all" policy).

ALTER TABLE alerts DROP CONSTRAINT alerts_pipeline_id_fkey;
ALTER TABLE alerts
  ADD CONSTRAINT alerts_pipeline_id_fkey
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE;

ALTER TABLE alerts DROP CONSTRAINT alerts_agent_project_id_fkey;
ALTER TABLE alerts
  ADD CONSTRAINT alerts_agent_project_id_fkey
  FOREIGN KEY (agent_project_id) REFERENCES agent_projects(id) ON DELETE CASCADE;
