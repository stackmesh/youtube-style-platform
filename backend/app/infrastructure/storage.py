import json
import logging
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.client import BaseClient, Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WILDCARD_PRINCIPALS = {"*", "arn:aws:iam::*:root"}
_FORBIDDEN_ACTIONS = {"*", "s3:*"}
_PRIVACY_PROBE_KEY = ".startup/privacy-probe"


def storage_client(endpoint_url: str | None = None) -> BaseClient:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def presigning_client() -> BaseClient:
    """Client whose signatures use the public S3 hostname."""
    return storage_client(get_settings().s3_public_endpoint_url)


def check_bucket() -> str:
    settings = get_settings()
    try:
        storage_client().head_bucket(Bucket=settings.s3_bucket)
    except (BotoCoreError, ClientError, OSError):
        logger.warning("object storage readiness check failed")
        return "fail"
    return "ok"


def assert_private_bucket() -> None:
    """Fail startup when the bucket is missing or an anonymous read succeeds."""
    settings = get_settings()
    client = storage_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        client.put_object(Bucket=settings.s3_bucket, Key=_PRIVACY_PROBE_KEY, Body=b"private")
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.error("object storage is not ready for startup")
        raise SystemExit(1) from exc
    if not _anonymous_read_is_denied(settings.s3_bucket, _PRIVACY_PROBE_KEY):
        logger.error("bucket allows anonymous reads")
        raise SystemExit(1)
    _reject_public_policy(client, settings.s3_bucket)
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=_PRIVACY_PROBE_KEY)
    except (BotoCoreError, ClientError, OSError):
        logger.warning("object storage privacy probe cleanup failed")


def policy_is_private(policy: dict[str, Any]) -> bool:
    statements = policy.get("Statement", [])
    if not isinstance(statements, list) or not statements:
        return False
    for statement in statements:
        if str(statement.get("Effect", "Allow")).lower() == "deny":
            continue
        if _principal_is_public(statement.get("Principal")):
            return False
        actions = statement.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        if any(action in _FORBIDDEN_ACTIONS for action in actions):
            return False
    return True


def _anonymous_read_is_denied(bucket: str, key: str) -> bool:
    anonymous = boto3.client(
        "s3",
        endpoint_url=get_settings().s3_endpoint_url,
        aws_access_key_id="",
        aws_secret_access_key="",
        region_name=get_settings().s3_region,
        config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}),
    )
    try:
        anonymous.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status in {401, 403}
    except (BotoCoreError, OSError):
        logger.warning("anonymous storage probe failed to connect")
        return False
    return False


def _reject_public_policy(client: BaseClient, bucket: str) -> None:
    try:
        response = client.get_bucket_policy(Bucket=bucket)
    except ClientError:
        return
    except (BotoCoreError, OSError) as exc:
        logger.error("object storage is not ready for startup")
        raise SystemExit(1) from exc
    policy = json.loads(response["Policy"])
    if not policy_is_private(policy):
        logger.error("bucket policy allows public access")
        raise SystemExit(1)


def _principal_is_public(principal: Any) -> bool:
    if isinstance(principal, str):
        return principal in _WILDCARD_PRINCIPALS
    if isinstance(principal, list):
        return any(_principal_is_public(item) for item in principal)
    if isinstance(principal, dict):
        return any(_principal_is_public(value) for value in principal.values())
    return False
