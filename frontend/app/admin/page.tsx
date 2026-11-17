"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ErrorState, Skeleton } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { externalHref, formatDate } from "@/lib/format";
import type { Brochure, PipelineRun, User } from "@/lib/types";

const TABS = ["pipeline", "users", "brochures"] as const;
type Tab = (typeof TABS)[number];
const STAGES = ["discover", "download", "extract", "images", "index"] as const;
const PAID = new Set(["extract", "images", "index"]);

function message(error: unknown): string {
  return error instanceof ApiError ? error.message : "The request failed.";
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "succeeded"
      ? "border-accent/50 text-accent"
      : status === "running"
        ? "border-line text-text"
        : status === "partial"
          ? "border-warn/40 text-warn"
          : "border-warn/60 text-warn";
  return <span className={`rounded-full border px-2 py-0.5 text-xs ${tone}`}>{status}</span>;
}

function summary(run: PipelineRun): string {
  const stats = (run.stats ?? {}) as Record<string, Record<string, number> | number | undefined>;
  const parts: string[] = [];
  const extract = stats.extract as Record<string, number> | undefined;
  if (extract && Object.keys(extract).length) {
    parts.push(`extract ${Object.entries(extract).map(([k, v]) => `${k} ${v}`).join(", ")}`);
  }
  const download = stats.download as Record<string, number> | undefined;
  if (download && Object.keys(download).length) {
    parts.push(`download ${Object.entries(download).map(([k, v]) => `${k} ${v}`).join(", ")}`);
  }
  const images = stats.images as Record<string, number> | undefined;
  if (images && typeof images.searches === "number") parts.push(`images ${images.searches} searches, ${images.inserted ?? 0} added`);
  if (typeof stats.error_count === "number" && stats.error_count) parts.push(`${stats.error_count} errors`);
  return parts.join(" · ") || (run.error ?? "");
}

