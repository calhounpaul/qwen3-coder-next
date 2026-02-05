# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Unified local LLM inference environment with browser automation MCP integration.

See [README.md](./README.md) for end-user documentation.

## Important Rules

- **NEVER use `huggingface-cli`** - Always use `hf` command instead for all HuggingFace operations (download, upload, etc.)
- **Image Analysis**: Do not use the Read tool for images (`.png`, `.jpg`, etc.). Use `vlm_chat` for image understanding instead.
- **ML Service Management**: ML services (OmniParser, GUI-Actor, VLM) use **MLServiceManager** for on-demand startup with mutual exclusion - only one ML service runs at a time (see `mcp-browser-co-gnome/src/novnc_automation/ml_services.py`)

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
| GPU 0 | Code LLM (primary), VLM | Code LLM uses ~20GB |
| GPU 1 | Code LLM (overflow), VLM, OmniParser, GUI-Actor | ML services MUST run on GPU 1 |

- Code LLM spans both GPUs via `--tensor-split 1.0,0.5` with `-fit off` (auto-fit hangs)
- OmniParser/GUI-Actor configured with `device_ids: ['1']` in docker-compose.yml
- If OmniParser OOMs, check `nvidia-smi` - GPU 0 is likely full

## ML Service Management

ML services (OmniParser, GUI-Actor, VLM) use the **MLServiceManager** for automatic lifecycle management:

- **On-demand startup**: Services start when MCP tools are called
- **Auto-stop**: Services stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT` env var)
- **Mutual exclusion**: Only one ML service runs at a time (stops others first to avoid GPU OOM)
- Environment variables: `ML_IDLE_TIMEOUT` (default: 300s), `ML_ALWAYS_ON` (comma-separated service names to never stop)
- Implementation: `mcp-browser-co-gnome/src/novnc_automation/ml_services.py`

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

When asked to click elements based on visual properties (e.g., "click the button with the largest face"):

1. **Start browser**: `browser_start`
2. **Take screenshot**: `browser_screenshot`
3. **Analyze with OmniParser**: `omniparser_analyze` - detects all UI elements with bounding boxes
4. **View screenshot**: Use `Read` tool on the screenshot file to see the image
5. **Identify target**: Match visual criteria (faces, icons, etc.) to element IDs from OmniParser
6. **Click element**: `omniparser_click` with the target element ID

**Example for finding faces:**
```
1. browser_screenshot → /path/to/screenshot.png
2. omniparser_analyze → Returns elements with IDs, positions, types
3. Read /path/to/screenshot.png → View image to identify faces
4. Match largest face to nearest element ID by position
5. omniparser_click(element_id=N)
```

## Docker Images

The Code LLM runs via `llama-server-cuda:latest` Docker image, built from `Dockerfile.llama-server`.
This Dockerfile clones llama.cpp at build time - no local source needed.

To rebuild the image (e.g., for llama.cpp updates):
```bash
docker rmi llama-server-cuda:latest
./local-cc.sh  # Will rebuild automatically
```
