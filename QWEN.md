# QWEN.md

This file provides guidance to Qwen Code (github.com/QwenLM/qwen-code) when working with code in this repository.

## Project Overview

Unified local LLM inference environment with browser automation MCP integration.

See [README.md](./README.md) for end-user documentation.

## Important Rules

- **NEVER use `huggingface-cli`** - Always use `hf` command instead for all HuggingFace operations (download, upload, etc.)
- **Image Analysis**: Do not use the Read tool for images (`.png`, `.jpg`, etc.). Use `vlm_chat` for image understanding instead.
- **ML Service Management**: ML services (OmniParser, GUI-Actor, VLM) use **MLServiceManager** for on-demand startup with mutual exclusion - only one ML service runs at a time (see `mcp-browser-co-gnome/src/novnc_automation/ml_services.py`)
- **GPU Memory**: Model quantization is auto-selected by GPU name (A6000 -> Q3_K_S, 2x RTX 3090 -> IQ3_XXS, fallback by VRAM).

## Avoiding Tool Call Loops

To prevent getting stuck in repetitive tool call loops:

1. **Grep before Read** - When searching for specific code patterns in large files, use Grep first to find line numbers, then Read those specific lines. Don't read the same file range multiple times hoping for different results.

2. **Read with offset** - For large files (>100 lines), read specific sections using offset/limit. If the first 40 lines don't have what you need, move to a different section - don't re-read the same lines.

3. **One search strategy per attempt** - If a search doesn't find what you need, try a different pattern or approach. Don't repeat the same grep/read with identical parameters.

4. **Track what you've seen** - Keep mental note of file sections you've already read. If you need to revisit, read a DIFFERENT section.

Example - finding a function in a 1687-line file:
```
BAD:  Read lines 1-40, Read lines 1-40, Read lines 1-40 (loop!)
GOOD: Grep for function name -> get line 847 -> Read lines 840-880
```

## Architecture

### Services

| Service | Port | Description |
|---------|------|-------------|
| Code LLM | 8003 | Qwen3-Coder-Next (120k ctx, auto-quant by GPU) |
| noVNC | 6080 | Browser visualization (password: `secret`) |
| CDP | 9222 | Chrome DevTools Protocol |
| VLM | 8004 | Qwen3-VL-4B (vision-language model) |
| OmniParser | 8010 | UI element detection (ML) |
| GUI-Actor | 8001 | Natural language clicks (ML) |

### ML Service Management

ML services use **MLServiceManager** for lifecycle management:

- **On-demand startup**: Services start when MCP tools are called
- **Auto-stop**: Services stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- **Mutual exclusion**: Only one ML service runs at a time (shared GPU memory)
- **Always-on services**: Set `ML_ALWAYS_ON=vlm` to prevent auto-stop

Environment variables:
- `ML_IDLE_TIMEOUT` (default: 300) - Seconds before idle shutdown
- `ML_ALWAYS_ON` - Comma-separated service names to never auto-stop

### Remote Service URLs

The MCP server supports connecting to remote services via environment variables. When set, the corresponding service is treated as externally managed (no Docker start/stop).

| Env Variable | Default | Used By |
|-------------|---------|---------|
| `VLM_URL` | `http://localhost:8004` | `mcp_server.py`, `ml_services.py` |
| `OMNIPARSER_URL` | `http://localhost:8010` | `mcp_server.py`, `ml_services.py` |
| `GUI_ACTOR_URL` | `http://localhost:8001` | `mcp_server.py`, `ml_services.py` |
| `CDP_ENDPOINT` | (empty = auto-detect Docker) | `mcp_server.py` |
| `TUNNEL_KEY` | (auto-generated if empty) | `mcp_server.py`, `ml_services.py` |

These are set automatically by `local-cc.sh` when `--remote-*` or `--tunnel-key` flags are used. The MCP server subprocess inherits them via `exec qwen`.

When `TUNNEL_KEY` is set, all httpx requests include an `X-Tunnel-Key` header for authentication through the Caddy gateway. If not set, a random key is auto-generated on MCP server startup and logged to stderr.

