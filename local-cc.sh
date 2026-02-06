#!/bin/bash
set -e

# =============================================================================
# local-cc.sh - Unified Local Qwen Code Environment
# =============================================================================
# Spins up:
#   - Code LLM (Qwen3-Coder-Next) on port 8003 (VRAM-auto quant)
#   - Browser automation containers (noVNC, video)
#   - VLM (Qwen3-VL-4B) on port 8004 with --vlm flag
#   - ML services (OmniParser, GUI-Actor) with --ml flag
#   - MCP browser server for Qwen Code
#
# Usage:
#   ./local-cc.sh              # Start services and launch Qwen Code
#   ./local-cc.sh --vlm        # Include VLM for image analysis
#   ./local-cc.sh --ml         # Include ML services (OmniParser, GUI-Actor)
#   ./local-cc.sh --stop       # Stop all services
#   ./local-cc.sh --status     # Show status of all services
#   ./local-cc.sh --install    # Install as 'local-cc' command system-wide
# =============================================================================

# TOOL_DIR: where local-cc.sh and its resources live (models, mcp-browser-co-gnome)
# PROJECT_DIR: where to launch Qwen Code (current working directory)
TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${LOCAL_CC_PROJECT_DIR:-$PWD}"

# Configuration - tools are in TOOL_DIR, not PROJECT_DIR
MODEL_DIR="$TOOL_DIR/models"
BROWSER_DIR="$TOOL_DIR/mcp-browser-co-gnome"

# Ensure submodule is initialized and up to date
if [[ ! -f "$BROWSER_DIR/docker-compose.yml" ]]; then
    echo "[INFO] Initializing mcp-browser-co-gnome submodule..."
    git -C "$TOOL_DIR" submodule update --init --recursive
else
    # Update submodule if it's behind the committed version
    git -C "$TOOL_DIR" submodule update --init --recursive --quiet
fi

# Ensure tmp directories exist with correct permissions
# Docker containers create directories as root, but MCP server runs as current user
ensure_tmp_permissions() {
    local tmp_dir="$BROWSER_DIR/tmp"
    local dirs=("screenshots" "omniparser" "logs" "sessions" "videos" "x11_screenshots")

    mkdir -p "$tmp_dir"
    for d in "${dirs[@]}"; do
        mkdir -p "$tmp_dir/$d"
    done

    # Fix ownership if directories were created by Docker as root
    if [[ -d "$tmp_dir" ]] && [[ "$(stat -c '%U' "$tmp_dir" 2>/dev/null)" == "root" ]]; then
        echo "[INFO] Fixing tmp directory permissions..."
        sudo chown -R "$(id -u):$(id -g)" "$tmp_dir"
    fi
}
ensure_tmp_permissions

# Docker image names
LLAMA_IMAGE="llama-server-cuda:latest"

# Container names
CODE_CONTAINER="qwen3-server"
BROWSER_CONTAINER="automation-browser"
VIDEO_CONTAINER="automation-video"
VLM_CONTAINER="automation-vlm"
OMNIPARSER_CONTAINER="automation-omniparser"
GUI_ACTOR_CONTAINER="automation-gui-actor"

# Model configuration
CODE_MODEL_REPO="unsloth/Qwen3-Coder-Next-GGUF"
CODE_MODEL_DIR="Qwen3-Coder-Next-GGUF"
# Model file is auto-selected based on GPU name (see select_model_quant)

# Ports
CODE_PORT=8003
VLM_PORT=8004
NOVNC_PORT=6080
VNC_PORT=5900
CDP_PORT=9222
OMNIPARSER_PORT=8010
GUI_ACTOR_PORT=8001
CHISEL_PORT=8889
PROXY_PORT=8013

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
header() { echo -e "\n${CYAN}=== $1 ===${NC}\n"; }

# =============================================================================
# Remote Server Configuration
# =============================================================================

# Remote endpoint URLs (empty = use local)
REMOTE_CODE_URL=""
REMOTE_VLM_URL=""
REMOTE_NOVNC_URL=""
REMOTE_CDP_URL=""
REMOTE_OMNIPARSER_URL=""
REMOTE_GUI_ACTOR_URL=""

# Single gateway tunnel (auto-derives all remote URLs)
REMOTE_TUNNEL_URL=""
TUNNEL_KEY=""

# Flag to skip security prompts (--insecure-ok)
INSECURE_OK=false

# Check if a URL is potentially insecure (http:// to non-IP domain)
check_url_security() {
    local url="$1"
    local service_name="$2"

    # Empty URL is fine (local)
    [[ -z "$url" ]] && return 0

    # Extract protocol and host
    local protocol="${url%%://*}"
    local host_port="${url#*://}"
    local host="${host_port%%:*}"
    local host="${host%%/*}"

    # Check if it's HTTP (not HTTPS)
    if [[ "$protocol" == "http" ]]; then
        # Check if host is NOT an IP address (v4 or v6)
        if ! [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && \
           ! [[ "$host" =~ ^localhost$ ]] && \
           ! [[ "$host" =~ ^\[.*\]$ ]] && \
           ! [[ "$host" =~ ^::1$ ]]; then
            # It's a domain name with HTTP - warn user
            if ! $INSECURE_OK; then
                echo ""
                echo -e "${RED}╔════════════════════════════════════════════════════════════════╗${NC}"
                echo -e "${RED}║                    ⚠️  SECURITY WARNING ⚠️                       ║${NC}"
                echo -e "${RED}╠════════════════════════════════════════════════════════════════╣${NC}"
                echo -e "${RED}║${NC} Service: ${YELLOW}${service_name}${NC}"
                echo -e "${RED}║${NC} URL:     ${YELLOW}${url}${NC}"
                echo -e "${RED}║${NC}"
                echo -e "${RED}║${NC} You are connecting to a ${YELLOW}non-local domain${NC} using ${YELLOW}HTTP${NC}"
                echo -e "${RED}║${NC} (unencrypted). Your API requests and responses will be"
                echo -e "${RED}║${NC} sent in ${YELLOW}plain text${NC} over the internet."
                echo -e "${RED}║${NC}"
                echo -e "${RED}║${NC} This could expose:"
                echo -e "${RED}║${NC}   • Your prompts and code"
                echo -e "${RED}║${NC}   • API keys or tokens"
                echo -e "${RED}║${NC}   • Model responses"
                echo -e "${RED}╚════════════════════════════════════════════════════════════════╝${NC}"
                echo ""
                echo -n "Do you want to continue with this insecure connection? [y/N] "
                read -r response
                if [[ ! "$response" =~ ^[Yy]$ ]]; then
                    error "Aborted. Use HTTPS or add --insecure-ok to skip this warning."
                fi
                echo ""
            else
                warn "Insecure HTTP connection to $service_name ($host) - proceeding due to --insecure-ok"
            fi
        fi
    fi

    return 0
}

# Parse a remote URL argument (format: URL or host:port)
parse_remote_url() {
    local input="$1"
    local default_port="$2"

    # If it already has a protocol, return as-is
    if [[ "$input" =~ ^https?:// ]]; then
        echo "$input"
        return
    fi

    # If it's just host:port or host, add http://
    if [[ "$input" =~ : ]]; then
        echo "http://${input}"
    else
        echo "http://${input}:${default_port}"
    fi
}

# =============================================================================
# GPU Detection and Model Selection
# =============================================================================

select_model_quant() {
    # Select quantization based on GPU name from nvidia-smi
    local gpu_names
    gpu_names=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader 2>/dev/null)

    if [[ -z "$gpu_names" ]]; then
        echo ""
        return
    fi

    # RTX A6000 (48GB) - can fit Q3_K_S (34.6GB) with room for KV cache
    if echo "$gpu_names" | grep -qi "A6000"; then
        echo "Qwen3-Coder-Next-Q3_K_S.gguf"
    # Dual RTX 3090 (24GB each) - IQ3_XXS across both GPUs
    elif [[ $(echo "$gpu_names" | grep -ci "RTX 3090") -ge 2 ]]; then
        echo "Qwen3-Coder-Next-UD-IQ3_XXS.gguf"
    # Single RTX 3090 or similar 24GB card
    elif echo "$gpu_names" | grep -qi "RTX 3090"; then
        echo "Qwen3-Coder-Next-UD-IQ3_XXS.gguf"
    # Fallback: use VRAM to guess
    else
        local vram_mb
        vram_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
        local vram_gb=$((vram_mb / 1024))
        if [[ $vram_gb -ge 45 ]]; then
            echo "Qwen3-Coder-Next-Q3_K_S.gguf"
        elif [[ $vram_gb -ge 32 ]]; then
            echo "Qwen3-Coder-Next-IQ4_XS.gguf"
        elif [[ $vram_gb -ge 24 ]]; then
            echo "Qwen3-Coder-Next-UD-IQ3_XXS.gguf"
        else
            echo "Qwen3-Coder-Next-UD-IQ2_XXS.gguf"
        fi
    fi
}

# =============================================================================
# Health Check Functions
# =============================================================================

check_container_healthy() {
    local container="$1"
    local status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "notfound")
    [[ "$status" == "healthy" ]]
}

check_container_running() {
    local container="$1"
    docker ps --format '{{.Names}}' | grep -q "^${container}$"
}

check_http_health() {
    local url="$1"
    curl -sf "$url" &>/dev/null
}

wait_for_health() {
    local url="$1"
    local name="$2"
    local max_wait="${3:-300}"  # Default 5 minutes for large models
    local check_json="${4:-true}"  # Whether to check for JSON status

    log "Waiting for $name to be ready..."
    local elapsed=0
    while [[ $elapsed -lt $max_wait ]]; do
        local response
        response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
        local http_code=$(echo "$response" | tail -1)
        local body=$(echo "$response" | head -n -1)

        if [[ "$http_code" == "200" ]]; then
            # For JSON APIs, check for status field; for web UIs, just check HTTP 200
            if [[ "$check_json" == "false" ]] || echo "$body" | grep -q '"status".*"ok"'; then
                log "$name is ready (took ${elapsed}s)"
                return 0
            fi
        fi

        # Show progress every 30 seconds
        if [[ $((elapsed % 30)) -eq 0 ]] && [[ $elapsed -gt 0 ]]; then
            log "Still waiting for $name... (${elapsed}s elapsed, HTTP $http_code)"
        fi

        sleep 2
        elapsed=$((elapsed + 2))
    done
    warn "$name did not become ready within ${max_wait}s"
    return 1
}

# =============================================================================
# Model Download Functions
# =============================================================================

ensure_models() {
    header "Checking Models"

    mkdir -p "$MODEL_DIR/$CODE_MODEL_DIR"

    # Select model based on GPU type
    CODE_MODEL_FILE=$(select_model_quant)
    if [[ -z "$CODE_MODEL_FILE" ]]; then
        warn "Could not detect GPU, using default IQ3_XXS quantization"
        CODE_MODEL_FILE="Qwen3-Coder-Next-UD-IQ3_XXS.gguf"
    fi
    local gpu_names
    gpu_names=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader 2>/dev/null | head -1)
    log "GPU: ${gpu_names:-unknown} -> $CODE_MODEL_FILE"

    # Export for other functions
    export CODE_MODEL_FILE

    # Check and download Code LLM
    if [[ ! -f "$MODEL_DIR/$CODE_MODEL_DIR/$CODE_MODEL_FILE" ]]; then
        log "Downloading Code LLM: $CODE_MODEL_FILE..."
        hf download "$CODE_MODEL_REPO" "$CODE_MODEL_FILE" \
            --local-dir "$MODEL_DIR/$CODE_MODEL_DIR"
    else
        log "Code LLM already present: $CODE_MODEL_FILE"
    fi
}

# =============================================================================
# Docker Build Functions
# =============================================================================

ensure_llama_image() {
    header "Checking LLaMA Server Image"

    if docker image inspect "$LLAMA_IMAGE" &>/dev/null; then
        log "LLaMA server image already exists: $LLAMA_IMAGE"
        return 0
    fi

    log "Building LLaMA server image (this may take a while)..."
    log "Cloning and compiling llama.cpp with CUDA support..."

    docker build -t "$LLAMA_IMAGE" -f "$TOOL_DIR/Dockerfile.llama-server" "$TOOL_DIR"

    log "LLaMA server image built successfully"
}

ensure_browser_images() {
    header "Checking Browser Automation Images"

    cd "$BROWSER_DIR"

    # Check if browser image needs building
    if ! docker image inspect "mcp-browser-co-gnome-browser" &>/dev/null && \
       ! docker image inspect "automation-browser" &>/dev/null; then
        log "Building browser automation images..."
        docker compose build browser
    else
        log "Browser image already exists"
    fi

    cd "$PROJECT_DIR"
}

ensure_ml_images() {
    header "Checking ML Service Images"

    cd "$BROWSER_DIR"

    # Check if ML images need building
    local need_build=false
    if ! docker image inspect "mcp-browser-co-gnome-omniparser" &>/dev/null; then
        need_build=true
    fi
    if ! docker image inspect "mcp-browser-co-gnome-gui-actor" &>/dev/null; then
        need_build=true
    fi

    if $need_build; then
        log "Building ML service images (this may take a while)..."
        docker compose --profile ml build
    else
        log "ML service images already exist"
    fi

    cd "$PROJECT_DIR"
}

# =============================================================================
# Service Start Functions
# =============================================================================

start_llm_servers() {
    header "Starting Code LLM"

    # Check if Code LLM is already healthy
    if check_container_running "$CODE_CONTAINER" && check_http_health "http://localhost:$CODE_PORT/health"; then
        log "Code LLM already running and healthy"
    else
        # Stop if running but unhealthy
        docker rm -f "$CODE_CONTAINER" 2>/dev/null || true

        log "Starting Code LLM on port $CODE_PORT with $CODE_MODEL_FILE..."
        docker run -d --name "$CODE_CONTAINER" \
            --gpus all \
            --restart unless-stopped \
            -v "$MODEL_DIR:/models:ro" \
            -p "${CODE_PORT}:8080" \
            --health-cmd="curl -sf http://localhost:8080/health || exit 1" \
            --health-interval=30s \
            --health-timeout=10s \
            --health-retries=3 \
            "$LLAMA_IMAGE" \
            --model "/models/$CODE_MODEL_DIR/$CODE_MODEL_FILE" \
            --alias "unsloth/Qwen3-Coder-Next" \
            --n-gpu-layers 999 \
            --ctx-size 120000 \
            --context-shift \
            --port 8080 \
            --jinja \
            --cache-type-k q8_0 --cache-type-v q8_0 \
            --flash-attn on \
            --temp 1.0 --top-p 0.95 --min-p 0.01 --top-k 40 \
            --batch-size 4096 --ubatch-size 1024
    fi

    # Wait for server to be ready (required before launching Qwen Code)
    wait_for_health "http://localhost:$CODE_PORT/health" "Code LLM" 300 || error "Code LLM failed to start within 5 minutes. Check logs: docker logs $CODE_CONTAINER"
}

