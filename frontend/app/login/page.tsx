"use client";

import { FormEvent, useState } from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { postJson } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      await postJson("/api/auth/login", { email, password, tenant_slug: tenantSlug.trim() || undefined });
      router.replace(searchParams.get("next") || "/dashboard");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-wash px-4 py-10">
      <div className="w-full max-w-md rounded-lg border border-line bg-white p-6 shadow-panel sm:p-8">
        <div className="mb-8">
          <div className="text-sm font-semibold uppercase text-brand">RapidX AI</div>
          <h1 className="mt-2 text-2xl font-semibold text-ink">Sign in</h1>
          <p className="mt-2 text-sm text-slate-600">Access your receptionist operations workspace.</p>
        </div>
        <form onSubmit={submit} className="space-y-5">
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Email</span>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="email"
              required
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Password</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              required
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Workspace</span>
            <input
              value={tenantSlug}
              onChange={(event) => setTenantSlug(event.target.value)}
              type="text"
              autoComplete="organization"
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
          </label>
          {error ? (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-danger">
              {error}
            </div>
          ) : null}
          <button
            type="submit"
            disabled={loading}
            className="focus-ring flex min-h-11 w-full items-center justify-center rounded-md bg-brand px-4 text-sm font-semibold text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Signing in" : "Sign in"}
          </button>
        </form>
        <div className="mt-6 text-center text-sm text-slate-600">
          Don&apos;t have an account?{" "}
          <Link href="/signup" className="font-semibold text-brand hover:underline">
            Create account
          </Link>
        </div>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-wash" />}>
      <LoginForm />
    </Suspense>
  );
}
