"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { sourceLabel } from "@/lib/format";
import { askStream } from "@/lib/stream";
import type { AnswerSegment, Evidence } from "@/lib/types";

interface Turn {
  id: number;
  question: string;
  status: "thinking" | "done" | "error";
  segments: AnswerSegment[];
  evidence: Evidence[];
  missing: string[];
  error?: { code: string; message: string };
}

const SEGMENT_STYLE: Record<AnswerSegment["kind"], { label: string; className: string }> = {
  verified_fact: { label: "Verified · brochure", className: "border-l-2 border-accent pl-4" },
  sourced_info: { label: "Sourced", className: "border-l-2 border-line pl-4" },
  explanation: { label: "AutoHub's take", className: "pl-4 text-muted italic" },
};

function EvidenceItem({ item, anchor }: { item: Evidence; anchor: string }) {
  const source = item.source;
  return (
    <li id={anchor} className="scroll-mt-24 rounded-xl border border-line bg-bg/40 p-3 text-xs target:border-accent/60">
      <div className="flex items-center gap-2">
        <span className="tabular rounded bg-surface-2 px-1.5 py-0.5 text-text">{item.id}</span>
        <span className="text-muted">
          {item.kind === "note" ? "Not in AutoHub's data" : item.kind === "catalogue" ? "AutoHub catalogue" : sourceLabel(source.type)}
          {source.page ? ` · page ${source.page}` : ""}
        </span>
        {source.url ? (
          <a href={String(source.url)} target="_blank" rel="noreferrer" className="ml-auto text-accent hover:underline">
            Brochure ↗
          </a>
        ) : null}
      </div>
      <p className="mt-2 whitespace-pre-line text-muted">{item.text}</p>
    </li>
  );
}

function Answer({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState(false);
  const cited = new Set(turn.segments.flatMap((s) => s.evidence_ids));
  const shown = turn.evidence.filter((e) => cited.has(e.id) || e.kind === "note");
  const prefix = `t${turn.id}-`;
  return (
    <div className="space-y-4">
      {turn.segments.map((segment, index) => {
        const style = SEGMENT_STYLE[segment.kind];
        return (
          <div key={index} className={style.className}>
            <p className="mb-1 text-[11px] uppercase tracking-[0.15em] text-muted not-italic">{style.label}</p>
            <p className="leading-relaxed">
              {segment.text}{" "}
              {segment.evidence_ids.map((id) => (
                <a
                  key={id}
                  href={`#${prefix}${id}`}
                  onClick={() => setOpen(true)}
                  className="tabular ml-0.5 rounded bg-surface-2 px-1 py-0.5 text-[11px] text-accent not-italic hover:bg-accent/20"
                >
                  {id}
                </a>
              ))}
            </p>
          </div>
        );
      })}
      {turn.missing.length ? (
        <div className="rounded-xl border border-line bg-surface-2/50 p-3 text-sm">
          <p className="text-[11px] uppercase tracking-[0.15em] text-muted">Not in AutoHub&apos;s data</p>
          <ul className="mt-1 list-disc pl-5 text-muted">
            {turn.missing.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {shown.length ? (
        <div>
          <button onClick={() => setOpen((v) => !v)} className="text-xs text-accent hover:underline" aria-expanded={open}>
            {open ? "Hide" : "Show"} evidence ({shown.length})
          </button>
          {open ? (
            <ul className="mt-3 space-y-2">
              {shown.map((item) => (
                <EvidenceItem key={item.id} item={item} anchor={`${prefix}${item.id}`} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function Chat({
  modelId,
  suggestions,
  initialQuestion,
  compact = false,
}: {
  modelId?: number;
  suggestions: string[];
  initialQuestion?: string;
  compact?: boolean;
}) {
  const { token } = useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const conversation = useRef<string | undefined>(undefined);
  const counter = useRef(0);
  const asked = useRef(false);
  const end = useRef<HTMLDivElement>(null);

  const update = (id: number, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));

  async function ask(question: string) {
    const text = question.trim();
    if (text.length < 2 || busy) return;
    const id = ++counter.current;
    setTurns((all) => [...all, { id, question: text, status: "thinking", segments: [], evidence: [], missing: [] }]);
    setDraft("");
    setBusy(true);
    try {
      await askStream(
        { question: text, model_id: modelId, conversation_id: conversation.current },
        {
          onEvent: (name, data) => {
            if (name === "evidence") update(id, { evidence: data as Evidence[] });
            else if (name === "segment") update(id, (t) => ({ segments: [...t.segments, data as AnswerSegment] }));
            else if (name === "done") {
              const done = data as { conversation_id: string; missing: string[] };
              conversation.current = done.conversation_id;
              update(id, { status: "done", missing: done.missing });
            } else if (name === "error") {
              const err = data as { code: string; message: string };
              update(id, { status: "error", error: err });
            }
          },
        },
        { token },
      );
    } catch (error) {
      const err =
        error instanceof ApiError
          ? { code: error.code, message: error.message }
          : { code: "error", message: "The answer could not be loaded." };
      update(id, { status: "error", error: err });
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (initialQuestion && !asked.current) {
      asked.current = true;
      void ask(initialQuestion);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuestion]);

  // Follow the newest answer inside the chat only; never move the page on first render.
  useEffect(() => {
    if (!turns.length) return;
    const list = end.current?.parentElement;
    if (compact && list) list.scrollTop = list.scrollHeight;
    else end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [turns, compact]);

  return (
    <div className="flex flex-col gap-6">
      {turns.length === 0 ? (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => void ask(s)}
              className="rounded-full border border-line px-3 py-1.5 text-left text-sm text-muted hover:border-accent/40 hover:text-text"
            >
              {s}
            </button>
          ))}
        </div>
      ) : null}

      <div className={`space-y-8 ${compact ? "max-h-[60vh] overflow-y-auto pr-2" : ""}`} aria-live="polite">
        {turns.map((turn) => (
          <article key={turn.id} className="space-y-4">
            <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-surface-2 px-4 py-2.5">{turn.question}</p>
            {turn.status === "thinking" && turn.segments.length === 0 ? (
              <p className="animate-pulse text-sm text-muted">Checking AutoHub&apos;s data…</p>
            ) : null}
            {turn.status === "error" ? (
              <div role="alert" className="rounded-xl border border-warn/30 bg-warn/5 p-4 text-sm">
                <p>
                  {turn.error?.code === "rate_limited"
                    ? "You're asking faster than AutoHub can answer. Wait a moment and try again."
                    : turn.error?.code === "upstream_unavailable"
                      ? "The assistant is temporarily unavailable (usage limit reached)."
                      : turn.error?.message}
                </p>
                <button onClick={() => void ask(turn.question)} className="mt-2 text-accent hover:underline" disabled={busy}>
                  Retry
                </button>
              </div>
            ) : null}
            {turn.segments.length ? <Answer turn={turn} /> : null}
          </article>
        ))}
        <div ref={end} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void ask(draft);
        }}
        className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2"
      >
        <label htmlFor={compact ? "ask-panel" : "ask-page"} className="sr-only">
          Your question
        </label>
        <textarea
          id={compact ? "ask-panel" : "ask-page"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(draft);
            }
          }}
          rows={1}
          maxLength={1000}
          placeholder="Ask about specs, features or which car fits you…"
          className="max-h-40 min-h-[2.5rem] flex-1 resize-none bg-transparent px-3 py-2 text-sm placeholder:text-muted/70 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || draft.trim().length < 2}
          className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-bg disabled:opacity-40"
        >
          {busy ? "…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
