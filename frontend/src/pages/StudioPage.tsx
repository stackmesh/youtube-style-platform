import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { getProcessing, uploadVideoFile, validateVideoFile } from "../api/uploads";
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
  const [loaded, setLoaded] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [processing, setProcessing] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const location = useLocation();

  useEffect(() => {
    const state = location.state as { error?: string; message?: string } | null;
    if (state?.error) {
      setError(state.error);
    }
    if (state?.message) {
      setMessage(state.message);
    }
  }, [location.state]);

  useEffect(() => {
    if (!ready || !user || !videoId) {
      return;
    }
    let cancelled = false;
    let timer = 0;
    const watch = () => {
      void getProcessing(videoId)
        .then((status) => {
          if (cancelled) {
            return;
          }
          setProcessing(status.processing_status);
          const waiting =
            status.processing_status === "processing" ||
            status.processing_status === "uploading" ||
            (status.processing_status === "uploaded" && !status.auto_thumbnail_ready);
          if (waiting) {
            timer = window.setTimeout(watch, 3000);
          }
        })
        .catch(() => undefined);
    };
    watch();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [ready, user, videoId]);

  useEffect(() => {
    if (!ready || !user) {
      return;
    }
    let cancelled = false;
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
            setGrants(page.items ?? []);
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
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [ready, user, videoId]);

  if (!ready) {
    return <p>Loading studio.</p>;
  }
  if (!user) {
    return <p>Log in to manage videos.</p>;
  }
  if (!loaded) {
    return <p>Loading studio.</p>;
  }

  function metadata() {
    return {
      title,
      description,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
      visibility,
    };
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current) {
      return;
    }
    setError("");
    setMessage("");
    const payload = metadata();
    try {
      if (videoId) {
        await updateVideo(videoId, payload);
        setMessage("Video updated. It stays a draft until processing exists.");
      } else {
        const created = await createVideo(payload);
        navigate(`/studio/${created.id}`, {
          state: { message: "Draft saved. Playback is not available yet." },
        });
      }
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not save the video.");
    }
  }

  async function onUpload() {
    if (busyRef.current) {
      return;
    }
    setError("");
    setMessage("");
    setFileError("");
    if (!file) {
      setFileError("Choose a video file.");
      return;
    }
    const problem = validateVideoFile(file);
    if (problem) {
      setFileError(problem);
      return;
    }
    busyRef.current = true;
    setBusy(true);
    const controller = new AbortController();
    abortRef.current = controller;
    let savedId = videoId;
    try {
      if (savedId) {
        await updateVideo(savedId, metadata());
      } else {
        const created = await createVideo(metadata());
        savedId = created.id;
      }
      setProgress(0);
      await uploadVideoFile(savedId, file, {
        onProgress: setProgress,
        signal: controller.signal,
      });
      navigate(`/studio/${savedId}`, {
        state: { message: "Upload stored. Playback starts after processing finishes." },
      });
    } catch (caught: unknown) {
      const text = caught instanceof ApiError || caught instanceof Error ? caught.message : "The upload failed.";
      if (savedId && savedId !== videoId) {
        navigate(`/studio/${savedId}`, { state: { error: text } });
        return;
      }
      setError(text);
    } finally {
      busyRef.current = false;
      setBusy(false);
      setProgress(null);
      abortRef.current = null;
    }
  }

  return (
    <section className="mx-auto grid max-w-5xl gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div>
        <h1 className="text-2xl font-semibold">Studio</h1>
        <p className="mt-2 text-sm text-muted">
          Upload a video file. The watch page plays it after processing publishes HLS.
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
        <label className="block text-sm">
          Video file
          <input
            aria-label="Video file"
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/x-matroska,video/mpeg,.mp4,.mov,.webm,.mkv,.mpeg"
            className="mt-1 block w-full text-sm"
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              setFile(selected);
              setFileError(selected ? validateVideoFile(selected) ?? "" : "");
            }}
          />
        </label>
        {fileError ? (
          <p role="alert" className="text-sm text-red-300">
            {fileError}
          </p>
        ) : null}
        {progress !== null ? (
          <p className="text-sm" aria-live="polite">
            Uploading {Math.round(progress * 100)}%
          </p>
        ) : null}
        {processing ? <p className="text-sm text-muted">Processing status: {processing}</p> : null}
        <p className="text-sm text-muted">
          Public, unlisted, and private apply after the video is ready. A draft is not in the feed.
          The player stays hidden until processing can publish the video.
        </p>
        {error ? (
          <p role="alert" className="text-sm text-red-300">
            {error}
          </p>
        ) : null}
        {message ? <p className="text-sm text-emerald-300">{message}</p> : null}
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-full bg-accent px-4 py-2 text-sm font-medium disabled:opacity-60"
          >
            {videoId ? "Save changes" : "Create draft"}
          </button>
          <button
            type="button"
            disabled={busy}
            className="rounded-full border border-line px-4 py-2 text-sm disabled:opacity-60"
            onClick={() => void onUpload()}
          >
            Upload video
          </button>
          {busy ? (
            <button
              type="button"
              className="rounded-full border border-line px-4 py-2 text-sm"
              onClick={() => abortRef.current?.abort()}
            >
              Cancel upload
            </button>
          ) : null}
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
            {(grants ?? []).length === 0 ? <p className="mt-3 text-sm text-muted">No grants.</p> : null}
            <ul className="mt-3 space-y-2">
              {(grants ?? []).map((grant) => (
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