start_browser_services() {
    local include_vlm="$1"

    header "Starting Browser Automation"

    cd "$BROWSER_DIR"

    # Check if browser is already healthy
    if check_container_running "$BROWSER_CONTAINER" && check_http_health "http://localhost:$NOVNC_PORT"; then
        log "Browser automation already running and healthy"
    else
        log "Starting browser and video containers..."
        docker compose up -d browser video
        # noVNC returns HTML, not JSON - use check_json=false
        wait_for_health "http://localhost:$NOVNC_PORT" "Browser (noVNC)" 60 false || true
    fi

    # Start VLM service if requested
    if $include_vlm; then
        if check_container_running "$VLM_CONTAINER" && check_http_health "http://localhost:$VLM_PORT/health"; then
            log "VLM already running and healthy"
        else
            log "Starting VLM (Qwen3-VL-4B) on port $VLM_PORT..."
            docker compose --profile vlm up -d vlm
            log "VLM starting in background (model download/loading may take a few minutes)"
        fi
    fi

    cd "$PROJECT_DIR"
}

start_ml_services() {
    header "ML Services Ready"

    # NOTE: ML services (OmniParser, GUI-Actor) are NOT started automatically.
    # They share GPU 1 and cannot run concurrently without OOM errors.
    # The MLServiceManager in the MCP server handles on-demand startup with
    # mutual exclusion - only one service runs at a time.
    #
    # Services start automatically when you call their MCP tools:
    #   - omniparser_analyze -> starts OmniParser
    #   - natural_language_click -> starts GUI-Actor
    #
    # Images were already built by ensure_ml_images().

    log "ML services will start on-demand when tools are called"
    log "  - omniparser_analyze/omniparser_click -> starts OmniParser"
    log "  - natural_language_click -> starts GUI-Actor"
    log "Note: Only ONE ML service can run at a time (shared GPU memory)"
}

# =============================================================================
# MCP Server Setup
# =============================================================================

ensure_mcp_server() {
    header "Setting Up MCP Browser Server"

    cd "$BROWSER_DIR"

    # Check if novnc-mcp is installed
    if ! command -v novnc-mcp &>/dev/null; then
        log "Installing novnc-automation package..."
        pip install -e . --quiet
    else
        log "novnc-mcp already installed"
    fi

    cd "$PROJECT_DIR"
}

