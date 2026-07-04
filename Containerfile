# Containerfile — one image for the Reddit→TikTok pipeline (k8s migration Phase 5).
# Built with Podman (natively on an amd64 node). Run as multiple k8s workloads
# (webapp/bot Deployments + render/upload/confirm CronJobs) with different
# commands — this image bakes CODE + DEPS only; data/config/secrets come from
# PVCs + Secrets at runtime.
#
# NOTE: the SvelteKit SPA is copied PRE-BUILT from webapp/frontend/build/ (the
# same artifact the systemd deploy serves; built via `make build-spa`/pnpm).
# Building the SPA in-image is a future improvement — the repo currently has no
# committed svelte.config, so an in-container `pnpm build` can't reproduce it
# from a clean clone. The build/ dir must be present in the build context.

# Playwright image matched to playwright==1.61.0: ships Chromium
# (Chrome-for-Testing) + all browser OS deps + Xvfb, and a non-root `pwuser`
# (Chromium is launched WITHOUT --no-sandbox, so we MUST run non-root).
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

# System ffmpeg (core/ffmpeg.py resolves it via shutil.which). curl + ca-certs
# are needed to fetch the litestream binary below.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Litestream (pinned to the host version 0.5.13) — streams the SQLite DB to R2.
# Runs as a sidecar in the webapp pod under k8s; baked into THIS image so the
# sidecar pulls from the in-cluster registry (no docker.io dependency), matching
# the Phase 6 one-image design. Asset is x86_64 (amd64 build node).
ARG LITESTREAM_VERSION=0.5.13
RUN curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz" \
      -o /tmp/litestream.tar.gz \
 && tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream \
 && rm /tmp/litestream.tar.gz \
 && litestream version

WORKDIR /app

# Python deps. requirements.txt auto-skips mlx-whisper (darwin marker).
# en_core_web_sm is not on PyPI → install from the wheel URL.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

# App code (NOT data/, config runtime state, secrets — those mount at runtime).
COPY core/ ./core/
COPY pipeline/ ./pipeline/
COPY scripts/ ./scripts/
COPY assets/ ./assets/
COPY alembic/ ./alembic/
COPY webapp/ ./webapp/
COPY alembic.ini main.py pyproject.toml config.toml ./

ENV TZ=Europe/Madrid \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Chromium sandbox requires non-root; pwuser's HOME (/home/pwuser) is writable
# so the crashpad-handler DB path resolves.
RUN chown -R pwuser:pwuser /app
USER pwuser

# Default command is the webapp; render/upload/bot/confirm override it in k8s.
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "webapp.backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
