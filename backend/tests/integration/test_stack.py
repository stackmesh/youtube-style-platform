import os
from urllib.parse import urlparse

import boto3
import pytest
from botocore import UNSIGNED
from botocore.client import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.migrate import upgrade_head
from app.main import app


def test_ready_with_live_dependencies() -> None:
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "postgres": "ok",
        "redis": "ok",
        "s3": "ok",
        "worker": "ok",
    }


def test_bucket_rejects_anonymous_read() -> None:
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id="",
        aws_secret_access_key="",
        config=Config(
            signature_version=UNSIGNED,
            s3={"addressing_style": "path"},
        ),
    )
    with pytest.raises(ClientError) as caught:
        client.get_object(Bucket=settings.s3_bucket, Key="private-probe")
    assert caught.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403


def test_alembic_upgrade_on_empty_database(monkeypatch) -> None:
    settings = get_settings()
    admin_url = make_url(settings.database_url).set(database="postgres")
    maintenance = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_name = "youtube_migration_test"
    with maintenance.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    maintenance.dispose()

    empty_url = admin_url.set(database=database_name)
    monkeypatch.setenv("DATABASE_URL", empty_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        upgrade_head()
        check = create_engine(empty_url)
        with check.connect() as connection:
            names = {row[0] for row in connection.execute(text("SELECT extname FROM pg_extension"))}
        check.dispose()
    finally:
        get_settings.cache_clear()
        monkeypatch.undo()
        cleanup = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with cleanup.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        cleanup.dispose()
        get_settings.cache_clear()

    assert "citext" in names
    assert "pg_trgm" in names
    parsed = urlparse(os.environ["DATABASE_URL"])
    assert parsed.path != f"/{database_name}"
