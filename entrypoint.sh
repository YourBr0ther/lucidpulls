#!/bin/sh
set -e

# Ensure writable directories exist with correct ownership.
# This handles the case where Docker bind-mounts create host
# directories as root before the container starts.
for dir in /app/data /tmp/lucidpulls; do
    if [ ! -w "$dir" ] 2>/dev/null; then
        echo "Fixing permissions on $dir"
        # Only attempt if we're running as root (entrypoint runs as root)
        chown -R lucidpulls:lucidpulls "$dir" 2>/dev/null || true
    fi
done

# Drop to non-root user and exec the main command
exec gosu lucidpulls "$@"
