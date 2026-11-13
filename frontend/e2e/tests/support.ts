import { randomBytes } from "node:crypto";
import http from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { expect, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";

export const suffix = randomBytes(3).toString("hex");
export const password = `e2e-${suffix}-password`;
export const evidenceDir = path.resolve(process.env.E2E_EVIDENCE_DIR ?? path.join(process.cwd(), "evidence"));

export type Account = {
  email: string;
  password: string;
  handle: string;
  name: string;
  id?: string;
  token?: string;
};

export function account(kind: string): Account {
  return {
    email: `e2e-${kind}-${suffix}@example.com`,
    password,
    handle: `${kind}${suffix}`,
    name: `E2E ${kind} ${suffix}`,
  };
}

export async function registerAccount(request: APIRequestContext, user: Account): Promise<void> {
  const response = await request.post("/api/v1/auth/register", {
    data: {
      email: user.email,
      password: user.password,
      handle: user.handle,
      name: user.name,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = (await response.json()) as { access_token: string; user: { id: string } };
  user.id = body.user.id;
  user.token = body.access_token;
}

export async function loginAccount(request: APIRequestContext, user: Account): Promise<void> {
  const response = await request.post("/api/v1/auth/login", {
    data: { email: user.email, password: user.password },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = (await response.json()) as { access_token: string; user: { id: string } };
  user.id = body.user.id;
  user.token = body.access_token;
}

export async function openSession(browser: Browser, user: Account): Promise<BrowserContext> {
  const context = await browser.newContext();
  await loginAccount(context.request, user);
  return context;
}

export function bearer(token: string | undefined): { Authorization: string } {
  if (!token) {
    throw new Error("missing access token");
  }
  return { Authorization: `Bearer ${token}` };
}

export async function playlistPaths(
  request: APIRequestContext,
  videoId: string,
  token?: string,
): Promise<{ master: string; variant: string; segment: string }> {
  const masterPath = `/api/v1/videos/${videoId}/hls/master.m3u8`;
  const master = await request.get(masterPath, { headers: token ? bearer(token) : undefined });
  expect(master.status(), await master.text()).toBe(200);
  const masterText = await master.text();
  expect(masterText).toContain("#EXTM3U");
  const variantLine = masterText
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.endsWith("/index.m3u8"));
  expect(variantLine).toBeTruthy();
  const variantPath = `/api/v1/videos/${videoId}/hls/${variantLine}`;
  const variant = await request.get(variantPath, { headers: token ? bearer(token) : undefined });
  expect(variant.status(), await variant.text()).toBe(200);
  const variantText = await variant.text();
  const segmentName = variantText
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.startsWith("segment_") && line.endsWith(".ts"));
  expect(segmentName).toBeTruthy();
  const rendition = variantLine?.split("/")[0];
  return {
    master: masterPath,
    variant: variantPath,
    segment: `/api/v1/videos/${videoId}/hls/${rendition}/${segmentName}`,
  };
}

export async function expectDenied(request: APIRequestContext, resource: string, token?: string): Promise<void> {
  const response = await request.get(resource, { headers: token ? bearer(token) : undefined });
  expect(response.status(), await response.text()).toBe(404);
}

export type PlaybackEvidence = {
  readyState: number;
  videoWidth: number;
  videoHeight: number;
  currentTime: number;
  mediaError: { code: number; message: string } | null;
  consoleErrors: string[];
  network: Array<{ path: string; status: number }>;
  screenshot: string;
};

export async function playVideo(page: Page, videoId: string, screenshotName: string): Promise<PlaybackEvidence> {
  const consoleErrors: string[] = [];
  const network: Array<{ path: string; status: number }> = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("favicon")) {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    consoleErrors.push(error.message);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.includes("/hls/") || url.hostname === "s3.localhost") {
      network.push({ path: `${url.hostname}${url.pathname}`, status: response.status() });
    }
  });
  await page.goto(`/videos/${videoId}`);
  const video = page.locator("video");
  await expect(video).toBeVisible();
  await expect(page.getByLabel("Quality")).toBeVisible();
  try {
    await page.waitForFunction(
      () => {
        const media = document.querySelector("video");
        return Boolean(media && media.readyState >= 1 && media.videoWidth > 0 && media.videoHeight > 0);
      },
      undefined,
      { timeout: 20_000 },
    );
  } catch (error) {
    const pageError = await page.locator("body").innerText();
    throw new Error(
      `playback did not decode a frame\nconsole=${consoleErrors.join(" | ")}\nnetwork=${JSON.stringify(network)}\npage=${pageError.slice(0, 500)}\n${error instanceof Error ? error.message : String(error)}`,
    );
  }
  const metrics = await video.evaluate(async (element) => {
    const media = element as HTMLVideoElement;
    media.muted = true;
    await media.play();
    const start = media.currentTime;
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("currentTime did not advance")), 20000);
      const finish = () => {
        if (media.videoWidth > 0 && media.videoHeight > 0 && media.currentTime > start + 0.2 && media.readyState >= 2) {
          window.clearTimeout(timer);
          resolve();
        }
      };
      media.addEventListener("timeupdate", finish);
      media.addEventListener("error", () => {
        window.clearTimeout(timer);
        reject(new Error(media.error?.message || "media element error"));
      });
      finish();
    });
    return {
      readyState: media.readyState,
      videoWidth: media.videoWidth,
      videoHeight: media.videoHeight,
      currentTime: media.currentTime,
      mediaError: media.error ? { code: media.error.code, message: media.error.message } : null,
    };
  });
  await mkdir(evidenceDir, { recursive: true });
  const screenshot = path.join(evidenceDir, screenshotName);
  await video.screenshot({ path: screenshot });
  const evidence: PlaybackEvidence = { ...metrics, consoleErrors, network, screenshot };
  expect(evidence.mediaError).toBeNull();
  expect(evidence.videoWidth).toBeGreaterThan(0);
  expect(evidence.videoHeight).toBeGreaterThan(0);
  expect(evidence.currentTime).toBeGreaterThan(0);
  expect(evidence.readyState).toBeGreaterThanOrEqual(2);
  expect(evidence.consoleErrors.filter((line) => /fatal|MEDIA_ELEMENT_ERROR/i.test(line))).toEqual([]);
  expect(evidence.network.some((entry) => entry.path.includes("master.m3u8") && entry.status === 200)).toBeTruthy();
  expect(evidence.network.some((entry) => entry.path.includes("index.m3u8") && entry.status === 200)).toBeTruthy();
  expect(evidence.network.some((entry) => entry.path.includes("s3.localhost") && entry.status === 200)).toBeTruthy();
  return evidence;
}

