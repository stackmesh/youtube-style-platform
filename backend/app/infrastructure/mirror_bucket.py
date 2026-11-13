"""Copy the private media bucket to a local directory. Secrets are not logged."""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.config import get_settings
from app.infrastructure.storage import storage_client


def main() -> None:
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/media-backup")
    if not destination.is_absolute():
        raise SystemExit("the backup directory must be an absolute path")
    settings = get_settings()
    bucket = settings.s3_bucket
    storage = storage_client()
    destination.mkdir(parents=True, exist_ok=True)
    token = None
    count = 0
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket}
        if token:
            kwargs["ContinuationToken"] = token
        page = storage.list_objects_v2(**kwargs)
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if key.startswith("/") or ".." in key.split("/"):
                raise SystemExit("refusing an object key that leaves the backup directory")
            target = (destination / key).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise SystemExit("refusing an object key that leaves the backup directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            storage.download_file(bucket, key, str(target))
            count += 1
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    print(f"mirrored {count} objects")


if __name__ == "__main__":
    main()
