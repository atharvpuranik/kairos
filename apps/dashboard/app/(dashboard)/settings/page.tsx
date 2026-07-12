import { ApiKeys } from "@/components/settings/ApiKeys";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const FREE_TIER_TRACES = 10_000;

export default async function SettingsPage() {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const monthKey = new Date().toISOString().slice(0, 8) + "01";
  const { data: usage } = await supabase
    .from("usage")
    .select("traces_count")
    .eq("month", monthKey)
    .maybeSingle();
  const used = usage?.traces_count ?? 0;
  const pct = Math.min(100, Math.round((used / FREE_TIER_TRACES) * 100));

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-xl font-bold">Settings</h1>

      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm">{user?.email}</p>
          <p className="mt-1 text-xs text-zinc-500">Free tier</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Usage this month</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm">
            <span className="font-bold">{used.toLocaleString()}</span> /{" "}
            {FREE_TIER_TRACES.toLocaleString()} traces
          </p>
          <div className="mt-2 h-2 w-full rounded-full bg-zinc-100">
            <div
              className={`h-2 rounded-full ${pct >= 90 ? "bg-red-500" : "bg-zinc-900"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>API keys</CardTitle>
        </CardHeader>
        <CardContent>
          <ApiKeys />
        </CardContent>
      </Card>
    </div>
  );
}
