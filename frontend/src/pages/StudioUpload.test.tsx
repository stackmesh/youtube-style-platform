import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { setAccessToken } from "../api/client";
import { StudioPage } from "./StudioPage";

const session = {
  access_token: "access",
  token_type: "Bearer",
  expires_in: 900,
  user: {
    id: "1",
    email: "person@example.com",
    role: "USER",
    channel: {
      handle: "person",
      name: "Person",
      description: "",
      avatar_url: null,
      subscriber_count: 0,
      viewer_subscribed: false,
    },
  },
};

class FakeXhr {
  static instances: FakeXhr[] = [];
  status = 200;
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  sent: Blob | null = null;

  constructor() {
    FakeXhr.instances.push(this);
  }

  open() {}

  setRequestHeader() {}

  getResponseHeader(name: string) {
    return name.toLowerCase() === "etag" ? '"part-etag"' : null;
  }

  send(body: Blob) {
    this.sent = body;
    this.upload.onprogress?.({ lengthComputable: true, loaded: body.size, total: body.size } as ProgressEvent);
    this.onload?.();
  }

  abort() {
    this.onabort?.();
  }
}

beforeEach(() => {
  FakeXhr.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  setAccessToken(null);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        return Response.json(session);
      }
      if (url.includes("/me/videos") || url.includes("/grants")) {
        return Response.json({ items: [], next_cursor: null });
      }
      if (url.endsWith("/api/v1/videos") && init?.method === "POST") {
        return Response.json({ id: "created", title: "Clip", visibility: "private", processing_status: "draft" });
      }
      if (url.endsWith("/api/v1/videos/created/upload") && init?.method === "POST") {
        return Response.json({
          session_id: "session-1",
          status: "initiated",
          part_size: 16_777_216,
          part_count: 2,
          expected_size: 10,
          expires_at: "2030-01-01T00:00:00Z",
        });
      }
      if (url.endsWith("/api/v1/videos/created/upload/parts")) {
        const body = JSON.parse(String(init?.body)) as { part_numbers: number[] };
        return Response.json({
          parts: body.part_numbers.map((part_number) => ({
            part_number,
            url: `http://s3.localhost/media/originals/created/source?partNumber=${part_number}`,
            content_length: part_number === 1 ? 6 : 4,
            expires_at: "2030-01-01T00:20:00Z",
          })),
        });
      }
      if (url.endsWith("/api/v1/videos/created/upload/complete")) {
        return Response.json({ id: "created", processing_status: "uploaded" });
      }
      if (url.endsWith("/api/v1/videos/created/upload/abort")) {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/api/v1/videos/created/processing")) {
        return Response.json({
          processing_status: "uploaded",
          attempt: 1,
          error_code: null,
          duration_seconds: null,
          width: null,
          height: null,
          auto_thumbnail_ready: false,
        });
      }
      if (url.includes("/videos/created")) {
        return Response.json({
          id: "created",
          title: "Clip",
          description: "Kept",
          tags: [],
          visibility: "private",
          processing_status: "draft",
          channel: { handle: "person", name: "Person" },
          view_count: 0,
        });
      }
      return Response.json({ items: [], next_cursor: null });
    }),
  );
});

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={["/studio"]}>
      <AuthProvider>
        <Routes>
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/studio/:videoId" element={<StudioPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("studio upload", () => {
  it("rejects an unsupported file before creating a video", async () => {
    renderStudio();
    const input = await screen.findByLabelText("Video file");
    const bad = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [bad] } });
    expect(await screen.findByText(/Use an MP4/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Upload video" }));
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/api/v1/videos") && (call[1] as RequestInit)?.method === "POST")).toBe(false);
  });

  it("uploads parts, reports progress, and keeps the title after a stored upload", async () => {
    renderStudio();
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Clip" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Kept" } });
    const file = new File([new Uint8Array(10)], "clip.mp4", { type: "video/mp4" });
    fireEvent.change(screen.getByLabelText("Video file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload video" }));
    expect(await screen.findByText(/Upload stored/)).toBeTruthy();
    expect(screen.getByLabelText("Title")).toHaveProperty("value", "Clip");
    expect(screen.getByLabelText("Description")).toHaveProperty("value", "Kept");
    expect(FakeXhr.instances).toHaveLength(2);
    expect(FakeXhr.instances[0]?.sent?.size).toBe(6);
    expect(FakeXhr.instances[1]?.sent?.size).toBe(4);
    expect(screen.queryByRole("application")).toBeNull();
  });

  it("does not start a second upload while the first request is in flight", async () => {
    let releaseCreate: (value: Response) => void = () => undefined;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        return Response.json(session);
      }
      if (url.endsWith("/api/v1/me/videos")) {
        return Response.json({ items: [], next_cursor: null });
      }
      if (url.endsWith("/api/v1/videos") && init?.method === "POST") {
        return new Promise((resolve) => {
          releaseCreate = resolve;
        });
      }
      return Response.json({ items: [] });
    });
    renderStudio();
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Clip" } });
    fireEvent.change(screen.getByLabelText("Video file"), {
      target: { files: [new File([new Uint8Array(4)], "clip.mp4", { type: "video/mp4" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload video" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload video" }));
    await waitFor(() => {
      const creates = fetchMock.mock.calls.filter(
        (call) => String(call[0]).endsWith("/api/v1/videos") && (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(creates).toHaveLength(1);
    });
    releaseCreate(Response.json({ id: "created", title: "Clip", processing_status: "draft" }));
  });

  it("queues another processing attempt when the video failed", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        return Response.json(session);
      }
      if (url.includes("/me/videos") || url.includes("/grants")) {
        return Response.json({ items: [], next_cursor: null });
      }
      if (url.endsWith("/api/v1/videos/created/processing/retry") && init?.method === "POST") {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/api/v1/videos/created/processing")) {
        return Response.json({
          processing_status: "failed",
          attempt: 1,
          error_code: "invalid_media",
          duration_seconds: null,
          width: null,
          height: null,
          auto_thumbnail_ready: false,
        });
      }
      if (url.includes("/videos/created")) {
        return Response.json({
          id: "created",
          title: "Clip",
          description: "",
          tags: [],
          visibility: "private",
          processing_status: "failed",
          channel: { handle: "person", name: "Person" },
          view_count: 0,
        });
      }
      return Response.json({ items: [], next_cursor: null });
    });
    render(
      <MemoryRouter initialEntries={["/studio/created"]}>
        <AuthProvider>
          <Routes>
            <Route path="/studio" element={<StudioPage />} />
            <Route path="/studio/:videoId" element={<StudioPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Retry processing" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            String(call[0]).endsWith("/processing/retry") &&
            (call[1] as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true);
    });
    expect(await screen.findByText("Processing queued again.")).toBeTruthy();
  });
});
