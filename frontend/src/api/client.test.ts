import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest, setAccessToken } from "./client";

afterEach(() => {
  setAccessToken(null);
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("accepts an empty success body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 200 }));
    await expect(apiRequest("/api/v1/moderation/videos/video-1/hide", { method: "POST" })).resolves.toBeUndefined();
  });

  it("keeps the bearer token in memory", async () => {
    setAccessToken("memory-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ ok: true }));
    await apiRequest("/api/v1/auth/me", {}, true);
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer memory-token");
    expect(window.localStorage.getItem("access_token")).toBeNull();
  });
});
