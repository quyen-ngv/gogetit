FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome (updated method for Debian 12+)
RUN wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

# Verify Chrome installation
RUN google-chrome --version

# Set working directory
WORKDIR /app

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY run_job.py .
COPY browser_runtime.py .
COPY upload_to_api.py .
COPY telegram_bot.py .
COPY api_server.py .
COPY job_store.py .
COPY contribution_import.py .
COPY place_resolver.py .
COPY place_searcher.py .
COPY backend_place_search.py .
COPY social_location_extractor.py .
COPY social_eval.py .
COPY config.py .
COPY place_pipeline.py .
COPY place_refresh_job.py .
COPY place_review_refresh_job.py .
COPY nationwide_job.py .
COPY nationwide_regions.py .
COPY nationwide_search_config.json .
COPY place_urls.py .
COPY start.sh .

# Fix Windows CRLF + ensure executable (CRLF breaks shebang on Linux)
RUN sed -i 's/\r$//' /app/start.sh \
    && chmod +x /app/start.sh \
    && mkdir -p /app/logs /app/output /app/model-cache

# Bake the local Whisper model into the image; otherwise every new container downloads it
# during its first social video job. Must match LOCAL_WHISPER_MODEL / LOCAL_WHISPER_CACHE_DIR.
RUN python -c "from faster_whisper.utils import download_model; download_model('small', cache_dir='/app/model-cache')"

# Catch missing runtime modules/configuration during image build, not at startup.
RUN gallery-dl --version \
    && python -c "import api_server, curl_cffi, gallery_dl; from nationwide_regions import load_search_config; load_search_config()"

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PLACE_API_HOST=0.0.0.0
ENV PLACE_API_PORT=8080
ENV PLACE_API_MAX_WORKERS=1
ENV PLACE_BROWSER_MAX_CONCURRENCY=1
ENV PLACE_BROWSER_PAGE_LOAD_STRATEGY=eager
ENV PLACE_BROWSER_BLOCK_NONESSENTIAL=true
ENV PLACE_BROWSER_BLOCK_IMAGES=true
ENV PLACE_JOB_HISTORY_TTL_HOURS=24
ENV PLACE_JOB_HISTORY_MAX_COUNT=200
ENV PLACE_REFRESH_ENABLED=false
ENV PLACE_REFRESH_HOUR=6
ENV PLACE_REFRESH_MINUTE=0
ENV PLACE_REFRESH_TIMEZONE=Asia/Bangkok
ENV PLACE_REVIEW_REFRESH_ENABLED=true
ENV PLACE_REVIEW_REFRESH_HOUR=7
ENV PLACE_REVIEW_REFRESH_MINUTE=0
ENV PLACE_REVIEW_REFRESH_MAX_AGE_HOURS=24
ENV PLACE_REVIEW_BROWSER_CRASH_RETRIES=1
ENV PLACE_REVIEW_MAX_SCROLLS=300
ENV PLACE_REVIEW_SCRAPE_TIMEOUT_SECONDS=360
ENV PLACE_BROWSER_COMMAND_TIMEOUT_SECONDS=45
ENV LOCAL_WHISPER_ENABLED=true
ENV LOCAL_WHISPER_MODEL=small
ENV LOCAL_WHISPER_DEVICE=cpu
ENV LOCAL_WHISPER_COMPUTE_TYPE=int8
ENV LOCAL_WHISPER_CACHE_DIR=/app/model-cache

EXPOSE 8080

# HTTP API (:8080) + Telegram bot (optional if TELEGRAM_BOT_TOKEN is set)
CMD ["sh", "/app/start.sh"]