For remote services, MLServiceManager:
- Skips Docker compose start/stop
- Only performs health checks against the remote URL
- Skips mutual exclusion (remote services don't use local GPU)
- Skips idle timeout shutdown

### Key Directories

| Directory | Purpose | Git Status |
|-----------|---------|------------|
| `models/` | GGUF model files (auto-downloaded) | gitignored |
| `mcp-browser-co-gnome/tmp/` | Ephemeral data (screenshots, sessions, logs) | gitignored |
| `mcp-browser-co-gnome/tmp/repos/` | ML model weights (OmniParser, GUI-Actor) | gitignored |

### Important Files

| File | Purpose |
|------|---------|
| `local-cc.sh` | Main launcher script |
| `Dockerfile.llama-server` | Code LLM Docker image (clones llama.cpp) |
| `mcp-browser-co-gnome/Caddyfile.gateway` | Caddy reverse proxy config for single gateway tunnel |
| `mcp-browser-co-gnome/src/novnc_automation/ml_services.py` | MLServiceManager implementation |
| `mcp-browser-co-gnome/src/novnc_automation/browser.py` | Browser automation core |
| `mcp-browser-co-gnome/src/novnc_automation/mcp_server.py` | MCP server exposing browser tools |
| `docker-compose.yml` | Full stack Docker services (Code LLM + MCP components) |
| `mcp-browser-co-gnome/docker-compose.yml` | Standalone MCP services (for Claude Code, etc.) |

## Quick Start

```bash
./local-cc.sh                         # Start Code LLM + browser and launch Qwen Code
./local-cc.sh --vlm                   # Include VLM for image analysis (vlm_chat tool)
./local-cc.sh --ml                    # Include ML services (OmniParser, GUI-Actor)
./local-cc.sh --vlm --ml              # Include all services
./local-cc.sh --server-only           # Start services only, no Qwen Code (headless/remote servers)
./local-cc.sh --client-only --remote-tunnel URL --tunnel-key SECRET  # Client only (no Docker/GPU)
./local-cc.sh --tmp-serve-api single  # Single gateway tunnel for all services (recommended)
./local-cc.sh --tmp-serve-api public  # Individual Cloudflare tunnels per service
./local-cc.sh --tmp-serve-api lan     # Show LAN IP addresses for API access
./local-cc.sh --stop                  # Stop all services
./local-cc.sh --stop-tunnels          # Stop only tunnels (keeps services running)
./local-cc.sh --status                # Check service status
./local-cc.sh --install               # Install as 'local-cc' command (run from anywhere)

# Restart services while keeping tunnel alive (no new credentials needed)
docker restart qwen3-server           # Restart Code LLM only
docker restart automation-vlm         # Restart VLM only
docker restart automation-browser     # Restart browser only

# Remote: connect via single gateway tunnel
./local-cc.sh --remote-tunnel URL --tunnel-key SECRET --vlm

# Remote: connect to individual services
./local-cc.sh --remote-code URL --remote-vlm URL --remote-novnc URL --remote-cdp URL --vlm
```

## Deployment Modes

| Mode | Flag | Requirements | Use Case |
|------|------|-------------|----------|
| Full (default) | _(none)_ | Docker + GPU + npm | Local development with all services |
| Server only | `--server-only` | Docker + GPU | Headless server exposing APIs (no Qwen Code installed) |
| Client only | `--client-only` | pip + npm | Connect to remote server (no Docker/GPU needed) |

- **Server only**: Starts all services and tunnels, prints status, then exits. Does not install or launch Qwen Code.
- **Client only**: Skips all Docker/GPU/model operations. Only installs MCP server + Qwen Code, then connects to remote services. Requires `--remote-*` flags.

Qwen Code telemetry is automatically disabled during installation (writes `~/.qwen/settings.json`).

## Directory Structure

```
.
├── local-cc.sh             # Unified launcher script
├── docker-compose.yml      # Full stack (Code LLM + all MCP components)
├── Dockerfile.llama-server # Builds llama-server with CUDA (clones llama.cpp)
├── models/                 # GGUF model files (auto-downloaded, gitignored)
│   └── Qwen3-Coder-Next-GGUF/
├── mcp-browser-co-gnome/   # Browser automation MCP server (submodule)
│   ├── docker-compose.yml  # Standalone MCP services (for Claude Code, etc.)
│   ├── docker/vlm/         # VLM Docker setup (model auto-downloads)
│   └── tmp/                # Ephemeral data (gitignored)
└── .qwen/                  # Local Qwen Code settings
```

## Docker Compose Files

**Main repo** (`docker-compose.yml`): Full stack with Code LLM + all MCP components
```bash
docker compose up -d                    # Core (Code LLM + browser)
docker compose --profile vlm up -d      # + VLM
docker compose --profile ml up -d       # + OmniParser + GUI-Actor
docker compose --profile all up -d      # Everything
```

**MCP submodule** (`mcp-browser-co-gnome/docker-compose.yml`): Standalone MCP services
```bash
cd mcp-browser-co-gnome
docker compose up -d                    # Core (browser + video)
docker compose --profile vlm up -d      # + VLM
docker compose --profile ml up -d       # + ML services
# Then: claude mcp add browser-automation novnc-mcp
```

## Services

| Service | Port | Description | Flag |
|---------|------|-------------|------|
| Code LLM | 8003 | Qwen3-Coder-Next 80B MoE (120k ctx, GGUF auto-quant) | default |
| noVNC | 6080 | Browser visualization (password: `secret`) | default |
| CDP | 9222 | Chrome DevTools Protocol | default |
| VLM | 8004 | Qwen3-VL-4B (vision-language model) | `--vlm` |
| OmniParser | 8010 | UI element detection | `--ml` |
| GUI-Actor | 8001 | Natural language clicks | `--ml` |

## Models

**Code LLM**: Qwen3-Coder-Next 80B MoE (80B total parameters, 3B active per token)
- Repo: `unsloth/Qwen3-Coder-Next-GGUF`
- Format: GGUF via llama.cpp with CUDA
- Auto-selected quantization by GPU:
  - A6000 (48GB): Q3_K_S (33GB) - leaves ~10GB for KV cache + VLM
  - 2x RTX 3090: IQ3_XXS
  - Fallback by VRAM: ≥45GB → Q3_K_S | ≥32GB → IQ4_XS | ≥24GB → IQ3_XXS | <24GB → IQ2_XXS
- Note: FP8 (~76GB) requires H100; GGUF quantization enables A6000/consumer GPUs

**VLM**: Qwen3-VL-4B vision-language model
- Repo: `unsloth/Qwen3-VL-4B-Instruct-GGUF` (Q8_0 + mmproj-F16)
- Context: 16k tokens (for high-resolution image processing)
- VRAM: ~5GB
- Stored in `mcp-browser-co-gnome/tmp/vlm-models/` (gitignored)

## GPU Allocation

GPU allocation depends on hardware:

**A6000 (48GB single GPU):**
| Service | VRAM Usage | Notes |
|---------|------------|-------|
| Code LLM | ~36-42GB | Q3_K_S (33GB) + KV cache for 120k context |
| VLM | ~5GB | Runs when Code LLM not using full context |
| OmniParser | ~4GB | On-demand, mutual exclusion with other ML services |
| GUI-Actor | ~4GB | On-demand, mutual exclusion with other ML services |

**2x RTX 3090 (24GB each):**
| Service | VRAM Usage | Notes |
|---------|------------|-------|
| Code LLM | ~20GB GPU 0 + ~12GB GPU 1 | IQ3_XXS with `--tensor-split 1.0,0.5` |
| VLM / OmniParser / GUI-Actor | ~4-5GB GPU 1 | On-demand, one at a time |

ML services use MLServiceManager for mutual exclusion to prevent OOM.

## ML Service Management

ML services use **MLServiceManager** for automatic lifecycle management:

- **On-demand startup**: Services start when MCP tools are called
- **Auto-stop**: Services stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- **Mutual exclusion**: Only one ML service runs at a time (stops others first to avoid GPU OOM)
- **GPU assignment**: All services share the single GPU (device 0)

Environment variables:
- `ML_IDLE_TIMEOUT` (default: 300) - Seconds before idle shutdown
- `ML_ALWAYS_ON` - Comma-separated service names to never auto-stop (e.g., `vlm`)

**Startup behavior**: When a tool requiring an ML service is called, the manager:
1. Stops any other running ML services
2. Starts the requested service
3. Waits for health check (5-6 min for OmniParser/GUI-Actor, 3 min for VLM)
4. Tracks usage time for idle shutdown

Implementation: `mcp-browser-co-gnome/src/novnc_automation/ml_services.py`

## Single Gateway Tunnel

The `--tmp-serve-api single` mode starts a Caddy reverse proxy (port 8888) + one cloudflared tunnel to expose all services through a single URL with path-based routing:

```
                                          /code-llm/*    -> localhost:8003
                                          /vlm/*         -> localhost:8004
TUNNEL_URL.trycloudflare.com -> Caddy:8888  /omniparser/*  -> localhost:8010
       (shared secret auth)               /gui-actor/*   -> localhost:8001
                                          /cdp/*         -> localhost:9222
                                          /*             -> localhost:6080 (noVNC)
```

- **API paths**: Authenticated via `X-Tunnel-Key` header or `Authorization: Bearer` token
- **noVNC root**: Authenticated via HTTP basic auth (user: `tunnel`, password: the secret)
- **Secret**: Auto-generated 32-char hex via `openssl rand -hex 16`
- **Config**: `mcp-browser-co-gnome/Caddyfile.gateway`

On the client side, `--remote-tunnel URL --tunnel-key SECRET` auto-derives all `REMOTE_*_URL` vars and exports `TUNNEL_KEY` for the MCP server.

**E2E Encryption**: When using `--remote-tunnel` with `--tunnel-key`, a chisel tunnel (SSH-over-HTTP) is automatically established. All service traffic is end-to-end encrypted through the chisel tunnel, preventing Cloudflare from inspecting data.

**Privacy note**: Without chisel (e.g., direct tunnel access), Cloudflare quick tunnels terminate TLS at Cloudflare's edge, meaning Cloudflare can inspect traffic in transit. The shared secret provides authentication but not end-to-end encryption. For sensitive workloads without chisel, use a VPN (e.g., Tailscale) instead.

## MCP Browser Tools

The `novnc-mcp` server exposes browser automation to Qwen Code:

**Core tools (always available):**
- `docker_start`, `docker_stop`, `docker_status`
- `browser_start`, `browser_goto`, `browser_click`, `browser_fill`, `browser_screenshot`

**VLM tool (with --vlm flag):**
- `vlm_chat` - Chat with vision model, supports images in conversation
  - Auto-retries on transient 500 errors (3 retries with exponential backoff)

> **Image Analysis**: Use `vlm_chat` instead of the Read tool for images. The Read tool is denied for image files (*.jpg, *.png, etc.) - use the VLM for image understanding, face detection, UI analysis, etc.
>
> Example: `vlm_chat` with `messages: [{"role": "user", "content": [{"type": "image_path", "image_path": "/path/to/image.png"}, {"type": "text", "text": "Describe this image"}]}]`

**ML tools (with --ml flag):**
- `omniparser_analyze`, `omniparser_click`, `omniparser_list_elements`
- `natural_language_click`

## File Locations

- **Screenshots**: `mcp-browser-co-gnome/tmp/screenshots/`
- **OmniParser output**: `mcp-browser-co-gnome/tmp/omniparser/`
- **VLM models**: `mcp-browser-co-gnome/tmp/vlm-models/`

## Visual Element Clicking Workflow

Use OmniParser for UI element detection and clicking:

1. **Start browser**: `browser_start`
2. **Navigate**: `browser_goto(url)`
3. **Take screenshot**: `browser_screenshot`
4. **Analyze**: `omniparser_analyze()` - returns annotated image + JSON with element details
5. **Identify target**: Match visual target (button, icon, text) to element ID from OmniParser results
6. **Click**: `omniparser_click(element_id=N)`

**For natural language clicks** (GUI-Actor):
- Use `natural_language_click(instruction="click the search button")`
- Works without OmniParser, but requires GUI-Actor container

## Docker Images

The Code LLM runs via `llama-server-cuda:latest` Docker image, built from `Dockerfile.llama-server`.
This Dockerfile clones llama.cpp at build time - no local source needed.

To rebuild the image (e.g., for llama.cpp updates):
```bash
docker rmi llama-server-cuda:latest
./local-cc.sh  # Will rebuild automatically
```
