import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { HomePage } from "./HomePage";
import { SearchPage } from "./SearchPage";
import { StudioPage } from "./StudioPage";
import { WatchPage } from "./WatchPage";

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

const video = {
  id: "video-1",
  title: "Harbour light",
  channel: { handle: "person", name: "Person" },
  view_count: 0,
  description: "Evening",
  tags: ["harbour"],
  visibility: "public",
  processing_status: "draft",
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("catalogue pages", () => {
  it("shows a public video and the media placeholder", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/api/v1/videos")) {
        return Response.json({ items: [video], next_cursor: null });
      }
      return Response.json({ status: "ready", checks: { postgres: "ok", redis: "ok", s3: "ok", worker: "ok" } });
    });
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Harbour light")).toBeTruthy();
    expect(screen.getByText("No media yet")).toBeTruthy();
  });

  it("shows an empty feed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/api/v1/videos")) {
        return Response.json({ items: [], next_cursor: null });
      }
      return Response.json({ status: "ready", checks: { postgres: "ok", redis: "ok", s3: "ok", worker: "ok" } });
    });
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("No published videos yet.")).toBeTruthy();
  });

  it("shows a feed error", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/api/v1/videos")) {
        return Response.json(
          { error: { code: "INTERNAL_ERROR", message: "Something went wrong.", details: [], request_id: null } },
          { status: 500 },
        );
      }
      return Response.json({ status: "ready", checks: { postgres: "ok", redis: "ok", s3: "ok", worker: "ok" } });
    });
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );
    expect((await screen.findByRole("alert")).textContent).toContain("Something went wrong.");
  });

  it("searches and shows an empty result", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ videos: { items: [], next_cursor: null }, channels: { items: [], next_cursor: null } }));
    render(
      <MemoryRouter initialEntries={["/search?q=missing"]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("No results.")).toBeTruthy();
  });

  it("does not show a title when the video is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/auth/refresh")) {
        return Response.json(
          { error: { code: "UNAUTHENTICATED", message: "Authentication is required.", details: [], request_id: null } },
          { status: 401 },
        );
      }
      return Response.json(
        { error: { code: "VIDEO_NOT_FOUND", message: "Video not found.", details: [], request_id: null } },
        { status: 404 },
      );
    });
    render(
      <MemoryRouter initialEntries={["/videos/secret"]}>
        <AuthProvider>
          <Routes>
            <Route path="/videos/:videoId" element={<WatchPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    expect((await screen.findByRole("alert")).textContent).toBe("Video not found.");
    expect(screen.queryByText("Harbour light")).toBeNull();
  });

  it("saves a draft with the selected visibility", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        return Response.json(session);
      }
      if (url.includes("/me/videos")) {
        return Response.json({ items: [], next_cursor: null });
      }
      if (url.includes("/grants")) {
        return Response.json({ items: [] });
      }
      if (url.endsWith("/api/v1/videos") && init?.method === "POST") {
        return Response.json({ ...video, id: "created", visibility: "unlisted" });
      }
      if (url.includes("/videos/created")) {
        return Response.json({ ...video, id: "created", title: "Harbour light", visibility: "unlisted" });
      }
      return Response.json({ items: [], next_cursor: null });
    });
    render(
      <MemoryRouter initialEntries={["/studio"]}>
        <AuthProvider>
          <Routes>
            <Route path="/studio" element={<StudioPage />} />
            <Route path="/studio/:videoId" element={<StudioPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Studio" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Harbour light" } });
    fireEvent.change(screen.getByLabelText("Visibility"), { target: { value: "unlisted" } });
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
    expect(await screen.findByText(/Draft saved/)).toBeTruthy();
    const createCall = fetchMock.mock.calls.find((call) => {
      const method = (call[1] as RequestInit | undefined)?.method;
      return String(call[0]).endsWith("/api/v1/videos") && method === "POST";
    });
    expect(String(createCall?.[1] && (createCall[1] as RequestInit).body)).toContain("unlisted");
  });
});
