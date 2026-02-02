FROM python:3.11-slim@sha256:5be45dbade29bebd6886af6b438fd7e0b4eb7b611f39ba62b430263f82de36d2

# Set working directory
WORKDIR /app

# Install git, SSH client, gosu, and Node.js (for JS/TS syntax validation)
RUN apt-get update && apt-get install -y \
    git \
    openssh-client \
    gosu \
    nodejs \
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
COPY alembic.ini .
COPY migrations/ ./migrations/

# Create data and temp directories for SQLite and repos
RUN mkdir -p /app/data /tmp/lucidpulls && \
    chown -R lucidpulls:lucidpulls /app /tmp/lucidpulls

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check - verify process is alive and heartbeat is recent
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=60s \
    CMD gosu lucidpulls python -m src.main --health-check || exit 1

# Entrypoint fixes bind-mount permissions then drops to non-root user
ENTRYPOINT ["entrypoint.sh"]

# Default command
CMD ["python", "-m", "src.main"]
