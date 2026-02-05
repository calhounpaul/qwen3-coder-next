#!/bin/bash
# Chromium launcher with optional uBlock Origin extension

CHROMIUM_BIN=$(find /ms-playwright -name "chrome" -type f | head -1)

# Base arguments
ARGS=(
    --no-sandbox
    --disable-gpu
    --disable-dev-shm-usage
    --start-maximized
    --no-first-run
    --disable-infobars
    --noerrdialogs
    --disable-session-crashed-bubble
    --remote-debugging-port=9223
    --disable-blink-features=AutomationControlled
)

# Add uBlock Origin extension if enabled
if [ "${INSTALL_UBLOCK}" = "true" ] && [ -d "/opt/extensions/ublock-origin" ]; then
    echo "Loading uBlock Origin extension..."
    ARGS+=(
        "--disable-extensions-except=/opt/extensions/ublock-origin"
        "--load-extension=/opt/extensions/ublock-origin"
    )
else
    echo "uBlock Origin disabled or not found"
fi

echo "Starting Chromium with args: ${ARGS[*]}"
exec "$CHROMIUM_BIN" "${ARGS[@]}"
