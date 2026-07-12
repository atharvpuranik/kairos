"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import type { ApiKeyInfo } from "@/lib/types";
import { format } from "date-fns";
import { useCallback, useEffect, useState } from "react";

export function ApiKeys() {
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [name, setName] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setKeys(await apiFetch<ApiKeyInfo[]>("/v1/keys"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load keys");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function createKey(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await apiFetch<ApiKeyInfo>("/v1/keys", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setFreshKey(created.key ?? null);
      setName("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create key");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setBusy(true);
    try {
      await apiFetch(`/v1/keys/${id}`, { method: "DELETE" });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to revoke key");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={createKey} className="flex items-center gap-2">
        <Input
          placeholder="Key name (e.g. production)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          className="max-w-xs"
          name="key-name"
        />
        <Button type="submit" disabled={busy}>
          Create key
        </Button>
      </form>

      {freshKey && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
          <p className="text-sm font-semibold text-emerald-800">
            Key created — copy it now, it will not be shown again:
          </p>
          <div className="mt-1 flex items-center gap-2">
            <code data-testid="fresh-key" className="break-all rounded bg-white px-2 py-1 text-xs">
              {freshKey}
            </code>
            <Button variant="outline" onClick={() => navigator.clipboard.writeText(freshKey)}>
              Copy
            </Button>
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      {!loaded ? (
        <p className="text-sm text-zinc-500">Loading keys…</p>
      ) : keys.length === 0 ? (
        <p className="text-sm text-zinc-500">No API keys yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-200 text-left text-xs uppercase tracking-wide text-zinc-500">
              <th className="py-2">Name</th>
              <th>Prefix</th>
              <th>Status</th>
              <th>Created</th>
              <th>Last used</th>
              <th></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {keys.map((k) => (
              <tr key={k.id}>
                <td className="py-2 font-medium">{k.name}</td>
                <td className="font-mono text-xs">{k.key_prefix}…</td>
                <td>
                  <Badge tone={k.is_active ? "green" : "red"}>
                    {k.is_active ? "active" : "revoked"}
                  </Badge>
                </td>
                <td className="text-zinc-500">{format(new Date(k.created_at), "PP")}</td>
                <td className="text-zinc-500">
                  {k.last_used_at ? format(new Date(k.last_used_at), "PPp") : "never"}
                </td>
                <td className="text-right">
                  {k.is_active && (
                    <Button variant="destructive" disabled={busy} onClick={() => revoke(k.id)}>
                      Revoke
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
