import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";
import { ApiError, getAccessToken } from "../api/client";
import { createPlayback, endPlayback, recordView } from "../api/playback";
import { MediaPlaceholder } from "./VideoCard";

type Phase = "loading" | "ready" | "error" | "unavailable";

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const remain = whole % 60;
  return `${minutes}:${remain.toString().padStart(2, "0")}`;
}

export function VideoPlayer({
  videoId,
  viewerId = null,
}: {
  videoId: string;
  viewerId?: string | null;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const started = useRef(false);
  const previousViewer = useRef<string | null | undefined>(undefined);
  const [phase, setPhase] = useState<Phase>("loading");
  const [attempt, setAttempt] = useState(0);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [completed, setCompleted] = useState(false);
  const [levels, setLevels] = useState<number[]>([]);
  const [quality, setQuality] = useState(-1);

  useEffect(() => {
    const previous = previousViewer.current;
    previousViewer.current = viewerId;
    if (previous && !viewerId) {
      setAttempt((value) => value + 1);
    }
  }, [viewerId]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    let cancelled = false;
    let player: Hls | null = null;
    started.current = false;
    setPhase("loading");
    setCompleted(false);
    setLevels([]);
    setQuality(-1);

    const onTime = () => {
      setCurrent(video.currentTime);
      setDuration(Number.isFinite(video.duration) ? video.duration : 0);
    };
    const onPlay = () => {
      if (started.current) {
        return;
      }
      started.current = true;
      void recordView(videoId).catch(() => undefined);
    };
    const onEnded = () => setCompleted(true);
    const onMediaError = () => setPhase("error");
    video.addEventListener("timeupdate", onTime);
    video.addEventListener("play", onPlay);
    video.addEventListener("ended", onEnded);
    video.addEventListener("error", onMediaError);

    const playlist = `/api/v1/videos/${videoId}/hls/master.m3u8`;
    createPlayback(videoId)
      .then(() => {
        if (cancelled) {
          return;
        }
        if (Hls.isSupported()) {
          player = new Hls({
            xhrSetup: (xhr) => {
              xhr.withCredentials = true;
              const token = getAccessToken();
              if (token) {
                xhr.setRequestHeader("Authorization", `Bearer ${token}`);
              }
            },
          });
          hlsRef.current = player;
          player.loadSource(playlist);
          player.attachMedia(video);
          player.on(Hls.Events.MANIFEST_PARSED, () => {
            if (cancelled) {
              return;
            }
            setLevels(player?.levels.map((level) => level.height) ?? []);
            setPhase((currentPhase) => (currentPhase === "error" ? currentPhase : "ready"));
          });
          player.on(Hls.Events.ERROR, (_event, data) => {
            if (!cancelled && data.fatal) {
              setPhase("error");
            }
          });
          return;
        }
        if (video.canPlayType("application/vnd.apple.mpegurl")) {
          video.src = playlist;
          setPhase("ready");
          return;
        }
        setPhase("error");
      })
      .catch((caught: unknown) => {
        if (cancelled) {
          return;
        }
        if (caught instanceof ApiError && caught.code === "VIDEO_NOT_READY") {
          setPhase("unavailable");
          return;
        }
        setPhase("error");
      });

    return () => {
      cancelled = true;
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("ended", onEnded);
      video.removeEventListener("error", onMediaError);
      player?.destroy();
      if (hlsRef.current === player) {
        hlsRef.current = null;
      }
      video.removeAttribute("src");
      void endPlayback(videoId).catch(() => undefined);
    };
  }, [videoId, attempt]);

  function chooseQuality(next: number) {
    setQuality(next);
    if (hlsRef.current) {
      hlsRef.current.currentLevel = next;
    }
  }

  if (phase === "unavailable") {
    return <MediaPlaceholder />;
  }

  return (
    <div className="overflow-hidden rounded-lg bg-black">
      <video
        ref={videoRef}
        className="aspect-video w-full"
        controls
        playsInline
        preload="metadata"
      />
      <div className="flex items-center justify-between gap-3 px-3 py-2 text-xs text-white">
        <span>
          {clock(current)} / {clock(duration)}
          {completed ? " · completed" : ""}
        </span>
        {phase === "loading" ? <span>Loading playback.</span> : null}
        {levels.length > 0 ? (
          <label className="flex items-center gap-2">
            Quality
            <select
              aria-label="Quality"
              value={String(quality)}
              onChange={(event) => chooseQuality(Number(event.target.value))}
              className="rounded bg-zinc-900 px-2 py-1"
            >
              <option value="-1">Auto</option>
              {levels.map((height, index) => (
                <option key={`${height}-${index}`} value={String(index)}>
                  {height}p
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {phase === "error" ? (
          <button type="button" className="underline" onClick={() => setAttempt((value) => value + 1)}>
            Retry playback
          </button>
        ) : null}
      </div>
    </div>
  );
}
