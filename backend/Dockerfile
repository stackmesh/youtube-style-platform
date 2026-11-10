FROM python:3.12-slim-bookworm AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY alembic ./alembic
COPY tests ./tests
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN pip install --no-cache-dir ".[dev]" \
    && chmod 755 /docker-entrypoint.sh \
    && mkdir -p /var/lib/media-work \
    && chown -R appuser:appuser /app /var/lib/media-work

USER appuser
EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]

FROM api AS worker

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
USER appuser
ENTRYPOINT ["python", "-m", "app.worker.main"]
