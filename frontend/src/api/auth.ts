import { apiRequest, getAccessToken, setAccessToken } from "./client";

export type ChannelSummary = {
  handle: string;
  name: string;
  description: string;
  avatar_url: string | null;
  subscriber_count: number;
  viewer_subscribed: boolean;
};

export type SessionUser = {
  id: string;
  email: string;
  role: string;
  channel: ChannelSummary;
};

export type SessionResponse = {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  user: SessionUser;
};

export type MeResponse = SessionUser & {
  quota: {
    bytes_committed: number;
    bytes_reserved: number;
    bytes_limit: number;
  };
};

export function rememberSession(session: SessionResponse): SessionUser {
  setAccessToken(session.access_token);
  return session.user;
}

export function clearSession(): void {
  setAccessToken(null);
}

export async function registerAccount(input: {
  email: string;
  password: string;
  handle: string;
  name: string;
}): Promise<SessionUser> {
  const session = await apiRequest<SessionResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return rememberSession(session);
}

export async function loginAccount(email: string, password: string): Promise<SessionUser> {
  const session = await apiRequest<SessionResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  return rememberSession(session);
}

let restoreInFlight: Promise<SessionUser | null> | null = null;

export function restoreSession(): Promise<SessionUser | null> {
  if (restoreInFlight) {
    return restoreInFlight;
  }
  restoreInFlight = apiRequest<SessionResponse>("/api/v1/auth/refresh", { method: "POST" })
    .then((session) => rememberSession(session))
    .catch(() => {
      clearSession();
      return null;
    })
    .finally(() => {
      restoreInFlight = null;
    });
  return restoreInFlight;
}

export async function logoutAccount(): Promise<void> {
  try {
    if (getAccessToken()) {
      await apiRequest<void>("/api/v1/auth/logout", { method: "POST" }, true);
    }
  } finally {
    clearSession();
  }
}

export async function currentUser(): Promise<MeResponse> {
  return apiRequest<MeResponse>("/api/v1/auth/me", {}, true);
}
