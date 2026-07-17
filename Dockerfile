# Multi-stage build for Vietnam Hearts Scheduler API
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /build

# Install locked production dependencies into a relocatable venv.
# Only the manifest and lockfile are needed here, so dependency layers
# cache until they actually change.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.12
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Production stage
FROM python:3.12-slim AS production

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

# Set working directory for the application
WORKDIR /app

# Copy the dependency venv (its python symlinks resolve against the same base image)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application files from the build context.
# Note: secrets/ is intentionally not copied here - it's gitignored and never
# present in the build context. The app falls back to Application Default
# Credentials when secrets/google_credentials.json is missing (see
# app/utils/google_credentials.py).
COPY app/ ./app/
COPY static/ ./static/
COPY templates/ ./templates/

# Create logs directory and set permissions BEFORE switching user
RUN mkdir -p /app/logs && chown -R app:app /app/logs

# Switch to non-root user
USER app

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
