import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  createVideo,
  deleteVideo,
  getGrants,
  getMyVideos,
  getVideo,
  grantAccess,
  revokeAccess,
  updateVideo,
  type GrantRecord,
  type VideoSummary,
} from "../api/videos";
import { useAuth } from "../auth/AuthContext";

const visibilities = ["private", "unlisted", "public"] as const;

export function StudioPage() {
  const { videoId } = useParams();
  const navigate = useNavigate();
  const { user, ready } = useAuth();
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [visibility, setVisibility] = useState<(typeof visibilities)[number]>("private");
  const [grants, setGrants] = useState<GrantRecord[]>([]);
  const [grantHandle, setGrantHandle] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!ready || !user) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    const list = getMyVideos().then((page) => {
      if (!cancelled) {
        setVideos(page.items);
      }
    });
    const detail = videoId
      ? getVideo(videoId).then((video) => {
          if (cancelled) {
            return;
          }
          setTitle(video.title ?? "");
          setDescription(video.description ?? "");
          setTags((video.tags ?? []).join(", "));
          if (video.visibility === "public" || video.visibility === "unlisted" || video.visibility === "private") {
            setVisibility(video.visibility);
          }
        })
      : Promise.resolve();
    const grantList = videoId
      ? getGrants(videoId).then((page) => {
          if (!cancelled) {
            setGrants(page.items);
          }
        })
      : Promise.resolve();
    Promise.all([list, detail, grantList])
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load your videos.");
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
  }, [ready, user, videoId]);

  if (!ready || loading) {
    return <p>Loading studio.</p>;
  }
  if (!user) {
    return <p>Log in to manage videos.</p>;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    const payload = {
      title,
      description,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
      visibility,
    };
    try {
      if (videoId) {
        await updateVideo(videoId, payload);
        setMessage("Video updated. It stays a draft until processing exists.");
      } else {
        const created = await createVideo(payload);
        setMessage("Draft saved. Playback is not available yet.");
        navigate(`/studio/${created.id}`);
      }
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not save the video.");
    }
  }

  return (
    <section className="mx-auto grid max-w-5xl gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div>
        <h1 className="text-2xl font-semibold">Studio</h1>
        <p className="mt-2 text-sm text-muted">
          Metadata only. Uploads and playback are not part of this stage.
        </p>
        <Link to="/studio" className="mt-4 inline-block text-sm text-accent">
          New draft
        </Link>
        {videos.length === 0 ? <p className="mt-4 text-sm text-muted">No videos yet.</p> : null}
        <ul className="mt-4 space-y-2">
          {videos.map((video) => (
            <li key={video.id}>
              <Link to={`/studio/${video.id}`} className="text-sm hover:underline">
                {video.title}
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <form className="space-y-4" onSubmit={(event) => void onSubmit(event)}>
        <h2 className="text-lg font-medium">{videoId ? "Edit video" : "Create a draft"}</h2>
        <label className="block text-sm">
          Title
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            required
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
          />
        </label>
        <label className="block text-sm">
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
            rows={4}
          />
        </label>
        <label className="block text-sm">
          Tags
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder="travel, night-drive"
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
          />
        </label>
        <label className="block text-sm">
          Visibility
          <select
            aria-label="Visibility"
            value={visibility}
            onChange={(event) => setVisibility(event.target.value as (typeof visibilities)[number])}
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
          >
            {visibilities.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <p className="text-sm text-muted">
          Public, unlisted, and private apply after the video is ready. A draft is not in the feed.
        </p>
        {error ? (
          <p role="alert" className="text-sm text-red-300">
            {error}
          </p>
        ) : null}
        {message ? <p className="text-sm text-emerald-300">{message}</p> : null}
        <div className="flex gap-3">
          <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm font-medium">
            {videoId ? "Save changes" : "Create draft"}
          </button>
          {videoId ? (
            <button
              type="button"
              className="rounded-full border border-line px-4 py-2 text-sm"
              onClick={() => {
                void deleteVideo(videoId).then(() => navigate("/studio"));
              }}
            >
              Delete
            </button>
          ) : null}
        </div>
        {videoId ? (
          <div className="border-t border-line pt-4">
            <h3 className="text-sm font-medium">Private access</h3>
            <div className="mt-2 flex gap-2">
              <label className="min-w-0 flex-1 text-sm">
                Handle
                <input
                  value={grantHandle}
                  onChange={(event) => setGrantHandle(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
                />
              </label>
              <button
                type="button"
                className="self-end rounded-full border border-line px-4 py-2 text-sm"
                onClick={() => {
                  void grantAccess(videoId, grantHandle).then((grant) => {
                    setGrants((current) => [grant, ...current]);
                    setGrantHandle("");
                  });
                }}
              >
                Grant access
              </button>
            </div>
            {grants.length === 0 ? <p className="mt-3 text-sm text-muted">No grants.</p> : null}
            <ul className="mt-3 space-y-2">
              {grants.map((grant) => (
                <li key={`${grant.handle}-${grant.created_at}`} className="flex items-center justify-between text-sm">
                  <span>
                    @{grant.handle}
                    {grant.revoked_at ? " · revoked" : ""}
                  </span>
                  {grant.revoked_at ? null : (
                    <button
                      type="button"
                      className="text-muted hover:text-white"
                      onClick={() => {
                        void revokeAccess(videoId, grant.handle).then(() => {
                          setGrants((current) =>
                            current.map((item) =>
                              item.handle === grant.handle
                                ? { ...item, revoked_at: new Date().toISOString() }
                                : item,
                            ),
                          );
                        });
                      }}
                    >
                      Revoke
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </form>
    </section>
  );
}
