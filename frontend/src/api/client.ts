// Thin fetch wrapper for bng-api. The session is an HttpOnly cookie; the CSRF token
// lives only in memory (re-issued by GET /api/auth/me after a reload).

let csrf = "";
let onUnauthorized: () => void = () => {};

export function setCsrf(token: string) {
  csrf = token;
}

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detail(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d))
    return d.map((e) => `${(e.loc ?? []).filter((x: unknown) => x !== "body").join(".") || "request"}: ${e.msg}`).join("; ");
  return fallback;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const method = (init.method ?? (init.json !== undefined ? "POST" : "GET")).toUpperCase();
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const r = await fetch(path, { ...init, method, headers, body, credentials: "same-origin" });
  const text = await r.text();
  const data = text && r.headers.get("content-type")?.includes("json") ? JSON.parse(text) : text;
  if (r.status === 401 && !path.startsWith("/api/auth/login")) onUnauthorized();
  if (!r.ok) throw new ApiError(r.status, detail(data, `${r.status} ${r.statusText}`));
  return data as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, json: unknown = {}) => api<T>(path, { json });
export const put = <T>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const patch = <T>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });
