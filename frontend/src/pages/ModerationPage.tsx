import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  commentReports,
  dismissReport,
  hideComment,
  hideVideo,
  moderationReports,
  restoreVideo,
  type ReportRow,
} from "../api/moderation";
import { useAuth } from "../auth/AuthContext";

export function ModerationPage() {
  const { ready, user } = useAuth();
  const [videos, setVideos] = useState<ReportRow[] | null>(null);
  const [comments, setComments] = useState<ReportRow[] | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const allowed = user?.role === "MODERATOR" || user?.role === "ADMIN";

  function reload() {
    return Promise.all([moderationReports(), commentReports()]).then(([videoPage, commentPage]) => {
      setVideos(videoPage.items);
      setComments(commentPage.items);
    });
  }

  useEffect(() => {
    if (!ready || !allowed) {
      return;
    }
    reload().catch((caught: unknown) => {
      setError(caught instanceof ApiError ? caught.message : "Could not load the queue.");
    });
  }, [ready, allowed]);

  if (!ready) {
    return <p>Loading moderation.</p>;
  }
  if (!user) {
    return <p>Log in to review reports.</p>;
  }
  if (!allowed) {
    return <p role="alert">You do not have permission to review reports.</p>;
  }
  if (error) {
    return <p role="alert" className="text-sm text-red-300">{error}</p>;
  }
  if (!videos || !comments) {
    return <p>Loading moderation.</p>;
  }
  return (
    <section className="space-y-8">
      <h1 className="text-2xl font-semibold">Moderation</h1>
      {message ? <p className="text-sm text-emerald-300">{message}</p> : null}
      <ReportList
        title="Video reports"
        rows={videos}
        empty="No open video reports."
        onHide={(row) => {
          if (!row.video_id) {
            return;
          }
          void hideVideo(row.video_id, "action from queue", row.id)
            .then(() => {
              setMessage("Video hidden.");
              return reload();
            })
            .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.message : "Hide failed."));
        }}
        onRestore={(row) => {
          if (!row.video_id) {
            return;
          }
          void restoreVideo(row.video_id, "restored from queue")
            .then(() => {
              setMessage("Video restored.");
              return reload();
            })
            .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.message : "Restore failed."));
        }}
        onDismiss={(row) => {
          void dismissReport(row.id, "dismissed from queue").then(() => {
            setMessage("Report dismissed.");
            return reload();
          });
        }}
      />
      <ReportList
        title="Comment reports"
        rows={comments}
        empty="No open comment reports."
        onHide={(row) => {
          if (!row.comment_id) {
            return;
          }
          void hideComment(row.comment_id, "action from queue").then(() => {
            setMessage("Comment hidden.");
            return reload();
          });
        }}
        onDismiss={(row) => {
          void dismissReport(row.id, "dismissed from queue").then(() => reload());
        }}
      />
    </section>
  );
}

function ReportList({
  title,
  rows,
  empty,
  onHide,
  onRestore,
  onDismiss,
}: {
  title: string;
  rows: ReportRow[];
  empty: string;
  onHide: (row: ReportRow) => void;
  onRestore?: (row: ReportRow) => void;
  onDismiss: (row: ReportRow) => void;
}) {
  return (
    <div>
      <h2 className="text-lg font-medium">{title}</h2>
      {rows.length === 0 ? <p className="mt-3 text-sm text-muted">{empty}</p> : null}
      <ul className="mt-3 space-y-3">
        {rows.map((row) => (
          <li key={row.id} className="rounded-lg border border-line p-3 text-sm">
            <p>{row.reason}{row.details ? ` · ${row.details}` : ""}</p>
            <p className="text-xs text-muted">{row.video_id ?? row.comment_id}</p>
            <div className="mt-2 flex gap-3">
              <button type="button" className="underline" onClick={() => onHide(row)}>Hide</button>
              {onRestore ? <button type="button" className="underline" onClick={() => onRestore(row)}>Restore</button> : null}
              <button type="button" className="underline" onClick={() => onDismiss(row)}>Dismiss</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
