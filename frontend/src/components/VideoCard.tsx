import { Link } from "react-router-dom";
import type { VideoSummary } from "../api/videos";

export function VideoCard({ video }: { video: VideoSummary }) {
  return (
    <article className="overflow-hidden rounded-xl border border-line bg-panel">
      <Link to={`/videos/${video.id}`} className="block">
        <div className="flex aspect-video items-center justify-center bg-black/40 px-3 text-center text-sm text-muted">
          No media yet
        </div>
        <div className="space-y-1 p-3">
          <h2 className="font-medium leading-snug">{video.title}</h2>
          <p className="text-sm text-muted">{video.channel.name}</p>
          <p className="text-xs text-muted">
            {video.view_count} views
            {video.processing_status ? ` · ${video.processing_status}` : ""}
            {video.visibility ? ` · ${video.visibility}` : ""}
          </p>
        </div>
      </Link>
    </article>
  );
}

export function MediaPlaceholder() {
  return (
    <div className="flex aspect-video items-center justify-center rounded-xl border border-line bg-panel px-6 text-center text-sm text-muted">
      No media file is attached. Playback is not available.
    </div>
  );
}
