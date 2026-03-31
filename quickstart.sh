#!/bin/bash
# Tempograph Quickstart — creates a clean isolated environment
# Usage: bash quickstart.sh

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

INSTALL_DIR="$HOME/.tempograph"

echo -e "${BOLD}Tempograph Quickstart${NC}"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Install Python 3.11+ first."
    exit 1
fi

PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MINOR" -lt 11 ]; then
    echo "Python 3.$PY_MINOR found, but 3.11+ is required."
    exit 1
fi

# Create isolated environment
echo -e "Creating environment at ${CYAN}$INSTALL_DIR${NC}..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Install
echo "Installing tempograph..."
pip install --quiet --upgrade pip
pip install --quiet "git+https://github.com/Elmoaid/tempograph.git"

# Create launcher scripts
mkdir -p "$INSTALL_DIR/bin"

cat > "$INSTALL_DIR/bin/tempograph" << LAUNCHER
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
exec tempograph "\$@"
LAUNCHER
chmod +x "$INSTALL_DIR/bin/tempograph"

cat > "$INSTALL_DIR/bin/tempograph-server" << LAUNCHER
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
exec tempograph-server "\$@"
LAUNCHER
chmod +x "$INSTALL_DIR/bin/tempograph-server"

# Add to PATH hint
SHELL_RC=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ]; then
    if ! grep -q "tempograph/bin" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo '# Tempograph' >> "$SHELL_RC"
        echo 'export PATH="$HOME/.tempograph/bin:$PATH"' >> "$SHELL_RC"
        echo -e "Added to ${CYAN}$SHELL_RC${NC}"
    fi
fi

export PATH="$INSTALL_DIR/bin:$PATH"

echo ""
echo -e "${GREEN}${BOLD}Installed!${NC}"
echo ""
echo -e "${BOLD}Try it:${NC}"
echo ""
echo "  tempograph /path/to/any/repo --mode overview"
echo ""
echo -e "${BOLD}For AI agents (Claude Code, Cursor, etc.):${NC}"
echo ""
echo "  Add to ~/.claude/settings.json or .mcp.json:"
echo ""
echo '  {'
echo '    "mcpServers": {'
echo '      "tempograph": {'
echo "        \"command\": \"$INSTALL_DIR/bin/tempograph-server\","
echo '        "args": []'
echo '      }'
echo '    }'
echo '  }'
echo ""
echo -e "${BOLD}Commands:${NC}"
echo "  tempograph <repo> --mode overview    # Orient in a codebase"
echo "  tempograph <repo> --mode focus -q X  # Deep dive on symbol X"
echo "  tempograph <repo> --mode blast -f X  # What breaks if X changes"
echo "  tempograph <repo> --mode hotspots    # Highest-risk code"
echo "  tempograph <repo> --mode dead        # Find unused code"
echo ""
echo -e "Docs: ${CYAN}https://github.com/Elmoaid/tempograph${NC}"
