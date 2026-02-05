#!/bin/bash
set -e

# =============================================================================
# local-cc.sh - Unified Local Claude Code Environment
# =============================================================================
# Spins up:
#   - Code LLM (Qwen3-Coder-Next) on port 8003 (VRAM-auto quant)
#   - Browser automation containers (noVNC, video)
#   - VLM (Qwen3-VL-4B) on port 8004 with --vlm flag
#   - ML services (OmniParser, GUI-Actor) with --ml flag
#   - MCP browser server for Claude
#
# Usage:
#   ./local-cc.sh              # Start services and launch Claude Code
#   ./local-cc.sh --vlm        # Include VLM for image analysis
#   ./local-cc.sh --ml         # Include ML services (OmniParser, GUI-Actor)
#   ./local-cc.sh --stop       # Stop all services
#   ./local-cc.sh --status     # Show status of all services
#   ./local-cc.sh --install    # Install as 'local-cc' command system-wide
# =============================================================================

# TOOL_DIR: where local-cc.sh and its resources live (models, mcp-browser-co-gnome)
# PROJECT_DIR: where to launch Claude Code (current working directory)
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
# Model file is auto-selected based on VRAM (see select_model_quant)

# Ports
CODE_PORT=8003
VLM_PORT=8004
NOVNC_PORT=6080
VNC_PORT=5900
CDP_PORT=9222
OMNIPARSER_PORT=8010
GUI_ACTOR_PORT=8001

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
# VRAM Detection and Model Selection
# =============================================================================

detect_vram() {
    # Get total VRAM in GB from first GPU
    local vram_mb
    vram_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
    if [[ -z "$vram_mb" ]]; then
        echo "0"
        return
    fi
    echo $((vram_mb / 1024))
}

