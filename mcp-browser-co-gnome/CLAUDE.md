# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**noVNC Browser Automation Suite** - A browser automation framework that runs Playwright in Docker with noVNC visualization, Python API control, session persistence, action recording, and Cloudflare tunnel integration.

**Stack**: Python 3.10+ (async/await), Playwright, Docker, Pydantic, MCP (Model Context Protocol)

## Common Commands

```bash
# Installation
pip install -e .                    # Basic install (Docker mode)
pip install -e ".[dev]"             # With dev dependencies (pytest, ruff)
pip install -e '.[local]'           # With playwright-stealth for local browser mode

# Linting/Formatting
ruff check src/                     # Check code
ruff format src/                    # Format code

# Testing
pytest                              # Run all tests
pytest -v                           # Verbose
pytest tests/test_file.py           # Specific file
pytest tests/test_file.py::test_func  # Single test

# Docker - Browser
docker compose up -d                # Start browser + video containers
docker compose --profile tunnel up -d  # Include Cloudflare tunnel
docker compose down                 # Stop all services

# Docker - ML Services (GPU required)
docker compose --profile ml up -d   # Start OmniParser + GUI-Actor
docker compose --profile ml build   # Build ML containers
```

## Architecture

```
src/novnc_automation/
├── browser.py        # AutomationBrowser - core class, Playwright lifecycle, all browser actions
├── config.py         # Pydantic config loading (YAML + env vars)
├── session.py        # SessionManager - save/restore cookies, localStorage
├── recording.py      # RecordingManager + ActionLogger - traces, HAR, action logs
├── input_capture.py  # JSInputCapture + X11InputCapture - user input recording
├── tunnel.py         # TunnelManager - Cloudflare quick tunnels
├── docker.py         # DockerOrchestrator - programmatic Docker Compose control
├── mcp_server.py     # MCP server exposing browser as tools for Claude
└── models/
    └── session_state.py  # Pydantic models for session/action data

docker/
├── browser/                # Browser container (Playwright + Xvfb + noVNC)
│   ├── Dockerfile
│   ├── supervisord.conf
│   ├── start_chromium.sh
│   └── x11_capture_service.py
├── omniparser/             # OmniParser ML container (YOLO + EasyOCR)
│   ├── Dockerfile
│   └── server.py           # FastAPI server on port 8000 (mapped to 8010)
└── gui-actor/              # GUI-Actor ML container (Qwen2.5-VL)
    ├── Dockerfile
    ├── server.py           # FastAPI server on port 8001
    └── gui_actor/          # Model code copied from HuggingFace Space
```

### Key Design Patterns

1. **Async Context Manager** - Always use `async with AutomationBrowser()` for proper cleanup
2. **Composition** - AutomationBrowser composes SessionManager and RecordingManager
3. **ActionLogger Context Manager** - All browser actions wrapped for automatic timing/logging
4. **Config Precedence** - Environment variables override YAML config
5. **ML via Docker APIs** - OmniParser and GUI-Actor run in separate containers, MCP server calls via HTTP

### Entry Points

- **Library**: `AutomationBrowser` class in `browser.py`
- **MCP Server**: `novnc-mcp` entry point runs `mcp_server.py`
- **Docker API**: `DockerOrchestrator` or `quick_start()`/`quick_stop()` in `docker.py`
- **Examples**: `examples/` directory shows usage patterns

## Key Directories

| Directory | Purpose | Git Status |
|-----------|---------|------------|
| `tmp/` | Ephemeral data and model cache (gitignored) | gitignored |
| `tmp/repos/` | OmniParser weights, GUI-Actor model | gitignored |
| `tmp/screenshots/` | MCP screenshots | gitignored |
| `tmp/x11_screenshots/` | X11-level click screenshots with coords | gitignored |
| `tmp/omniparser/` | OmniParser annotated images and JSON | gitignored |
| `tmp/logs/` | Action logs (MCP + X11 events) | gitignored |
| `tmp/videos/` | Screen recordings | gitignored |
| `tmp/traces/` | Playwright traces | gitignored |
| `tmp/har/` | HAR network logs | gitignored |
| `tmp/sessions/` | Saved session state (cookies, localStorage) | gitignored |
| `docker/` | All Dockerfiles and container code | tracked |

## Docker Services

**Core Services:**
- **browser**: Playwright + Xvfb + noVNC + uBlock Origin (ports: 6080 noVNC, 5900 VNC, 9222 CDP)
- **video**: FFmpeg screen capture from browser container
- **cloudflared**: Optional tunnel (use `--profile tunnel`)