configure_qwen_mcp() {
    header "Configuring Qwen Code MCP"

    local mcp_command="novnc-mcp"
    local mcp_name="browser-automation"

    # Check if qwen CLI is available
    if ! command -v qwen &>/dev/null; then
        warn "Qwen Code CLI not found. Skipping MCP configuration."
        warn "Install Qwen Code and run: qwen mcp add --transport stdio $mcp_name $mcp_command"
        return 0
    fi

    # Disable telemetry in Qwen Code global settings
    local qwen_settings_dir="$HOME/.qwen"
    local qwen_settings_file="$qwen_settings_dir/settings.json"
    if [[ ! -f "$qwen_settings_file" ]]; then
        mkdir -p "$qwen_settings_dir"
        cat > "$qwen_settings_file" <<'SETTINGS'
{
  "telemetry": {
    "enabled": false
  }
}
SETTINGS
        log "Disabled Qwen Code telemetry in $qwen_settings_file"
    elif ! grep -q '"telemetry"' "$qwen_settings_file" 2>/dev/null; then
        # Settings file exists but no telemetry config - add it
        # Use a temp file to merge (simple approach: if it's a valid JSON object, prepend the key)
        local tmp_settings
        tmp_settings=$(python3 -c "
import json, sys
with open('$qwen_settings_file') as f:
    d = json.load(f)
d.setdefault('telemetry', {})['enabled'] = False
json.dump(d, sys.stdout, indent=2)
" 2>/dev/null)
        if [[ -n "$tmp_settings" ]]; then
            echo "$tmp_settings" > "$qwen_settings_file"
            log "Disabled Qwen Code telemetry in $qwen_settings_file"
        fi
    fi

    # Check if MCP server is already configured
    if qwen mcp list 2>/dev/null | grep -q "$mcp_name"; then
        log "MCP server '$mcp_name' already configured"
    else
        log "Adding MCP server to Qwen Code..."
        qwen mcp add --transport stdio "$mcp_name" "$mcp_command" || {
            warn "Failed to add MCP server automatically."
            warn "Add manually: qwen mcp add --transport stdio $mcp_name $mcp_command"
        }
    fi
}

# configure_claude_mcp() {
#     header "Configuring Claude MCP"
#
#     local mcp_command="novnc-mcp"
#     local mcp_name="browser-automation"
#
#     # Check if claude CLI is available
#     if ! command -v claude &>/dev/null; then
#         warn "Claude CLI not found. Skipping MCP configuration."
#         warn "Install Claude Code and run: claude mcp add-json $mcp_name '{\"type\":\"stdio\",\"command\":\"$mcp_command\"}'"
#         return 0
#     fi
#
#     # Check if MCP server is already configured
#     if claude mcp list 2>/dev/null | grep -q "$mcp_name"; then
#         log "MCP server '$mcp_name' already configured"
#     else
#         log "Adding MCP server to Claude..."
#         claude mcp add-json "$mcp_name" "{\"type\":\"stdio\",\"command\":\"$mcp_command\"}" || {
#             warn "Failed to add MCP server automatically."
#             warn "Add manually: claude mcp add-json $mcp_name '{\"type\":\"stdio\",\"command\":\"$mcp_command\"}'"
#         }
#     fi
# }

# =============================================================================
# Stop Functions
# =============================================================================

stop_all() {
    header "Stopping All Services"

    # Stop context proxy
    pkill -f "context-proxy.py" 2>/dev/null || true

    # Stop any running tunnels
    stop_tunnels

    # Stop Code LLM container
    log "Stopping Code LLM..."
    docker rm -f "$CODE_CONTAINER" 2>/dev/null && log "Stopped $CODE_CONTAINER" || true

    # Stop browser automation and all profiles
    log "Stopping browser automation and services..."
    cd "$BROWSER_DIR"
    docker compose --profile ml --profile vlm --profile tunnel down 2>/dev/null || true
    cd "$PROJECT_DIR"

    log "All services stopped"
}

# =============================================================================
# Cloudflare Tunnel Functions (--tmp-serve-api)
# =============================================================================

# Tunnel container name prefix
TUNNEL_PREFIX="api-tunnel"

stop_tunnels() {
    # Stop all tunnel containers
    local tunnels
    tunnels=$(docker ps -aq --filter "name=${TUNNEL_PREFIX}-" 2>/dev/null)
    if [[ -n "$tunnels" ]]; then
        log "Stopping API tunnels..."
        docker rm -f $tunnels 2>/dev/null || true
    fi
    # Also stop the gateway proxy and chisel containers
    docker rm -f automation-gateway 2>/dev/null || true
    docker rm -f automation-chisel-server 2>/dev/null || true
    docker rm -f automation-chisel-client 2>/dev/null || true
}

start_tunnel() {
    local name="$1"
    local port="$2"
    local container_name="${TUNNEL_PREFIX}-${name}"

    # Remove existing tunnel if any
    docker rm -f "$container_name" 2>/dev/null || true

    # Start cloudflared tunnel in background (suppress docker run output)
    docker run -d --name "$container_name" \
        --network host \
        cloudflare/cloudflared:latest \
        tunnel --no-autoupdate --url "http://localhost:${port}" \
        >/dev/null 2>&1

    # Wait for tunnel URL to appear in logs (max 30s)
    local url=""
    local elapsed=0
    while [[ -z "$url" ]] && [[ $elapsed -lt 30 ]]; do
        sleep 2
        elapsed=$((elapsed + 2))
        url=$(docker logs "$container_name" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1)
    done

    if [[ -n "$url" ]]; then
        echo "$url"
    else
        echo "pending..."
    fi
}

get_lan_ip() {
    # Get primary LAN IP address
    ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || \
    hostname -I 2>/dev/null | awk '{print $1}' || \
    echo "localhost"
}

serve_apis_lan() {
    header "LAN API Access"

    local lan_ip
    lan_ip=$(get_lan_ip)

    echo ""
    echo "=========================================="
    echo "  LAN API Endpoints (http://$lan_ip)"
    echo "=========================================="
    echo ""

    # Check and display each service
    if check_http_health "http://localhost:$CODE_PORT/health"; then
        printf "  %-12s -> http://%s:%s/v1\n" "code-llm" "$lan_ip" "$CODE_PORT"
        echo "                  OpenAI-compatible API"
    fi

    if check_http_health "http://localhost:$NOVNC_PORT"; then
        printf "  %-12s -> http://%s:%s\n" "novnc" "$lan_ip" "$NOVNC_PORT"
        echo "                  Browser view (password: secret)"
    fi

    if check_container_running "$VLM_CONTAINER" && check_http_health "http://localhost:$VLM_PORT/health"; then
        printf "  %-12s -> http://%s:%s\n" "vlm" "$lan_ip" "$VLM_PORT"
    fi

    if check_container_running "$OMNIPARSER_CONTAINER" && check_http_health "http://localhost:$OMNIPARSER_PORT/health"; then
        printf "  %-12s -> http://%s:%s\n" "omniparser" "$lan_ip" "$OMNIPARSER_PORT"
    fi

    if check_container_running "$GUI_ACTOR_CONTAINER" && check_http_health "http://localhost:$GUI_ACTOR_PORT/health"; then
        printf "  %-12s -> http://%s:%s\n" "gui-actor" "$lan_ip" "$GUI_ACTOR_PORT"
    fi

    if check_container_running "$BROWSER_CONTAINER"; then
        printf "  %-12s -> http://%s:%s\n" "cdp" "$lan_ip" "$CDP_PORT"
        echo "                  Playwright/CDP endpoint"
    fi

    echo ""
    echo "  NOTE: Ensure firewall allows access to these ports."
    echo "  For Ubuntu: sudo ufw allow 8003,6080,8004,8010,8001,9222/tcp"
    echo "=========================================="
    echo ""

    # Save LAN URLs to file
    local url_file="$TOOL_DIR/.lan-urls"
    > "$url_file"
    echo "LAN_IP=$lan_ip" >> "$url_file"
    echo "CODE_LLM=http://${lan_ip}:${CODE_PORT}/v1" >> "$url_file"
    echo "NOVNC=http://${lan_ip}:${NOVNC_PORT}" >> "$url_file"
    echo "VLM=http://${lan_ip}:${VLM_PORT}" >> "$url_file"
    echo "OMNIPARSER=http://${lan_ip}:${OMNIPARSER_PORT}" >> "$url_file"
    echo "GUI_ACTOR=http://${lan_ip}:${GUI_ACTOR_PORT}" >> "$url_file"
    echo "CDP=http://${lan_ip}:${CDP_PORT}" >> "$url_file"
    log "LAN URLs saved to $url_file"
}

GATEWAY_PORT=8888

serve_single_tunnel() {
    header "Starting Single Gateway Tunnel"

    # Generate shared secret
    local secret
    secret=$(openssl rand -hex 16)
    local secret_hash
    secret_hash=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$secret")

    # Stop any existing tunnels/gateway
    stop_tunnels

    # Start Caddy gateway with host networking
    docker run -d --name automation-gateway \
        --network host \
        -e "TUNNEL_SECRET=$secret" \
        -e "TUNNEL_SECRET_HASH=$secret_hash" \
        -v "$BROWSER_DIR/Caddyfile.gateway:/etc/caddy/Caddyfile:ro" \
        caddy:2-alpine

    # Wait for Caddy to be ready
    local elapsed=0
    while ! check_http_health "http://localhost:$GATEWAY_PORT"; do
        sleep 1; elapsed=$((elapsed + 1))
        [[ $elapsed -ge 10 ]] && { warn "Gateway not ready after 10s"; break; }
    done

    # Start chisel server for E2E encrypted tunneling
    docker rm -f automation-chisel-server 2>/dev/null || true
    docker run -d --name automation-chisel-server \
        --network host \
        jpillora/chisel server \
        --port "$CHISEL_PORT" \
        --auth "tunnel:$secret" \
        >/dev/null 2>&1
    log "Chisel server started on port $CHISEL_PORT (E2E encryption available)"

    # Start single cloudflared tunnel
    docker run -d --name "${TUNNEL_PREFIX}-gateway" \
        --network host \
        cloudflare/cloudflared:latest \
        tunnel --no-autoupdate --url "http://localhost:$GATEWAY_PORT"

    # Extract tunnel URL
    local url="" elapsed=0
    while [[ -z "$url" ]] && [[ $elapsed -lt 30 ]]; do
        sleep 2; elapsed=$((elapsed + 2))
        url=$(docker logs "${TUNNEL_PREFIX}-gateway" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1)
    done

    # Save to .tunnel-urls
    local tunnel_file="$TOOL_DIR/.tunnel-urls"
    cat > "$tunnel_file" <<EOF
tunnel=${url:-pending}
secret=$secret
EOF

    # Display
    echo ""
    echo "=========================================="
    echo "  Single Gateway Tunnel (E2E Encrypted)"
    echo "=========================================="
    echo ""
    echo "  URL:    ${url:-pending...}"
    echo "  Secret: $secret"
    echo ""
    echo "  Services available at:"
    echo "    noVNC:      ${url}/ (basic auth user: tunnel)"
    echo "    Code LLM:   ${url}/code-llm/"
    echo "    VLM:         ${url}/vlm/"
    echo "    OmniParser:  ${url}/omniparser/"
    echo "    GUI-Actor:   ${url}/gui-actor/"
    echo "    CDP:         ${url}/cdp/"
    echo ""
    echo "  E2E Encryption:"
    echo "    Chisel (SSH-over-HTTP) tunnel on ${url}/chisel"
    echo "    Clients using --remote-tunnel + --tunnel-key auto-encrypt"
    echo "    all traffic. Cloudflare cannot inspect service data."
    echo ""
    echo "  Client usage:"
    echo "    ./local-cc.sh --remote-tunnel ${url} --tunnel-key $secret"
    echo "=========================================="
}

serve_apis() {
    local scope="$1"  # "public", "lan", or specific services

    # Handle single gateway mode
    if [[ "$scope" == "single" ]]; then
        serve_single_tunnel
        return 0
    fi

    # Handle LAN mode - just display IPs, no tunnels
    if [[ "$scope" == "lan" ]]; then
        serve_apis_lan
        return 0
    fi

    header "Starting Cloudflare Tunnels"

    # Check if cloudflared image exists, pull if not
    if ! docker image inspect cloudflare/cloudflared:latest &>/dev/null; then
        log "Pulling cloudflared image..."
        docker pull cloudflare/cloudflared:latest
    fi

    # Stop existing tunnels
    stop_tunnels

    declare -A tunnel_urls

    # Always tunnel Code LLM if running
    if check_http_health "http://localhost:$CODE_PORT/health"; then
        log "Creating tunnel for Code LLM (port $CODE_PORT)..."
        tunnel_urls["code-llm"]=$(start_tunnel "code-llm" "$CODE_PORT")
    fi

    # Tunnel noVNC if running
    if check_http_health "http://localhost:$NOVNC_PORT"; then
        log "Creating tunnel for noVNC (port $NOVNC_PORT)..."
        tunnel_urls["novnc"]=$(start_tunnel "novnc" "$NOVNC_PORT")
    fi

    # Tunnel VLM if running
    if check_container_running "$VLM_CONTAINER" && check_http_health "http://localhost:$VLM_PORT/health"; then
        log "Creating tunnel for VLM (port $VLM_PORT)..."
        tunnel_urls["vlm"]=$(start_tunnel "vlm" "$VLM_PORT")
    fi

    # Tunnel OmniParser if running
    if check_container_running "$OMNIPARSER_CONTAINER" && check_http_health "http://localhost:$OMNIPARSER_PORT/health"; then
        log "Creating tunnel for OmniParser (port $OMNIPARSER_PORT)..."
        tunnel_urls["omniparser"]=$(start_tunnel "omniparser" "$OMNIPARSER_PORT")
    fi

    # Tunnel GUI-Actor if running
    if check_container_running "$GUI_ACTOR_CONTAINER" && check_http_health "http://localhost:$GUI_ACTOR_PORT/health"; then
        log "Creating tunnel for GUI-Actor (port $GUI_ACTOR_PORT)..."
        tunnel_urls["gui-actor"]=$(start_tunnel "gui-actor" "$GUI_ACTOR_PORT")
    fi

    # Tunnel CDP (Playwright) if browser is running
    if check_container_running "$BROWSER_CONTAINER"; then
        log "Creating tunnel for CDP/Playwright (port $CDP_PORT)..."
        tunnel_urls["cdp"]=$(start_tunnel "cdp" "$CDP_PORT")
    fi

    # Display tunnel URLs
    echo ""
    echo "=========================================="
    echo "  Public API Tunnels (trycloudflare.com)"
    echo "=========================================="
    echo ""

    for service in "${!tunnel_urls[@]}"; do
        local url="${tunnel_urls[$service]}"
        local port=""
        case "$service" in
            code-llm) port="$CODE_PORT" ;;
            novnc) port="$NOVNC_PORT" ;;
            vlm) port="$VLM_PORT" ;;
            omniparser) port="$OMNIPARSER_PORT" ;;
            gui-actor) port="$GUI_ACTOR_PORT" ;;
            cdp) port="$CDP_PORT" ;;
        esac
        printf "  %-12s (:%s) -> %s\n" "$service" "$port" "$url"
    done

    echo ""
    echo "  NOTE: These are temporary tunnels that expire when stopped."
    echo "  Use './local-cc.sh --stop-tunnels' to close them."
    echo "=========================================="
    echo ""

    # Save tunnel URLs to a file for reference
    local tunnel_file="$TOOL_DIR/.tunnel-urls"
    > "$tunnel_file"
    for service in "${!tunnel_urls[@]}"; do
        echo "${service}=${tunnel_urls[$service]}" >> "$tunnel_file"
    done
    log "Tunnel URLs saved to $tunnel_file"
}

