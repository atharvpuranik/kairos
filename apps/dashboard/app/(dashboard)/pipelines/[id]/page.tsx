import { PipelineHealthChart } from "@/components/charts/PipelineHealthChart";
import { ScoreTimeline } from "@/components/charts/ScoreTimeline";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/server";
import type { EvalScore, HealthDaily, Pipeline } from "@/lib/types";
import { formatScore } from "@/lib/utils";
import Link from "next/link";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function PipelineDetailPage({ params }: { params: { id: string } }) {
  const supabase = createClient();

  const { data: pipeline } = await supabase
    .from("pipelines")
    .select("*")
    .eq("id", params.id)
    .maybeSingle();
  if (!pipeline) notFound();

  const since30d = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);
  const [healthRes, scoresRes, traceCountRes] = await Promise.all([
    supabase
      .from("pipeline_health_daily")
      .select("*")
      .eq("pipeline_id", params.id)
      .gte("date", since30d)
      .order("date", { ascending: true }),
    supabase
      .from("eval_scores")
      .select("*")
      .eq("pipeline_id", params.id)
      .order("computed_at", { ascending: false })
      .limit(100),
    supabase
      .from("traces")
      .select("id", { count: "exact", head: true })
      .eq("pipeline_id", params.id),
  ]);

  const scores = (scoresRes.data ?? []) as EvalScore[];
  const avg = (key: "faithfulness" | "answer_relevance" | "context_precision") => {
    const values = scores.map((s) => Number(s[key])).filter((v) => !Number.isNaN(v));
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  };
  const hallucinations = scores.filter((s) => s.hallucination_flag).length;

  const summary = [
    { label: "Traces", value: String(traceCountRes.count ?? 0) },
    { label: "Faithfulness (recent)", value: formatScore(avg("faithfulness")) },
    { label: "Relevance (recent)", value: formatScore(avg("answer_relevance")) },
    { label: "Precision (recent)", value: formatScore(avg("context_precision")) },
    { label: "Hallucinations (recent)", value: String(hallucinations) },
  ];

  const failureCounts = new Map<string, number>();
  for (const s of scores) {
    if (s.failure_category) {
      failureCounts.set(s.failure_category, (failureCounts.get(s.failure_category) ?? 0) + 1);
    }
  }

  const p = pipeline as Pipeline;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">{p.name}</h1>
        <p className="mt-1 flex items-center gap-2 text-sm text-zinc-500">
          <Badge>{p.framework ?? "custom"}</Badge>
          <span>eval sampling {Math.round(Number(p.eval_sample_rate) * 100)}%</span>
          <span className="font-mono text-xs">{p.id}</span>
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        {summary.map((s) => (
          <Card key={s.label}>
            <CardContent className="py-3">
              <p className="text-xs uppercase tracking-wide text-zinc-500">{s.label}</p>
              <p className="mt-1 text-xl font-bold">{s.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Health score (nightly, last 30 days)</CardTitle>
        </CardHeader>
        <CardContent>
          <PipelineHealthChart data={(healthRes.data ?? []) as HealthDaily[]} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Eval scores (last 100 traces)</CardTitle>
        </CardHeader>
        <CardContent>
          <ScoreTimeline scores={scores} />
        </CardContent>
      </Card>

      {failureCounts.size > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Failure categories (recent)</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {[...failureCounts.entries()].map(([category, count]) => (
              <Badge key={category} tone="red">
                {category}: {count}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}

      <p className="text-sm">
        <Link href={`/traces?pipeline=${p.id}`} className="text-zinc-600 underline">
          View traces for this pipeline →
        </Link>
      </p>
    </div>
  );
}
