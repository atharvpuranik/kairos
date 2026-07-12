import { Badge, evalStatusTone } from "@/components/ui/badge";
import type { Trace } from "@/lib/types";
import { evalScoreOf, formatScore, scoreColor } from "@/lib/utils";
import { formatDistanceToNow } from "date-fns";
import Link from "next/link";

export function TraceTable({ traces }: { traces: Trace[] }) {
  if (traces.length === 0) {
    return <p className="text-sm text-zinc-500">No traces match these filters.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-zinc-200 text-left text-xs uppercase tracking-wide text-zinc-500">
          <th className="py-2">Query</th>
          <th>Pipeline</th>
          <th>Status</th>
          <th className="text-right">Faith.</th>
          <th className="text-right">Rel.</th>
          <th className="text-right">Prec.</th>
          <th className="text-right">Latency</th>
          <th className="text-right">When</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-zinc-100">
        {traces.map((t) => {
          const score = evalScoreOf(t);
          return (
            <tr key={t.id} className="hover:bg-zinc-50">
              <td className="max-w-md py-2 pr-3">
                <Link href={`/traces/${t.id}`} className="block truncate font-medium hover:underline">
                  {t.query}
                </Link>
              </td>
              <td className="pr-3 text-zinc-500">{t.pipelines?.name ?? "—"}</td>
              <td className="pr-3">
                <span className="inline-flex items-center gap-1">
                  <Badge tone={evalStatusTone(t.eval_status)}>{t.eval_status}</Badge>
                  {score?.hallucination_flag && <Badge tone="red">hallucination</Badge>}
                </span>
              </td>
              <td className={`pr-3 text-right font-mono ${scoreColor(score?.faithfulness)}`}>
                {formatScore(score?.faithfulness)}
              </td>
              <td className={`pr-3 text-right font-mono ${scoreColor(score?.answer_relevance)}`}>
                {formatScore(score?.answer_relevance)}
              </td>
              <td className={`pr-3 text-right font-mono ${scoreColor(score?.context_precision)}`}>
                {formatScore(score?.context_precision)}
              </td>
              <td className="pr-3 text-right text-zinc-500">{t.latency_ms}ms</td>
              <td className="whitespace-nowrap text-right text-xs text-zinc-400">
                {formatDistanceToNow(new Date(t.created_at), { addSuffix: true })}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
