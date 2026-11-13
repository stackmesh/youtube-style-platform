export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details: { field: string; issue: string }[];
    request_id: string | null;
  };
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: { field: string; issue: string }[];

  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message);
    this.status = status;
    this.code = body.error.code;
    this.details = body.error.details;
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}, authenticated = false): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (authenticated && accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new Error("Could not reach the API.");
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const raw = await response.text();
  if (!raw) {
    if (!response.ok) {
      throw new Error("The request failed.");
    }
    return undefined as T;
  }
  const payload = JSON.parse(raw) as T | ApiErrorBody;
  if (!response.ok) {
    throw new ApiError(response.status, payload as ApiErrorBody);
  }
  return payload as T;
}
