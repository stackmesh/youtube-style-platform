"""Create the private media bucket and its S3 controls.

Runs only in the one-shot init container. That container holds the storage-admin
identity. The API and worker do not.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def client():
    return boto3.client(
        "s3",
        endpoint_url=_required("S3_ENDPOINT_URL").rstrip("/"),
        aws_access_key_id=_required("S3_ADMIN_ACCESS_KEY"),
        aws_secret_access_key=_required("S3_ADMIN_SECRET_KEY"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def signed_quota(method: str, payload: dict[str, object] | None) -> tuple[int, bytes]:
    endpoint = _required("S3_ENDPOINT_URL").rstrip("/")
    bucket = _required("S3_BUCKET")
    region = os.environ.get("S3_REGION", "us-east-1")
    access_key = _required("S3_ADMIN_ACCESS_KEY")
    secret_key = _required("S3_ADMIN_SECRET_KEY")
    body = b"" if payload is None else json.dumps(payload).encode()
    headers = {"x-amz-content-sha256": hashlib.sha256(body).hexdigest()}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = AWSRequest(
        method=method,
        url=f"{endpoint}/{bucket}?seaweedfs-quota",
        data=body or None,
        headers=headers,
    )
    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    credentials = session.get_credentials()
    if credentials is None:
        raise SystemExit("storage admin credentials are missing")
    SigV4Auth(credentials.get_frozen_credentials(), "s3", region).add_auth(request)
    prepared = request.prepare()
    outbound = urllib.request.Request(
        prepared.url,
        data=prepared.body,
        method=method,
        headers={key: value for key, value in prepared.headers.items()},
    )
    try:
        with urllib.request.urlopen(outbound, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"quota {method} failed with HTTP {exc.code}: {detail[:180]}") from exc


def wait_until_ready(s3) -> None:
    for _ in range(30):
        try:
            s3.list_buckets()
            return
        except (BotoCoreError, ClientError, OSError):
            time.sleep(2)
    raise SystemExit("object storage did not accept the init client")


def ensure_bucket(s3) -> None:
    bucket = _required("S3_BUCKET")
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise
        print("bucket already exists")
        return
    print("bucket created")


def configure(s3) -> None:
    bucket = _required("S3_BUCKET")
    origin = _required("PUBLIC_APP_URL")
    quota_bytes = int(_required("S3_BUCKET_QUOTA_BYTES"))
    if quota_bytes < 1:
        raise SystemExit("S3_BUCKET_QUOTA_BYTES is too small")
    s3.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": [origin],
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedHeaders": ["Content-Type", "Content-Length", "x-amz-*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 300,
                }
            ]
        },
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "abort-incomplete-multipart",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                }
            ]
        },
    )
    status, _body = signed_quota(
        "PUT",
        {"quota_size": quota_bytes, "quota_unit": "B", "quota_enabled": True},
    )
    if status not in {200, 204}:
        raise SystemExit(f"bucket quota write returned HTTP {status}")
    _status, raw = signed_quota("GET", None)
    saved = json.loads(raw)
    if not saved.get("quota_enabled") or int(saved.get("quota_size", 0)) != quota_bytes:
        raise SystemExit("bucket quota read-back did not match")
    print("bucket controls applied")


def main() -> None:
    s3 = client()
    wait_until_ready(s3)
    ensure_bucket(s3)
    configure(s3)
    print("object storage initialization finished")


if __name__ == "__main__":
    main()
