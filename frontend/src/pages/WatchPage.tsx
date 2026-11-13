import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { getRelated, getVideo, type VideoSummary } from "../api/videos";
import { MediaPlaceholder, VideoCard } from "../components/VideoCard";
import { VideoPlayer } from "../components/VideoPlayer";
import { useAuth } from "../auth/AuthContext";

export function WatchPage() {
  const { videoId = "" } = useParams();
  const { ready, user } = useAuth();
  const [video, setVideo] = useState<VideoSummary | null>(null);
  const [related, setRelated] = useState<VideoSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!ready) {
      return;
    }
    let cancelled = false;
    setVideo(null);
    setRelated([]);
    setError("");
    getVideo(videoId)
      .then(async (loaded) => {
        if (cancelled) {
          return;
        }
        setVideo(loaded);
        const page = await getRelated(videoId);
        if (!cancelled) {
          setRelated(page.items);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load this video.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [ready, videoId]);

  if (!ready || (!video && !error)) {
    return <p>Loading video.</p>;
  }
  if (error) {
    return (
      <p role="alert" className="text-sm text-red-300">
        {error}
      </p>
    );
  }
  if (!video) {
    return <p>Loading video.</p>;
  }
  return (
    <article className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div>
        {video.processing_status && video.processing_status !== "ready" ? (
          <div>
            <MediaPlaceholder />
            <p className="mt-2 text-sm text-muted">Status: {video.processing_status}</p>
          </div>
        ) : (
          <VideoPlayer videoId={video.id} viewerId={user?.id ?? null} />
        )}
        <h1 className="mt-4 text-2xl font-semibold">{video.title}</h1>
        <p className="mt-1 text-sm text-muted">
          <Link to={`/channels/${video.channel.handle}`} className="hover:underline">
            {video.channel.name}
          </Link>
          {" · "}
          {video.view_count} views
          {video.visibility ? ` · ${video.visibility}` : ""}
        </p>
        <p className="mt-4 max-w-3xl whitespace-pre-wrap text-sm">
          {video.description || "No description yet."}
        </p>
        {video.tags && video.tags.length > 0 ? (
          <ul className="mt-4 flex flex-wrap gap-2">
            {video.tags.map((tag) => (
              <li key={tag} className="rounded-full bg-panel px-3 py-1 text-xs text-muted">
                {tag}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <aside>
        <h2 className="text-sm font-medium text-muted">Related</h2>
        <div className="mt-3 space-y-3">
          {related.length === 0 ? <p className="text-sm text-muted">No related videos.</p> : null}
          {related.map((item) => (
            <VideoCard key={item.id} video={item} />
          ))}
        </div>
      </aside>
    </article>
  );
}
