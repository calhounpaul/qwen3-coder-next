# qwen3-coder-next

Unified local LLM inference environment with browser automation MCP integration for Qwen Code.

## Quick Start

```bash
./local-cc.sh                         # Start Code LLM + browser, launch Qwen Code
./local-cc.sh --vlm                   # Include VLM for image analysis
./local-cc.sh --ml                    # Include ML services (OmniParser, GUI-Actor)
./local-cc.sh --vlm --ml              # All services
./local-cc.sh --server-only           # Start services only (no Qwen Code, for headless/remote servers)
./local-cc.sh --client-only --remote-tunnel URL --tunnel-key SECRET  # Client only (no Docker/GPU needed)
./local-cc.sh --tmp-serve-api single  # Expose all APIs via single gateway tunnel (recommended)
./local-cc.sh --tmp-serve-api public  # Individual Cloudflare tunnels per service
./local-cc.sh --tmp-serve-api lan     # Show LAN access URLs
./local-cc.sh --stop                  # Stop all services
./local-cc.sh --status                # Check service status
./local-cc.sh --install               # Install as 'local-cc' command (run from anywhere)
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
| Code LLM | 8003 | Qwen3-Coder-Next (120k context, auto-quant by GPU) | default |
| noVNC | 6080 | Browser visualization (password: `secret`) | default |
| VLM | 8004 | Qwen3-VL-4B vision-language model | `--vlm` |
| OmniParser | 8010 | UI element detection | `--ml` |
| GUI-Actor | 8001 | Natural language clicks | `--ml` |

View the browser at http://localhost:6080 (password: `secret`)

## Models

Models are automatically downloaded on first run:

- **Code LLM**: `unsloth/Qwen3-Coder-Next-GGUF` (auto-selected by GPU: A6000 -> Q3_K_S, 2x RTX 3090 -> IQ3_XXS, fallback by VRAM)
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

Share all local APIs through a single authenticated gateway tunnel:

```bash
# Single gateway tunnel (recommended) - one URL for all services
./local-cc.sh --tmp-serve-api single
# Output: URL + secret, services at /code-llm/, /vlm/, /omniparser/, etc.

# Individual tunnels per service (legacy)
./local-cc.sh --tmp-serve-api public

# LAN access (show local IP addresses)
./local-cc.sh --tmp-serve-api lan

# Management
./local-cc.sh --show-tunnels   # Show active tunnel URLs
./local-cc.sh --stop-tunnels   # Close all tunnels and gateway
```

The single gateway uses Caddy reverse proxy on port 8888 with a shared secret. API paths use `X-Tunnel-Key` or `Authorization: Bearer` header auth. The noVNC root path uses browser-friendly basic auth (user: `tunnel`, password: the secret).

### Using Remote APIs (Client Mode)

Connect to a remote gateway tunnel with a single URL:

```bash
# Connect via single gateway tunnel (recommended)
./local-cc.sh --remote-tunnel https://xxx.trycloudflare.com --tunnel-key SECRET

# Or connect to individual services
./local-cc.sh --remote-code https://xxx.trycloudflare.com
./local-cc.sh --remote-code 192.168.1.10:8003

# All remote options:
#   --remote-tunnel URL      Single gateway tunnel (auto-derives all URLs below)
#   --tunnel-key SECRET      Shared secret for gateway authentication
#   --remote-code URL        Remote Code LLM
#   --remote-vlm URL         Remote VLM server
#   --remote-novnc URL       Remote browser (noVNC viewer)
#   --remote-cdp URL         Remote CDP endpoint (browser automation)
#   --remote-omniparser URL  Remote OmniParser
#   --remote-gui-actor URL   Remote GUI-Actor
```

When using `--remote-tunnel`, all `REMOTE_*_URL` vars are auto-derived from the gateway URL. The `TUNNEL_KEY` is exported for the MCP server (adds `X-Tunnel-Key` header to httpx requests) and set as `OPENAI_API_KEY` (so the OpenAI SDK sends `Authorization: Bearer` for LLM auth).

**Security**: Using HTTP with non-local domains will trigger a security warning. Use HTTPS or add `--insecure-ok` to skip the warning.

**Privacy note**: Cloudflare quick tunnels terminate TLS at Cloudflare's edge, meaning Cloudflare can inspect traffic in transit. The shared secret provides authentication (preventing unauthorized access) but not end-to-end encryption from Cloudflare. For sensitive workloads, use a VPN (e.g., Tailscale) or SSH tunneling instead.

## Deployment Modes

| Mode | Flag | Requirements | Use Case |
|------|------|-------------|----------|
| Full (default) | _(none)_ | Docker + GPU + npm | Local development with all services |
| Server only | `--server-only` | Docker + GPU | Headless server exposing APIs (no Qwen Code) |
| Client only | `--client-only` | pip + npm | Connect to remote server (no Docker/GPU needed) |

Qwen Code telemetry is automatically disabled on install.

## Requirements

**Full / Server mode:**
- Docker with NVIDIA GPU support
- `hf` CLI (`pip install huggingface_hub`)

**Client mode** (`--client-only`):
- Python 3.10+ with pip
- npm (for Qwen Code CLI)
- No Docker or GPU required

## License

MIT
