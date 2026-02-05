# qwen3-coder-next

Unified local LLM inference environment with browser automation MCP integration.

## Quick Start

```bash
# Start everything and launch Claude Code
./local-cc.sh

# With ML services (OmniParser, GUI-Actor for visual automation)
./local-cc.sh --ml

# Stop all services
./local-cc.sh --stop

# Check status
./local-cc.sh --status

# Install as system command (run from anywhere)
./local-cc.sh --install
```

After `--install`, you can run `local-cc` from any directory.

## What Gets Started

| Service | Port | Description |
|---------|------|-------------|
| Code LLM | 8003 | Qwen3-Coder-Next (65k context, dual GPU) |
| VLM | 8004 | UI-TARS-2B (vision-language model) |
| noVNC | 6080 | Browser visualization (password: `secret`) |
| OmniParser | 8010 | UI element detection (with `--ml`) |
| GUI-Actor | 8001 | Natural language clicks (with `--ml`) |

## Components

- **llama.cpp** - High-performance LLM inference engine with CUDA support
- **mcp-browser-co-gnome** - Browser automation via MCP (Playwright + noVNC)
- **models/** - GGUF model files (auto-downloaded from HuggingFace)

## Models

Models are automatically downloaded on first run:

- **Code LLM**: `unsloth/Qwen3-Coder-Next-GGUF` (~33GB)
- **VLM**: `unsloth/Qwen3-VL-4B-Instruct-GGUF` (~3GB)

## MCP Browser Tools

Once running, Claude has access to browser automation:

```
docker_start, docker_stop, docker_status
browser_start, browser_goto, browser_click, browser_fill, browser_screenshot
omniparser_analyze, omniparser_click (with --ml)
natural_language_click (with --ml)
```

View the browser at http://localhost:6080 (password: `secret`)

## Requirements

- Docker with NVIDIA GPU support
- `huggingface-cli` (`pip install huggingface_hub`)
- Claude Code CLI

## Manual Setup (if needed)

### Build llama.cpp Docker Image

```bash
cd llama.cpp
docker build -t llama-server-cuda:latest -f .devops/cuda.Dockerfile .
```

### Build Browser Automation

```bash
cd mcp-browser-co-gnome
docker compose build
pip install -e .
```

## License

- llama.cpp: MIT
- mcp-browser-co-gnome: MIT