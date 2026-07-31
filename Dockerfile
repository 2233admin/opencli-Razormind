# ── Stage 1: builder ──────────────────────────────────────────────────────────
ARG REGISTRY=
FROM ${REGISTRY}python:3.13-slim AS builder

WORKDIR /app

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a prefix so we can copy them cleanly
COPY pyproject.toml .
RUN pip install --upgrade pip \
    && pip install --prefix=/install .

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
ARG REGISTRY=
FROM ${REGISTRY}python:3.13-slim AS runtime

WORKDIR /app

# Runtime system deps (psycopg2 needs libpq, opencli needs Node.js 22+)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install opencli globally — available as 'opencli' on PATH
ARG OPENCLI_VERSION=1.8.5
ARG IMAGE_TAG=latest
COPY scripts/patch-opencli.js /tmp/patch-opencli.js
RUN npm install -g @jackwener/opencli@${OPENCLI_VERSION} \
    && node /tmp/patch-opencli.js \
    && rm /tmp/patch-opencli.js \
    && rm -rf /root/.npm

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY backend/ ./backend/
COPY scripts/patch-opencli.js ./scripts/patch-opencli.js
COPY scripts/install-agent.sh ./scripts/install-agent.sh
COPY alembic.ini .

# Entrypoint handles migrations
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Non-root user for security; pre-create /data so the SQLite volume is writable
RUN useradd -m -u 1000 appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /app /data
USER appuser

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Bake the image tag so the system config API can serve it to clients.
ARG IMAGE_TAG=latest
ENV IMAGE_TAG=${IMAGE_TAG}

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--access-log", "--log-level", "info"]