export async function writeEvidence(name: string, value: unknown): Promise<void> {
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(path.join(evidenceDir, name), `${JSON.stringify(value, null, 2)}\n`);
}

export function presignedStatus(target: string): Promise<number> {
  const url = new URL(target);
  return new Promise((resolve, reject) => {
    const request = http.request(
      {
        host: "127.0.0.1",
        port: url.port || 80,
        path: `${url.pathname}${url.search}`,
        method: "GET",
        headers: { Host: url.host },
      },
      (response) => {
        response.resume();
        resolve(response.statusCode ?? 0);
      },
    );
    request.setTimeout(15000, () => {
      request.destroy(new Error("presigned request timed out"));
    });
    request.on("error", reject);
    request.end();
  });
}

export async function uploadFromStudio(page: Page, title: string, visibility: "public" | "private", filePath: string): Promise<string> {
  await page.goto("/studio");
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Description").fill(`Generated test source ${title}`);
  await page.getByLabel("Visibility").selectOption(visibility);
  await page.getByLabel("Video file").setInputFiles(filePath);
  const progress = page.getByText(/Uploading \d+%/).waitFor({ timeout: 20_000 });
  await page.getByRole("button", { name: "Upload video" }).click();
  await progress;
  await expect(page.getByText("Upload stored. Playback starts after processing finishes.")).toBeVisible();
  await page.waitForURL(/\/studio\/[^/]+$/);
  const videoId = page.url().split("/").pop();
  if (!videoId) {
    throw new Error("studio did not open the uploaded video");
  }
  await expect(page.getByLabel("Title")).toHaveValue(title);
  await expect(page.getByLabel("Visibility")).toHaveValue(visibility);
  await expect(page.getByText("Processing status: ready")).toBeVisible({ timeout: 150_000 });
  return videoId;
}
