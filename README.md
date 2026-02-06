# qwen3-coder-next

Unified local LLM inference environment with browser automation MCP integration for Qwen Code.

## Quick Start

```bash
./local-cc.sh                        # Start Code LLM + browser, launch Qwen Code
./local-cc.sh --vlm                  # Include VLM for image analysis
./local-cc.sh --ml                   # Include ML services (OmniParser, GUI-Actor)
./local-cc.sh --vlm --ml             # All services
./local-cc.sh --tmp-serve-api public # Expose APIs via Cloudflare tunnels
./local-cc.sh --tmp-serve-api lan    # Show LAN access URLs
./local-cc.sh --stop                 # Stop all services
./local-cc.sh --status               # Check service status
./local-cc.sh --install              # Install as 'local-cc' command (run from anywhere)
```

After `--install`, run `local-cc` from any project directory. The tools (models, browser automation) are found automatically, while Qwen Code operates in your current directory.

## Architecture

```
Qwen Code ──stdio──> MCP Server (novnc-mcp) ──CDP──> Browser (Docker)
                          │
                          ├──HTTP──> OmniParser (:8010) - UI element detection
                          ├──HTTP──> GUI-Actor (:8001)  - NL click prediction
                          └──HTTP──> VLM (:8004)        - Vision-language model
```

## Services

| Service | Port | Description | Flag |
|---------|------|-------------|------|
| Code LLM | 8003 | Qwen3-Coder-Next (120k context, Q3_K_S) | default |
| noVNC | 6080 | Browser visualization (password: `secret`) | default |
| VLM | 8004 | Qwen3-VL-4B vision-language model | `--vlm` |
| OmniParser | 8010 | UI element detection | `--ml` |
| GUI-Actor | 8001 | Natural language clicks | `--ml` |

View the browser at http://localhost:6080 (password: `secret`)

## Models

Models are automatically downloaded on first run:

- **Code LLM**: `unsloth/Qwen3-Coder-Next-GGUF` (auto-selected by VRAM: >=45GB Q3_K_S, >=32GB IQ4_XS, >=24GB IQ3_XXS, <24GB IQ2_XXS)
- **VLM**: `unsloth/Qwen3-VL-4B-Instruct-GGUF` (~3GB, downloads to `mcp-browser-co-gnome/tmp/vlm-models/`)

## MCP Browser Tools

Once running, Qwen Code has access to browser automation:

- **Core** (always available): `docker_start`, `docker_stop`, `docker_status`, `browser_start`, `browser_goto`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_get_text`, `browser_evaluate`
- **VLM** (`--vlm`): `vlm_chat` - vision model for image analysis
- **ML** (`--ml`): `omniparser_analyze`, `omniparser_click`, `omniparser_list_elements`, `natural_language_click`

## ML Service Management

ML services use **on-demand startup** with mutual exclusion:

- Start automatically when their MCP tools are called
- Only one runs at a time (shared GPU memory)
- Auto-stop after 5 minutes idle (configurable via `ML_IDLE_TIMEOUT`)
- Set `ML_ALWAYS_ON=vlm` to keep specific services running

## Remote API Access

### Sharing Your APIs (Server Mode)

Share your local APIs with others using temporary Cloudflare tunnels or LAN access:

```bash
# Public access via Cloudflare (temporary URLs)
./local-cc.sh --tmp-serve-api public
./local-cc.sh --show-tunnels   # Show active tunnel URLs
./local-cc.sh --stop-tunnels   # Close all tunnels

# LAN access (show local IP addresses)
./local-cc.sh --tmp-serve-api lan
```

Tunnels are temporary and expire when stopped. Each running service gets its own tunnel URL.

### Using Remote APIs (Client Mode)

Connect to remote servers instead of starting local services:

```bash
# Use a remote Code LLM via Cloudflare tunnel
./local-cc.sh --remote-code https://xxx.trycloudflare.com

# Use a remote server on your LAN
./local-cc.sh --remote-code 192.168.1.10:8003

# Use multiple remote services
./local-cc.sh --remote-code https://llm.example.com --remote-vlm https://vlm.example.com

# All remote options:
#   --remote-code URL        Remote Code LLM
#   --remote-vlm URL         Remote VLM server
#   --remote-novnc URL       Remote browser (noVNC viewer)
#   --remote-cdp URL         Remote CDP endpoint (browser automation)
#   --remote-omniparser URL  Remote OmniParser
#   --remote-gui-actor URL   Remote GUI-Actor
```

When using `--remote-*` flags, the corresponding local Docker service is skipped. The MCP server reads remote URLs from environment variables (`VLM_URL`, `OMNIPARSER_URL`, `GUI_ACTOR_URL`, `CDP_ENDPOINT`) which are automatically set by `local-cc.sh`.

**Security**: Using HTTP with non-local domains will trigger a security warning. Use HTTPS or add `--insecure-ok` to skip the warning.

## Requirements

- Docker with NVIDIA GPU support
- `hf` CLI (`pip install huggingface_hub`)
- Qwen Code CLI (`npm install -g @qwen-code/qwen-code@latest`)

## License

MIT
