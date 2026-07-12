"use client";

import type { HealthDaily } from "@/lib/types";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function PipelineHealthChart({ data }: { data: HealthDaily[] }) {
  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-zinc-500">
        No health history yet — daily scores appear after the nightly rollup
        (or once evals start flowing today).
      </p>
    );
  }

  const points = data.map((d) => ({
    date: d.date,
    health: d.health_score === null ? null : Number(d.health_score),
  }));

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer>
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: number) => [v?.toFixed(1), "Health"]} />
          <Line
            type="monotone"
            dataKey="health"
            stroke="#18181b"
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
