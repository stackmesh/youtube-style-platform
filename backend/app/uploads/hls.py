"""Adaptive HLS packaging.

Rendition directory names are the API allow-list: 360, 720, and 1080.
A source below 360p is stored under 360 at its own height and is not upscaled.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.uploads.media import MediaError, ProbeResult
from app.uploads.objects import StorageError, head_size, put_bytes

SEGMENT_NAME = re.compile(r"^segment_[0-9]{5}\.ts$")
VARIANT_NAME = re.compile(r"^(360|720|1080)/index\.m3u8$")
_RENDITION_NAMES = ("360", "720", "1080")
_LADDER = (
    ("360", 360, 800_000, 856_000),
    ("720", 720, 2_800_000, 2_996_000),
    ("1080", 1080, 5_000_000, 5_350_000),
)
_AUDIO_BITRATE = 128_000


@dataclass(frozen=True)
class Rendition:
    name: str
    width: int
    height: int
    video_bitrate: int
    maxrate: int


def renditions_for(width: int, height: int) -> list[Rendition]:
    """Choose rungs that never exceed the source height."""
    if height < 360:
        out_width, out_height = _even_size(width, height, height)
        return [Rendition("360", out_width, out_height, 800_000, 856_000)]
    chosen: list[Rendition] = []
    for name, target, bitrate, maxrate in _LADDER:
        if height >= target:
            out_width, out_height = _even_size(width, height, target)
            chosen.append(Rendition(name, out_width, out_height, bitrate, maxrate))
    return chosen


def transcode_timeout(duration_seconds: int) -> int:
    return min(3 * 60 * 60, max(30 * 60, 4 * duration_seconds))


def rendition_command(source: Path, rendition: Rendition, probe: ProbeResult) -> list[str]:
    """Return one ffmpeg argv. The caller sets cwd to the rendition directory."""
    scale = f"scale={rendition.width}:{rendition.height},format=yuv420p"
    if probe.audio_codec:
        audio = "[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000[a]"
    else:
        seconds = max(1, probe.duration_seconds)
        audio = f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={seconds}[a]"
    graph = f"[0:v]{scale}[v];{audio}"
    bufsize = rendition.maxrate * 2
    return [
        "ffmpeg",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        str(rendition.video_bitrate),
        "-maxrate",
        str(rendition.maxrate),
        "-bufsize",
        str(bufsize),
        "-sc_threshold",
        "0",
        "-force_key_frames",
        "expr:gte(t,n_forced*6)",
        "-c:a",
        "aac",
        "-profile:a",
        "aac_low",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-hls_time",
        "6",
        "-hls_playlist_type",
        "vod",
        "-hls_flags",
        "independent_segments",
        "-start_number",
        "1",
        "-hls_segment_filename",
        "segment_%05d.ts",
        "index.m3u8",
    ]


def prepare_rendition_dirs(work: Path, renditions: list[Rendition]) -> Path:
    root = work / "hls"
    root.mkdir(parents=True, exist_ok=True)
    for rendition in renditions:
        (root / rendition.name).mkdir(parents=True, exist_ok=True)
    return root


def write_master(root: Path, renditions: list[Rendition]) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for rendition in renditions:
        bandwidth = rendition.maxrate + _AUDIO_BITRATE
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
            f"RESOLUTION={rendition.width}x{rendition.height}"
        )
        lines.append(f"{rendition.name}/index.m3u8")
    (root / "master.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


def relativize_variant(path: Path) -> None:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(stripped)
            continue
        name = Path(stripped).name
        if not SEGMENT_NAME.fullmatch(name):
            raise MediaError("playlist_invalid", fatal=False, detail="segment name")
        lines.append(name)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_tree(root: Path, duration_seconds: int) -> None:
    master_path = root / "master.m3u8"
    if not master_path.is_file():
        raise MediaError("playlist_invalid", fatal=False, detail="missing master")
    variants = referenced_variants(master_path.read_text(encoding="utf-8"))
    if not variants:
        raise MediaError("playlist_invalid", fatal=False, detail="no variants")
    for name in variants:
        playlist = root / name / "index.m3u8"
        if not playlist.is_file():
            raise MediaError("playlist_invalid", fatal=False, detail="missing variant")
        segments = referenced_segments(playlist.read_text(encoding="utf-8"))
        if not segments:
            raise MediaError("playlist_invalid", fatal=False, detail="no segments")
        total = _duration_of(playlist.read_text(encoding="utf-8"))
        if abs(total - duration_seconds) > 2:
            raise MediaError("playlist_invalid", fatal=False, detail="duration")
        for segment in segments:
            if not (root / name / segment).is_file():
                raise MediaError("playlist_invalid", fatal=False, detail="missing segment")


def upload_tree(video_id: str, root: Path) -> tuple[str, int]:
    """Overwrite the deterministic HLS keys. Returns the master key and byte total."""
    master_key = f"hls/{video_id}/master.m3u8"
    total = 0
    master = (root / "master.m3u8").read_bytes()
    put_bytes(master_key, master, "application/vnd.apple.mpegurl", cache_control="no-store")
    total += len(master)
    for name in _RENDITION_NAMES:
        directory = root / name
        if not directory.is_dir():
            continue
        playlist = (directory / "index.m3u8").read_bytes()
        playlist_key = f"hls/{video_id}/{name}/index.m3u8"
        put_bytes(
            playlist_key,
            playlist,
            "application/vnd.apple.mpegurl",
            cache_control="no-store",
        )
        total += len(playlist)
        for segment in sorted(directory.glob("segment_*.ts")):
            if not SEGMENT_NAME.fullmatch(segment.name):
                raise MediaError("playlist_invalid", fatal=False, detail="segment name")
            body = segment.read_bytes()
            put_bytes(
                f"hls/{video_id}/{name}/{segment.name}",
                body,
                "video/mp2t",
                cache_control="private, max-age=120",
            )
            total += len(body)
    if head_size(master_key) is None:
        raise MediaError("storage_error", fatal=False)
    return master_key, total


def referenced_variants(body: str) -> list[str]:
    names: list[str] = []
    for line in _uri_lines(body):
        if not VARIANT_NAME.fullmatch(line):
            raise MediaError("playlist_invalid", fatal=False, detail="variant uri")
        names.append(line.split("/", 1)[0])
    return names


def referenced_segments(body: str) -> list[str]:
    names: list[str] = []
    for line in _uri_lines(body):
        if not SEGMENT_NAME.fullmatch(line):
            raise MediaError("playlist_invalid", fatal=False, detail="segment uri")
        names.append(line)
    return names


def sanitize_master(body: str) -> str:
    """Return a master playlist whose URIs are relative API paths."""
    try:
        variants = referenced_variants(body)
    except MediaError as exc:
        raise StorageError("playlist") from exc
    kept = [line for line in body.splitlines() if line.strip()]
    if not variants or any("X-Amz-" in line or "://" in line for line in kept):
        raise StorageError("playlist")
    return "\n".join(kept) + "\n"


def sanitize_variant(body: str) -> str:
    try:
        segments = referenced_segments(body)
    except MediaError as exc:
        raise StorageError("playlist") from exc
    kept = [line.strip() for line in body.splitlines() if line.strip()]
    if not segments or any("X-Amz-" in line or "://" in line for line in kept):
        raise StorageError("playlist")
    return "\n".join(kept) + "\n"


def _uri_lines(body: str) -> list[str]:
    uris: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if ".." in line or "://" in line:
                raise MediaError("playlist_invalid", fatal=False, detail="playlist line")
            continue
        if ".." in line or "://" in line or "?" in line or line.startswith("/"):
            raise MediaError("playlist_invalid", fatal=False, detail="playlist uri")
        uris.append(line)
    return uris


def _duration_of(body: str) -> float:
    total = 0.0
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("#EXTINF:"):
            continue
        value = line.split(":", 1)[1].split(",", 1)[0]
        try:
            total += float(value)
        except ValueError as exc:
            raise MediaError("playlist_invalid", fatal=False, detail="extinf") from exc
    return total


def _even_size(width: int, height: int, target_height: int) -> tuple[int, int]:
    out_height = min(height, target_height)
    out_height -= out_height % 2
    out_width = round(width * out_height / height)
    out_width -= out_width % 2
    return max(out_width, 2), max(out_height, 2)
