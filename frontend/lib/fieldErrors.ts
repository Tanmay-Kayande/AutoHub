import { ApiError } from "./api";

/** Map a 422 response's details (`body.name`, `body.email`, …) to per-field messages. */
export function fieldErrors(error: unknown): { fields: Record<string, string>; message: string | null } {
  if (!(error instanceof ApiError)) return { fields: {}, message: "Something went wrong. Please try again." };
  const fields: Record<string, string> = {};
  if (Array.isArray(error.details)) {
    for (const item of error.details as { field?: string; message?: string }[]) {
      const name = item.field?.split(".").pop();
      if (name && item.message && !fields[name]) fields[name] = item.message.replace(/^Value error, /, "");
    }
  }
  return { fields, message: Object.keys(fields).length ? null : error.message };
}
