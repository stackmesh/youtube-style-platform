import { apiRequest } from "./client";

export type PlaybackSession = {
  expires_at: string;
};

export function createPlayback(videoId: string): Promise<PlaybackSession> {
  return apiRequest<PlaybackSession>(`/api/v1/videos/${videoId}/playback`, { method: "POST" }, true);
}

export function endPlayback(videoId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/videos/${videoId}/playback`, { method: "DELETE" }, true);
}

export function recordView(videoId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/videos/${videoId}/views`, { method: "POST" }, true);
}
