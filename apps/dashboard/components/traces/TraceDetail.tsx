import { Badge, evalStatusTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Trace } from "@/lib/types";
import { evalScoreOf, formatScore, scoreColor } from "@/lib/utils";
import { format } from "date-fns";

export function TraceDetail({ trace }: { trace: Trace }) {
  const score = evalScoreOf(trace);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Query</CardTitle>
          <span className="flex items-center gap-2 text-xs text-zinc-400">
            <Badge tone={evalStatusTone(trace.eval_status)}>{trace.eval_status}</Badge>
            {format(new Date(trace.created_at), "PPpp")}
          </span>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm">{trace.query}</p>
          <p className="mt-2 text-xs text-zinc-500">
            {trace.latency_ms}ms
            {trace.token_count_input != null && <> · {trace.token_count_input} in</>}
            {trace.token_count_output != null && <> · {trace.token_count_output} out</>}
            {trace.estimated_cost_usd != null && (
              <> · ${Number(trace.estimated_cost_usd).toFixed(4)}</>
            )}
            {trace.pipelines?.name && <> · {trace.pipelines.name}</>}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Eval scores</CardTitle>
        </CardHeader>
        <CardContent>
          {!score ? (
            <p className="text-sm text-zinc-500">
              {trace.eval_status === "pending"
                ? "Evaluation pending — scores will appear once the worker processes this trace."
                : trace.eval_status === "skipped"
                  ? "Evaluation skipped (sampling or daily cap)."
                  : "No scores available."}
            </p>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-4">
                {(
                  [
                    ["Faithfulness", score.faithfulness],
                    ["Answer relevance", score.answer_relevance],
                    ["Context precision", score.context_precision],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
                    <p className={`text-2xl font-bold ${scoreColor(value)}`}>{formatScore(value)}</p>
                  </div>
                ))}
              </div>
              {score.hallucination_flag && (
                <div className="rounded-md border border-red-200 bg-red-50 p-3">
                  <p className="text-sm font-semibold text-red-800">Hallucination detected</p>
                  {score.hallucination_detail && (
                    <p className="mt-1 text-sm text-red-700">{score.hallucination_detail}</p>
                  )}
                </div>
              )}
              {score.failure_category && (
                <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                  <p className="text-sm font-semibold text-amber-800">
                    Root cause: {score.failure_category}
                  </p>
                  {score.failure_reason && (
                    <p className="mt-1 text-sm text-amber-700">{score.failure_reason}</p>
                  )}
                </div>
              )}
              <p className="text-xs text-zinc-400">
                Scored by {score.model_used} ({score.prompt_version}) at{" "}
                {format(new Date(score.computed_at), "PPpp")}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Final answer</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm">{trace.final_answer}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Retrieved chunks ({trace.retrieved_chunks.length})</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {trace.retrieved_chunks.map((chunk, i) => (
            <div key={i} className="rounded-md border border-zinc-200 p-3">
              <div className="mb-1 flex items-center justify-between text-xs text-zinc-500">
                <span className="font-mono">{chunk.doc_id}</span>
                <span>score {Number(chunk.score).toFixed(3)}</span>
              </div>
              <p className="whitespace-pre-wrap text-sm">{chunk.content}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      {trace.metadata && Object.keys(trace.metadata).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-md bg-zinc-50 p-3 text-xs">
              {JSON.stringify(trace.metadata, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
