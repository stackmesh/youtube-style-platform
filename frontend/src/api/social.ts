import { apiRequest, getAccessToken } from "./client";
import type { ChannelSummary } from "./auth";
import type { VideoPage } from "./videos";

export type Reaction = {
  like_count: number;
  dislike_count: number;
  viewer_reaction: "like" | "dislike" | null;
};

export type CommentItem = {
  id: string;
  body: string;
  created_at: string;
  edited: boolean;
  deleted: boolean;
  author: { handle: string; name: string } | null;
  reply_count: number;
  replies: CommentItem[];
};

export type CommentPage = {
  items: CommentItem[];
  next_cursor: string | null;
};

export type PlaylistSummary = {
  id: string;
  title: string;
  description: string;
  visibility: string;
};

export type PlaylistDetail = PlaylistSummary & {
  channel: { handle: string; name: string };
  items: {
    video_id: string;
    title?: string;
    unavailable?: boolean;
    channel?: { handle: string; name: string };
  }[];
};

function authed<T>(path: string, init: RequestInit = {}): Promise<T> {
  return apiRequest<T>(path, init, Boolean(getAccessToken()));
}

export function setReaction(videoId: string, value: "like" | "dislike" | "none"): Promise<Reaction> {
  return apiRequest<Reaction>(
    `/api/v1/videos/${videoId}/reaction`,
    { method: "PUT", body: JSON.stringify({ value }) },
    true,
  );
}

export function getComments(videoId: string): Promise<CommentPage> {
  return authed<CommentPage>(`/api/v1/videos/${videoId}/comments`);
}

export function addComment(videoId: string, body: string, parentId?: string): Promise<CommentItem> {
  return apiRequest<CommentItem>(
    `/api/v1/videos/${videoId}/comments`,
    { method: "POST", body: JSON.stringify({ body, parent_id: parentId ?? null }) },
    true,
  );
}

export function editComment(commentId: string, body: string): Promise<CommentItem> {
  return apiRequest<CommentItem>(
    `/api/v1/comments/${commentId}`,
    { method: "PATCH", body: JSON.stringify({ body }) },
    true,
  );
}

export function deleteComment(commentId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/comments/${commentId}`, { method: "DELETE" }, true);
}

export function reportVideo(videoId: string, reason: string, details: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/videos/${videoId}/reports`,
    { method: "POST", body: JSON.stringify({ reason, details: details || null }) },
    true,
  );
}

export function reportComment(commentId: string, reason: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/comments/${commentId}/reports`,
    { method: "POST", body: JSON.stringify({ reason }) },
    true,
  );
}

export function subscribe(handle: string): Promise<void> {
  return apiRequest<void>(`/api/v1/channels/${handle}/subscription`, { method: "PUT" }, true);
}

export function unsubscribe(handle: string): Promise<void> {
  return apiRequest<void>(`/api/v1/channels/${handle}/subscription`, { method: "DELETE" }, true);
}

export function mySubscriptions(): Promise<ChannelSummary[]> {
  return apiRequest<ChannelSummary[]>("/api/v1/me/subscriptions", {}, true);
}

export function subscriptionFeed(): Promise<VideoPage> {
  return apiRequest<VideoPage>("/api/v1/me/subscriptions/videos", {}, true);
}

export function channelPlaylists(handle: string): Promise<{ items: PlaylistSummary[] }> {
  return authed<{ items: PlaylistSummary[] }>(`/api/v1/channels/${handle}/playlists`);
}

export function createPlaylist(title: string, description: string, visibility: string): Promise<PlaylistDetail> {
  return apiRequest<PlaylistDetail>(
    "/api/v1/playlists",
    { method: "POST", body: JSON.stringify({ title, description, visibility }) },
    true,
  );
}

export function getPlaylist(playlistId: string): Promise<PlaylistDetail> {
  return authed<PlaylistDetail>(`/api/v1/playlists/${playlistId}`);
}

export function saveToPlaylist(playlistId: string, videoId: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/playlists/${playlistId}/items`,
    { method: "POST", body: JSON.stringify({ video_id: videoId }) },
    true,
  );
}

export function removeFromPlaylist(playlistId: string, videoId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/playlists/${playlistId}/items/${videoId}`, { method: "DELETE" }, true);
}

export function reorderPlaylist(playlistId: string, videoIds: string[]): Promise<void> {
  return apiRequest<void>(
    `/api/v1/playlists/${playlistId}/items`,
    { method: "PATCH", body: JSON.stringify({ video_ids: videoIds }) },
    true,
  );
}

export function deletePlaylist(playlistId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/playlists/${playlistId}`, { method: "DELETE" }, true);
}
