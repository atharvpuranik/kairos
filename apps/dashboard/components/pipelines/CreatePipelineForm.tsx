"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export function CreatePipelineForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [framework, setFramework] = useState("custom");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) return;

    // direct insert under RLS policy "own pipelines all"
    const { error } = await supabase
      .from("pipelines")
      .insert({ user_id: user.id, name, framework });
    if (error) {
      setError(error.message);
      setBusy(false);
      return;
    }
    setName("");
    setBusy(false);
    router.refresh();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-center gap-2">
      <Input
        placeholder="Pipeline name (e.g. docs-qa-prod)"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        className="max-w-xs"
        name="pipeline-name"
      />
      <select
        value={framework}
        onChange={(e) => setFramework(e.target.value)}
        className="rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm"
        aria-label="Framework"
      >
        <option value="custom">custom</option>
        <option value="langchain">langchain</option>
        <option value="llamaindex">llamaindex</option>
      </select>
      <Button type="submit" disabled={busy}>
        {busy ? "Creating…" : "Create pipeline"}
      </Button>
      {error && <p className="w-full text-sm text-red-600">{error}</p>}
    </form>
  );
}
