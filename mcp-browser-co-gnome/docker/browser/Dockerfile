FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install VNC and noVNC dependencies + build tools for pynput/evdev
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    supervisor \
    net-tools \
    procps \
    socat \
    build-essential \
    linux-libc-dev \
    python3-dev \
    curl \
    unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Download and install uBlock Origin extension
# https://github.com/gorhill/uBlock/releases
ARG UBLOCK_VERSION=1.62.0
RUN mkdir -p /opt/extensions && \
    curl -L "https://github.com/gorhill/uBlock/releases/download/${UBLOCK_VERSION}/uBlock0_${UBLOCK_VERSION}.chromium.zip" \
        -o /tmp/ublock.zip && \
    unzip /tmp/ublock.zip -d /opt/extensions/ && \
    mv /opt/extensions/uBlock0.chromium /opt/extensions/ublock-origin && \
    rm /tmp/ublock.zip && \
    chmod -R 755 /opt/extensions/ublock-origin

# Create a wrapper script to find and launch Playwright's chromium
RUN echo '#!/bin/bash\nexec $(find /ms-playwright -name "chrome" -type f | head -1) "$@"' > /usr/local/bin/chromium-pw \
    && chmod +x /usr/local/bin/chromium-pw

# Create symlink for noVNC
RUN ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# Set up working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Copy X11 input capture service
COPY x11_capture_service.py /app/x11_capture_service.py

# Copy chromium launcher script
COPY start_chromium.sh /app/start_chromium.sh
RUN chmod +x /app/start_chromium.sh

# Create directories for tmp (ephemeral data) and sessions
RUN mkdir -p /app/tmp/{videos,traces,har,logs,screenshots,x11_screenshots} /app/sessions

# Environment variables
ENV DISPLAY=:99
ENV VNC_PASSWORD=secret
ENV RESOLUTION=1920x1080x24
ENV VNC_PORT=5900
ENV NOVNC_PORT=6080
ENV INSTALL_UBLOCK=true

# Expose ports
# 6080 - noVNC web interface
# 5900 - VNC
# 9222 - Chrome DevTools Protocol
EXPOSE 6080 5900 9222

# Create startup script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# Set VNC password\n\
mkdir -p ~/.vnc\n\
x11vnc -storepasswd "${VNC_PASSWORD}" ~/.vnc/passwd\n\
\n\
# Start supervisor\n\
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf\n\
' > /app/start.sh && chmod +x /app/start.sh

CMD ["/app/start.sh"]
