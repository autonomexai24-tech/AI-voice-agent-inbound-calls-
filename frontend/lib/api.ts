export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type ApiOptions = RequestInit & {
  parseAs?: "json" | "text" | "none";
};

export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const hasBody = options.body !== undefined && options.body !== null;

  if (hasBody && !headers.has("Content-Type") && typeof options.body === "string") {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = response.statusText || "Request failed";
    try {
      const data = (await response.json()) as { detail?: unknown; message?: unknown };
      detail = String(data.detail || data.message || detail);
    } catch {
      try {
        detail = await response.text();
      } catch {
        detail = response.statusText || "Request failed";
      }
    }
    throw new ApiError(response.status, detail);
  }

  if (options.parseAs === "none") {
    return undefined as T;
  }

  if (options.parseAs === "text") {
    return (await response.text()) as T;
  }

  return (await response.json()) as T;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
