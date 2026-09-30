/**
 * Single HTTP boundary for the SPA.
 *
 * - Cookie session (credentials: "same-origin") + the CSRF header the backend requires.
 * - Every non-2xx becomes an ApiError carrying the server's {code, message, details}.
 * - Network failures become ApiError(status 0, code "network_error").
 * - Requests sent with an Idempotency-Key are retried automatically on network failure with the
 *   SAME key: if the first attempt actually reached the server, the retry replays its response
 *   instead of repeating the side effect. Requests without a key are never auto-retried.
 */

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId?: string;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /** Field-level messages from a 422 validation error, keyed by the last loc segment. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    const fields = (this.details.fields as { loc: string[]; message: string }[] | undefined) ?? [];
    for (const f of fields) out[f.loc[f.loc.length - 1]] = f.message.replace(/^Value error, /, "");
    if (typeof this.details.field === "string") out[this.details.field] = this.message;
    return out;
  }
}

export type Query = Record<string, string | number | boolean | undefined | null | (string | number)[]>;

export function newIdempotencyKey(): string {
  return `ui-${crypto.randomUUID()}`;
}

interface Options {
  method?: string;
  body?: unknown;
  query?: Query;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`/api/v1${path}`, window.location.origin);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, String(x)));
    else url.searchParams.set(k, String(v));
  }
  return url.pathname + url.search;
}

const MAX_NETWORK_RETRIES = 2;

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", "X-Requested-With": "opsflow" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  const retries = opts.idempotencyKey ? MAX_NETWORK_RETRIES : 0;
  let response: Response | undefined;
  for (let attempt = 0; ; attempt++) {
    try {
      response = await fetch(buildUrl(path, opts.query), {
        method,
        headers,
        credentials: "same-origin",
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        signal: opts.signal,
      });
      break;
    } catch (err) {
      if ((err as Error).name === "AbortError") throw err;
      if (attempt >= retries) {
        throw new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again.");
      }
      await new Promise((r) => setTimeout(r, 400 * 2 ** attempt));
    }
  }

  if (response.status === 204) return undefined as T;
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body (e.g. proxy error page) */
  }
  if (!response.ok) {
    const e = (payload as { error?: { code: string; message: string; details?: Record<string, unknown> }; request_id?: string })
      ?.error;
    throw new ApiError(
      response.status,
      e?.code ?? "http_error",
      e?.message ?? `Request failed (${response.status})`,
      e?.details ?? {},
      (payload as { request_id?: string })?.request_id,
    );
  }
  return payload as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}
