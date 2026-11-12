import { useEffect, useState } from "react";
import { getReadiness, type ReadyResponse } from "../api/health";
import { ApiError } from "../api/client";
import { getFeed, type VideoSummary } from "../api/videos";
import { VideoCard } from "../components/VideoCard";

const labels = [
  ["postgres", "PostgreSQL"],
  ["redis", "Redis"],
  ["s3", "Object storage"],
  ["worker", "Worker"],
] as const;

type View =
  | { kind: "loading" }
  | { kind: "ready"; data: ReadyResponse }
  | { kind: "degraded"; data: ReadyResponse }
  | { kind: "error"; message: string };

type FeedView =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "ready"; items: VideoSummary[]; cursor: string | null }
  | { kind: "error"; message: string };

export function HomePage() {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [feed, setFeed] = useState<FeedView>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getFeed()
      .then((page) => {
        if (cancelled) {
          return;
        }
        setFeed(
          page.items.length === 0
            ? { kind: "empty" }
            : { kind: "ready", items: page.items, cursor: page.next_cursor },
        );
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        const message = error instanceof ApiError ? error.message : "Could not load videos.";
        setFeed({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getReadiness()
      .then((data) => {
        if (cancelled) {
          return;
        }
        setView(data.status === "ready" ? { kind: "ready", data } : { kind: "degraded", data });
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : "Could not reach the API.";
        setView({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold">Home</h1>
      <div className="mt-4">
        {feed.kind === "loading" ? <p>Loading videos.</p> : null}
        {feed.kind === "empty" ? <p className="text-sm text-muted">No published videos yet.</p> : null}
        {feed.kind === "error" ? (
          <p className="text-sm text-red-300" role="alert">
            {feed.message}
          </p>
        ) : null}
        {feed.kind === "ready" ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {feed.items.map((video) => (
              <VideoCard key={video.id} video={video} />
            ))}
          </div>
        ) : null}
      </div>
      <h2 className="mt-10 text-xl font-semibold">Service status</h2>
      <p className="mt-2 text-sm text-muted">
        These checks confirm the services the platform needs.
      </p>
      <div className="mt-6 max-w-3xl">
        {view.kind === "loading" ? <p>Checking services</p> : null}
        {view.kind === "ready" ? <StatusList data={view.data} healthy /> : null}
        {view.kind === "degraded" ? <StatusList data={view.data} healthy={false} /> : null}
        {view.kind === "error" ? (
          <p className="text-sm text-red-300" role="alert">
            {view.message}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function StatusList({ data, healthy }: { data: ReadyResponse; healthy: boolean }) {
  return (
    <div>
      {healthy ? (
        <p className="text-sm text-emerald-300">All dependencies are ready.</p>
      ) : (
        <p className="text-sm text-red-300" role="alert">
          One or more dependencies are unavailable.
        </p>
      )}
      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {labels.map(([key, label]) => {
          const ok = data.checks[key] === "ok";
          return (
            <li key={key} className="rounded-xl border border-line bg-panel p-4">
              <p className="text-sm text-muted">{label}</p>
              <p className={`mt-1 text-lg font-medium ${ok ? "text-emerald-300" : "text-red-300"}`}>
                {ok ? "Ready" : "Unavailable"}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
