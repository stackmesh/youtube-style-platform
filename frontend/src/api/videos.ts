import { apiRequest, getAccessToken } from "./client";

export type VideoSummary = {
  id: string;
  title: string;
  thumbnail_url?: string | null;
  channel: { handle: string; name: string };
  view_count: number;
  published_at?: string | null;
  duration_seconds?: number | null;
  visibility?: string;
  processing_status?: string;
  moderation_status?: string;
  description?: string;
  tags?: string[];
  like_count?: number;
  dislike_count?: number;
  comment_count?: number;
  viewer_reaction?: "like" | "dislike" | null;
  viewer_subscribed?: boolean;
};

export type VideoPage = {
  items: VideoSummary[];
  next_cursor: string | null;
};

export type GrantRecord = {
  handle: string;
  created_at: string;
  revoked_at: string | null;
};

function withAuth<T>(path: string, init: RequestInit = {}): Promise<T> {
  return apiRequest<T>(path, init, Boolean(getAccessToken()));
}

export function getFeed(cursor?: string | null): Promise<VideoPage> {
  const params = new URLSearchParams({ limit: "24" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return apiRequest<VideoPage>(`/api/v1/videos?${params.toString()}`);
}

export function getVideo(videoId: string): Promise<VideoSummary> {
  return withAuth<VideoSummary>(`/api/v1/videos/${videoId}`);
}

export function getRelated(videoId: string): Promise<VideoPage> {
  return withAuth<VideoPage>(`/api/v1/videos/${videoId}/related`);
}

export function getChannelVideos(handle: string, cursor?: string | null): Promise<VideoPage> {
  const params = new URLSearchParams({ limit: "24" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return withAuth<VideoPage>(`/api/v1/channels/${handle}/videos?${params.toString()}`);
}

export function getMyVideos(): Promise<VideoPage> {
  return apiRequest<VideoPage>("/api/v1/me/videos", {}, true);
}

export function createVideo(input: {
  title: string;
  description: string;
  tags: string[];
  visibility: string;
}): Promise<VideoSummary> {
  return apiRequest<VideoSummary>(
    "/api/v1/videos",
    { method: "POST", body: JSON.stringify(input) },
    true,
  );
}

export function updateVideo(
  videoId: string,
  input: { title?: string; description?: string; tags?: string[]; visibility?: string },
): Promise<VideoSummary> {
  return apiRequest<VideoSummary>(
    `/api/v1/videos/${videoId}`,
    { method: "PATCH", body: JSON.stringify(input) },
    true,
  );
}

export function deleteVideo(videoId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/videos/${videoId}`, { method: "DELETE" }, true);
}

export function getGrants(videoId: string): Promise<{ items: GrantRecord[] }> {
  return apiRequest<{ items: GrantRecord[] }>(`/api/v1/videos/${videoId}/grants`, {}, true);
}

export function grantAccess(videoId: string, handle: string): Promise<GrantRecord> {
  return apiRequest<GrantRecord>(
    `/api/v1/videos/${videoId}/grants`,
    { method: "POST", body: JSON.stringify({ handle }) },
    true,
  );
}

export function revokeAccess(videoId: string, handle: string): Promise<void> {
  return apiRequest<void>(`/api/v1/videos/${videoId}/grants/${handle}`, { method: "DELETE" }, true);
}

export type SearchResults = {
  videos?: VideoPage;
  channels?: { items: { handle: string; name: string }[]; next_cursor: string | null };
};

export function searchCatalogue(query: string, sort = "relevance"): Promise<SearchResults> {
  const params = new URLSearchParams({ q: query, type: "all", sort, limit: "24" });
  return apiRequest<SearchResults>(`/api/v1/search?${params.toString()}`);
}
