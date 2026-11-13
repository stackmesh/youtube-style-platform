import { fireEvent, render, screen } from "@testing-library/react";
import { Route, Routes, MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import authSource from "../api/auth.ts?raw";
import clientSource from "../api/client.ts?raw";
import { AppShell } from "../components/AppShell";
import { AuthProvider } from "../auth/AuthContext";
import { restoreSession } from "../api/auth";
import { ChannelPage } from "./ChannelPage";
import { LoginPage } from "./LoginPage";
import { ProfilePage } from "./ProfilePage";
import { RegisterPage } from "./RegisterPage";
import type { ReactNode } from "react";

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

function renderWithAuth(element: ReactNode) {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="*" element={element} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("account forms", () => {
  it("rejects a short password before calling registration", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    renderWithAuth(<RegisterPage />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.change(screen.getByLabelText("Handle"), { target: { value: "person" } });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Person" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toContain("10 to 128");
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("/auth/register"))).toBe(false);
  });

  it("rejects a login with a missing email before calling the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    renderWithAuth(<LoginPage />);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect((await screen.findByRole("alert")).textContent).toContain("email");
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("/auth/login"))).toBe(false);
  });

  it("shows the API error when login is rejected", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/auth/login")) {
        return Response.json(
          {
            error: {
              code: "INVALID_CREDENTIALS",
              message: "Email or password is incorrect.",
              details: [],
              request_id: null,
            },
          },
          { status: 401 },
        );
      }
      return Response.json({}, { status: 401 });
    });
    renderWithAuth(<LoginPage />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Email or password is incorrect.");
  });

  it("shows the signed-in name and clears it on logout", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/api/v1/auth/refresh")) {
        return Response.json(session);
      }
      return new Response(null, { status: 204 });
    });
    renderWithAuth(<LoginPage />);
    expect(await screen.findByText("Person")).toBeTruthy();
    expect(screen.getByText("USER")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("link", { name: "Log in" })).toBeTruthy();
    expect(screen.queryByText("Person")).toBeNull();
  });

  it("renders a public channel", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) {
        return Response.json(
          { error: { code: "UNAUTHENTICATED", message: "Authentication is required.", details: [], request_id: null } },
          { status: 401 },
        );
      }
      if (url.includes("/videos") || url.includes("/playlists")) {
        return Response.json({ items: [], next_cursor: null });
      }
      return Response.json({
        handle: "studio",
        name: "Studio",
        description: "Field notes",
        avatar_url: null,
        subscriber_count: 0,
        viewer_subscribed: false,
      });
    });
    render(
      <MemoryRouter initialEntries={["/channels/studio"]}>
        <AuthProvider>
          <Routes>
            <Route path="/channels/:handle" element={<ChannelPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    expect((await screen.findByRole("heading", { name: "Studio" })).textContent).toBe("Studio");
    expect(screen.getByText("Field notes")).toBeTruthy();
  });

  it("asks a signed-out visitor to log in before showing a profile", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({}, { status: 401 }));
    renderWithAuth(<ProfilePage />);
    expect(await screen.findByText(/to see your profile/)).toBeTruthy();
  });
});

describe("session restore", () => {
  it("uses one refresh request when restore runs twice together", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(session));
    const [first, second] = await Promise.all([restoreSession(), restoreSession()]);
    expect(first?.channel.name).toBe("Person");
    expect(second?.email).toBe(first?.email);
    const refreshes = fetchMock.mock.calls.filter((call) => String(call[0]).includes("/auth/refresh"));
    expect(refreshes).toHaveLength(1);
  });
});

describe("token storage", () => {
  it("keeps the access token out of browser storage", () => {
    expect(authSource + clientSource).not.toMatch(/localStorage|sessionStorage/);
  });
});
