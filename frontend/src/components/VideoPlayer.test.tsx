import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VideoPlayer } from "./VideoPlayer";

vi.mock("hls.js", () => {
  class FakeHls {
    static isSupported() {
      return true;
    }

    static Events = { MANIFEST_PARSED: "parsed", ERROR: "error" };

    loadSource = vi.fn();
    attachMedia = vi.fn();
    destroy = vi.fn();
    on = vi.fn((event: string, handler: () => void) => {
      if (event === "parsed") {
        handler();
      }
    });
  }
  return { default: FakeHls };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("VideoPlayer", () => {
  it("loads a session and ends it when the player unmounts", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const method = init?.method ?? "GET";
      calls.push(`${method} ${String(input)}`);
      if (method === "DELETE" || String(input).endsWith("/views")) {
        return new Response(null, { status: 204 });
      }
      return Response.json({ expires_at: "2026-11-13T00:00:00+00:00" });
    });
    const view = render(<VideoPlayer videoId="video-1" />);
    expect(await screen.findByText("0:00 / 0:00")).toBeTruthy();
    view.unmount();
    expect(calls.some((call) => call.startsWith("POST") && call.includes("/playback"))).toBe(true);
    expect(calls.some((call) => call.startsWith("DELETE") && call.includes("/playback"))).toBe(true);
    expect(calls.some((call) => call.includes("/views"))).toBe(false);
  });

  it("shows the placeholder when the video is not ready", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          error: {
            code: "VIDEO_NOT_READY",
            message: "Video is not ready.",
            details: [],
            request_id: null,
          },
        },
        { status: 409 },
      ),
    );
    render(<VideoPlayer videoId="video-2" />);
    expect(await screen.findByText("No media file is attached. Playback is not available.")).toBeTruthy();
  });
});
