import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { getChannel } from "../api/channels";
import type { ChannelSummary } from "../api/auth";
import { getChannelVideos, type VideoSummary } from "../api/videos";
import { VideoCard } from "../components/VideoCard";
import { useAuth } from "../auth/AuthContext";

export function ChannelPage() {
  const { handle = "" } = useParams();
  const { ready } = useAuth();
  const [channel, setChannel] = useState<ChannelSummary | null>(null);
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [error, setError] = useState("");
  const [videoError, setVideoError] = useState("");

  useEffect(() => {
    if (!ready) {
      return;
    }
    let cancelled = false;
    setChannel(null);
    setVideos(null);
    setError("");
    setVideoError("");
    getChannel(handle)
      .then((loaded) => {
        if (!cancelled) {
          setChannel(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load this channel.");
        }
      });
    getChannelVideos(handle)
      .then((page) => {
        if (!cancelled) {
          setVideos(page.items);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setVideoError(caught instanceof ApiError ? caught.message : "Could not load videos.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [handle, ready]);

  if (error) {
    return (
      <p role="alert" className="text-accent">
        {error}
      </p>
    );
  }
  if (!ready || !channel) {
    return <p>Loading channel.</p>;
  }
  return (
    <article>
      <p className="text-sm text-muted">@{channel.handle}</p>
      <h1 className="mt-1 text-2xl font-semibold">{channel.name}</h1>
      <p className="mt-4 max-w-2xl whitespace-pre-wrap">{channel.description || "No description yet."}</p>
      <p className="mt-4 text-sm text-muted">{channel.subscriber_count} subscribers</p>
      <h2 className="mt-8 text-lg font-medium">Videos</h2>
      {videoError ? (
        <p role="alert" className="mt-3 text-sm text-red-300">
          {videoError}
        </p>
      ) : null}
      {videos === null && !videoError ? <p className="mt-3">Loading videos.</p> : null}
      {videos && videos.length === 0 ? <p className="mt-3 text-sm text-muted">No published videos yet.</p> : null}
      {videos && videos.length > 0 ? (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {videos.map((video) => (
            <VideoCard key={video.id} video={video} />
          ))}
        </div>
      ) : null}
    </article>
  );
}
