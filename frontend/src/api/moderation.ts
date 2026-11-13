import { apiRequest } from "./client";

export type ReportRow = {
  id: string;
  video_id?: string;
  comment_id?: string;
  reason: string;
  details: string | null;
  status: string;
  created_at: string;
};

export function moderationReports(): Promise<{ items: ReportRow[]; next_cursor: string | null }> {
  return apiRequest("/api/v1/moderation/reports", {}, true);
}

export function commentReports(): Promise<{ items: ReportRow[]; next_cursor: string | null }> {
  return apiRequest("/api/v1/moderation/comment-reports", {}, true);
}

export function hideVideo(videoId: string, reason: string, reportId?: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/moderation/videos/${videoId}/hide`,
    { method: "POST", body: JSON.stringify({ reason, report_id: reportId ?? null }) },
    true,
  );
}

export function restoreVideo(videoId: string, reason: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/moderation/videos/${videoId}/restore`,
    { method: "POST", body: JSON.stringify({ reason }) },
    true,
  );
}

export function hideComment(commentId: string, reason: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/moderation/comments/${commentId}/hide`,
    { method: "POST", body: JSON.stringify({ reason }) },
    true,
  );
}

export function dismissReport(reportId: string, reason: string): Promise<void> {
  return apiRequest<void>(
    `/api/v1/moderation/reports/${reportId}/dismiss`,
    { method: "POST", body: JSON.stringify({ reason }) },
    true,
  );
}
