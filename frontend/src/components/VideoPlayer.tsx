import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { createPlayback, endPlayback, recordView } from "../api/playback";
import { MediaPlaceholder } from "./VideoCard";

type Phase = "loading" | "ready" | "error" | "unavailable";

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const remain = whole % 60;
  return `${minutes}:${remain.toString().padStart(2, "0")}`;
}

export function VideoPlayer({ videoId }: { videoId: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const started = useRef(false);
  const [phase, setPhase] = useState<Phase>("loading");
  const [attempt, setAttempt] = useState(0);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [completed, setCompleted] = useState(false);

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
            },
          });
          player.loadSource(playlist);
          player.attachMedia(video);
          player.on(Hls.Events.MANIFEST_PARSED, () => {
            if (!cancelled) {
              setPhase((current) => (current === "error" ? current : "ready"));
            }
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
      video.removeAttribute("src");
      void endPlayback(videoId).catch(() => undefined);
    };
  }, [videoId, attempt]);

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
        {phase === "error" ? (
          <button type="button" className="underline" onClick={() => setAttempt((value) => value + 1)}>
            Retry playback
          </button>
        ) : null}
      </div>
    </div>
  );
}
