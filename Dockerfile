FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies in builder stage only
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Production stage ──
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY src/ ./src/

# Create non-root user. /app is root-owned, so the writable runtime directories
# have to be created and handed over explicitly — history.py and pattern_memory.py
# call data/.mkdir() at runtime, and the email service writes HTML into out/,
# which otherwise fails with EACCES.
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data /app/logs /app/out \
    && chown -R appuser:appuser /app/data /app/logs /app/out
USER appuser

# Disable verbose console output in production
ENV AZBRIEF_VERBOSE=false

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run the application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
