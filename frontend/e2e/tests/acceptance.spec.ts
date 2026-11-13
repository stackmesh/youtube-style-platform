import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import {
  account,
  bearer,
  expectDenied,
  loginAccount,
  openSession,
  playVideo,
  playlistPaths,
  presignedStatus,
  registerAccount,
  uploadFromStudio,
  writeEvidence,
} from "./support";

const videoFile = process.env.E2E_VIDEO_FILE;
const owner = account("owner");
const viewer = account("viewer");
const stranger = account("stranger");
const grantee = account("grantee");
const moderator = account("mod");
const publicTitle = `e2e public ${owner.handle}`;
const privateTitle = `e2e private ${owner.handle}`;
const playlistTitle = `e2e public playlist ${owner.handle}`;
const privatePlaylistTitle = `e2e private playlist ${owner.handle}`;

const created = {
  publicVideoId: "",
  privateVideoId: "",
  publicPlaylistId: "",
  privatePlaylistId: "",
  presignedBeforeRevoke: "",
  privateSegmentPath: "",
  publicSegmentPath: "",
  presignedBeforeHide: "",
};

let ownerContext: BrowserContext;
let ownerPage: Page;
let viewerContext: BrowserContext | undefined;
let strangerContext: BrowserContext | undefined;
let granteeContext: BrowserContext | undefined;
let moderatorContext: BrowserContext | undefined;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser, request }) => {
  if (!videoFile) {
    throw new Error("E2E_VIDEO_FILE is required");
  }
  if (!process.env.E2E_ADMIN_EMAIL || !process.env.E2E_ADMIN_PASSWORD) {
    throw new Error("E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD are required");
  }
  await registerAccount(request, viewer);
  await registerAccount(request, stranger);
  await registerAccount(request, grantee);
  ownerContext = await browser.newContext();
  ownerPage = await ownerContext.newPage();
});

test.afterAll(async ({ request }) => {
  await viewerContext?.close();
  await strangerContext?.close();
  await granteeContext?.close();
  await moderatorContext?.close();
  await loginAccount(request, owner).catch(() => undefined);
  for (const videoId of [created.publicVideoId, created.privateVideoId]) {
    if (!videoId || !owner.token) {
      continue;
    }
    await request.delete(`/api/v1/videos/${videoId}`, { headers: bearer(owner.token) });
  }
  for (const playlistId of [created.publicPlaylistId, created.privatePlaylistId]) {
    if (!playlistId || !owner.token) {
      continue;
    }
    await request.delete(`/api/v1/playlists/${playlistId}`, { headers: bearer(owner.token) });
  }
  await ownerContext?.close();
});

test("home page renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Playback" })).toBeVisible();
  await expect(page.getByText("All dependencies are ready.")).toBeVisible();
});

test("disabled documentation paths return 404", async ({ request }) => {
  for (const path of ["/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"]) {
    const response = await request.get(path);
    expect(response.status()).toBe(404);
    const body = await response.text();
    expect(body).not.toContain("<title>Playback</title>");
    expect(body).toContain("Not found");
  }
});

test("registration and login", async () => {
  await ownerPage.goto("/register");
  await ownerPage.getByLabel("Email").fill(owner.email);
  await ownerPage.getByLabel("Password").fill(owner.password);
  await ownerPage.getByLabel("Handle").fill(owner.handle);
  await ownerPage.getByLabel("Display name").fill(owner.name);
  await ownerPage.getByRole("button", { name: "Create account" }).click();
  await expect(ownerPage).toHaveURL(new RegExp(`/channels/${owner.handle}$`));
  await expect(ownerPage.getByRole("heading", { name: owner.name })).toBeVisible();
});

test("logout and protected studio", async () => {
  await ownerPage.getByRole("button", { name: "Log out" }).click();
  await expect(ownerPage.getByRole("link", { name: "Log in" })).toBeVisible();
  await ownerPage.goto("/studio");
  await expect(ownerPage.getByText("Log in to manage videos.")).toBeVisible();
  await ownerPage.goto("/login");
  await ownerPage.getByLabel("Email").fill(owner.email);
  await ownerPage.getByLabel("Password").fill(owner.password);
  await ownerPage.getByRole("button", { name: "Log in" }).click();
  await expect(ownerPage).toHaveURL("/profile");
  await ownerPage.goto("/studio");
  await expect(ownerPage.getByRole("heading", { name: "Studio" })).toBeVisible();
});

test("upload, processing, and public pages", async () => {
  created.publicVideoId = await uploadFromStudio(ownerPage, publicTitle, "public", videoFile as string);
  await ownerPage.goto(`/channels/${owner.handle}`);
  await expect(ownerPage.getByRole("link", { name: publicTitle })).toBeVisible();
  await ownerPage.goto(`/videos/${created.publicVideoId}`);
  await expect(ownerPage.getByRole("heading", { name: publicTitle })).toBeVisible();
});

test("search results", async () => {
  await ownerPage.goto("/");
  await ownerPage.getByPlaceholder("Search").fill(publicTitle);
  await ownerPage.getByPlaceholder("Search").press("Enter");
  await expect(ownerPage).toHaveURL(/\/search\?q=/);
  await expect(ownerPage.getByRole("link", { name: publicTitle })).toBeVisible();
});

