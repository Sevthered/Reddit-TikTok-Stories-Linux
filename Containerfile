# Containerfile — one image for the Reddit→TikTok pipeline (k8s migration Phase 5).
# Built with Podman (natively on an amd64 node). Run as multiple k8s workloads
# (webapp/bot Deployments + render/upload/confirm CronJobs) with different
# commands — this image bakes CODE + DEPS only; data/config/secrets come from
# PVCs + Secrets at runtime.

# ---- Stage 1: build the SvelteKit static SPA ----
FROM node:22-slim AS spa
WORKDIR /spa
RUN npm install -g pnpm
# Install deps first (cache layer), then build.
COPY webapp/frontend/package.json webapp/frontend/pnpm-lock.yaml webapp/frontend/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY webapp/frontend/ ./
RUN pnpm build
# → /spa/build (adapter-static, index.html fallback)

# ---- Stage 2: runtime ----
# Playwright image matched to playwright==1.61.0: ships Chromium
# (Chrome-for-Testing) + all browser OS deps + Xvfb, and a non-root `pwuser`
# (Chromium is launched WITHOUT --no-sandbox, so we MUST run non-root).
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble AS runtime

# System ffmpeg (core/ffmpeg.py resolves it via shutil.which).
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

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
COPY webapp/ ./webapp/
COPY scripts/ ./scripts/
COPY assets/ ./assets/
COPY alembic/ ./alembic/
COPY alembic.ini main.py pyproject.toml config.toml ./

# Built SPA from stage 1 → where FastAPI serves it (settings.FRONTEND_BUILD_DIR).
COPY --from=spa /spa/build ./webapp/frontend/build

ENV TZ=Europe/Madrid \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Chromium sandbox requires non-root; pwuser's HOME (/home/pwuser) is writable
# so the crashpad-handler DB path resolves (the systemd HOME-redirect bug does
# not apply here — nothing makes $HOME inaccessible in the container).
RUN chown -R pwuser:pwuser /app
USER pwuser

# Default command is the webapp; render/upload/bot/confirm override it in k8s.
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "webapp.backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
