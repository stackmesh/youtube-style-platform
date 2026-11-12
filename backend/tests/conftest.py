import os

os.environ.setdefault("STORAGE_STARTUP_CHECK", "false")
os.environ.setdefault("JWT_SECRET", "dev-only-jwt-secret-change-me-32b!")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://youtube:youtube@localhost:5432/youtube",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:8333")
os.environ.setdefault("S3_PUBLIC_ENDPOINT_URL", "http://s3.localhost")
os.environ.setdefault("S3_ACCESS_KEY", "media-app")
os.environ.setdefault("S3_SECRET_KEY", "media-app-local-secret")
os.environ.setdefault("S3_BUCKET", "media")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost")
os.environ.setdefault("CORS_ORIGINS", "http://localhost")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "test-bootstrap-password")
