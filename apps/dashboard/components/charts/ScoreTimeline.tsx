"use client";

import type { EvalScore } from "@/lib/types";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function ScoreTimeline({ scores }: { scores: EvalScore[] }) {
  if (scores.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-zinc-500">
        No eval scores yet for this pipeline.
      </p>
    );
  }

  const points = [...scores]
    .sort((a, b) => a.computed_at.localeCompare(b.computed_at))
    .map((s) => ({
      time: new Date(s.computed_at).toLocaleTimeString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      } as Intl.DateTimeFormatOptions),
      faithfulness: s.faithfulness === null ? null : Number(s.faithfulness),
      relevance: s.answer_relevance === null ? null : Number(s.answer_relevance),
      precision: s.context_precision === null ? null : Number(s.context_precision),
    }));

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer>
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
          <XAxis dataKey="time" tick={{ fontSize: 11 }} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="faithfulness" stroke="#059669" strokeWidth={2} dot={false} connectNulls />
          <Line type="monotone" dataKey="relevance" stroke="#2563eb" strokeWidth={2} dot={false} connectNulls />
          <Line type="monotone" dataKey="precision" stroke="#d97706" strokeWidth={2} dot={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
