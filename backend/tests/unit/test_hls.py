from pathlib import Path

from app.uploads.hls import rendition_command, renditions_for
from app.uploads.media import ProbeResult


def _probe(width: int, height: int) -> ProbeResult:
    return ProbeResult(
        duration_seconds=2,
        width=width,
        height=height,
        video_codec="h264",
        audio_codec="aac",
        container="mov,mp4",
    )


def test_ladder_does_not_upscale() -> None:
    low = renditions_for(128, 72)
    assert [item.name for item in low] == ["360"]
    assert (low[0].width, low[0].height) == (128, 72)
    assert [item.height for item in renditions_for(854, 480)] == [360]
    assert [item.height for item in renditions_for(1280, 720)] == [360, 720]
    assert [item.height for item in renditions_for(1920, 1080)] == [360, 720, 1080]


def test_ffmpeg_arguments_are_a_list() -> None:
    command = rendition_command(
        Path("/var/lib/media-work/job/source"),
        renditions_for(128, 72)[0],
        _probe(128, 72),
    )
    assert isinstance(command, list)
    assert command[0] == "ffmpeg"
    assert any("scale=128:72,format=yuv420p" in part for part in command)