show_tunnels() {
    header "Active API Tunnels"

    local tunnel_file="$TOOL_DIR/.tunnel-urls"
    local found_tunnels=false

    # Check for single gateway mode
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^automation-gateway$"; then
        # Gateway is running - show tunnel-urls file info
        if [[ -f "$tunnel_file" ]]; then
            echo ""
            echo "  Single Gateway Mode:"
            local gw_url gw_secret
            gw_url=$(grep '^tunnel=' "$tunnel_file" 2>/dev/null | cut -d= -f2-)
            gw_secret=$(grep '^secret=' "$tunnel_file" 2>/dev/null | cut -d= -f2-)
            if [[ -n "$gw_url" ]]; then
                echo "    URL:    $gw_url"
                echo "    Secret: $gw_secret"
                echo ""
                # Show chisel status
                if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^automation-chisel-server$"; then
                    echo "    E2E Encryption: ACTIVE (chisel on port $CHISEL_PORT)"
                else
                    echo "    E2E Encryption: NOT RUNNING"
                fi
                echo ""
                echo "    Services:"
                echo "      noVNC:      ${gw_url}/ (basic auth user: tunnel)"
                echo "      Code LLM:   ${gw_url}/code-llm/"
                echo "      VLM:        ${gw_url}/vlm/"
                echo "      OmniParser: ${gw_url}/omniparser/"
                echo "      GUI-Actor:  ${gw_url}/gui-actor/"
                echo "      CDP:        ${gw_url}/cdp/"
                echo ""
                echo "    Client usage:"
                echo "      ./local-cc.sh --remote-tunnel ${gw_url} --tunnel-key $gw_secret"
                found_tunnels=true
            fi
        fi
        echo ""
    fi

    # Check for individual tunnel containers
    local tunnels
    tunnels=$(docker ps --filter "name=${TUNNEL_PREFIX}-" --format '{{.Names}}' 2>/dev/null | grep -v "gateway$")

    if [[ -n "$tunnels" ]]; then
        echo ""
        echo "  Individual Tunnels:"
        for container in $tunnels; do
            local service="${container#${TUNNEL_PREFIX}-}"
            local url
            url=$(docker logs "$container" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
            if [[ -n "$url" ]]; then
                printf "    %-12s -> %s\n" "$service" "$url"
                found_tunnels=true
            fi
        done
        echo ""
    fi

    if ! $found_tunnels; then
        echo "  No active tunnels. Start with:"
        echo "    ./local-cc.sh --tmp-serve-api single   (recommended)"
        echo "    ./local-cc.sh --tmp-serve-api public   (individual tunnels)"
    fi
}

# =============================================================================
# Status Function
# =============================================================================

show_status() {
    header "Service Status"

    echo "Code LLM:"
    if check_container_running "$CODE_CONTAINER"; then
        local health=$(check_http_health "http://localhost:$CODE_PORT/health" && echo "healthy" || echo "unhealthy")
        echo -e "  ${GREEN}[RUNNING]${NC} Qwen3-Coder-Next - http://localhost:$CODE_PORT - $health"
    else
        echo -e "  ${RED}[STOPPED]${NC} Qwen3-Coder-Next"
    fi

    echo ""
    echo "Browser Automation:"
    if check_container_running "$BROWSER_CONTAINER"; then
        local health=$(check_http_health "http://localhost:$NOVNC_PORT" && echo "healthy" || echo "unhealthy")
        echo -e "  ${GREEN}[RUNNING]${NC} Browser (noVNC) - http://localhost:$NOVNC_PORT - $health"
    else
        echo -e "  ${RED}[STOPPED]${NC} Browser (noVNC)"
    fi

    if check_container_running "$VIDEO_CONTAINER"; then
        echo -e "  ${GREEN}[RUNNING]${NC} Video Recording"
    else
        echo -e "  ${RED}[STOPPED]${NC} Video Recording"
    fi

    echo ""
    echo "Vision/ML Services (optional):"
    if check_container_running "$VLM_CONTAINER"; then
        local health=$(check_http_health "http://localhost:$VLM_PORT/health" && echo "healthy" || echo "loading...")
        echo -e "  ${GREEN}[RUNNING]${NC} VLM (Qwen3-VL-4B) - http://localhost:$VLM_PORT - $health"
    else
        echo -e "  ${RED}[STOPPED]${NC} VLM (Qwen3-VL-4B) - start with --vlm flag"
    fi

    if check_container_running "$OMNIPARSER_CONTAINER"; then
        local health=$(check_http_health "http://localhost:$OMNIPARSER_PORT/health" && echo "healthy" || echo "loading...")
        echo -e "  ${GREEN}[RUNNING]${NC} OmniParser - http://localhost:$OMNIPARSER_PORT - $health"
    else
        echo -e "  ${RED}[STOPPED]${NC} OmniParser - start with --ml flag"
    fi

    if check_container_running "$GUI_ACTOR_CONTAINER"; then
        local health=$(check_http_health "http://localhost:$GUI_ACTOR_PORT/health" && echo "healthy" || echo "loading...")
        echo -e "  ${GREEN}[RUNNING]${NC} GUI-Actor - http://localhost:$GUI_ACTOR_PORT - $health"
    else
        echo -e "  ${RED}[STOPPED]${NC} GUI-Actor - start with --ml flag"
    fi

    echo ""
    echo "GPU Usage:"
    nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null | sed 's/^/  GPU /' || echo "  No NVIDIA GPU detected"
}

# =============================================================================
# Install Function
# =============================================================================

install_command() {
    header "Installing local-cc Command"

    local install_path="/usr/local/bin/local-cc"
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -e "$install_path" ]]; then
        log "Updating existing installation..."
    else
        log "Installing local-cc to $install_path..."
    fi

    # Remove any existing file/symlink first (critical - symlinks follow through on write!)
    sudo rm -f "$install_path"

    # Create wrapper script that preserves current working directory
    sudo tee "$install_path" > /dev/null << EOF