test("public video playback decodes frames", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const evidence = await playVideo(page, created.publicVideoId, "public-playback.png");
  await writeEvidence("public-playback.json", evidence);
  const paths = await playlistPaths(context.request, created.publicVideoId);
  created.publicSegmentPath = paths.segment;
  const segment = await context.request.get(paths.segment, { maxRedirects: 0 });
  expect(segment.status()).toBe(302);
  created.presignedBeforeHide = segment.headers().location ?? "";
  expect(new URL(created.presignedBeforeHide).hostname).toBe("s3.localhost");
  await context.close();
});

test("likes and unlikes", async () => {
  await ownerPage.goto(`/videos/${created.publicVideoId}`);
  await ownerPage.getByRole("button", { name: /^Like ·/ }).click();
  await expect(ownerPage.getByRole("button", { name: /^Liked ·/ })).toBeVisible();
  await ownerPage.getByRole("button", { name: /^Liked ·/ }).click();
  await expect(ownerPage.getByRole("button", { name: /^Like ·/ })).toBeVisible();
});

test("comments and replies", async () => {
  await ownerPage.goto(`/videos/${created.publicVideoId}`);
  const comment = `e2e comment ${owner.handle}`;
  const reply = `e2e reply ${owner.handle}`;
  await ownerPage.getByLabel("Comment").fill(comment);
  await ownerPage.getByRole("button", { name: "Post comment" }).click();
  await expect(ownerPage.getByText(comment)).toBeVisible();
  await ownerPage.getByRole("button", { name: "Reply" }).click();
  await expect(ownerPage.getByText("Replying to a comment.")).toBeVisible();
  await ownerPage.getByLabel("Comment").fill(reply);
  await ownerPage.getByRole("button", { name: "Post comment" }).click();
  await expect(ownerPage.getByText(reply)).toBeVisible();
});

test("channel subscription", async ({ browser }) => {
  viewerContext = await openSession(browser, viewer);
  const page = await viewerContext.newPage();
  await page.goto(`/channels/${owner.handle}`);
  await page.getByRole("button", { name: "Subscribe" }).click();
  await expect(page.getByRole("button", { name: "Unsubscribe" })).toBeVisible();
  await page.goto("/subscriptions");
  await expect(page.getByRole("link", { name: owner.name, exact: true })).toBeVisible();
});

test("playlist creation and visibility", async ({ browser }) => {
  await ownerPage.goto("/playlists");
  await ownerPage.getByLabel("Title").fill(playlistTitle);
  await ownerPage.getByLabel("Playlist visibility").selectOption("public");
  await ownerPage.getByRole("button", { name: "Create playlist" }).click();
  await expect(ownerPage.getByRole("heading", { name: playlistTitle })).toBeVisible();
  await expect(ownerPage.getByText(/public ·/)).toBeVisible();
  created.publicPlaylistId = ownerPage.url().split("/").pop() ?? "";
  await ownerPage.goto("/playlists");
  await expect(ownerPage.getByRole("link", { name: playlistTitle })).toBeVisible();

  await ownerPage.getByLabel("Title").fill(privatePlaylistTitle);
  await ownerPage.getByLabel("Playlist visibility").selectOption("private");
  await ownerPage.getByRole("button", { name: "Create playlist" }).click();
  await expect(ownerPage.getByRole("heading", { name: privatePlaylistTitle })).toBeVisible();
  await expect(ownerPage.getByText(/private ·/)).toBeVisible();
  created.privatePlaylistId = ownerPage.url().split("/").pop() ?? "";

  const anonymous = await browser.newContext();
  const publicPage = await anonymous.newPage();
  await publicPage.goto(`/playlists/${created.publicPlaylistId}`);
  await expect(publicPage.getByRole("heading", { name: playlistTitle })).toBeVisible();
  const privatePage = await anonymous.newPage();
  await privatePage.goto(`/playlists/${created.privatePlaylistId}`);
  await expect(privatePage.getByRole("alert")).toBeVisible();
  await anonymous.close();
});

test("private playback for owner and approved grantee", async ({ browser }) => {
  created.privateVideoId = await uploadFromStudio(ownerPage, privateTitle, "private", videoFile as string);
  const ownerEvidence = await playVideo(ownerPage, created.privateVideoId, "private-owner.png");
  const token = await accessToken();
  const paths = await playlistPaths(ownerPage.request, created.privateVideoId, token);
  created.privateSegmentPath = paths.segment;
  const segment = await ownerPage.request.get(paths.segment, { maxRedirects: 0, headers: bearer(token) });
  expect(segment.status()).toBe(302);
  const location = segment.headers().location ?? "";
  expect(new URL(location).hostname).toBe("s3.localhost");
  created.presignedBeforeRevoke = location;

  await ownerPage.goto(`/studio/${created.privateVideoId}`);
  await ownerPage.getByLabel("Handle").fill(grantee.handle);
  await ownerPage.getByRole("button", { name: "Grant access" }).click();
  await expect(ownerPage.getByText(`@${grantee.handle}`)).toBeVisible();

  granteeContext = await openSession(browser, grantee);
  const granteePage = await granteeContext.newPage();
  const granteeEvidence = await playVideo(granteePage, created.privateVideoId, "private-grantee.png");
  await writeEvidence("private-playback.json", {
    owner: ownerEvidence,
    grantee: granteeEvidence,
    master: paths.master,
    variant: paths.variant,
    segment: paths.segment,
  });
});