function Pipeline({ token }: { token: string }) {
  const [runs, setRuns] = useState<PipelineRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stages, setStages] = useState<string[]>(["discover", "download"]);
  const [brands, setBrands] = useState("");
  const [maxExtractions, setMaxExtractions] = useState("");
  const [maxImages, setMaxImages] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.admin
      .pipelineRuns(token)
      .then((page) => {
        setRuns(page.items);
        setError(null);
      })
      .catch((e) => setError(message(e)));
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const paid = stages.filter((s) => PAID.has(s));
  const limit = (text: string) => (text.trim() === "" ? null : Math.max(0, Number.parseInt(text, 10) || 0));

  async function start() {
    setBusy(true);
    setNotice(null);
    try {
      const list = brands
        .split(",")
        .map((b) => b.trim().toLowerCase())
        .filter(Boolean);
      const { run_id } = await api.admin.startPipeline(token, {
        brands: list.length ? list : null,
        stages: STAGES.filter((s) => stages.includes(s)),
        limits: { max_extractions: limit(maxExtractions), max_image_searches: limit(maxImages) },
      });
      setNotice(`Run ${run_id} started.`);
      setConfirming(false);
      load();
    } catch (e) {
      setNotice(message(e));
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm focus:border-accent/60 focus:outline-none";
  return (
    <div className="space-y-10">
      <section className="rounded-2xl border border-line p-4 sm:p-6">
        <h2 className="font-display text-lg">Start a run</h2>
        <fieldset className="mt-4">
          <legend className="mb-2 text-xs uppercase tracking-[0.15em] text-muted">Stages</legend>
          <div className="flex flex-wrap gap-4 text-sm">
            {STAGES.map((stage) => (
              <label key={stage} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={stages.includes(stage)}
                  onChange={() => {
                    setConfirming(false);
                    setStages((all) => (all.includes(stage) ? all.filter((s) => s !== stage) : [...all, stage]));
                  }}
                />
                {stage}
                {PAID.has(stage) ? <span className="text-xs text-warn">paid API</span> : null}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <label className="text-sm">
            <span className="mb-1 block text-muted">Brands (comma-separated keys; empty = all)</span>
            <input value={brands} onChange={(e) => setBrands(e.target.value)} placeholder="hyundai, tata" className={input} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-muted">Max extractions (empty = default)</span>
            <input inputMode="numeric" value={maxExtractions} onChange={(e) => setMaxExtractions(e.target.value)} className={input} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-muted">Max image searches (empty = default)</span>
            <input inputMode="numeric" value={maxImages} onChange={(e) => setMaxImages(e.target.value)} className={input} />
          </label>
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          {confirming ? (
            <>
              <p className="text-sm text-warn">
                {paid.length
                  ? `This run calls paid APIs (${paid.join(", ")}) within the limits above. Start it?`
                  : "Start this run?"}
              </p>
              <button
                onClick={() => void start()}
                disabled={busy}
                className="rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-bg disabled:opacity-40"
              >
                {busy ? "Starting…" : "Yes, start"}
              </button>
              <button onClick={() => setConfirming(false)} className="text-sm text-muted hover:text-text">
                Cancel
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              disabled={stages.length === 0}
              className="rounded-full border border-line px-4 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40"
            >
              Start run…
            </button>
          )}
          {notice ? (
            <p role="status" className="text-sm text-muted">
              {notice}
            </p>
          ) : null}
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h2 className="font-display text-lg">Recent runs</h2>
          <button onClick={load} className="text-sm text-accent hover:underline">
            Refresh
          </button>
        </div>
        {error ? <ErrorState message={error} onRetry={load} /> : null}
        {runs === null && !error ? <Skeleton className="mt-4 h-40 w-full" /> : null}
        {runs ? (
          <div className="mt-4 overflow-x-auto rounded-2xl border border-line">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <thead className="bg-surface-2 text-xs uppercase tracking-[0.1em] text-muted">
                <tr>
                  <th scope="col" className="p-3">Run</th>
                  <th scope="col" className="p-3">Status</th>
                  <th scope="col" className="p-3">Stages</th>
                  <th scope="col" className="p-3">Started</th>
                  <th scope="col" className="p-3">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="tabular p-3">
                      {run.id} <span className="text-xs text-muted">{run.trigger}</span>
                    </td>
                    <td className="p-3">
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="p-3 text-muted">{((run.options?.stages as string[] | undefined) ?? []).join(", ")}</td>
                    <td className="p-3 text-muted">{when(run.started_at)}</td>
                    <td className="max-w-md p-3 text-xs text-muted">{summary(run)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function Users({ token, me }: { token: string; me: User }) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<number | null>(null);

  const load = useCallback(() => {
    api.admin
      .users(token)
      .then((page) => {
        setUsers(page.items);
        setTotal(page.total);
        setError(null);
      })
      .catch((e) => setError(message(e)));
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function setActive(user: User, active: boolean) {
    setPending(user.id);
    try {
      const saved = await api.admin.setUserActive(token, user.id, active);
      setUsers((all) => all?.map((u) => (u.id === saved.id ? saved : u)) ?? null);
    } catch (e) {
      setError(message(e));
    } finally {
      setPending(null);
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!users) return <Skeleton className="h-40 w-full" />;
  return (
    <>
      <p className="mb-3 text-sm text-muted">
        {total} accounts{total > users.length ? `; showing the first ${users.length}` : ""}. Roles are changed with{" "}
        <code className="text-text">python -m tools.make_admin</code>.
      </p>
      <div className="overflow-x-auto rounded-2xl border border-line">
        <table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="bg-surface-2 text-xs uppercase tracking-[0.1em] text-muted">
            <tr>
              <th scope="col" className="p-3">Name</th>
              <th scope="col" className="p-3">Email</th>
              <th scope="col" className="p-3">Role</th>
              <th scope="col" className="p-3">Joined</th>
              <th scope="col" className="p-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {users.map((user) => (
              <tr key={user.id}>
                <td className="p-3">{user.name}</td>
                <td className="p-3 text-muted">{user.email}</td>
                <td className="p-3">{user.role}</td>
                <td className="p-3 text-muted">{formatDate(user.created_at)}</td>
                <td className="p-3">
                  {user.id === me.id ? (
                    <span className="text-xs text-muted">you</span>
                  ) : (
                    <button
                      onClick={() => void setActive(user, !user.is_active)}
                      disabled={pending === user.id}
                      className={`rounded-full border px-3 py-1 text-xs disabled:opacity-40 ${
                        user.is_active ? "border-line hover:bg-surface-2" : "border-warn/50 text-warn"
                      }`}
                    >
                      {user.is_active ? "Active · deactivate" : "Inactive · activate"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Brochures({ token }: { token: string }) {
  const [brochures, setBrochures] = useState<Brochure[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.admin
      .brochures(token)
      .then((items) => {
        setBrochures(items);
        setError(null);
      })
      .catch((e) => setError(message(e)));
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!brochures) return <Skeleton className="h-40 w-full" />;
  return (
    <>
      <p className="mb-3 text-sm text-muted">{brochures.length} brochures discovered.</p>
      <div className="overflow-x-auto rounded-2xl border border-line">
        <table className="w-full min-w-[44rem] text-left text-sm">
          <thead className="bg-surface-2 text-xs uppercase tracking-[0.1em] text-muted">
            <tr>
              <th scope="col" className="p-3">Brochure</th>
              <th scope="col" className="p-3">Brand</th>
              <th scope="col" className="p-3">Versions</th>
              <th scope="col" className="p-3">Latest extraction</th>
              <th scope="col" className="p-3">Last seen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {brochures.map((b) => {
              const latest = [...b.versions].sort((x, y) => y.id - x.id)[0];
              const href = externalHref(b.source_url);
              return (
                <tr key={b.id}>
                  <td className="p-3">
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                        {b.title ?? b.model_hint ?? b.layout_group}
                      </a>
                    ) : (
                      (b.title ?? b.model_hint ?? b.layout_group)
                    )}
                    {b.is_primary_layout ? null : <span className="ml-2 text-xs text-muted">alt. layout</span>}
                  </td>
                  <td className="p-3 text-muted">{b.adapter}</td>
                  <td className="tabular p-3">{b.versions.length}</td>
                  <td className="p-3">
                    {latest?.extraction_status ? <StatusBadge status={latest.extraction_status} /> : <span className="text-xs text-muted">not extracted</span>}
                  </td>
                  <td className="p-3 text-muted">{formatDate(b.last_seen_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function AdminPage() {
  const { user, token, ready } = useAuth();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("pipeline");

  useEffect(() => {
    if (ready && !user) router.replace("/login?next=/admin");
  }, [ready, user, router]);

  if (!ready || !user || !token) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 px-4 py-20">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (user.role !== "admin") {
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message="This area is for AutoHub administrators." />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">Admin</p>
      <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">Operations</h1>
      <div role="tablist" aria-label="Admin sections" className="mt-8 flex gap-2 border-b border-line">
        {TABS.map((name) => (
          <button
            key={name}
            role="tab"
            id={`tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`panel-${name}`}
            onClick={() => setTab(name)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm capitalize ${
              tab === name ? "border-accent text-text" : "border-transparent text-muted hover:text-text"
            }`}
          >
            {name}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="mt-8">
        {tab === "pipeline" ? <Pipeline token={token} /> : null}
        {tab === "users" ? <Users token={token} me={user} /> : null}
        {tab === "brochures" ? <Brochures token={token} /> : null}
      </div>
    </div>
  );
}
