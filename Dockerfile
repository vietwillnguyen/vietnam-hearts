# Multi-stage build for Vietnam Hearts Scheduler API
FROM python:3.10-slim as builder

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

# Install dependencies
RUN poetry install

# Production stage
FROM python:3.10-slim as production

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
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application files from builder (already copied there)
COPY --from=builder /build/app/ ./app/
COPY --from=builder /build/static/ ./static/
COPY --from=builder /build/templates/ ./templates/
COPY --from=builder /build/secrets/ ./secrets/

# Create logs directory and set permissions BEFORE switching user
RUN mkdir -p /app/logs && chown -R app:app /app/logs

# Switch to non-root user
USER app

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Update CMD to create logs directory if it doesn't exist
CMD ["sh", "-c", "mkdir -p /app/logs && chmod 755 /app/logs && uvicorn app.main:app --host 0.0.0.0 --port 8080"]