test("private playback denial", async ({ browser }) => {
  strangerContext = await openSession(browser, stranger);
  const anonymous = await browser.newContext();
  for (const context of [anonymous, strangerContext]) {
    await expectDenied(context.request, `/api/v1/videos/${created.privateVideoId}/hls/master.m3u8`);
    await expectDenied(context.request, created.privateSegmentPath);
    const page = await context.newPage();
    await page.goto(`/videos/${created.privateVideoId}`);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.locator("video")).toHaveCount(0);
  }
  await expectDenied(
    strangerContext.request,
    `/api/v1/videos/${created.privateVideoId}/hls/master.m3u8`,
    stranger.token,
  );
  await expectDenied(strangerContext.request, created.privateSegmentPath, stranger.token);
  await anonymous.close();
});

test("revoked grant denies a new playback session", async () => {
  await ownerPage.goto(`/studio/${created.privateVideoId}`);
  await ownerPage.getByRole("button", { name: "Revoke" }).click();
  await expect(ownerPage.getByText("revoked")).toBeVisible();
  if (!granteeContext) {
    throw new Error("grantee session was not created");
  }
  await loginAccount(granteeContext.request, grantee);
  await expectDenied(
    granteeContext.request,
    `/api/v1/videos/${created.privateVideoId}/hls/master.m3u8`,
    grantee.token,
  );
  await expectDenied(granteeContext.request, created.privateSegmentPath, grantee.token);
  const granteePage = await granteeContext.newPage();
  await granteePage.goto(`/videos/${created.privateVideoId}`);
  await expect(granteePage.getByRole("alert")).toBeVisible();
  const residual = created.presignedBeforeRevoke ? await presignedStatus(created.presignedBeforeRevoke) : 0;
  await writeEvidence("revoked-grant.json", {
    newPlaylist: 404,
    newSegment: 404,
    previouslyIssuedPresignedStatus: residual,
    presignedHost: "s3.localhost",
    note: "A presigned segment URL issued before revocation stays valid until its 120 second TTL. New playlist and segment authorization stops.",
  });
});

test("reports and hidden video denial", async ({ browser, request }) => {
  const reporter = await viewerContext?.newPage();
  if (!reporter) {
    throw new Error("viewer session was not created");
  }
  await reporter.goto(`/videos/${created.publicVideoId}`);
  await reporter.getByLabel("Report reason").selectOption("spam");
  await reporter.getByRole("button", { name: "Report video" }).click();
  await expect(reporter.getByText("Report sent.")).toBeVisible();

  await ownerPage.goto("/moderation");
  await expect(ownerPage.getByText("You do not have permission to review reports.")).toBeVisible();

  await registerAccount(request, moderator);
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: {
      email: process.env.E2E_ADMIN_EMAIL,
      password: process.env.E2E_ADMIN_PASSWORD,
    },
  });
  expect(adminLogin.ok()).toBeTruthy();
  const admin = (await adminLogin.json()) as { access_token: string };
  const role = await request.post(`/api/v1/admin/users/${moderator.id}/role`, {
    headers: bearer(admin.access_token),
    data: { role: "MODERATOR", reason: "browser acceptance fixture" },
  });
  expect(role.status()).toBe(204);

  moderatorContext = await openSession(browser, moderator);
  const moderatorPage = await moderatorContext.newPage();
  await moderatorPage.goto("/moderation");
  const row = moderatorPage.locator("li", { hasText: created.publicVideoId });
  await row.getByRole("button", { name: "Hide" }).click();
  await expect(moderatorPage.getByText("Video hidden.")).toBeVisible();

  const fresh = await browser.newContext();
  await expectDenied(fresh.request, `/api/v1/videos/${created.publicVideoId}/hls/master.m3u8`);
  await expectDenied(fresh.request, created.publicSegmentPath);
  const hidden = await fresh.newPage();
  await hidden.goto(`/videos/${created.publicVideoId}`);
  await expect(hidden.getByRole("alert")).toBeVisible();
  await expect(hidden.locator("video")).toHaveCount(0);
  const residual = created.presignedBeforeHide ? await presignedStatus(created.presignedBeforeHide) : 0;
  await writeEvidence("hidden-video.json", {
    newPlaylist: 404,
    newSegment: 404,
    previouslyIssuedPresignedStatus: residual,
    note: "A new playback session cannot load a hidden video. A presigned segment URL issued before the hide stays valid until its 120 second TTL.",
  });
  await fresh.close();
});

async function accessToken(): Promise<string> {
  await loginAccount(ownerPage.request, owner);
  if (!owner.token) {
    throw new Error("owner token missing");
  }
  return owner.token;
}
