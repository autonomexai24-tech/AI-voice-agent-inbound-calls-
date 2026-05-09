"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { apiFetch, postJson } from "@/lib/api";
import type { User } from "@/lib/types";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/calls", label: "Calls" },
  { href: "/bookings", label: "Bookings" },
  { href: "/settings/agent", label: "Agent" },
  { href: "/settings/voice", label: "Voice" },
  { href: "/settings/business", label: "Business" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;

    apiFetch<{ user: User }>("/api/auth/me")
      .then((data) => {
        if (active) {
          setUser(data.user);
          setReady(true);
        }
      })
      .catch(() => {
        router.replace("/login");
      });

    return () => {
      active = false;
    };
  }, [router]);

  const activeLabel = useMemo(() => {
    return navItems.find((item) => pathname.startsWith(item.href))?.label || "Dashboard";
  }, [pathname]);

  async function logout() {
    await postJson<{ ok: boolean }>("/api/auth/logout", {});
    router.replace("/login");
    router.refresh();
  }

  if (!ready) {
    return (
      <main className="min-h-screen bg-wash p-6">
        <div className="mx-auto max-w-6xl rounded-lg border border-line bg-white p-8 shadow-panel">
          <div className="h-3 w-32 animate-pulse rounded bg-slate-200" />
          <div className="mt-6 h-24 animate-pulse rounded bg-slate-100" />
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-wash lg:flex">
      <aside className="border-b border-line bg-white lg:fixed lg:inset-y-0 lg:left-0 lg:w-64 lg:border-b-0 lg:border-r">
        <div className="flex min-h-16 items-center justify-between px-4 lg:block lg:min-h-0 lg:px-5 lg:py-6">
          <Link href="/dashboard" className="focus-ring rounded text-lg font-semibold text-ink">
            RapidX AI
          </Link>
          <div className="text-sm font-medium text-slate-600 lg:hidden">{activeLabel}</div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:block lg:px-3 lg:pb-0">
          {navItems.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`focus-ring flex min-h-10 shrink-0 items-center rounded-md px-3 text-sm font-medium transition lg:mb-1 ${
                  active ? "bg-teal-50 text-brand" : "text-slate-700 hover:bg-slate-50 hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="hidden border-t border-line p-5 lg:absolute lg:bottom-0 lg:left-0 lg:right-0 lg:block">
          <div className="truncate text-sm font-semibold text-ink">{user?.tenant_name || "Tenant"}</div>
          <div className="mt-1 truncate text-xs text-slate-500">{user?.email}</div>
          <button
            type="button"
            onClick={logout}
            className="focus-ring mt-4 min-h-9 rounded-md border border-line bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </aside>
      <div className="lg:pl-64">
        <header className="sticky top-0 z-10 border-b border-line bg-white/95 backdrop-blur">
          <div className="flex min-h-16 items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase text-slate-500">Tenant</div>
              <div className="truncate text-sm font-semibold text-ink">{user?.tenant_name}</div>
            </div>
            <button
              type="button"
              onClick={logout}
              className="focus-ring rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 lg:hidden"
            >
              Sign out
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
