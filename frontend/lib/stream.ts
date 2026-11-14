// Minimal Server-Sent Events reader over fetch (EventSource cannot POST a body).
import { ApiError, streamUrl } from "./api";

export interface StreamHandlers {
  onEvent: (name: string, data: unknown) => void;
}

export async function askStream(
  body: { question: string; model_id?: number; variant_ids?: number[]; conversation_id?: string },
  { onEvent }: StreamHandlers,
  options: { token?: string | null; signal?: AbortSignal } = {},
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(streamUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      },
      body: JSON.stringify(body),
      signal: options.signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "AutoHub's server could not be reached.");
  }
  if (!response.ok || !response.body) {
    let code = "http_error";
    let message = `Request failed (${response.status}).`;
    try {
      const data = await response.json();
      code = data?.error?.code ?? code;
      message = data?.error?.message ?? message;
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, code, message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let name = "message";
      const data: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) name = line.slice(7).trim();
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (data.length) {
        try {
          onEvent(name, JSON.parse(data.join("\n")));
        } catch {
          onEvent(name, data.join("\n"));
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}
