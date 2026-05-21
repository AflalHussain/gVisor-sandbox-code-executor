FROM python:3.11-slim-bookworm

# 1. Provision standard networking packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# 2. Add common engineering packages
RUN pip install --no-cache-dir numpy pandas requests pydantic

# 3. Handle wrapper mappings
WORKDIR /sandbox
COPY executor.py /usr/local/bin/executor.py
RUN chmod +x /usr/local/bin/executor.py

# Give the unprivileged runtime user a writable home so pip user installs work.
RUN mkdir -p /home/nobody /workspace && \
    chown -R nobody:nogroup /home/nobody /workspace && \
    chmod 755 /home/nobody && \
    chmod 777 /workspace
ENV HOME=/home/nobody
VOLUME /workspace

# Drop privileges to non-root account for security
USER nobody
