# qwen3-coder-next

Unified local LLM inference environment with browser automation MCP integration.

## Quick Start

```bash
./local-cc.sh              # Start Code LLM + browser, launch Claude Code
./local-cc.sh --vlm        # Include VLM for image analysis
./local-cc.sh --ml         # Include ML services (OmniParser, GUI-Actor)
./local-cc.sh --vlm --ml   # All services
./local-cc.sh --stop       # Stop all services
./local-cc.sh --status     # Check service status
./local-cc.sh --install    # Install as 'local-cc' command (run from anywhere)
```

After `--install`, run `local-cc` from any project directory. The tools (models, browser automation) are found automatically, while Claude Code operates in your current directory.

## Architecture

```
Claude Code ──stdio──> MCP Server (novnc-mcp) ──CDP──> Browser (Docker)
                            │
                            ├──HTTP──> OmniParser (:8010) - UI element detection
                            ├──HTTP──> GUI-Actor (:8001)  - NL click prediction
                            └──HTTP──> VLM (:8004)        - Vision-language model
```

## Services

| Service | Port | Description | Flag |
|---------|------|-------------|------|
| Code LLM | 8003 | Qwen3-Coder-Next (120k context, dual GPU) | default |
| noVNC | 6080 | Browser visualization (password: `secret`) | default |
| VLM | 8004 | Qwen3-VL-4B vision-language model | `--vlm` |
| OmniParser | 8010 | UI element detection | `--ml` |
| GUI-Actor | 8001 | Natural language clicks | `--ml` |

View the browser at http://localhost:6080 (password: `secret`)

## Models

Models are automatically downloaded on first run:

- **Code LLM**: `unsloth/Qwen3-Coder-Next-GGUF` (auto-selected by VRAM: >=48GB Q8_0, >=32GB IQ4_XS, >=24GB IQ3_XXS, <24GB IQ2_XXS)
- **VLM**: `unsloth/Qwen3-VL-4B-Instruct-GGUF` (~3GB, downloads to `mcp-browser-co-gnome/tmp/vlm-models/`)

## MCP Browser Tools

Once running, Claude has access to browser automation:

- **Core** (always available): `docker_start`, `docker_stop`, `docker_status`, `browser_start`, `browser_goto`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_get_text`, `browser_evaluate`
- **VLM** (`--vlm`): `vlm_chat` - vision model for image analysis
- **ML** (`--ml`): `omniparser_analyze`, `omniparser_click`, `omniparser_list_elements`, `natural_language_click`

## ML Service Management

ML services use **on-demand startup** with mutual exclusion:

- Start automatically when their MCP tools are called
- Only one runs at a time (shared GPU 1 memory)
- Auto-stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- Set `ML_ALWAYS_ON=vlm` to keep specific services running

## Requirements

- Docker with NVIDIA GPU support
- `hf` CLI (`pip install huggingface_hub`)
- Claude Code CLI

## License

MIT
