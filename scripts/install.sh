#!/usr/bin/env bash
set -euo pipefail

PACKAGE="mcp-harbour"
SERVICE_NAME="mcp-harbour"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; exit 1; }

# ── 1. Install package ─────────────────────────────────────────────

REPO="https://github.com/GPARS-org/mcp-harbour.git"

if command -v uv &>/dev/null; then
    info "Installing ${PACKAGE} via uv..."
    uv tool install "$PACKAGE" 2>/dev/null || uv tool install "git+${REPO}"
elif command -v pipx &>/dev/null; then
    info "Installing ${PACKAGE} via pipx..."
    pipx install "$PACKAGE" 2>/dev/null || pipx install "git+${REPO}"
else
    error "Neither uv nor pipx found. Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
fi

# Verify installation
if ! command -v harbour &>/dev/null; then
    error "'harbour' command not found after install. Check that ~/.local/bin is in your PATH."
fi

HARBOUR_BIN=$(command -v harbour)
info "Installed harbour at ${HARBOUR_BIN}"

# ── 2. Register service ────────────────────────────────────────────

OS=$(uname -s)

if [ "$OS" = "Linux" ]; then
    # systemd user service
    UNIT_DIR="${HOME}/.config/systemd/user"
    UNIT_FILE="${UNIT_DIR}/${SERVICE_NAME}.service"

    mkdir -p "$UNIT_DIR"

    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=MCP Harbour Daemon
After=network.target

[Service]
Type=simple
ExecStart=${HARBOUR_BIN} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user start "$SERVICE_NAME"

    info "Registered systemd user service: ${UNIT_FILE}"
    info "Daemon started on 127.0.0.1:4767"

    echo ""
    info "Manage with:"
    echo "  harbour status"
    echo "  harbour stop"
    echo "  harbour start"

elif [ "$OS" = "Darwin" ]; then
    # launchd user agent
    PLIST_DIR="${HOME}/Library/LaunchAgents"
    PLIST_FILE="${PLIST_DIR}/dev.mcp-harbour.daemon.plist"

    mkdir -p "$PLIST_DIR"

    LOG_DIR="${HOME}/.mcp-harbour"
    mkdir -p "$LOG_DIR"

    cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.mcp-harbour.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>${HARBOUR_BIN}</string>
        <string>serve</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/daemon.log</string>
</dict>
</plist>
EOF

    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load "$PLIST_FILE"

    info "Registered launchd agent: ${PLIST_FILE}"
    info "Daemon started on 127.0.0.1:4767"

    echo ""
    info "Manage with:"
    echo "  harbour status"
    echo "  harbour stop"
    echo "  harbour start"

else
    error "Unsupported OS: ${OS}. Use install.ps1 for Windows."
fi

echo ""
info "Installation complete."
