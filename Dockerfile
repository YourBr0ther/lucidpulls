FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install git, SSH client, and gosu for entrypoint user switching
RUN apt-get update && apt-get install -y \
    git \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user early for safer pip install
RUN useradd -m -u 1000 lucidpulls && \
    chown -R lucidpulls:lucidpulls /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies as non-root user
USER lucidpulls
ENV PATH="/home/lucidpulls/.local/bin:$PATH"
RUN pip install --user --no-cache-dir -r requirements.txt

# Switch back to root to copy files with correct ownership
USER root

# Copy application code
COPY src/ ./src/
COPY pyproject.toml .

# Create data and temp directories for SQLite and repos
RUN mkdir -p /app/data /tmp/lucidpulls && \
    chown -R lucidpulls:lucidpulls /app /tmp/lucidpulls

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Lightweight health check - verify main process is alive
HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=30s \
    CMD gosu lucidpulls python -c "import os, signal; os.kill(1, 0)" || exit 1

# Entrypoint fixes bind-mount permissions then drops to non-root user
ENTRYPOINT ["entrypoint.sh"]

# Default command
CMD ["python", "-m", "src.main"]
