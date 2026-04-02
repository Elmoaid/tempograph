FROM python:3.12-slim

WORKDIR /app

# Install git (needed for co-change analysis and diff context)
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install tempograph with all optional dependencies
COPY pyproject.toml README.md LICENSE ./
COPY tempograph/ tempograph/
COPY tempo/ tempo/

RUN pip install --no-cache-dir ".[full]"

# Expose for inspection
EXPOSE 3000

# Default: SSE transport for Docker (inspectable by Glama)
# For stdio transport (Claude/Cursor local): tempograph-server
ENTRYPOINT ["tempograph-server", "--transport", "sse", "--port", "3000"]
