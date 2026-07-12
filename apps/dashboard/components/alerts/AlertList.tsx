"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";
import type { Alert } from "@/lib/types";
import { formatDistanceToNow } from "date-fns";
import { useRouter } from "next/navigation";
import { useState } from "react";

export function AlertList({ alerts }: { alerts: Alert[] }) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);

  if (alerts.length === 0) {
    return <p className="text-sm text-zinc-500">No active alerts. All pipelines healthy.</p>;
  }

  async function resolve(id: string) {
    setBusyId(id);
    // owner UPDATE allowed by RLS policy "own alerts resolve"
    await createClient().from("alerts").update({ resolved: true }).eq("id", id);
    router.refresh();
    setBusyId(null);
  }

  return (
    <ul className="space-y-2">
      {alerts.map((alert) => (
        <li
          key={alert.id}
          className="flex items-start justify-between gap-3 rounded-md border border-zinc-200 p-3"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Badge tone={alert.severity === "critical" ? "red" : "amber"}>
                {alert.severity}
              </Badge>
              <span className="text-xs text-zinc-400">
                {formatDistanceToNow(new Date(alert.created_at), { addSuffix: true })}
              </span>
            </div>
            <p className="mt-1 text-sm">{alert.message}</p>
            {alert.metric_before != null && alert.metric_after != null && (
              <p className="text-xs text-zinc-500">
                {Number(alert.metric_before).toFixed(2)} → {Number(alert.metric_after).toFixed(2)}
              </p>
            )}
          </div>
          <Button
            variant="outline"
            disabled={busyId === alert.id}
            onClick={() => resolve(alert.id)}
          >
            Resolve
          </Button>
        </li>
      ))}
    </ul>
  );
}