#!/bin/bash
# Wrapper for local-cc - preserves current working directory
export LOCAL_CC_PROJECT_DIR="\$PWD"
exec "$script_dir/local-cc.sh" "\$@"
EOF
    sudo chmod +x "$install_path"

    if [[ -x "$install_path" ]]; then
        log "Successfully installed! You can now run 'local-cc' from any project directory."
        echo ""
        echo "Usage:"
        echo "  local-cc           # Start services and launch Qwen Code"
        echo "  local-cc --vlm     # Include VLM for image analysis"
        echo "  local-cc --ml      # Include ML services (OmniParser, GUI-Actor)"
        echo "  local-cc --stop    # Stop all services"
        echo "  local-cc --status  # Show service status"
    else
        error "Installation failed"
    fi
}

# =============================================================================
# Qwen Code CLI Installation
# =============================================================================

ensure_qwen_cli() {
    if command -v qwen &>/dev/null; then
        log "Qwen Code CLI already installed"
        return 0
    fi

    header "Installing Qwen Code CLI"
    log "Qwen Code CLI not found. Installing..."

    # Install using npm (most reliable method) - use sudo for global install
    if command -v npm &>/dev/null; then
        sudo npm install -g @qwen-code/qwen-code@latest 2>/dev/null || npm install -g @qwen-code/qwen-code@latest
        if command -v qwen &>/dev/null; then
            log "Qwen Code CLI installed successfully via npm"
            return 0
        fi
    fi

    # Fallback: try homebrew on macOS/Linux
    if command -v brew &>/dev/null; then
        log "Trying homebrew..."
        brew install qwen-code
        if command -v qwen &>/dev/null; then
            log "Qwen Code CLI installed successfully via homebrew"
            return 0
        fi
    fi

    if command -v qwen &>/dev/null; then
        log "Qwen Code CLI installed successfully"
    else
        warn "Could not install Qwen Code CLI automatically."
        warn "Please install manually: sudo npm install -g @qwen-code/qwen-code@latest"
        warn "Or visit: https://github.com/QwenLM/qwen-code"
    fi
}

# =============================================================================
# Claude CLI Installation (DEPRECATED - kept for reference)
# =============================================================================

# ensure_claude_cli() {
#     if command -v claude &>/dev/null; then
#         log "Claude CLI already installed"
#         return 0
#     fi
#
#     header "Installing Claude CLI"
#     log "Claude CLI not found. Installing..."
#
#     # Install using npm (most reliable method)
#     if command -v npm &>/dev/null; then
#         npm install -g @anthropic-ai/claude-code
#         if command -v claude &>/dev/null; then
#             log "Claude CLI installed successfully via npm"
#             return 0
#         fi
#     fi
#
#     # Fallback: direct install script
#     log "Trying direct install script..."
#     curl -fsSL https://claude.ai/install.sh | sh
#
#     if command -v claude &>/dev/null; then
#         log "Claude CLI installed successfully"
#     else
#         warn "Could not install Claude CLI automatically."
#         warn "Please install manually: npm install -g @anthropic-ai/claude-code"
#         warn "Or visit: https://claude.ai/code"
#     fi
# }

# =============================================================================
# Launch Qwen Code
# =============================================================================

launch_qwen() {
    local include_vlm="$1"
    shift  # Remove include_vlm from args

    header "Launching Qwen Code"

    # Determine upstream for Code LLM
    local upstream
    if [[ -n "$REMOTE_CODE_URL" ]]; then
        upstream="$REMOTE_CODE_URL"
        log "Using remote Code LLM: $REMOTE_CODE_URL"
    else
        upstream="http://localhost:$CODE_PORT"
    fi

    # Start context proxy (truncates oversized tool results to fit context window)
    pkill -f "context-proxy.py" 2>/dev/null || true
    sleep 0.2
    log "Starting context proxy on port $PROXY_PORT -> $upstream"
    python3 "$TOOL_DIR/context-proxy.py" "$PROXY_PORT" "$upstream" &
    local proxy_pid=$!
    trap "kill $proxy_pid 2>/dev/null; wait $proxy_pid 2>/dev/null" EXIT INT TERM

    # Wait for proxy to be ready (forwards /health to upstream)
    local proxy_ready=false
    for i in $(seq 1 10); do
        if curl -sf "http://localhost:$PROXY_PORT/health" &>/dev/null; then
            proxy_ready=true
            break
        fi
        sleep 0.5
    done
    if ! $proxy_ready; then
        warn "Context proxy may not be ready, continuing anyway"
    fi

    # Point Qwen Code at the proxy
    export OPENAI_BASE_URL="http://localhost:$PROXY_PORT/v1"

    # When using tunnel key, set it as OPENAI_API_KEY so the SDK sends
    # Authorization: Bearer <key> which Caddy accepts as tunnel auth
    if [[ -n "$TUNNEL_KEY" ]]; then
        export OPENAI_API_KEY="$TUNNEL_KEY"
    else
        export OPENAI_API_KEY='sk-no-key-required'
    fi
    export OPENAI_MODEL="unsloth/Qwen3-Coder-Next"

    # Export remote ML service URLs for MCP server (novnc-mcp inherits env)
    [[ -n "$REMOTE_VLM_URL" ]] && export VLM_URL="$REMOTE_VLM_URL"
    [[ -n "$REMOTE_OMNIPARSER_URL" ]] && export OMNIPARSER_URL="$REMOTE_OMNIPARSER_URL"
    [[ -n "$REMOTE_GUI_ACTOR_URL" ]] && export GUI_ACTOR_URL="$REMOTE_GUI_ACTOR_URL"
    [[ -n "$REMOTE_CDP_URL" ]] && export CDP_ENDPOINT="$REMOTE_CDP_URL"

    echo ""
    echo "=========================================="
    echo "  Qwen Code Environment Ready"
    echo "=========================================="
    echo ""
    if [[ -n "$REMOTE_CODE_URL" ]]; then
        echo "  Code LLM: $REMOTE_CODE_URL (remote)"
    else
        echo "  Code LLM: http://localhost:$CODE_PORT"
    fi
    if $include_vlm; then
        if [[ -n "$REMOTE_VLM_URL" ]]; then
            echo "  VLM:      $REMOTE_VLM_URL (remote)"
        else
            echo "  VLM:      http://localhost:$VLM_PORT"
        fi
    fi
    if [[ -n "$REMOTE_NOVNC_URL" ]]; then
        echo "  noVNC:    $REMOTE_NOVNC_URL (remote)"
    else
        echo "  noVNC:    http://localhost:$NOVNC_PORT (password: secret)"
    fi
    if [[ -n "$REMOTE_CDP_URL" ]]; then
        echo "  CDP:      $REMOTE_CDP_URL (remote)"
    fi
    if [[ -n "$REMOTE_OMNIPARSER_URL" ]]; then
        echo "  OmniParser: $REMOTE_OMNIPARSER_URL (remote)"
    fi
    if [[ -n "$REMOTE_GUI_ACTOR_URL" ]]; then
        echo "  GUI-Actor: $REMOTE_GUI_ACTOR_URL (remote)"
    fi
    echo ""
    echo "  MCP Browser tools available in Qwen Code"
    echo ""
    echo "=========================================="
    echo ""

    # Launch Qwen Code (no exec - trap needs to clean up proxy)
    qwen "$@"
}

