"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AskBar({ placeholder = "Ask anything — “Which SUV has the most powerful engine?”" }: { placeholder?: string }) {
  const router = useRouter();
  const [value, setValue] = useState("");
  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        const question = value.trim();
        if (question.length >= 2) router.push(`/ask?q=${encodeURIComponent(question)}`);
      }}
      className="flex w-full max-w-2xl items-center gap-2 rounded-full border border-line bg-surface/80 p-1.5 pl-5 backdrop-blur"
    >
      <label htmlFor="ask-bar" className="sr-only">
        Ask AutoHub
      </label>
      <input
        id="ask-bar"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        maxLength={1000}
        className="min-w-0 flex-1 bg-transparent py-2 text-sm text-text placeholder:text-muted/70 focus:outline-none"
      />
      <button type="submit" className="rounded-full bg-accent px-5 py-2 text-sm font-medium text-bg hover:brightness-110">
        Ask
      </button>
    </form>
  );
}
