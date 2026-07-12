export interface Pipeline {
  id: string;
  name: string;
  description: string | null;
  framework: string | null;
  eval_sample_rate: number;
  is_active: boolean;
  created_at: string;
}

export interface RetrievedChunk {
  content: string;
  score: number;
  doc_id: string;
  metadata: Record<string, unknown> | null;
}

export interface EvalScore {
  id: string;
  trace_id: string;
  pipeline_id: string;
  faithfulness: number | null;
  answer_relevance: number | null;
  context_precision: number | null;
  hallucination_flag: boolean | null;
  hallucination_detail: string | null;
  failure_reason: string | null;
  failure_category: string | null;
  computed_at: string;
  model_used: string;
  prompt_version: string;
}

export interface Trace {
  id: string;
  pipeline_id: string;
  query: string;
  retrieved_chunks: RetrievedChunk[];
  reranked_chunks: RetrievedChunk[] | null;
  final_answer: string;
  latency_ms: number;
  token_count_input: number | null;
  token_count_output: number | null;
  estimated_cost_usd: number | null;
  metadata: Record<string, unknown> | null;
  eval_status: string;
  created_at: string;
  // PostgREST returns a single object for this embed (one-to-one via
  // UNIQUE(trace_id)); older shapes were arrays — normalize via evalScoreOf().
  eval_scores?: EvalScore | EvalScore[] | null;
  pipelines?: { name: string } | null;
}

export interface HealthDaily {
  date: string;
  health_score: number | null;
  avg_faithfulness: number | null;
  avg_answer_relevance: number | null;
  avg_context_precision: number | null;
  eval_count: number;
  hallucination_count: number;
}

export interface Alert {
  id: string;
  pipeline_id: string | null;
  alert_type: string;
  severity: string;
  message: string;
  metric_before: number | null;
  metric_after: number | null;
  resolved: boolean;
  created_at: string;
}

export interface ApiKeyInfo {
  id: string;
  key_prefix: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  is_active: boolean;
  key?: string; // present only in the creation response
}
