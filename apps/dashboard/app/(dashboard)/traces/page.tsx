import { TraceTable } from "@/components/traces/TraceTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/server";
import type { Pipeline, Trace } from "@/lib/types";
import Link from "next/link";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 25;
const STATUSES = ["all", "pending", "completed", "failed", "skipped"] as const;

interface SearchParams {
  pipeline?: string;
  status?: string;
  page?: string;
}

export default async function TracesPage({ searchParams }: { searchParams: SearchParams }) {
  const supabase = createClient();
  const page = Math.max(1, Number(searchParams.page ?? 1) || 1);
  const status = searchParams.status && searchParams.status !== "all" ? searchParams.status : null;
  const pipeline = searchParams.pipeline ?? null;

  let query = supabase
    .from("traces")
    .select("id, query, eval_status, latency_ms, created_at, pipelines(name), eval_scores(*)", {
      count: "exact",
    })
    .order("created_at", { ascending: false })
    .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE - 1);
  if (status) query = query.eq("eval_status", status);
  if (pipeline) query = query.eq("pipeline_id", pipeline);

  const [{ data: traces, count }, { data: pipelines }] = await Promise.all([
    query,
    supabase.from("pipelines").select("id, name").order("name"),
  ]);

  const totalPages = Math.max(1, Math.ceil((count ?? 0) / PAGE_SIZE));
  const buildHref = (overrides: Partial<SearchParams>) => {
    const params = new URLSearchParams();
    const merged = { ...searchParams, ...overrides };
    if (merged.pipeline) params.set("pipeline", merged.pipeline);
    if (merged.status && merged.status !== "all") params.set("status", merged.status);
    if (merged.page && merged.page !== "1") params.set("page", merged.page);
    const qs = params.toString();
    return qs ? `/traces?${qs}` : "/traces";
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">Traces</h1>
        <span className="text-sm text-zinc-500">{count ?? 0} total</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-zinc-500">Status:</span>
        {STATUSES.map((s) => (
          <Link
            key={s}
            href={buildHref({ status: s, page: "1" })}
            className={`rounded-full px-2.5 py-0.5 ${
              (searchParams.status ?? "all") === s
                ? "bg-zinc-900 text-white"
                : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
            }`}
          >
            {s}
          </Link>
        ))}
        <span className="ml-4 text-zinc-500">Pipeline:</span>
        <Link
          href={buildHref({ pipeline: undefined, page: "1" })}
          className={`rounded-full px-2.5 py-0.5 ${
            !pipeline ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
          }`}
        >
          all
        </Link>
        {((pipelines ?? []) as Pick<Pipeline, "id" | "name">[]).map((p) => (
          <Link
            key={p.id}
            href={buildHref({ pipeline: p.id, page: "1" })}
            className={`rounded-full px-2.5 py-0.5 ${
              pipeline === p.id ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
            }`}
          >
            {p.name}
          </Link>
        ))}
      </div>

      <Card>
        <CardContent>
          <TraceTable traces={(traces ?? []) as unknown as Trace[]} />
        </CardContent>
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2">
          {page > 1 && (
            <Link href={buildHref({ page: String(page - 1) })}>
              <Button variant="outline">Previous</Button>
            </Link>
          )}
          <span className="text-sm text-zinc-500">
            Page {page} of {totalPages}
          </span>
          {page < totalPages && (
            <Link href={buildHref({ page: String(page + 1) })}>
              <Button variant="outline">Next</Button>
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
