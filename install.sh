#!/bin/bash
# Tempograph — one-command install
# Usage: curl -fsSL https://raw.githubusercontent.com/Elmoaid/tempograph/main/install.sh | bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Installing tempograph...${NC}"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 not found. Install Python 3.11+ first.${NC}"
    echo "  macOS: brew install python@3.12"
    echo "  Ubuntu: sudo apt install python3.12"
    echo "  Windows: https://python.org/downloads"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VERSION | cut -d. -f1)
PY_MINOR=$(echo $PY_VERSION | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo -e "${RED}Python $PY_VERSION found, but 3.11+ is required.${NC}"
    exit 1
fi

echo -e "  Python $PY_VERSION ${GREEN}OK${NC}"

# Install with pipx if available, else pip
if command -v pipx &> /dev/null; then
    echo "  Using pipx..."
    pipx install tempograph 2>/dev/null || pipx install "git+https://github.com/Elmoaid/tempograph.git"
elif command -v pip &> /dev/null; then
    echo "  Using pip..."
    pip install tempograph 2>/dev/null || pip install "git+https://github.com/Elmoaid/tempograph.git"
else
    echo "  Using pip3..."
    pip3 install tempograph 2>/dev/null || pip3 install "git+https://github.com/Elmoaid/tempograph.git"
fi

echo ""
echo -e "${GREEN}Installed!${NC} Try:"
echo ""
echo "  tempograph /path/to/your/repo --mode overview"
echo ""
echo "For AI agent integration (Claude, Cursor, etc.), add to your MCP config:"
echo ""
echo '  {'
echo '    "mcpServers": {'
echo '      "tempograph": {'
echo '        "command": "tempograph-server",'
echo '        "args": []'
echo '      }'
echo '    }'
echo '  }'
echo ""
echo "For 170+ language support: pip install 'tempograph[full]'"
