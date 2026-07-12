import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(2);
}

export function scoreColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-zinc-400";
  const v = Number(value);
  if (v >= 0.8) return "text-emerald-600";
  if (v >= 0.6) return "text-amber-600";
  return "text-red-600";
}

import type { EvalScore, Trace } from "@/lib/types";

export function evalScoreOf(trace: Pick<Trace, "eval_scores">): EvalScore | undefined {
  const es = trace.eval_scores;
  if (!es) return undefined;
  return Array.isArray(es) ? es[0] : es;
}
