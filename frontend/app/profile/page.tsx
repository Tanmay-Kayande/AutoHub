"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Field } from "@/components/ui/AuthForm";
import { Skeleton } from "@/components/ui/States";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fieldErrors } from "@/lib/fieldErrors";
import { formatDate } from "@/lib/format";
import type { Conversation, User } from "@/lib/types";

function Notice({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <p role={ok ? "status" : "alert"} className={`text-sm ${ok ? "text-accent" : "text-warn"}`}>
      {children}
    </p>
  );
}

function EditProfile({ user, token, onSaved }: { user: User; token: string; onSaved: (user: User) => void }) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="mt-4 space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        setBusy(true);
        setErrors({});
        setNotice(null);
        try {
          const saved = await api.updateMe(token, {
            name: String(form.get("name") ?? "").trim(),
            location: String(form.get("location") ?? "").trim() || null,
            gender: String(form.get("gender") ?? "") || null,
          });
          onSaved(saved);
          setNotice({ ok: true, text: "Profile saved." });
        } catch (error) {
          const { fields, message } = fieldErrors(error);
          setErrors(fields);
          if (message) setNotice({ ok: false, text: message });
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field id="name" name="name" label="Name" defaultValue={user.name} required maxLength={50} error={errors.name} />
      <Field id="location" name="location" label="City" defaultValue={user.location ?? ""} maxLength={60} error={errors.location} />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="gender" className="text-sm text-muted">
          Gender
        </label>
        <select
          id="gender"
          name="gender"
          defaultValue={user.gender ?? ""}
          className="rounded-xl border border-line bg-surface px-3 py-2.5 text-text focus:border-accent/60 focus:outline-none"
        >
          <option value="">Prefer not to say</option>
          <option value="F">Female</option>
          <option value="M">Male</option>
          <option value="O">Other</option>
        </select>
      </div>
      {notice ? <Notice ok={notice.ok}>{notice.text}</Notice> : null}
      <button type="submit" disabled={busy} className="rounded-full bg-accent px-5 py-2 text-sm font-medium text-bg disabled:opacity-40">
        {busy ? "Saving…" : "Save profile"}
      </button>
    </form>
  );
}

function ChangePassword({ token }: { token: string }) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="mt-4 space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        const formElement = event.currentTarget;
        const form = new FormData(formElement);
        const next = String(form.get("new_password") ?? "");
        setErrors({});
        setNotice(null);
        if (next !== String(form.get("confirm_password") ?? "")) {
          setErrors({ confirm_password: "The two new passwords do not match." });
          return;
        }
        setBusy(true);
        try {
          await api.updateMe(token, { current_password: String(form.get("current_password") ?? ""), new_password: next });
          formElement.reset();
          setNotice({ ok: true, text: "Password changed." });
        } catch (error) {
          const { fields, message } = fieldErrors(error);
          setErrors(fields);
          if (message) setNotice({ ok: false, text: message });
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field id="current_password" name="current_password" type="password" label="Current password" autoComplete="current-password" required error={errors.current_password} />
      <Field id="new_password" name="new_password" type="password" label="New password (at least 8 characters)" autoComplete="new-password" required minLength={8} maxLength={100} error={errors.new_password} />
      <Field id="confirm_password" name="confirm_password" type="password" label="Repeat the new password" autoComplete="new-password" required error={errors.confirm_password} />
      {notice ? <Notice ok={notice.ok}>{notice.text}</Notice> : null}
      <button type="submit" disabled={busy} className="rounded-full border border-line px-5 py-2 text-sm hover:bg-surface-2 disabled:opacity-40">
        {busy ? "Changing…" : "Change password"}
      </button>
    </form>
  );
}

export default function ProfilePage() {
  const { user, token, ready, logout, updateUser } = useAuth();
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[] | null>(null);

  useEffect(() => {
    if (ready && !user) router.replace("/login?next=/profile");
  }, [ready, user, router]);

  useEffect(() => {
    if (!token) return;
    api
      .conversations(token)
      .then(setConversations)
      .catch(() => setConversations([]));
  }, [token]);

  if (!ready || !user) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-20">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <h1 className="font-display text-3xl font-semibold tracking-tight">{user.name}</h1>
      <dl className="mt-6 grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
        <dt className="text-muted">Email</dt>
        <dd>{user.email}</dd>
        {user.location ? (
          <>
            <dt className="text-muted">City</dt>
            <dd>{user.location}</dd>
          </>
        ) : null}
        <dt className="text-muted">Member since</dt>
        <dd>{formatDate(user.created_at)}</dd>
      </dl>
      <button
        onClick={() => {
          logout();
          router.push("/");
        }}
        className="mt-6 rounded-full border border-line px-4 py-1.5 text-sm hover:bg-surface-2"
      >
        Log out
      </button>

      {user.role === "admin" ? (
        <Link href="/admin" className="ml-3 mt-6 inline-block text-sm text-accent hover:underline">
          Admin
        </Link>
      ) : null}

      {token ? (
        <div className="mt-14 grid gap-12 md:grid-cols-2">
          <section>
            <h2 className="font-display text-xl">Edit profile</h2>
            <EditProfile key={user.id} user={user} token={token} onSaved={updateUser} />
          </section>
          <section>
            <h2 className="font-display text-xl">Password</h2>
            <ChangePassword token={token} />
          </section>
        </div>
      ) : null}

      <section className="mt-14">
        <h2 className="font-display text-xl">Your questions</h2>
        {conversations === null ? (
          <Skeleton className="mt-4 h-24 w-full" />
        ) : conversations.length === 0 ? (
          <p className="mt-3 text-sm text-muted">
            No conversations yet.{" "}
            <Link href="/ask" className="text-accent hover:underline">
              Ask AutoHub something
            </Link>
            .
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-line rounded-2xl border border-line">
            {conversations.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-4 p-4 text-sm">
                <span className="truncate">{c.title ?? "Conversation"}</span>
                <span className="shrink-0 text-xs text-muted">{formatDate(c.updated_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
