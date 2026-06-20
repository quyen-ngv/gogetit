FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
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
COPY upload_to_api.py .
COPY telegram_bot.py .

# Create directory for logs
RUN mkdir -p /app/logs /app/output

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Default environment variables (can be overridden)
ENV TELEGRAM_BOT_TOKEN=8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE
ENV TELEGRAM_USER_ID=5636346689

# Run bot
CMD ["python", "-u", "telegram_bot.py"]
