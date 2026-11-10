import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.storage import policy_is_private
from app.main import refuse_root_credentials
from app.worker.heartbeat import write_heartbeat


def test_process_environment_has_no_storage_admin_credentials() -> None:
    assert "MINIO_ROOT_USER" not in os.environ
    assert "MINIO_ROOT_PASSWORD" not in os.environ
    assert "S3_ADMIN_ACCESS_KEY" not in os.environ
    assert "S3_ADMIN_SECRET_KEY" not in os.environ


def test_startup_refuses_root_credentials(monkeypatch) -> None:
    monkeypatch.setenv("S3_ADMIN_SECRET_KEY", "should-not-be-here")
    with pytest.raises(SystemExit):
        refuse_root_credentials()


def test_public_bucket_policy_is_rejected() -> None:
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::media/*"],
            }
        ]
    }
    assert policy_is_private(policy) is False


def test_admin_wildcard_action_is_rejected() -> None:
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["arn:aws:iam::minio:user/media-app"]},
                "Action": ["s3:*"],
                "Resource": ["arn:aws:s3:::media/*"],
            }
        ]
    }
    assert policy_is_private(policy) is False


def test_app_bucket_policy_is_private() -> None:
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["arn:aws:iam::minio:user/media-app"]},
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": ["arn:aws:s3:::media/*"],
            }
        ]
    }
    assert policy_is_private(policy) is True


def test_upload_cap_cannot_exceed_part_budget() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://youtube:youtube@localhost/youtube",
            redis_url="redis://localhost:6379/0",
            s3_endpoint_url="http://seaweedfs:8333",
            s3_public_endpoint_url="http://s3.localhost",
            s3_access_key="media-app",
            s3_secret_key="media-app-local-secret",
            jwt_secret="dev-only-jwt-secret-change-me-32b!",
            public_app_url="http://localhost",
            cors_origins="http://localhost",
            max_upload_bytes=16_777_216 * 512 + 1,
        )


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        self.ttl = ex


def test_heartbeat_writes_timestamp() -> None:
    client = FakeRedis()
    write_heartbeat(client)  # type: ignore[arg-type]
    assert "worker:heartbeat" in client.values
    assert client.ttl == 90
