import { TraceDetail } from "@/components/traces/TraceDetail";
import { createClient } from "@/lib/supabase/server";
import type { Trace } from "@/lib/types";
import Link from "next/link";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function TraceDetailPage({ params }: { params: { id: string } }) {
  const supabase = createClient();
  const { data: trace } = await supabase
    .from("traces")
    .select("*, eval_scores(*), pipelines(name)")
    .eq("id", params.id)
    .maybeSingle();
  if (!trace) notFound();

  return (
    <div className="space-y-4">
      <Link href="/traces" className="text-sm text-zinc-500 hover:underline">
        ← Back to traces
      </Link>
      <TraceDetail trace={trace as unknown as Trace} />
    </div>
  );
}
