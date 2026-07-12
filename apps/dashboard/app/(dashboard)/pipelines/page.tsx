import { CreatePipelineForm } from "@/components/pipelines/CreatePipelineForm";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/server";
import type { Pipeline } from "@/lib/types";
import { format } from "date-fns";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function PipelinesPage() {
  const supabase = createClient();
  const { data: pipelines } = await supabase
    .from("pipelines")
    .select("*")
    .order("created_at", { ascending: false });

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Pipelines</h1>

      <Card>
        <CardHeader>
          <CardTitle>New pipeline</CardTitle>
        </CardHeader>
        <CardContent>
          <CreatePipelineForm />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your pipelines</CardTitle>
        </CardHeader>
        <CardContent>
          {(pipelines ?? []).length === 0 ? (
            <p className="text-sm text-zinc-500">No pipelines yet — create one above.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-200 text-left text-xs uppercase tracking-wide text-zinc-500">
                  <th className="py-2">Name</th>
                  <th>Framework</th>
                  <th>Eval sampling</th>
                  <th>Created</th>
                  <th></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {(pipelines as Pipeline[]).map((p) => (
                  <tr key={p.id}>
                    <td className="py-2 font-medium">
                      <Link href={`/pipelines/${p.id}`} className="hover:underline">
                        {p.name}
                      </Link>
                    </td>
                    <td>
                      <Badge>{p.framework ?? "custom"}</Badge>
                    </td>
                    <td>{Math.round(Number(p.eval_sample_rate) * 100)}%</td>
                    <td className="text-zinc-500">{format(new Date(p.created_at), "PP")}</td>
                    <td className="text-right font-mono text-xs text-zinc-400">{p.id.slice(0, 8)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
