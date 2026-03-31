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

# Default: run MCP server (stdio transport for Claude/Cursor integration)
ENTRYPOINT ["tempograph-server"]
