/**
 * Minimal browser reference client for the current M-Agent Chat API.
 *
 * This is documentation sample code, not a versioned SDK. It deliberately uses
 * fetch() instead of EventSource so authenticated SSE requests can carry a
 * Bearer token without placing that token in the URL.
 */

export type JsonObject = Record<string, unknown>;

export interface SSEEnvelope {
  run_id?: string;
  thread_id?: string;
  seq: number;
  timestamp: string;
  type: string;
  payload: JsonObject;
}

export interface RunAcceptedResponse {
  run_id: string;
  status: "queued" | string;
  thread_id: string;
  user_id: string | null;
  events_url: string;
  result_url: string;
}

export interface RunSnapshot {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  thread_id: string;
  result: (JsonObject & { answer?: string }) | null;
  error: string | null;
}

export class MAgentHttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
  ) {
    // Do not display this diagnostic message directly in end-user UI.
    super(`M-Agent request failed with HTTP ${status}`);
    this.name = "MAgentHttpError";
  }
}

export interface StreamOptions {
  signal?: AbortSignal;
  afterSeq?: number;
  onProtocolError?: (error: Error) => void;
}

type EventHandler = (event: SSEEnvelope) => void | Promise<void>;

interface SSEFrame {
  id?: string;
  event?: string;
  data?: string;
}

const delay = (milliseconds: number, signal?: AbortSignal): Promise<void> =>
  new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });

async function readErrorBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => null);
  }
  return response.text().catch(() => "");
}

async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });

      // M-Agent emits LF-delimited frames. Normalizing CRLF also makes the
      // parser work when an intermediary rewrites line endings.
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");

        if (!block || block.startsWith(":")) continue;
        const frame: SSEFrame = {};
        const data: string[] = [];
        for (const line of block.split("\n")) {
          const separator = line.indexOf(":");
          const field = separator >= 0 ? line.slice(0, separator) : line;
          const valueText = separator >= 0 ? line.slice(separator + 1).replace(/^ /, "") : "";
          if (field === "id") frame.id = valueText;
          if (field === "event") frame.event = valueText;
          if (field === "data") data.push(valueText);
        }
        if (data.length > 0) frame.data = data.join("\n");
        yield frame;
      }

      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}

export class MAgentBrowserClient {
  private readonly baseUrl: URL;

  constructor(baseUrl: string, private token: string) {
    this.baseUrl = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  }

  /** Replace an expired token after the application completes its login flow. */
  setToken(token: string): void {
    this.token = token;
  }

  private url(path: string): URL {
    return new URL(path.replace(/^\//, ""), this.baseUrl);
  }

  private headers(extra?: HeadersInit): Headers {
    const headers = new Headers(extra);
    headers.set("Authorization", `Bearer ${this.token}`);
    return headers;
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = this.headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(this.url(path), { ...init, headers, credentials: "omit" });
    if (!response.ok) {
      throw new MAgentHttpError(response.status, await readErrorBody(response));
    }
    return (await response.json()) as T;
  }

  /**
   * This mutation is intentionally attempted once. The server does not support
   * an idempotency key, so callers must not automatically replay it after an
   * ambiguous network failure.
   */
  createRun(threadId: string, message: string, signal?: AbortSignal): Promise<RunAcceptedResponse> {
    return this.json<RunAcceptedResponse>("/v1/chat/runs", {
      method: "POST",
      signal,
      body: JSON.stringify({ thread_id: threadId, message }),
    });
  }

  getRun(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
    return this.json<RunSnapshot>(`/v1/chat/runs/${encodeURIComponent(runId)}`, { signal });
  }

  streamRun(
    runId: string,
    onEvent: EventHandler,
    options: StreamOptions = {},
  ): Promise<number> {
    return this.streamWithReconnect(
      `/v1/chat/runs/${encodeURIComponent(runId)}/events`,
      onEvent,
      (event) => event.type === "run_completed" || event.type === "run_failed",
      { ...options, afterSeq: Math.max(0, options.afterSeq ?? 0) },
    );
  }

  streamThread(
    threadId: string,
    onEvent: EventHandler,
    options: StreamOptions = {},
  ): Promise<number> {
    return this.streamWithReconnect(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/events`,
      onEvent,
      () => false,
      { ...options, afterSeq: options.afterSeq ?? -1 },
    );
  }

  private async streamWithReconnect(
    path: string,
    onEvent: EventHandler,
    isTerminal: (event: SSEEnvelope) => boolean,
    options: StreamOptions,
  ): Promise<number> {
    let lastProcessedSeq = options.afterSeq ?? 0;
    let consecutiveFailures = 0;

    while (!options.signal?.aborted) {
      const url = this.url(path);
      // The server does not consume Last-Event-ID; after_seq is the recovery contract.
      url.searchParams.set("after_seq", String(lastProcessedSeq));

      try {
        const response = await fetch(url, {
          method: "GET",
          headers: this.headers({ Accept: "text/event-stream" }),
          signal: options.signal,
          cache: "no-store",
          credentials: "omit",
        });
        if (!response.ok) {
          throw new MAgentHttpError(response.status, await readErrorBody(response));
        }
        if (!response.body) throw new Error("SSE response body is unavailable");

        for await (const frame of parseSSE(response.body)) {
          if (!frame.data) continue;
          let event: SSEEnvelope;
          try {
            event = JSON.parse(frame.data) as SSEEnvelope;
            if (!Number.isSafeInteger(event.seq) || typeof event.type !== "string") {
              throw new Error("SSE envelope has no valid seq/type");
            }
          } catch (error) {
            options.onProtocolError?.(error instanceof Error ? error : new Error(String(error)));
            // Advancing from a valid SSE id avoids an infinite reconnect loop on
            // one malformed JSON event. The final snapshot remains authoritative.
            const frameSeq = Number(frame.id);
            if (Number.isSafeInteger(frameSeq)) lastProcessedSeq = Math.max(lastProcessedSeq, frameSeq);
            continue;
          }

          if (event.seq <= lastProcessedSeq) continue;
          await onEvent(event);
          // Commit the sequence only after the handler applies the event.
          lastProcessedSeq = event.seq;
          consecutiveFailures = 0;
          if (isTerminal(event)) return lastProcessedSeq;
        }
      } catch (error) {
        if (options.signal?.aborted) break;
        if (error instanceof MAgentHttpError) {
          // 401 requires login; 404 means absent or not visible. Other 4xx
          // responses are request errors, not reconnect candidates.
          if (error.status < 500) throw error;
        }
        consecutiveFailures += 1;
      }

      const cappedAttempt = Math.min(consecutiveFailures, 5);
      const backoffMs = Math.min(10_000, 500 * 2 ** cappedAttempt);
      const jitterMs = Math.floor(Math.random() * 250);
      await delay(backoffMs + jitterMs, options.signal);
    }

    return lastProcessedSeq;
  }
}

// Example usage:
// const controller = new AbortController();
// const api = new MAgentBrowserClient("https://api.example.com", sessionToken);
// const run = await api.createRun("demo-thread", "Hello", controller.signal);
// await api.streamRun(run.run_id, (event) => {
//   if (event.type === "assistant_message") {
//     renderFinalAnswer(String(event.payload.answer ?? ""));
//   }
// }, { signal: controller.signal });
// On logout: controller.abort(); then POST /v1/auth/logout with the same token.
