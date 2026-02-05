# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Unified local LLM inference environment with browser automation MCP integration.

See [README.md](./README.md) for end-user documentation.

## Important Rules

- **NEVER use `huggingface-cli`** - Always use `hf` command instead for all HuggingFace operations (download, upload, etc.)
- **Image Analysis**: Do not use the Read tool for images (`.png`, `.jpg`, etc.). Use `vlm_chat` for image understanding instead.
- **ML Service Management**: ML services (OmniParser, GUI-Actor, VLM) use **MLServiceManager** for on-demand startup with mutual exclusion - only one ML service runs at a time (see `mcp-browser-co-gnome/src/novnc_automation/ml_services.py`)
- **GPU Memory**: Code LLM spans GPU 0 and GPU 1 via `--tensor-split 1.0,0.5`. ML services MUST run on GPU 1 only.

## Architecture

### Services

| Service | Port | Description |
|---------|------|-------------|
| Code LLM | 8003 | Qwen3-Coder-Next (65k ctx, dual-GPU) |
| noVNC | 6080 | Browser visualization (password: `secret`) |
| CDP | 9222 | Chrome DevTools Protocol |
| VLM | 8004 | Qwen3-VL-4B (vision-language model) |
| OmniParser | 8010 | UI element detection (ML) |
| GUI-Actor | 8001 | Natural language clicks (ML) |

### ML Service Management

ML services use **MLServiceManager** for lifecycle management:

- **On-demand startup**: Services start when MCP tools are called
- **Auto-stop**: Services stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- **Mutual exclusion**: Only one ML service runs at a time (shared GPU 1 memory)
- **Always-on services**: Set `ML_ALWAYS_ON=vlm` to prevent auto-stop

Environment variables:
- `ML_IDLE_TIMEOUT` (default: 300) - Seconds before idle shutdown
- `ML_ALWAYS_ON` - Comma-separated service names to never auto-stop

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
| `mcp-browser-co-gnome/src/novnc_automation/ml_services.py` | MLServiceManager implementation |
| `mcp-browser-co-gnome/src/novnc_automation/browser.py` | Browser automation core |
| `mcp-browser-co-gnome/src/novnc_automation/mcp_server.py` | MCP server exposing browser tools |
| `mcp-browser-co-gnome/docker-compose.yml` | Docker service definitions |

## Quick Start

```bash
./local-cc.sh              # Start Code LLM + browser and launch Claude Code
./local-cc.sh --vlm        # Include VLM for image analysis (vlm_chat tool)
./local-cc.sh --ml         # Include ML services (OmniParser, GUI-Actor)
./local-cc.sh --vlm --ml   # Include all services
./local-cc.sh --stop       # Stop all services
./local-cc.sh --status     # Check service status
./local-cc.sh --install    # Install as 'local-cc' command (run from anywhere)
```

## Directory Structure

```
.
├── local-cc.sh             # Unified launcher script
├── Dockerfile.llama-server # Builds llama-server with CUDA (clones llama.cpp)
├── models/                 # GGUF model files (auto-downloaded, gitignored)
│   └── Qwen3-Coder-Next-GGUF/
├── mcp-browser-co-gnome/   # Browser automation MCP server
│   ├── docker/vlm/         # VLM Docker setup (model auto-downloads)
│   └── tmp/                    # Ephemeral data (gitignored)
└── .claude/                # Local Claude Code settings
```

## Services

| Service | Port | Description | Flag |
|---------|------|-------------|------|
| Code LLM | 8003 | Qwen3-Coder-Next (65k ctx, VRAM-auto quant) | default |
| noVNC | 6080 | Browser visualization (password: `secret`) | default |
| CDP | 9222 | Chrome DevTools Protocol | default |
| VLM | 8004 | Qwen3-VL-4B (vision-language model) | `--vlm` |
| OmniParser | 8010 | UI element detection | `--ml` |
| GUI-Actor | 8001 | Natural language clicks | `--ml` |

## Models

**Code LLM** (auto-selected based on VRAM):
- `unsloth/Qwen3-Coder-Next-GGUF`
- ≥48GB: Q8_0 | ≥32GB: IQ4_XS | ≥24GB: IQ3_XXS | <24GB: IQ2_XXS

**VLM** (Docker-managed, auto-downloads on first `--vlm` run):
- `unsloth/Qwen3-VL-4B-Instruct-GGUF` (Q8_0 + mmproj)
- Stored in `mcp-browser-co-gnome/tmp/vlm-models/` (gitignored)

## GPU Allocation (2x RTX 3090)

| GPU | Services | Notes |
|-----|----------|-------|
| GPU 0 | Code LLM (primary) | Code LLM uses ~20GB |
| GPU 1 | Code LLM (overflow), VLM, OmniParser, GUI-Actor | ML services MUST run on GPU 1 |

**Important**: Code LLM uses `--tensor-split 1.0,0.5` to span both GPUs. ML services must use GPU 1 only (configured via `device_ids: ['1']` in docker-compose.yml).

## ML Service Management

ML services use **MLServiceManager** for automatic lifecycle management:

- **On-demand startup**: Services start when MCP tools are called
- **Auto-stop**: Services stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- **Mutual exclusion**: Only one ML service runs at a time (stops others first to avoid GPU OOM)
- **GPU assignment**: ML services must run on GPU 1 only (`device_ids: ['1']`)

Environment variables:
- `ML_IDLE_TIMEOUT` (default: 300) - Seconds before idle shutdown
- `ML_ALWAYS_ON` - Comma-separated service names to never auto-stop (e.g., `vlm`)

**Startup behavior**: When a tool requiring an ML service is called, the manager:
1. Stops any other running ML services
2. Starts the requested service
3. Waits for health check (5-6 min for OmniParser/GUI-Actor, 3 min for VLM)
4. Tracks usage time for idle shutdown

Implementation: `mcp-browser-co-gnome/src/novnc_automation/ml_services.py`

## MCP Browser Tools

The `novnc-mcp` server exposes browser automation to Claude:

**Core tools (always available):**
- `docker_start`, `docker_stop`, `docker_status`
- `browser_start`, `browser_goto`, `browser_click`, `browser_fill`, `browser_screenshot`

**VLM tool (with --vlm flag):**
- `vlm_chat` - Chat with vision model, supports images in conversation

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
