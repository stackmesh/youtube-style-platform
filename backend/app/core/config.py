from functools import lru_cache
from typing import Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    redis_url: str
    s3_endpoint_url: str
    s3_public_endpoint_url: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket: str = "media"
    s3_region: str = "us-east-1"
    part_size_bytes: int = 16_777_216
    max_parts: int = 512
    upload_part_url_ttl_seconds: int = 1200
    jwt_secret: SecretStr
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    public_app_url: str
    cookie_secure: bool = False
    cors_origins: str
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: SecretStr
    max_upload_bytes: int = 2_147_483_648
    max_duration_seconds: int = 3600
    default_quota_bytes: int = 10_737_418_240
    max_active_uploads: int = 2
    ffmpeg_concurrency: int = 1
    work_dir: str = "/var/lib/media-work"
    playback_segment_ttl_seconds: int = 120
    storage_startup_check: bool = True
    docs_enabled: bool = True
    trusted_hosts: str = ""
    worker_heartbeat_ttl_seconds: int = 90
    worker_heartbeat_max_age_seconds: int = 120

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @field_validator("max_upload_bytes")
    @classmethod
    def upload_cap_is_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_UPLOAD_BYTES must be at least 1")
        return value

    @model_validator(mode="after")
    def validate_upload_geometry(self) -> Self:
        ceiling = self.part_size_bytes * self.max_parts
        if self.max_upload_bytes > ceiling:
            raise ValueError("MAX_UPLOAD_BYTES exceeds PART_SIZE_BYTES * MAX_PARTS")
        return self

    @model_validator(mode="after")
    def secure_cookies_when_public_url_is_https(self) -> Self:
        if self.public_app_url.lower().startswith("https://") and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when PUBLIC_APP_URL uses https")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.trusted_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
