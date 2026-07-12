import { createClient } from "@/lib/supabase/server";
import { SignOutButton } from "@/components/SignOutButton";
import { Activity, Bell, GitBranch, LayoutDashboard, Settings } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";

const nav = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/pipelines", label: "Pipelines", icon: GitBranch },
  { href: "/traces", label: "Traces", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 flex-col border-r border-zinc-200 bg-white">
        <div className="flex items-center gap-2 border-b border-zinc-100 px-4 py-4">
          <Bell className="h-5 w-5" />
          <span className="text-lg font-bold tracking-tight">Kairos</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {nav.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="border-t border-zinc-100 p-3">
          <p className="truncate px-2 pb-2 text-xs text-zinc-400" title={user.email ?? ""}>
            {user.email}
          </p>
          <SignOutButton />
        </div>
      </aside>
      <main className="flex-1 overflow-x-hidden p-6">{children}</main>
    </div>
  );
}
