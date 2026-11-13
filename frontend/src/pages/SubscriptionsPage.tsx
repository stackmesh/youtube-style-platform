import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { ChannelSummary } from "../api/auth";
import { mySubscriptions, subscriptionFeed } from "../api/social";
import type { VideoSummary } from "../api/videos";
import { VideoCard } from "../components/VideoCard";
import { useAuth } from "../auth/AuthContext";

export function SubscriptionsPage() {
  const { ready, user } = useAuth();
  const [channels, setChannels] = useState<ChannelSummary[] | null>(null);
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!ready || !user) {
      return;
    }
    let cancelled = false;
    Promise.all([mySubscriptions(), subscriptionFeed()])
      .then(([listed, feed]) => {
        if (!cancelled) {
          setChannels(listed);
          setVideos(feed.items);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load subscriptions.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [ready, user]);

  if (!ready) {
    return <p>Loading subscriptions.</p>;
  }
  if (!user) {
    return <p>Log in to see subscriptions.</p>;
  }
  if (error) {
    return <p role="alert" className="text-sm text-red-300">{error}</p>;
  }
  if (!channels || !videos) {
    return <p>Loading subscriptions.</p>;
  }
  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Subscriptions</h1>
        {channels.length === 0 ? <p className="mt-3 text-sm text-muted">You have not subscribed to a channel yet.</p> : null}
        <ul className="mt-3 space-y-2">
          {channels.map((channel) => (
            <li key={channel.handle}>
              <Link to={`/channels/${channel.handle}`} className="hover:underline">
                {channel.name}
              </Link>
              <span className="ml-2 text-sm text-muted">{channel.subscriber_count} subscribers</span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h2 className="text-lg font-medium">New from subscriptions</h2>
        {videos.length === 0 ? <p className="mt-3 text-sm text-muted">No public videos from your subscriptions yet.</p> : null}
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {videos.map((video) => (
            <VideoCard key={video.id} video={video} />
          ))}
        </div>
      </div>
    </section>
  );
}
