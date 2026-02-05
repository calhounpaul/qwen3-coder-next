# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Unified local LLM inference environment with browser automation MCP integration. Runs Qwen3-Coder-Next via llama.cpp on dual GPUs, with a Playwright-based browser automation suite exposed as MCP tools.

## Important Rules

- **NEVER use `huggingface-cli`** - Always use `hf` command instead for all HuggingFace operations
- **Image Analysis**: Never use the Read tool for images (`.png`, `.jpg`, etc.). Use `vlm_chat` for image understanding instead
- **GPU Memory**: Code LLM spans both GPUs via `--tensor-split 1.0,0.5`. ML services MUST run on GPU 1 only (`device_ids: ['1']`)
- **ML Mutual Exclusion**: Only one ML service (OmniParser, GUI-Actor, VLM) runs at a time. MLServiceManager handles on-demand startup and idle shutdown (5 min default)

## Commands

### System Launcher

```bash
./local-cc.sh              # Start Code LLM + browser, launch Claude Code
./local-cc.sh --vlm        # Include VLM for image analysis
./local-cc.sh --ml         # Include ML services (OmniParser, GUI-Actor)
./local-cc.sh --stop       # Stop all services
./local-cc.sh --status     # Check service status
```

### Development (mcp-browser-co-gnome submodule)

```bash
cd mcp-browser-co-gnome
pip install -e ".[dev]"                    # Install with dev deps (pytest, ruff)
ruff check src/                            # Lint
ruff format src/                           # Format
pytest                                     # Run all tests (async mode auto)
pytest tests/test_file.py::test_func       # Single test
```

### Docker Services

```bash
cd mcp-browser-co-gnome
docker compose up -d                       # Start browser + video
docker compose --profile ml up -d          # Start ML services
docker compose --profile tunnel up -d      # Include Cloudflare tunnel
docker compose down                        # Stop all
docker logs automation-<service> --tail 50 # Debug service issues
```

### Rebuild Code LLM Image

```bash
docker rmi llama-server-cuda:latest && ./local-cc.sh  # Rebuilds automatically
```

## Architecture

### Service Map

| Service | Port | Container | GPU | Startup |
|---------|------|-----------|-----|---------|
| Code LLM | 8003 | qwen3-server | 0+1 | default |
| noVNC | 6080 | automation-browser | - | default |
| CDP | 9222 | automation-browser | - | default |
| VLM | 8004 | automation-vlm | 1 | on-demand (`--vlm`) |
| OmniParser | 8010 | automation-omniparser | 1 | on-demand (`--ml`) |
| GUI-Actor | 8001 | automation-gui-actor | 1 | on-demand (`--ml`) |

### How It Fits Together

```
Claude Code ──stdio──> MCP Server (novnc-mcp) ──CDP──> Browser (Docker)
                            │
                            ├──HTTP──> OmniParser (:8010) - UI element detection
                            ├──HTTP──> GUI-Actor (:8001)  - NL click prediction
                            └──HTTP──> VLM (:8004)        - Vision-language model
```

- `local-cc.sh` orchestrates everything: VRAM detection, model download, Docker builds, health checks, MCP registration with `claude mcp add-json`
- The MCP server (`mcp-browser-co-gnome/src/novnc_automation/mcp_server.py`) exposes 20+ tools to Claude via stdio
- ML services start on-demand when their MCP tools are called, managed by `MLServiceManager` in `ml_services.py`
- Browser runs Playwright inside Docker with noVNC visualization; MCP controls it remotely via CDP on port 9222

### Key Source Files

| File | Purpose |
|------|---------|
| `local-cc.sh` | Main launcher: VRAM detection, model selection, Docker builds, health checks, MCP config |
| `Dockerfile.llama-server` | Multi-stage build: clones llama.cpp, compiles with CUDA |
| `mcp-browser-co-gnome/src/novnc_automation/mcp_server.py` | MCP tool definitions and handlers (1249 lines) |
| `mcp-browser-co-gnome/src/novnc_automation/browser.py` | AutomationBrowser - async context manager for Playwright |
| `mcp-browser-co-gnome/src/novnc_automation/ml_services.py` | MLServiceManager - on-demand lifecycle with mutual exclusion |
| `mcp-browser-co-gnome/src/novnc_automation/docker.py` | DockerOrchestrator - programmatic Docker Compose control |
| `mcp-browser-co-gnome/docker-compose.yml` | All service definitions, GPU assignments, health checks |

### ML Service API Endpoints

**OmniParser** (`localhost:8010`): `/health`, `/ready`, `/analyze` (POST image -> elements + annotated image)
**GUI-Actor** (`localhost:8001`): `/health`, `/ready`, `/predict` (POST image + instruction -> click coords)
**VLM** (`localhost:8004`): OpenAI-compatible `/chat/completions`

### Code LLM Configuration

Runs via `llama-server-cuda:latest` with: `--ctx-size 120000`, `--context-shift`, `--tensor-split 1.0,0.5`, `-fit off`, `--cache-type-k q8_0 --cache-type-v q8_0`. Model auto-selected by VRAM: >=48GB Q8_0, >=32GB IQ4_XS, >=24GB IQ3_XXS, <24GB IQ2_XXS.

### Design Patterns (mcp-browser-co-gnome)

- **Async Context Manager**: `async with AutomationBrowser()` for proper cleanup
- **Config Precedence**: Environment variables override YAML config (`config.yml`)
- **Ruff**: Line length 100, target Python 3.10
- **Hatch build system** with `pyproject.toml`

## Visual Element Clicking Workflow

1. `browser_start` -> `browser_goto(url)` -> `browser_screenshot`
2. `omniparser_analyze()` - returns annotated image + JSON with element details
3. Match visual target to element ID, then `omniparser_click(element_id=N)`

Alternative: `natural_language_click(instruction="click the search button")` (uses GUI-Actor)

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ML_IDLE_TIMEOUT` | 300 | Seconds before ML service auto-stop |
| `ML_ALWAYS_ON` | - | Comma-separated services to never auto-stop (e.g., `vlm`) |
| `HEADLESS` | false | Run browser headless |
| `STEALTH_MODE` | true | Anti-detection browser flags |
| `INSTALL_UBLOCK` | true | Install uBlock Origin in browser |

## Troubleshooting

- **503 from Code LLM**: Normal during startup (model loading)
- **CUDA OOM**: Check `nvidia-smi` - only one ML service should be on GPU 1 at a time
- **`-fit off` required**: Auto-fit algorithm hangs indefinitely on multi-GPU setups
