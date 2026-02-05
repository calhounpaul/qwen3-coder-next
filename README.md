# qwen3-coder-next

Unified local LLM inference environment with browser automation MCP integration.

## Quick Start

```bash
# Start everything and launch Claude Code
./local-cc.sh

# With VLM for image analysis
./local-cc.sh --vlm

# With ML services (OmniParser, GUI-Actor for visual automation)
./local-cc.sh --ml

# With all services
./local-cc.sh --vlm --ml

# Stop all services
./local-cc.sh --stop

# Check status
./local-cc.sh --status

# Install as system command (run from anywhere)
./local-cc.sh --install
```

After `--install`, you can run `local-cc` from any project directory. The tools (models, browser automation) are found automatically, while Claude Code operates in your current directory.

## What Gets Started

| Service | Port | Description |
|---------|------|-------------|
| Code LLM | 8003 | Qwen3-Coder-Next (120k context, dual GPU) |
| VLM | 8004 | Qwen3-VL-4B (vision-language model) |
| noVNC | 6080 | Browser visualization (password: `secret`) |
| OmniParser | 8010 | UI element detection (with `--ml`) |
| GUI-Actor | 8001 | Natural language clicks (with `--ml`) |

## Components

- **llama.cpp** - High-performance LLM inference engine with CUDA support
- **mcp-browser-co-gnome** - Browser automation via MCP (Playwright + noVNC)
- **models/** - GGUF model files (auto-downloaded from HuggingFace)

## Models

Models are automatically downloaded on first run:

- **Code LLM**: `unsloth/Qwen3-Coder-Next-GGUF` (auto-selected based on VRAM)
  - ≥48GB: Q8_0 | ≥32GB: IQ4_XS | ≥24GB: IQ3_XXS | <24GB: IQ2_XXS
- **VLM**: `unsloth/Qwen3-VL-4B-Instruct-GGUF` (~3GB, auto-downloads to `mcp-browser-co-gnome/tmp/vlm-models/`)

## MCP Browser Tools

Once running, Claude has access to browser automation:

```
# Core tools (always available)
docker_start, docker_stop, docker_status
browser_start, browser_goto, browser_click, browser_fill, browser_screenshot

# VLM tool (with --vlm flag)
vlm_chat - Chat with vision model, supports images

# ML tools (with --ml flag)
omniparser_analyze, omniparser_click, omniparser_list_elements
natural_language_click
```

View the browser at http://localhost:6080 (password: `secret`)

## Visual Element Clicking Workflow

For clicking elements based on visual properties:

1. `browser_start` → `browser_screenshot` → `omniparser_analyze`
2. View screenshot to identify target element
3. Match visual target to element ID from OmniParser
4. `omniparser_click(element_id=N)`

## Requirements

- Docker with NVIDIA GPU support
- `hf` CLI (`pip install huggingface_hub`)
- Claude Code CLI

## ML Service Management

ML services (OmniParser, GUI-Actor, VLM) use **on-demand startup** with mutual exclusion:

- Services start automatically when their MCP tools are called
- Only one ML service runs at a time (shared GPU 1 memory)
- Auto-stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- Environment variables: `ML_IDLE_TIMEOUT`, `ML_ALWAYS_ON` (comma-separated service names)

See `mcp-browser-co-gnome/CLAUDE.md` for more details on ML service management.

## License

- llama.cpp: MIT
- mcp-browser-co-gnome: MIT