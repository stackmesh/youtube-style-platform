import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { searchCatalogue, type SearchResults } from "../api/videos";
import { VideoCard } from "../components/VideoCard";

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const sort = params.get("sort") ?? "relevance";
  const [draft, setDraft] = useState(query);
  const [results, setResults] = useState<SearchResults | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setDraft(query);
    if (!query.trim()) {
      setResults(null);
      setError("");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    searchCatalogue(query, sort)
      .then((loaded) => {
        if (!cancelled) {
          setResults(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setResults(null);
          setError(caught instanceof ApiError ? caught.message : "Could not search the catalogue.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [query, sort]);

  const videos = results?.videos?.items ?? [];
  const channels = results?.channels?.items ?? [];

  return (
    <section className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold">Search</h1>
      <form
        className="mt-4 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setParams({ q: draft.trim(), sort });
        }}
      >
        <label className="min-w-0 flex-1">
          <span className="sr-only">Search query</span>
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Search videos"
            className="w-full rounded-full border border-line bg-panel px-4 py-2 text-sm"
          />
        </label>
        <label>
          <span className="sr-only">Sort</span>
          <select
            aria-label="Sort"
            value={sort}
            onChange={(event) => setParams({ q: query, sort: event.target.value })}
            className="rounded-full border border-line bg-panel px-3 py-2 text-sm"
          >
            <option value="relevance">Relevance</option>
            <option value="newest">Newest</option>
            <option value="views">Views</option>
          </select>
        </label>
        <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm font-medium">
          Search
        </button>
      </form>
      {loading ? <p className="mt-6">Loading results.</p> : null}
      {error ? (
        <p role="alert" className="mt-6 text-sm text-red-300">
          {error}
        </p>
      ) : null}
      {!loading && !error && query.trim() && videos.length === 0 && channels.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No results.</p>
      ) : null}
      {!query.trim() && !loading ? <p className="mt-6 text-sm text-muted">Enter a search.</p> : null}
      {videos.length > 0 ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {videos.map((video) => (
            <VideoCard key={video.id} video={video} />
          ))}
        </div>
      ) : null}
      {channels.length > 0 ? (
        <ul className="mt-6 space-y-2">
          {channels.map((channel) => (
            <li key={channel.handle}>
              <Link to={`/channels/${channel.handle}`} className="text-sm hover:underline">
                {channel.name} <span className="text-muted">@{channel.handle}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
