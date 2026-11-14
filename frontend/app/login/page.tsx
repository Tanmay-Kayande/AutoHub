"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Field } from "@/components/ui/AuthForm";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function safeNext(next: string | null): string {
  // Only same-site paths: "/x" but not "//evil.example".
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/profile";
}

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="mt-8 space-y-5"
      onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        setBusy(true);
        setError(null);
        try {
          await login(String(form.get("email")), String(form.get("password")));
          router.push(safeNext(next));
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) setError("Email or password is incorrect.");
          else setError(e instanceof ApiError ? e.message : "Login failed.");
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field id="email" name="email" type="email" label="Email" autoComplete="email" required />
      <Field id="password" name="password" type="password" label="Password" autoComplete="current-password" required />
      {error ? (
        <p role="alert" className="text-sm text-warn">
          {error}
        </p>
      ) : null}
      <button disabled={busy} className="w-full rounded-xl bg-accent py-2.5 font-medium text-bg disabled:opacity-50">
        {busy ? "Signing in…" : "Log in"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="mx-auto max-w-sm px-4 py-20">
      <h1 className="font-display text-3xl font-semibold tracking-tight">Log in</h1>
      <p className="mt-2 text-sm text-muted">
        New here?{" "}
        <Link href="/signup" className="text-accent hover:underline">
          Create an account
        </Link>
      </p>
      <Suspense>
        <LoginForm />
      </Suspense>
    </div>
  );
}
