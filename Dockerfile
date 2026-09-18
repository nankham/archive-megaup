# Base image
FROM python:3.11-slim

# System dependencies (removed curl and unzip as rclone is no longer needed)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates git ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create non-root system user for security compliance
RUN groupadd -r botuser && useradd -r -g botuser -m botuser

# Set working directory
WORKDIR /app

# Copy application files
COPY --chown=botuser:botuser . /app

# Ensure entrypoint is executable
RUN chmod +x /app/entrypoint.sh

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create runtime download directory and configure permissions
RUN mkdir -p /downloads && \
    chown -R botuser:botuser /downloads && \
    chmod 750 /downloads

# Define volume for temp downloads
VOLUME ["/downloads"]

# Environment variables defaults
ENV TEMP_DOWNLOAD_DIR=/downloads \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as non-root user
USER botuser

# Container entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
