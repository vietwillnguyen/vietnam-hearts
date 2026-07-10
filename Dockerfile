# Multi-stage build for Vietnam Hearts Scheduler API
FROM python:3.10-slim AS builder

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry

# Set working directory for dependency installation
WORKDIR /build

# Copy the entire project for Poetry to understand the structure
COPY . .

# Configure Poetry to not create virtual environment
RUN poetry config virtualenvs.create false

# Install production dependencies only
RUN poetry install --only main

# Production stage
FROM python:3.10-slim AS production

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

# Set working directory for the application
WORKDIR /app

# Copy only the Python packages we need
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages

# Copy only installed entry-point scripts (not build tools like poetry/pip)
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy application files from builder (already copied there)
# NOTE: no secrets/ in the image - Cloud Run authenticates via Application
# Default Credentials; local Docker runs mount credentials at runtime instead.
COPY --from=builder /build/app/ ./app/
COPY --from=builder /build/static/ ./static/
COPY --from=builder /build/templates/ ./templates/

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