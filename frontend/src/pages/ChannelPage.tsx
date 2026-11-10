import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { getChannel } from "../api/channels";
import type { ChannelSummary } from "../api/auth";

export function ChannelPage() {
  const { handle = "" } = useParams();
  const [channel, setChannel] = useState<ChannelSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setChannel(null);
    setError("");
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
    return () => {
      cancelled = true;
    };
  }, [handle]);

  if (error) {
    return (
      <p role="alert" className="text-accent">
        {error}
      </p>
    );
  }
  if (!channel) {
    return <p>Loading channel.</p>;
  }
  return (
    <article>
      <p className="text-sm text-muted">@{channel.handle}</p>
      <h1 className="mt-1 text-2xl font-semibold">{channel.name}</h1>
      <p className="mt-4 max-w-2xl whitespace-pre-wrap">{channel.description || "No description yet."}</p>
      <p className="mt-4 text-sm text-muted">{channel.subscriber_count} subscribers</p>
    </article>
  );
}
