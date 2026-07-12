import { AlertList } from "@/components/alerts/AlertList";
import { Badge, evalStatusTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/server";
import type { Alert, Trace } from "@/lib/types";
import { formatDistanceToNow } from "date-fns";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const supabase = createClient();

  const monthStart = new Date();
  monthStart.setUTCDate(1);
  const monthKey = monthStart.toISOString().slice(0, 10);

  const [pipelines, usage, health, alerts, recentTraces] = await Promise.all([
    supabase.from("pipelines").select("id", { count: "exact", head: true }),
    supabase.from("usage").select("traces_count").eq("month", monthKey).maybeSingle(),
    supabase
      .from("pipeline_health_daily")
      .select("health_score")
      .gte("date", new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10)),
    supabase
      .from("alerts")
      .select("*")
      .eq("resolved", false)
      .order("created_at", { ascending: false }),
    supabase
      .from("traces")
      .select("id, query, eval_status, created_at, pipelines(name)")
      .order("created_at", { ascending: false })
      .limit(8),
  ]);

  const healthScores = (health.data ?? [])
    .map((h) => Number(h.health_score))
    .filter((v) => !Number.isNaN(v));
  const avgHealth =
    healthScores.length > 0
      ? (healthScores.reduce((a, b) => a + b, 0) / healthScores.length).toFixed(1)
      : "—";

  const stats = [
    { label: "Pipelines", value: String(pipelines.count ?? 0) },
    { label: "Traces this month", value: String(usage.data?.traces_count ?? 0) },
    { label: "Avg health (7d)", value: avgHealth },
    { label: "Active alerts", value: String(alerts.data?.length ?? 0) },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Overview</h1>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardContent className="py-4">
              <p className="text-xs uppercase tracking-wide text-zinc-500">{s.label}</p>
              <p className="mt-1 text-2xl font-bold">{s.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Active alerts</CardTitle>
        </CardHeader>
        <CardContent>
          <AlertList alerts={(alerts.data ?? []) as Alert[]} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent traces</CardTitle>
        </CardHeader>
        <CardContent>
          {(recentTraces.data ?? []).length === 0 ? (
            <p className="text-sm text-zinc-500">
              No traces yet — install the SDK and send your first trace.
            </p>
          ) : (
            <ul className="divide-y divide-zinc-100">
              {(recentTraces.data as unknown as Trace[]).map((t) => (
                <li key={t.id} className="flex items-center justify-between gap-3 py-2">
                  <Link
                    href={`/traces/${t.id}`}
                    className="min-w-0 flex-1 truncate text-sm hover:underline"
                  >
                    {t.query}
                  </Link>
                  <Badge tone={evalStatusTone(t.eval_status)}>{t.eval_status}</Badge>
                  <span className="w-28 shrink-0 text-right text-xs text-zinc-400">
                    {formatDistanceToNow(new Date(t.created_at), { addSuffix: true })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