**ML Services (use `--profile ml`):**
- **omniparser**: OmniParser v2 inference (port 8010) - YOLO + EasyOCR for UI element detection
- **gui-actor**: GUI-Actor inference (port 8001) - Qwen2.5-VL for natural language clicks

ML services require NVIDIA GPU. Models are cached in `tmp/repos/` (symlinked, shared across projects).

## Configuration

Config loaded from `config.yml` (if exists) + environment variables. Key env vars:
- `HEADLESS`, `STEALTH_MODE`, `VIEWPORT_WIDTH`, `VIEWPORT_HEIGHT`
- `RECORD_VIDEO`, `RECORD_TRACE`, `RECORD_HAR`, `RECORD_ACTIONS`
- `VNC_PASSWORD`, `RESOLUTION`, `ENABLE_TUNNEL`
- `INSTALL_UBLOCK` - Install uBlock Origin ad blocker (default: `true`)
- `OMNIPARSER_PORT` - OmniParser API port (default: 8010)
- `GUI_ACTOR_PORT` - GUI-Actor API port (default: 8001)

See `.env.example` and `config.yml.example` for all options.

## MCP Server Tools

The MCP server exposes browser control to Claude Desktop. ML tools only appear when their Docker containers are healthy.

**Docker Management:**
- `docker_start` - Start Docker containers (browser, video, tunnel), returns tunnel URL
- `docker_stop` - Stop all Docker containers
- `docker_status` - Get status including tunnel URL, VNC URL, ML service health

**Browser Control:**
- `browser_start` - Start browser session (auto-connects to Docker via CDP if running)
- `browser_stop`, `browser_goto`, `browser_click`, `browser_fill`
- `browser_screenshot`, `browser_get_text`, `browser_evaluate`

**ML Tools (require `--profile ml` containers running):**
- `natural_language_click` - Click element by natural language description (GUI-Actor)
- `omniparser_analyze` - Detect UI elements, save annotated image + JSON
- `omniparser_click` - Click an element by ID from analysis results
- `omniparser_get_html` - Get HTML of element at bbox center
- `omniparser_list_elements` - List all detected elements

**Session/Logs:**
- `list_sessions`, `list_screenshots`
- `get_action_logs` - Get logs with optional `source` filter ('user' or 'api')

Configure in Claude Desktop config (see `mcp_config.example.json`).

### ML Service Architecture

ML models run in separate Docker containers with FastAPI servers:

**OmniParser (localhost:8010):**
- `/health` - Health check with `models_loaded` and `status` fields
- `/ready` - Returns 200 only when models are loaded
- `/analyze` - POST with image file, returns JSON with elements + base64 annotated image
- Uses YOLO for icon detection, EasyOCR for text extraction
- Florence-2 captioning disabled due to transformers compatibility (icons labeled as "icon")

**GUI-Actor (localhost:8001):**
- `/health` - Health check with `model_loaded` status
- `/ready` - Returns 200 only when model is loaded
- `/predict` - POST with image file + instruction, returns click coordinates
- Requires `transformers<5.0.0` (v5.0 moved `hidden_size` to `text_config`, breaking model loading)

The MCP server checks `/health` endpoints to determine tool availability. Tools appear when service reports `status: "healthy"`.

### User Input Capture

Input is captured at two levels:

**JS Level** (via Playwright `addInitScript`):
- Captures DOM events: clicks, inputs, keyboard, scroll, focus, submit
- Semantic info: element selector, text, type, coordinates
- Logged with `source: "user"`, `level: "js"`

**X11 Level** (via pynput in Docker):
- Captures raw mouse/keyboard from display server
- Takes screenshot on each click
- Logs pixel coords (x, y) and normalized coords (0.0-1.0)
- Logged with `source: "user"`, `level: "x11"`
- Screenshots saved to `tmp/x11_screenshots/`

### CDP Connection

When Docker is running, `browser_start` automatically connects via Chrome DevTools Protocol (CDP) on port 9222. This means:
- Browser runs inside Docker with noVNC visualization
- Python/MCP controls browser remotely via CDP
- No local browser installation needed (just the `playwright` Python package)

## Stealth Mode

Enabled by default via browser args in Docker container:
- `--disable-blink-features=AutomationControlled` removes `navigator.webdriver`
- Automation flags disabled

For local (non-Docker) mode, install `pip install -e '.[local]'` for `playwright-stealth` package.
