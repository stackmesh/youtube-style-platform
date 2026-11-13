"""Time a few local API calls. This is a developer baseline, not a capacity test."""

import json
import statistics
import time
import urllib.request
import uuid

BASE = "http://localhost/api/v1"


def call(path: str, method: str = "GET", payload: dict | None = None, token: str | None = None) -> float:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(BASE + path, data=data, method=method)
    if payload is not None:
        request.add_header("content-type", "application/json")
    if token:
        request.add_header("authorization", f"Bearer {token}")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
    return time.perf_counter() - started


def sample(label: str, path: str, count: int = 20, **kwargs) -> None:
    timings = [call(path, **kwargs) for _ in range(count)]
    timings.sort()
    print(
        json.dumps(
            {
                "label": label,
                "path": path,
                "samples": count,
                "p50_ms": round(statistics.median(timings) * 1000, 2),
                "p95_ms": round(timings[max(0, int(len(timings) * 0.95) - 1)] * 1000, 2),
                "max_ms": round(timings[-1] * 1000, 2),
            }
        )
    )


def main() -> None:
    with urllib.request.urlopen(BASE + "/videos?limit=1", timeout=10) as response:
        feed = json.load(response)
    video_id = feed["items"][0]["id"] if feed["items"] else None
    sample("feed", "/videos?limit=24")
    sample("search", "/search?q=a&type=video")
    if video_id:
        sample("detail", f"/videos/{video_id}")
        sample("comments", f"/videos/{video_id}/comments?limit=20")
        sample("playback", f"/videos/{video_id}/playback", count=10, method="POST")
    suffix = uuid.uuid4().hex[:8]
    register = json.load(
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + "/auth/register",
                data=json.dumps(
                    {
                        "email": f"perf-{suffix}@example.com",
                        "password": "correct-password",
                        "handle": f"p{suffix}",
                        "name": "Perf",
                    }
                ).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
        )
    )
    token = register["access_token"]
    created = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + "/videos",
                data=json.dumps(
                    {"title": "baseline", "description": "", "tags": [], "visibility": "private"}
                ).encode(),
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                },
                method="POST",
            )
        ).read()
    )
    elapsed = call(
        f"/videos/{created['id']}/upload",
        method="POST",
        payload={"byte_size": 1024, "content_type": "video/mp4"},
        token=token,
    )
    print(json.dumps({"label": "upload_initiate", "samples": 1, "ms": round(elapsed * 1000, 2)}))


if __name__ == "__main__":
    main()
