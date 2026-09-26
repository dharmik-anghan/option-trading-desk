# Two stages, one shipped. Node builds the frontend and is discarded; the
# runtime is python:3.12-slim carrying the pinned runtime dependencies and the
# source. No uv, no build toolchain, no dev group, no test suite.

FROM node:22-alpine AS ui
WORKDIR /ui
# Manifests first, so editing a component does not reinstall node_modules.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies alone, and before the source, so a code change reuses this
# layer. The project itself is not installed: it is an application run from
# its source tree, not a library, so PYTHONPATH above is all it needs.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
COPY --from=ui /ui/dist ./frontend/dist

# No privileges are needed; the only writable path is the mounted data volume.
RUN useradd --create-home --uid 10001 desk \
 && mkdir -p /app/data \
 && chown -R desk:desk /app
USER desk

EXPOSE 8000
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
