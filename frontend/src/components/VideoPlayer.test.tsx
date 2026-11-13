import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VideoPlayer } from "./VideoPlayer";

const hlsBehavior = {
  fatal: false,
  levels: [] as { height: number }[],
  currentLevel: -1,
};

vi.mock("hls.js", () => {
  class FakeHls {
    static isSupported() {
      return true;
    }

    static Events = { MANIFEST_PARSED: "parsed", ERROR: "error" };

    loadSource = vi.fn();
    attachMedia = vi.fn();
    destroy = vi.fn();
    get levels() {
      return hlsBehavior.levels;
    }
    get currentLevel() {
      return hlsBehavior.currentLevel;
    }
    set currentLevel(value: number) {
      hlsBehavior.currentLevel = value;
    }
    on = vi.fn((event: string, handler: (event: string, data?: { fatal?: boolean }) => void) => {
      if (event === "parsed") {
        handler(event);
      }
      if (event === "error" && hlsBehavior.fatal) {
        handler(event, { fatal: true });
      }
    });
  }
  return { default: FakeHls };
});

afterEach(() => {
  cleanup();
  hlsBehavior.fatal = false;
  hlsBehavior.levels = [];
  hlsBehavior.currentLevel = -1;
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

  it("shows a retry after a fatal player error", async () => {
    hlsBehavior.fatal = true;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ expires_at: "2026-11-13T00:00:00+00:00" }),
    );
    render(<VideoPlayer videoId="video-3" />);
    expect(await screen.findByRole("button", { name: "Retry playback" })).toBeTruthy();
  });

  it("lets the viewer pick a rendition when hls.js reports levels", async () => {
    hlsBehavior.levels = [{ height: 360 }, { height: 720 }];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ expires_at: "2026-11-13T00:00:00+00:00" }),
    );
    render(<VideoPlayer videoId="video-4" />);
    const quality = await screen.findByLabelText("Quality");
    fireEvent.change(quality, { target: { value: "1" } });
    expect(hlsBehavior.currentLevel).toBe(1);
  });

  it("ends the session when the signed-in viewer logs out", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      calls.push(`${init?.method ?? "GET"} ${String(input)}`);
      return Response.json({ expires_at: "2026-11-13T00:00:00+00:00" });
    });
    const view = render(<VideoPlayer videoId="video-5" viewerId="person-1" />);
    expect(await screen.findByText("0:00 / 0:00")).toBeTruthy();
    calls.length = 0;
    view.rerender(<VideoPlayer videoId="video-5" viewerId={null} />);
    await waitFor(() => {
      expect(calls.some((call) => call.startsWith("DELETE") && call.includes("/playback"))).toBe(true);
    });
  });
});