# =============================================================================
# Launch Claude Code (DEPRECATED - kept for reference)
# =============================================================================

# launch_claude() {
#     local include_vlm="$1"
#     shift  # Remove include_vlm from args
#
#     header "Launching Claude Code"
#
#     export ANTHROPIC_BASE_URL="http://localhost:$CODE_PORT"
#     export ANTHROPIC_API_KEY='sk-no-key-required'
#
#     echo ""
#     echo "=========================================="
#     echo "  Local Claude Code Environment Ready"
#     echo "=========================================="
#     echo ""
#     echo "  Code LLM: http://localhost:$CODE_PORT"
#     if $include_vlm; then
#         echo "  VLM:      http://localhost:$VLM_PORT (vlm_chat tool available)"
#     fi
#     echo "  noVNC:    http://localhost:$NOVNC_PORT (password: secret)"
#     echo ""
#     echo "  MCP Browser tools available in Claude"
#     echo ""
#     echo "=========================================="
#     echo ""
#
#     # Launch Claude Code
#     exec claude --model "unsloth/Qwen3-Coder-Next" "$@"
# }

# =============================================================================
# Main
# =============================================================================

main() {
    local include_ml=false
    local include_vlm=false
    local serve_api=""
    local server_only=false
    local client_only=false
    local remaining_args=()
    local pending_arg=""  # Track which arg is expecting a value

    # Parse arguments
    for arg in "$@"; do
        # Handle pending argument values
        if [[ -n "$pending_arg" ]]; then
            case "$pending_arg" in
                serve_api)
                    serve_api="$arg"
                    ;;
                remote_code)
                    REMOTE_CODE_URL=$(parse_remote_url "$arg" "$CODE_PORT")
                    ;;
                remote_vlm)
                    REMOTE_VLM_URL=$(parse_remote_url "$arg" "$VLM_PORT")
                    ;;
                remote_novnc)
                    REMOTE_NOVNC_URL=$(parse_remote_url "$arg" "$NOVNC_PORT")
                    ;;
                remote_omniparser)
                    REMOTE_OMNIPARSER_URL=$(parse_remote_url "$arg" "$OMNIPARSER_PORT")
                    ;;
                remote_guiactor)
                    REMOTE_GUI_ACTOR_URL=$(parse_remote_url "$arg" "$GUI_ACTOR_PORT")
                    ;;
                remote_cdp)
                    REMOTE_CDP_URL=$(parse_remote_url "$arg" "$CDP_PORT")
                    ;;
                remote_tunnel)
                    REMOTE_TUNNEL_URL="$arg"
                    ;;
                tunnel_key)
                    TUNNEL_KEY="$arg"
                    ;;
            esac
            pending_arg=""
            continue
        fi

        case "$arg" in
            --stop)
                stop_all
                exit 0
                ;;
            --status)
                show_status
                exit 0
                ;;
            --install)
                install_command
                exit 0
                ;;
            --tmp-serve-api)
                pending_arg="serve_api"
                ;;
            --stop-tunnels)
                stop_tunnels
                log "All API tunnels stopped"
                exit 0
                ;;
            --show-tunnels)
                show_tunnels
                exit 0
                ;;
            --remote-code)
                pending_arg="remote_code"
                ;;
            --remote-vlm)
                pending_arg="remote_vlm"
                ;;
            --remote-novnc)
                pending_arg="remote_novnc"
                ;;
            --remote-omniparser)
                pending_arg="remote_omniparser"
                ;;
            --remote-gui-actor)
                pending_arg="remote_guiactor"
                ;;
            --remote-cdp)
                pending_arg="remote_cdp"
                ;;
            --remote-tunnel)
                pending_arg="remote_tunnel"
                ;;
            --tunnel-key)
                pending_arg="tunnel_key"
                ;;
            --insecure-ok)
                INSECURE_OK=true
                ;;
            --server-only)
                server_only=true
                ;;
            --client-only)
                client_only=true
                ;;
            --ml)
                include_ml=true
                ;;
            --vlm)
                include_vlm=true
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS] [-- QWEN_ARGS]"
                echo ""
                echo "Modes:"
                echo "  (default)                Start local services + Qwen Code"
                echo "  --server-only            Start local services only (no Qwen Code, for hosting)"
                echo "  --client-only            Connect to remote services only (no Docker/GPU needed)"
                echo ""
                echo "Local Services:"
                echo "  --vlm                    Include VLM for image analysis (vlm_chat tool)"
                echo "  --ml                     Include ML services (OmniParser, GUI-Actor)"
                echo "  --stop                   Stop all services"
                echo "  --status                 Show status of all services"
                echo "  --install                Install as 'local-cc' command"
                echo ""
                echo "Remote Servers (client mode):"
                echo "  --remote-tunnel URL      Use single gateway tunnel (from --tmp-serve-api single)"
                echo "  --tunnel-key SECRET      Shared secret for gateway authentication"
                echo "  --remote-code URL        Use remote Code LLM (e.g., https://xxx.trycloudflare.com)"
                echo "  --remote-vlm URL         Use remote VLM server"
                echo "  --remote-novnc URL       Use remote noVNC browser"
                echo "  --remote-omniparser URL  Use remote OmniParser"
                echo "  --remote-gui-actor URL   Use remote GUI-Actor"
                echo "  --remote-cdp URL         Use remote CDP endpoint (browser automation)"
                echo "  --insecure-ok            Skip HTTP security warnings"
                echo ""
                echo "Sharing (server mode):"
                echo "  --tmp-serve-api single   Single gateway tunnel for all services (recommended)"
                echo "  --tmp-serve-api public   Individual Cloudflare tunnels per service"
                echo "  --tmp-serve-api lan      Show LAN IP addresses for all running APIs"
                echo "  --stop-tunnels           Stop all tunnels and gateway"
                echo "  --show-tunnels           Show active tunnel URLs"
                echo ""
                echo "E2E Encryption:"
                echo "  When using --remote-tunnel with --tunnel-key, a chisel tunnel"
                echo "  (SSH over HTTP) is automatically established. All traffic is"
                echo "  end-to-end encrypted. Cloudflare cannot inspect any service data."
                echo ""
                echo "Examples:"
                echo "  $0                                    # Start everything locally"
                echo "  $0 --vlm --ml                         # Start with all ML services"
                echo "  $0 --server-only --tmp-serve-api single  # Host for remote clients"
                echo "  $0 --client-only --remote-tunnel URL --tunnel-key SECRET  # Client only"
                echo "  $0 --remote-tunnel URL --tunnel-key SECRET  # Connect + launch locally"
                echo "  $0 --remote-code 192.168.1.10:8003    # Use LAN server"
                echo "  $0 -- --resume                        # Pass --resume to Qwen Code"
                exit 0
                ;;
            --)
                shift
                remaining_args=("$@")
                break
                ;;
            *)
                remaining_args+=("$arg")
                ;;
        esac
    done

    # Validate mutually exclusive modes
    if $server_only && $client_only; then
        error "--server-only and --client-only cannot be used together"
    fi

    # Auto-derive all remote URLs from single gateway tunnel
    if [[ -n "$REMOTE_TUNNEL_URL" ]]; then
        local base="${REMOTE_TUNNEL_URL%/}"
        REMOTE_CODE_URL="$base/code-llm"
        REMOTE_VLM_URL="$base/vlm"
        REMOTE_NOVNC_URL="$base"
        REMOTE_OMNIPARSER_URL="$base/omniparser"
        REMOTE_GUI_ACTOR_URL="$base/gui-actor"
        REMOTE_CDP_URL="$base/cdp"
    fi

    # E2E Encryption: Start chisel tunnel when using remote gateway + key
    if [[ -n "$REMOTE_TUNNEL_URL" ]] && [[ -n "$TUNNEL_KEY" ]]; then
        header "Starting E2E Encrypted Tunnel (Chisel)"

        # Clean up any existing chisel client
        docker rm -f automation-chisel-client 2>/dev/null || true

        # Ensure chisel image is available
        if ! docker image inspect jpillora/chisel &>/dev/null; then
            log "Pulling chisel image..."
            docker pull jpillora/chisel
        fi

        local chisel_url="${REMOTE_TUNNEL_URL%/}/chisel"
        log "Connecting to chisel server at $chisel_url"

        # Start chisel client - forward all service ports through encrypted tunnel
        docker run -d --name automation-chisel-client \
            --network host \
            jpillora/chisel client \
            --auth "tunnel:$TUNNEL_KEY" \
            "$chisel_url" \
            "$CODE_PORT:localhost:$CODE_PORT" \
            "$VLM_PORT:localhost:$VLM_PORT" \
            "$OMNIPARSER_PORT:localhost:$OMNIPARSER_PORT" \
            "$GUI_ACTOR_PORT:localhost:$GUI_ACTOR_PORT" \
            "$CDP_PORT:localhost:$CDP_PORT" \
            "$NOVNC_PORT:localhost:$NOVNC_PORT" \
            >/dev/null 2>&1

        # Wait for chisel connection
        local chisel_elapsed=0
        local chisel_connected=false
        while [[ $chisel_elapsed -lt 15 ]]; do
            if docker logs automation-chisel-client 2>&1 | grep -q "Connected"; then
                chisel_connected=true
                break
            fi
            sleep 1
            chisel_elapsed=$((chisel_elapsed + 1))
        done

        if $chisel_connected; then
            log "Chisel tunnel connected (E2E encrypted)"

            # Override all remote URLs to localhost - traffic goes through chisel
            REMOTE_CODE_URL="http://localhost:$CODE_PORT"
            REMOTE_VLM_URL="http://localhost:$VLM_PORT"
            REMOTE_NOVNC_URL="http://localhost:$NOVNC_PORT"
            REMOTE_OMNIPARSER_URL="http://localhost:$OMNIPARSER_PORT"
            REMOTE_GUI_ACTOR_URL="http://localhost:$GUI_ACTOR_PORT"
            REMOTE_CDP_URL="http://localhost:$CDP_PORT"

            # Clear TUNNEL_KEY - chisel handles auth+encryption
            TUNNEL_KEY=""

            # Cleanup chisel client on exit
            trap 'docker rm -f automation-chisel-client 2>/dev/null' EXIT
        else
            warn "Chisel tunnel failed to connect within 15s"
            warn "Falling back to direct tunnel access (Cloudflare can inspect traffic)"
            docker rm -f automation-chisel-client 2>/dev/null || true
        fi
    fi

    # Export TUNNEL_KEY for MCP server and set OPENAI_API_KEY for LLM auth
    if [[ -n "$TUNNEL_KEY" ]]; then
        export TUNNEL_KEY="$TUNNEL_KEY"
    fi

    # =========================================================================
    # CLIENT-ONLY MODE: No Docker, no GPU, no local services
    # Only installs MCP server + Qwen Code CLI and connects to remote services
    # =========================================================================
    if $client_only; then
        if [[ -z "$REMOTE_CODE_URL" ]]; then
            error "--client-only requires --remote-tunnel or --remote-code to be set"
        fi

        # Validate remote URLs for security
        check_url_security "$REMOTE_CODE_URL" "Code LLM"
        check_url_security "$REMOTE_VLM_URL" "VLM"
        check_url_security "$REMOTE_NOVNC_URL" "noVNC"
        check_url_security "$REMOTE_OMNIPARSER_URL" "OmniParser"
        check_url_security "$REMOTE_GUI_ACTOR_URL" "GUI-Actor"
        check_url_security "$REMOTE_CDP_URL" "CDP"

        # Only need pip (for MCP server) and npm (for Qwen Code)
        ensure_mcp_server
        ensure_qwen_cli
        configure_qwen_mcp

        launch_qwen "$include_vlm" "${remaining_args[@]}"
        return  # exec in launch_qwen doesn't return, but just in case
    fi

    # =========================================================================
    # SERVER-ONLY MODE: Start local services + tunnels, no Qwen Code
    # =========================================================================
    # (also used as common path for default mode, with client steps added after)

    # Validate remote URLs for security
    check_url_security "$REMOTE_CODE_URL" "Code LLM"
    check_url_security "$REMOTE_VLM_URL" "VLM"
    check_url_security "$REMOTE_NOVNC_URL" "noVNC"
    check_url_security "$REMOTE_OMNIPARSER_URL" "OmniParser"
    check_url_security "$REMOTE_GUI_ACTOR_URL" "GUI-Actor"
    check_url_security "$REMOTE_CDP_URL" "CDP"

    # Check if using any remote services
    local use_remote_code=false
    local use_remote_all=false
    [[ -n "$REMOTE_CODE_URL" ]] && use_remote_code=true

    # If all main services are remote, skip local Docker requirements
    if [[ -n "$REMOTE_CODE_URL" ]] && [[ -n "$REMOTE_NOVNC_URL" ]]; then
        use_remote_all=true
    fi

    # Only require Docker if not using all remote services
    if ! $use_remote_all; then
        command -v docker &>/dev/null || error "Docker not found. Please install Docker."
        command -v hf &>/dev/null || error "hf CLI not found. Install with: pip install huggingface_hub[cli]"
    fi

    # Run setup steps only for local services
    if ! $use_remote_code; then
        ensure_models
        ensure_llama_image
    fi

    if [[ -z "$REMOTE_NOVNC_URL" ]]; then
        ensure_browser_images
    fi

    if $include_vlm && [[ -z "$REMOTE_VLM_URL" ]]; then
        header "Building VLM Image (if needed)"
        cd "$BROWSER_DIR"
        docker compose --profile vlm build vlm 2>/dev/null || log "VLM image already built or building..."
        cd "$PROJECT_DIR"
    fi

    if $include_ml && [[ -z "$REMOTE_OMNIPARSER_URL" ]] && [[ -z "$REMOTE_GUI_ACTOR_URL" ]]; then
        ensure_ml_images
    fi

    # Start local services (skip if using remote)
    if ! $use_remote_code; then
        start_llm_servers
    else
        header "Using Remote Code LLM"
        log "Remote Code LLM: $REMOTE_CODE_URL"
    fi

    if [[ -z "$REMOTE_NOVNC_URL" ]]; then
        start_browser_services "$include_vlm"
    else
        header "Using Remote Browser"
        log "Remote noVNC: $REMOTE_NOVNC_URL"
    fi

    if $include_ml; then
        start_ml_services
    fi

    # Start API tunnels if requested
    if [[ -n "$serve_api" ]] && [[ "$serve_api" != "pending" ]]; then
        serve_apis "$serve_api"
    fi

    # Server-only: services are running, print status and exit
    if $server_only; then
        header "Server Ready"
        log "All services started. Use --stop to shut down."
        if [[ -z "$serve_api" ]]; then
            log "Tip: Add --tmp-serve-api single to expose services via tunnel"
        fi
        exit 0
    fi

    # =========================================================================
    # DEFAULT MODE: Start services + Qwen Code
    # =========================================================================
    ensure_mcp_server
    ensure_qwen_cli
    configure_qwen_mcp

    # Launch Qwen Code
    launch_qwen "$include_vlm" "${remaining_args[@]}"
}

main "$@"
