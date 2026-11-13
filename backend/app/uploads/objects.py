"""S3 operations for private originals. Presigns use the public hostname."""

import logging
from dataclasses import dataclass
from datetime import datetime

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.infrastructure.storage import presigning_client, storage_client

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Object storage could not complete an operation. The message is an operation name."""

    def __init__(self, operation: str) -> None:
        super().__init__(operation)
        self.operation = operation


@dataclass(frozen=True)
class StoredPart:
    part_number: int
    etag: str
    size: int


def original_key(video_id: str) -> str:
    return f"originals/{video_id}/source"


def thumbnail_key(video_id: str) -> str:
    return f"thumbnails/{video_id}/auto.jpg"


def custom_thumbnail_key(video_id: str) -> str:
    return f"thumbnails/{video_id}/custom.jpg"


def avatar_key(channel_id: str) -> str:
    return f"avatars/{channel_id}.jpg"


def create_multipart(key: str, content_type: str) -> str:
    try:
        response = storage_client().create_multipart_upload(
            Bucket=get_settings().s3_bucket,
            Key=key,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("create multipart upload failed")
        raise StorageError("create_multipart") from exc
    upload_id = response.get("UploadId")
    if not isinstance(upload_id, str) or not upload_id:
        raise StorageError("create_multipart")
    return upload_id


def presign_part(key: str, upload_id: str, part_number: int, content_length: int) -> str:
    settings = get_settings()
    try:
        url = presigning_client().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": settings.s3_bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
                "ContentLength": content_length,
            },
            ExpiresIn=settings.upload_part_url_ttl_seconds,
            HttpMethod="PUT",
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("presign part failed")
        raise StorageError("presign_part") from exc
    if settings.s3_secret_key.get_secret_value() in url:
        raise StorageError("presign_part")
    return str(url)


def list_parts(key: str, upload_id: str) -> list[StoredPart]:
    client = storage_client()
    bucket = get_settings().s3_bucket
    parts: list[StoredPart] = []
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, object] = {
                "Bucket": bucket,
                "Key": key,
                "UploadId": upload_id,
            }
            if token is not None:
                kwargs["PartNumberMarker"] = token
            response = client.list_parts(**kwargs)
            for part in response.get("Parts", []):
                parts.append(
                    StoredPart(
                        part_number=int(part["PartNumber"]),
                        etag=str(part["ETag"]),
                        size=int(part["Size"]),
                    )
                )
            if not response.get("IsTruncated"):
                break
            token = str(response["NextPartNumberMarker"])
    except (BotoCoreError, ClientError, OSError, KeyError, TypeError, ValueError) as exc:
        logger.warning("list parts failed")
        raise StorageError("list_parts") from exc
    return parts


def complete_multipart(key: str, upload_id: str, parts: list[StoredPart]) -> None:
    try:
        storage_client().complete_multipart_upload(
            Bucket=get_settings().s3_bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"ETag": part.etag, "PartNumber": part.part_number}
                    for part in sorted(parts, key=lambda item: item.part_number)
                ]
            },
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("complete multipart upload failed")
        raise StorageError("complete_multipart") from exc


def abort_multipart(key: str, upload_id: str) -> None:
    try:
        storage_client().abort_multipart_upload(
            Bucket=get_settings().s3_bucket,
            Key=key,
            UploadId=upload_id,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchUpload", "404"}:
            return
        logger.warning("abort multipart upload failed")
        raise StorageError("abort_multipart") from exc
    except (BotoCoreError, OSError) as exc:
        logger.warning("abort multipart upload failed")
        raise StorageError("abort_multipart") from exc


def head_size(key: str) -> int | None:
    try:
        response = storage_client().head_object(Bucket=get_settings().s3_bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return None
        logger.warning("head object failed")
        raise StorageError("head_object") from exc
    except (BotoCoreError, OSError) as exc:
        logger.warning("head object failed")
        raise StorageError("head_object") from exc
    return int(response["ContentLength"])


def delete_object(key: str) -> None:
    try:
        storage_client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("delete object failed")
        raise StorageError("delete_object") from exc


def put_bytes(key: str, body: bytes, content_type: str, cache_control: str | None = None) -> None:
    extra: dict[str, str] = {}
    if cache_control:
        extra["CacheControl"] = cache_control
    try:
        storage_client().put_object(
            Bucket=get_settings().s3_bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            **extra,
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("put object failed")
        raise StorageError("put_object") from exc


def get_bytes(key: str) -> bytes | None:
    try:
        response = storage_client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return None
        logger.warning("get object failed")
        raise StorageError("get_object") from exc
    except (BotoCoreError, OSError) as exc:
        logger.warning("get object failed")
        raise StorageError("get_object") from exc
    return response["Body"].read()


def presign_get(key: str) -> str:
    settings = get_settings()
    try:
        url = presigning_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=settings.playback_segment_ttl_seconds,
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("presign get failed")
        raise StorageError("presign_get") from exc
    secret = settings.s3_secret_key.get_secret_value()
    if secret and secret in url:
        raise StorageError("presign_get")
    return url


def prefix_size(prefix: str) -> int:
    return sum(size for _key, size in _list_prefix(prefix))


def delete_prefix(prefix: str) -> None:
    client = storage_client()
    bucket = get_settings().s3_bucket
    try:
        for key, _size in _list_prefix(prefix):
            client.delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("delete prefix failed")
        raise StorageError("delete_prefix") from exc


def _list_prefix(prefix: str) -> list[tuple[str, int]]:
    client = storage_client()
    bucket = get_settings().s3_bucket
    found: list[tuple[str, int]] = []
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": prefix}
            if token is not None:
                kwargs["ContinuationToken"] = token
            page = client.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                found.append((str(item["Key"]), int(item["Size"])))
            if not page.get("IsTruncated"):
                return found
            token = str(page["NextContinuationToken"])
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("list prefix failed")
        raise StorageError("list_prefix") from exc


def get_to_path(key: str, path: str) -> None:
    try:
        storage_client().download_file(get_settings().s3_bucket, key, path)
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("download object failed")
        raise StorageError("download_object") from exc


def storage_reachable() -> bool:
    try:
        storage_client().head_bucket(Bucket=get_settings().s3_bucket)
    except (BotoCoreError, ClientError, OSError):
        return False
    return True


def list_orphan_uploads(known_ids: set[str]) -> list[tuple[str, str, datetime | None]]:
    """Return key, upload id, and start time for uploads that have no session row."""
    client = storage_client()
    bucket = get_settings().s3_bucket
    found: list[tuple[str, str, datetime | None]] = []
    key_marker: str | None = None
    upload_marker: str | None = None
    try:
        while True:
            kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": "originals/"}
            if key_marker is not None:
                kwargs["KeyMarker"] = key_marker
            if upload_marker is not None:
                kwargs["UploadIdMarker"] = upload_marker
            response = client.list_multipart_uploads(**kwargs)
            for item in response.get("Uploads", []):
                upload_id = str(item.get("UploadId", ""))
                key = str(item.get("Key", ""))
                started = item.get("Initiated")
                if upload_id and upload_id not in known_ids:
                    started_at = started if isinstance(started, datetime) else None
                    found.append((key, upload_id, started_at))
            if not response.get("IsTruncated"):
                break
            key_marker = str(response.get("NextKeyMarker") or "")
            upload_marker = str(response.get("NextUploadIdMarker") or "")
            if not key_marker:
                break
    except (BotoCoreError, ClientError, OSError):
        logger.warning("list multipart uploads failed")
        raise StorageError("list_multipart_uploads") from None
    return found
