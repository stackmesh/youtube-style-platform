import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  addComment,
  channelPlaylists,
  deleteComment,
  getComments,
  reportVideo,
  saveToPlaylist,
  setReaction,
  type CommentItem,
  type PlaylistSummary,
} from "../api/social";
import { useAuth } from "../auth/AuthContext";

type Props = {
  videoId: string;
  likeCount: number;
  dislikeCount: number;
  viewerReaction: string | null;
};

export function EngagementPanel({ videoId, likeCount, dislikeCount, viewerReaction }: Props) {
  const { user } = useAuth();
  const [likes, setLikes] = useState(likeCount);
  const [dislikes, setDislikes] = useState(dislikeCount);
  const [reaction, setCurrent] = useState(viewerReaction);
  const [comments, setComments] = useState<CommentItem[] | null>(null);
  const [commentError, setCommentError] = useState("");
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([]);
  const [playlistId, setPlaylistId] = useState("");
  const [reportReason, setReportReason] = useState("spam");

  useEffect(() => {
    let cancelled = false;
    setComments(null);
    setCommentError("");
    getComments(videoId)
      .then((page) => {
        if (!cancelled) {
          setComments(page.items);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setCommentError(caught instanceof ApiError ? caught.message : "Could not load comments.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  useEffect(() => {
    if (!user) {
      setPlaylists([]);
      return;
    }
    channelPlaylists(user.channel.handle)
      .then((page) => setPlaylists(page.items))
      .catch(() => setPlaylists([]));
  }, [user]);

  async function choose(value: "like" | "dislike") {
    const next = reaction === value ? "none" : value;
    const result = await setReaction(videoId, next);
    setLikes(result.like_count);
    setDislikes(result.dislike_count);
    setCurrent(result.viewer_reaction);
  }

  async function submitComment() {
    setMessage("");
    setCommentError("");
    try {
      await addComment(videoId, draft, replyTo ?? undefined);
      setDraft("");
      setReplyTo(null);
      const page = await getComments(videoId);
      setComments(page.items);
      setMessage("Comment posted.");
    } catch (caught: unknown) {
      setCommentError(caught instanceof ApiError ? caught.message : "Could not post the comment.");
    }
  }

  return (
    <section className="mt-6 space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" className="rounded-full border border-line px-3 py-1" onClick={() => void choose("like")} disabled={!user}>
          {reaction === "like" ? "Liked" : "Like"} · {likes}
        </button>
        <button type="button" className="rounded-full border border-line px-3 py-1" onClick={() => void choose("dislike")} disabled={!user}>
          {reaction === "dislike" ? "Disliked" : "Dislike"} · {dislikes}
        </button>
        {!user ? <span className="text-muted">Log in to react or comment.</span> : null}
      </div>
      {user && playlists.length > 0 ? (
        <form
          className="flex flex-wrap items-center gap-2 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            if (!playlistId) {
              return;
            }
            void saveToPlaylist(playlistId, videoId)
              .then(() => setMessage("Saved to playlist."))
              .catch((caught: unknown) => {
                setCommentError(caught instanceof ApiError ? caught.message : "Could not save the video.");
              });
          }}
        >
          <label>
            Save to playlist
            <select aria-label="Save to playlist" className="ml-2 rounded border border-line bg-panel px-2 py-1" value={playlistId} onChange={(event) => setPlaylistId(event.target.value)}>
              <option value="">Choose</option>
              {playlists.map((playlist) => (
                <option key={playlist.id} value={playlist.id}>
                  {playlist.title}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="rounded-full border border-line px-3 py-1">
            Save
          </button>
        </form>
      ) : null}
      {user ? (
        <form
          className="flex flex-wrap items-center gap-2 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void reportVideo(videoId, reportReason, "")
              .then(() => setMessage("Report sent."))
              .catch((caught: unknown) => {
                setCommentError(caught instanceof ApiError ? caught.message : "Could not send the report.");
              });
          }}
        >
          <label>
            Report
            <select aria-label="Report reason" className="ml-2 rounded border border-line bg-panel px-2 py-1" value={reportReason} onChange={(event) => setReportReason(event.target.value)}>
              {["spam", "harassment", "violence", "sexual", "copyright", "other"].map((reason) => (
                <option key={reason} value={reason}>
                  {reason}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="rounded-full border border-line px-3 py-1">
            Report video
          </button>
        </form>
      ) : null}
      <h2 className="text-lg font-medium">Comments</h2>
      {commentError ? <p role="alert" className="text-sm text-red-300">{commentError}</p> : null}
      {message ? <p className="text-sm text-emerald-300">{message}</p> : null}
      {comments === null && !commentError ? <p className="text-sm text-muted">Loading comments.</p> : null}
      {comments && comments.length === 0 ? <p className="text-sm text-muted">No comments yet.</p> : null}
      <ul className="space-y-3">
        {comments?.map((comment) => (
          <li key={comment.id} className="rounded-lg border border-line p-3 text-sm">
            <CommentBody
              comment={comment}
              canDelete={user?.channel.handle === comment.author?.handle}
              onReply={() => setReplyTo(comment.id)}
              onDelete={() => {
                void deleteComment(comment.id).then(() => getComments(videoId).then((page) => setComments(page.items)));
              }}
            />
            {comment.replies.length > 0 ? (
              <ul className="mt-3 space-y-2 border-l border-line pl-3">
                {comment.replies.map((reply) => (
                  <li key={reply.id}>
                    <CommentBody comment={reply} canDelete={user?.channel.handle === reply.author?.handle} onDelete={() => {
                      void deleteComment(reply.id).then(() => getComments(videoId).then((page) => setComments(page.items)));
                    }} />
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
      {user ? (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            void submitComment();
          }}
        >
          {replyTo ? <p className="text-xs text-muted">Replying to a comment. <button type="button" className="underline" onClick={() => setReplyTo(null)}>Cancel</button></p> : null}
          <label className="block text-sm">
            Comment
            <textarea aria-label="Comment" value={draft} onChange={(event) => setDraft(event.target.value)} className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2" rows={3} />
          </label>
          <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm">
            Post comment
          </button>
        </form>
      ) : null}
    </section>
  );
}

function CommentBody({
  comment,
  canDelete,
  onReply,
  onDelete,
}: {
  comment: CommentItem;
  canDelete: boolean;
  onReply?: () => void;
  onDelete: () => void;
}) {
  return (
    <div>
      <p>
        {comment.author ? (
          <Link to={`/channels/${comment.author.handle}`} className="font-medium hover:underline">
            {comment.author.name}
          </Link>
        ) : (
          <span className="text-muted">Comment deleted</span>
        )}
        {comment.edited ? <span className="text-muted"> · edited</span> : null}
      </p>
      <p className="mt-1 whitespace-pre-wrap">{comment.body}</p>
      <div className="mt-2 flex gap-3 text-xs text-muted">
        {onReply ? (
          <button type="button" className="underline" onClick={onReply}>
            Reply
          </button>
        ) : null}
        {canDelete && !comment.deleted ? (
          <button type="button" className="underline" onClick={onDelete}>
            Delete
          </button>
        ) : null}
      </div>
    </div>
  );
}
