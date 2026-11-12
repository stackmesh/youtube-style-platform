import { apiRequest } from "./client";
import type { ChannelSummary } from "./auth";

export async function getChannel(handle: string): Promise<ChannelSummary> {
  return apiRequest<ChannelSummary>(`/api/v1/channels/${encodeURIComponent(handle)}`);
}

export async function updateMyChannel(input: {
  name?: string;
  description?: string;
}): Promise<ChannelSummary> {
  return apiRequest<ChannelSummary>(
    "/api/v1/channels/me",
    { method: "PATCH", body: JSON.stringify(input) },
    true,
  );
}
