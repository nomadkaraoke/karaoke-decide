# Nomad Karaoke Decide - Production Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl lbzip2 zstd \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry==2.2.1

# Copy dependency files (poetry.lock pins shared packages to karaoke-gen's versions)
COPY pyproject.toml poetry.lock ./

# Install dependencies (no dev deps in production)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main --no-root

# Copy application code
COPY karaoke_decide/ ./karaoke_decide/
COPY backend/ ./backend/

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check for local `docker run` only (Cloud Run ignores Docker HEALTHCHECK and uses
# its own probes). Nothing sets HEALTHCHECK_PATH; the search service (same image, different
# command) is probed on /api/search/health by Cloud Run.
ENV HEALTHCHECK_PATH=/api/health
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f "http://localhost:8000${HEALTHCHECK_PATH}" || exit 1

# Run the API server. The search service overrides the command (Cloud Run --command):
#   uvicorn backend.search.app:search_app --host 0.0.0.0 --port 8000
# It downloads its index to SONG_INDEX_DIR (default /tmp/song-index, in-memory on Cloud Run).
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
