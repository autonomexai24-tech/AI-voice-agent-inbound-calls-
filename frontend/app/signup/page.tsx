"use client";

import { FormEvent, useState } from "react";
import { Suspense } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { postJson } from "@/lib/api";
import type { User } from "@/lib/types";

function workspaceSlugPreview(company: string) {
  return company.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function SignupForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const previewSlug = workspaceSlugPreview(company);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      const result = await postJson<{ user: User }>("/api/auth/signup", {
        name: name.trim(),
        company: company.trim(),
        phone_number: phone.trim(),
        email: email.trim().toLowerCase(),
        password,
      });
      if (result.user.tenant_slug) {
        window.localStorage.setItem("rapid_workspace_slug", result.user.tenant_slug);
      }
      router.replace("/dashboard?signup=1");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signup failed");
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-wash px-4 py-10">
      <div className="w-full max-w-md rounded-lg border border-line bg-white p-6 shadow-panel sm:p-8">
        <div className="mb-8">
          <div className="text-sm font-semibold uppercase text-brand">RapidX AI</div>
          <h1 className="mt-2 text-2xl font-semibold text-ink">Create account</h1>
          <p className="mt-2 text-sm text-slate-600">
            Set up your AI receptionist workspace in under a minute.
          </p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Full name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              type="text"
              autoComplete="name"
              required
              minLength={2}
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
            {previewSlug ? (
              <span className="mt-1 block text-xs text-slate-500">
                Workspace slug: <span className="font-semibold text-slate-700">{previewSlug}</span>
              </span>
            ) : null}
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Company name</span>
            <input
              value={company}
              onChange={(event) => setCompany(event.target.value)}
              type="text"
              autoComplete="organization"
              required
              minLength={2}
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Phone number</span>
            <input
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              type="tel"
              autoComplete="tel"
              required
              placeholder="+91 98765 43210"
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
          </label>
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
              autoComplete="new-password"
              required
              minLength={8}
              className="focus-ring mt-2 min-h-11 w-full rounded-md border border-line px-3 text-sm shadow-sm"
            />
            <span className="mt-1 block text-xs text-slate-500">Minimum 8 characters.</span>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Confirm password</span>
            <input
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
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
            {loading ? "Creating account…" : "Create account"}
          </button>
        </form>
        <div className="mt-6 text-center text-sm text-slate-600">
          Already have an account?{" "}
          <Link href="/login" className="font-semibold text-brand hover:underline">
            Sign in
          </Link>
        </div>
      </div>
    </main>
  );
}

export default function SignupPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-wash" />}>
      <SignupForm />
    </Suspense>
  );
}
