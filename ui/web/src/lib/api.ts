// The panel's only way to the server (SPEC §9b): same-origin JSON over 127.0.0.1. Every POST carries the per-launch
// token the server put into this page's <meta name="cf-token">; the browser adds the Origin header itself.
const token = document.querySelector<HTMLMetaElement>('meta[name="cf-token"]')?.content ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) { super(message); }
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = undefined;
  try { body = text ? JSON.parse(text) : undefined; } catch { body = text; }
  if (!res.ok) {
    const msg = typeof body === "object" && body && "error" in body ? String((body as { error: unknown }).error) : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, msg, body);
  }
  return body as T;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return handle<T>(await fetch(path, { headers: { Accept: "application/json" }, signal, cache: "no-store" }));
}

// called after every POST (lib/prefetch.ts forgets its kept answers: a save can change any page's data)
const afterPost: (() => void)[] = [];
export function onPost(fn: () => void) { afterPost.push(fn); }

export async function postJson<T>(path: string, data: unknown = {}): Promise<T> {
  try { return await post<T>(path, data); } finally { for (const fn of afterPost) fn(); }
}

async function post<T>(path: string, data: unknown): Promise<T> {
  return handle<T>(await fetch(path, {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-CF-Token": token },
    body: JSON.stringify(data),
  }));
}
