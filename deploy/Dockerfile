# syntax=docker/dockerfile:1
# Build context is the repository root: `docker build -f deploy/Dockerfile .`

# ── builder ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy metadata + source, then install the package (not editable) into the venv.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# ── runtime ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/health.db

# Non-root user; /data owned by it so a fresh named volume inherits ownership.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

COPY --from=builder /opt/venv /opt/venv

USER appuser
WORKDIR /home/appuser
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "personal_health_mcp.server:create_default_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
