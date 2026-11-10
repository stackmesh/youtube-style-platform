import { useEffect, useState } from "react";
import { getReadiness, type ReadyResponse } from "../api/health";

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

export function HomePage() {
  const [view, setView] = useState<View>({ kind: "loading" });

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
    <section className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold">Service status</h1>
      <p className="mt-2 text-sm text-muted">
        This foundation checks the services the platform needs. Video browsing is not built yet.
      </p>
      <div className="mt-6">
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