select_model_quant() {
    local vram_gb="$1"
    # Select quantization based on available VRAM
    # Reserve ~10% for other processes
    if [[ $vram_gb -ge 48 ]]; then
        echo "Qwen3-Coder-Next-Q8_0.gguf"
    elif [[ $vram_gb -ge 32 ]]; then
        echo "Qwen3-Coder-Next-IQ4_XS.gguf"
    elif [[ $vram_gb -ge 24 ]]; then
        echo "Qwen3-Coder-Next-UD-IQ3_XXS.gguf"
    else
        echo "Qwen3-Coder-Next-UD-IQ2_XXS.gguf"
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

    log "Waiting for $name to be ready..."
    local elapsed=0
    while [[ $elapsed -lt $max_wait ]]; do
        local response
        response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
        local http_code=$(echo "$response" | tail -1)
        local body=$(echo "$response" | head -n -1)

        if [[ "$http_code" == "200" ]] && echo "$body" | grep -q '"status".*"ok"'; then
            log "$name is ready (took ${elapsed}s)"
            return 0
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

    # Detect VRAM and select appropriate quantization
    local vram_gb
    vram_gb=$(detect_vram)
    if [[ "$vram_gb" -eq 0 ]]; then
        warn "Could not detect VRAM, using default IQ3_XXS quantization"
        vram_gb=24
    else
        log "Detected ${vram_gb}GB VRAM"
    fi

    CODE_MODEL_FILE=$(select_model_quant "$vram_gb")
    log "Selected model quantization: $CODE_MODEL_FILE"

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
            --split-mode layer \
            --tensor-split 1.0,0.5 \
            -fit off \
            --ctx-size 98304 \
            --context-shift \
            --port 8080 \
            --jinja \
            --cache-type-k q8_0 --cache-type-v q8_0 \
            --flash-attn on \
            --temp 1.0 --top-p 0.95 --min-p 0.01 --top-k 40 \
            --batch-size 4096 --ubatch-size 1024
    fi

    # Wait for server to be ready (required before launching Claude)
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
        wait_for_health "http://localhost:$NOVNC_PORT" "Browser (noVNC)" 60 || true
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

configure_claude_mcp() {
    header "Configuring Claude MCP"

    local mcp_command="novnc-mcp"
    local mcp_name="browser-automation"

    # Check if claude CLI is available
    if ! command -v claude &>/dev/null; then
        warn "Claude CLI not found. Skipping MCP configuration."
        warn "Install Claude Code and run: claude mcp add-json $mcp_name '{\"type\":\"stdio\",\"command\":\"$mcp_command\"}'"
        return 0
    fi

    # Check if MCP server is already configured
    if claude mcp list 2>/dev/null | grep -q "$mcp_name"; then
        log "MCP server '$mcp_name' already configured"
    else
        log "Adding MCP server to Claude..."
        claude mcp add-json "$mcp_name" "{\"type\":\"stdio\",\"command\":\"$mcp_command\"}" || {
            warn "Failed to add MCP server automatically."
            warn "Add manually: claude mcp add-json $mcp_name '{\"type\":\"stdio\",\"command\":\"$mcp_command\"}'"
        }
    fi
}

# =============================================================================
# Stop Functions
# =============================================================================

stop_all() {
    header "Stopping All Services"

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
        echo "  local-cc           # Start services and launch Claude Code"
        echo "  local-cc --vlm     # Include VLM for image analysis"
        echo "  local-cc --ml      # Include ML services (OmniParser, GUI-Actor)"
        echo "  local-cc --stop    # Stop all services"
        echo "  local-cc --status  # Show service status"
    else
        error "Installation failed"
    fi
}

# =============================================================================
# Claude CLI Installation
# =============================================================================

ensure_claude_cli() {
    if command -v claude &>/dev/null; then
        log "Claude CLI already installed"
        return 0
    fi

    header "Installing Claude CLI"
    log "Claude CLI not found. Installing..."

    # Install using npm (most reliable method)
    if command -v npm &>/dev/null; then
        npm install -g @anthropic-ai/claude-code
        if command -v claude &>/dev/null; then
            log "Claude CLI installed successfully via npm"
            return 0
        fi
    fi

    # Fallback: direct install script
    log "Trying direct install script..."
    curl -fsSL https://claude.ai/install.sh | sh

    if command -v claude &>/dev/null; then
        log "Claude CLI installed successfully"
    else
        warn "Could not install Claude CLI automatically."
        warn "Please install manually: npm install -g @anthropic-ai/claude-code"
        warn "Or visit: https://claude.ai/code"
    fi
}

# =============================================================================
# Launch Claude Code
# =============================================================================

launch_claude() {
    local include_vlm="$1"
    shift  # Remove include_vlm from args

    header "Launching Claude Code"

    export ANTHROPIC_BASE_URL="http://localhost:$CODE_PORT"
    export ANTHROPIC_API_KEY='sk-no-key-required'

    echo ""
    echo "=========================================="
    echo "  Local Claude Code Environment Ready"
    echo "=========================================="
    echo ""
    echo "  Code LLM: http://localhost:$CODE_PORT"
    if $include_vlm; then
        echo "  VLM:      http://localhost:$VLM_PORT (vlm_chat tool available)"
    fi
    echo "  noVNC:    http://localhost:$NOVNC_PORT (password: secret)"
    echo ""
    echo "  MCP Browser tools available in Claude"
    echo ""
    echo "=========================================="
    echo ""

    # Launch Claude Code
    exec claude --model "unsloth/Qwen3-Coder-Next" "$@"
}

# =============================================================================
# Main
# =============================================================================

main() {
    local include_ml=false
    local include_vlm=false
    local remaining_args=()

    # Parse arguments
    for arg in "$@"; do
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
            --ml)
                include_ml=true
                ;;
            --vlm)
                include_vlm=true
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS] [-- CLAUDE_ARGS]"
                echo ""
                echo "Options:"
                echo "  --vlm       Include VLM for image analysis (vlm_chat tool)"
                echo "  --ml        Include ML services (OmniParser, GUI-Actor)"
                echo "  --stop      Stop all services"
                echo "  --status    Show status of all services"
                echo "  --install   Install as 'local-cc' command"
                echo "  --help      Show this help message"
                echo ""
                echo "Examples:"
                echo "  $0                    # Start services and launch Claude"
                echo "  $0 --vlm              # Start with VLM for image analysis"
                echo "  $0 --vlm --ml         # Start with VLM and ML services"
                echo "  $0 --stop             # Stop all services"
                echo "  $0 -- --resume        # Pass --resume to Claude"
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

    # Ensure we have required tools
    command -v docker &>/dev/null || error "Docker not found. Please install Docker."
    command -v hf &>/dev/null || error "hf CLI not found. Install with: pip install huggingface_hub[cli]"

    # Run setup steps
    ensure_models
    ensure_llama_image
    ensure_browser_images

    if $include_vlm; then
        header "Building VLM Image (if needed)"
        cd "$BROWSER_DIR"
        docker compose --profile vlm build vlm 2>/dev/null || log "VLM image already built or building..."
        cd "$PROJECT_DIR"
    fi

    if $include_ml; then
        ensure_ml_images
    fi

    # Start services
    start_llm_servers
    start_browser_services "$include_vlm"

    if $include_ml; then
        start_ml_services
    fi

    # Setup MCP and Claude CLI
    ensure_mcp_server
    ensure_claude_cli
    configure_claude_mcp

    # Launch Claude
    launch_claude "$include_vlm" "${remaining_args[@]}"
}

main "$@"
