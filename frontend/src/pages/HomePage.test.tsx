import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { HomePage } from "./HomePage";

const healthy = {
  status: "ready",
  checks: { postgres: "ok", redis: "ok", s3: "ok", worker: "ok" },
};

function renderHome() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );
}

describe("home page", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the application shell", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    render(<App />);
    expect(screen.getByText("Playback")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Service status" })).toBeTruthy();
    expect(screen.getByPlaceholderText("Search is not available yet")).toBeTruthy();
  });

  it("shows a loading state while the health request is pending", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    renderHome();
    expect(screen.getByText("Checking services")).toBeTruthy();
  });

  it("shows each dependency when the backend is ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(healthy), { status: 200 })),
    );
    renderHome();
    expect(await screen.findByText("All dependencies are ready.")).toBeTruthy();
    expect(screen.getByText("PostgreSQL")).toBeTruthy();
    expect(screen.getByText("Redis")).toBeTruthy();
    expect(screen.getByText("Object storage")).toBeTruthy();
    expect(screen.getByText("Worker")).toBeTruthy();
  });

  it("shows dependency failures returned by the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            status: "not_ready",
            checks: { postgres: "fail", redis: "ok", s3: "ok", worker: "fail" },
          }),
          { status: 503 },
        ),
      ),
    );
    renderHome();
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "One or more dependencies are unavailable.",
    );
    expect(screen.getAllByText("Unavailable")).toHaveLength(2);
  });

  it("shows an error when the health request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    renderHome();
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Could not reach the API.");
  });
});
