"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Field } from "@/components/ui/AuthForm";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fieldErrors } from "@/lib/fieldErrors";

export default function SignupPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [fields, setFields] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <div className="mx-auto max-w-sm px-4 py-20">
      <h1 className="font-display text-3xl font-semibold tracking-tight">Create an account</h1>
      <p className="mt-2 text-sm text-muted">
        Already registered?{" "}
        <Link href="/login" className="text-accent hover:underline">
          Log in
        </Link>
      </p>
      <form
        noValidate
        className="mt-8 space-y-5"
        onSubmit={async (event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const data = {
            name: String(form.get("name") ?? "").trim(),
            email: String(form.get("email") ?? "").trim(),
            password: String(form.get("password") ?? ""),
            location: String(form.get("location") ?? "").trim() || undefined,
          };
          setBusy(true);
          setFields({});
          setMessage(null);
          try {
            await api.signup(data);
            await login(data.email, data.password);
            router.push("/profile");
          } catch (error) {
            const result = fieldErrors(error);
            setFields(result.fields);
            setMessage(result.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field id="name" name="name" label="Name" autoComplete="name" maxLength={50} error={fields.name} />
        <Field id="email" name="email" type="email" label="Email" autoComplete="email" error={fields.email} />
        <Field
          id="password"
          name="password"
          type="password"
          label="Password (8+ characters)"
          autoComplete="new-password"
          error={fields.password}
        />
        <Field id="location" name="location" label="City (optional)" maxLength={60} error={fields.location} />
        {message ? (
          <p role="alert" className="text-sm text-warn">
            {message}
          </p>
        ) : null}
        <button disabled={busy} className="w-full rounded-xl bg-accent py-2.5 font-medium text-bg disabled:opacity-50">
          {busy ? "Creating account…" : "Sign up"}
        </button>
      </form>
    </div>
  );
}
