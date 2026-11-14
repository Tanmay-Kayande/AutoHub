"use client";

export function Field({
  id,
  label,
  error,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { id: string; label: string; error?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm text-muted">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`rounded-xl border bg-surface px-3 py-2.5 text-text focus:border-accent/60 focus:outline-none ${
          error ? "border-warn/60" : "border-line"
        }`}
        {...props}
      />
      {error ? (
        <p id={`${id}-error`} className="text-xs text-warn">
          {error}
        </p>
      ) : null}
    </div>
  );
}
