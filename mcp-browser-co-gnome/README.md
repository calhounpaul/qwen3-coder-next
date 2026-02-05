# noVNC Browser Automation Suite

A comprehensive browser automation suite that runs Playwright in Docker with noVNC visualization, provides a Python API for control, records all browser actions/state for debugging, enables session persistence and replay, and integrates Cloudflare quick tunnels for remote access.

## Features

- **Visual Browser Access**: View and interact with the browser via noVNC web interface
- **Playwright Automation**: Full Playwright API with stealth mode for anti-detection
- **Session Persistence**: Save and restore browser state (cookies, localStorage, URL)
- **Comprehensive Recording**: Video, Playwright traces, HAR files, and action logs
- **Remote Access**: Cloudflare quick tunnels for sharing browser access
- **Docker-based**: Consistent environment with easy deployment

## Quick Start

### 1. Start the Docker Services

```bash
# Start browser and video recording
docker compose up -d

# Or with remote access tunnel
docker compose --profile tunnel up -d
```

### 2. Access noVNC

Open http://localhost:6080 in your browser. Default password: `secret`

### 3. Install the Python Package

```bash
pip install -e .
```

### 4. Run Automation

```python
import asyncio
from novnc_automation import AutomationBrowser

async def main():
    # Connect to the Docker browser via CDP
    async with AutomationBrowser(
        session_id="my-session",
        cdp_endpoint="http://localhost:9222"
    ) as browser:
        await browser.goto("https://example.com")
        await browser.click("a")
        await browser.screenshot("result")

asyncio.run(main())
```

Or use the Docker orchestrator for programmatic control:

```python
from novnc_automation.docker import quick_start, quick_stop

# Start Docker containers with tunnel
status = quick_start(with_tunnel=True)
print(f"Tunnel URL: {status.tunnel_url}")
print(f"noVNC URL: {status.novnc_url}")

# ... run automation ...

quick_stop()
```

## Architecture

```
+-------------------+     +-------------------+     +-------------------+
|  Python Client    |---->|  Browser Container|---->|  Video Recorder   |
|  (Host machine)   |     |  (Playwright+VNC) |     |  (FFmpeg)         |
+-------------------+     +-------------------+     +-------------------+
                                  |
                                  v
                          +-------------------+
                          |  Cloudflared      |---> trycloudflare.com
                          |  (Quick Tunnel)   |     (Public URL)
                          +-------------------+
```

## API Reference

### AutomationBrowser

The main class for browser automation.

```python
from novnc_automation import AutomationBrowser

# Basic usage
async with AutomationBrowser(session_id="my-session") as browser:
    await browser.goto("https://example.com")
    await browser.click("#button")
    await browser.fill("#input", "text")
    await browser.screenshot("screenshot_name")

# Restore a previous session
async with AutomationBrowser() as browser:
    await browser.start(restore_session="my-session")
    # Browser state (cookies, localStorage) restored
```

#### Navigation Methods

- `goto(url, wait_until="load")` - Navigate to URL
- `reload()` - Reload page
- `go_back()` - Go back in history
- `go_forward()` - Go forward in history

#### Interaction Methods

- `click(selector)` - Click element
- `fill(selector, value)` - Fill input field
- `type(selector, text, delay=0)` - Type with key simulation
- `press(selector, key)` - Press keyboard key
- `select_option(selector, value)` - Select dropdown option
- `check(selector)` / `uncheck(selector)` - Toggle checkbox
- `hover(selector)` - Hover over element

#### Waiting Methods

- `wait_for_selector(selector, state="visible")` - Wait for element
- `wait_for_load_state(state="load")` - Wait for page load
- `wait_for_url(url_pattern)` - Wait for URL match

#### Content Extraction

- `get_text(selector)` - Get element text
- `get_attribute(selector, name)` - Get attribute value
- `get_inner_html(selector)` - Get inner HTML
- `evaluate(js_expression)` - Run JavaScript

### SessionManager

Manage browser session persistence.

```python
from novnc_automation import SessionManager

manager = SessionManager()

# List all sessions
sessions = await manager.list_sessions()

# Load session state
state = await manager.load_session_state("my-session")

# Delete session
await manager.delete_session("my-session")
```

### TunnelManager

Create Cloudflare quick tunnels for remote access.

```python
from novnc_automation import TunnelManager

# As context manager
async with TunnelManager() as tunnel:
    print(f"Remote URL: {tunnel.url}")
    # Keep running...

# Manual control
tunnel = TunnelManager()
url = await tunnel.start()
print(f"Remote URL: {url}")
# ...
await tunnel.stop()

# Get URL from Docker container
url = TunnelManager.get_tunnel_url_from_docker_logs()
```

## Ephemeral Data

All ephemeral data is saved to the `tmp/` directory (gitignored):

| Type | Location | Format |
|------|----------|--------|
| Video | `tmp/videos/` | MP4 |
| Playwright Trace | `tmp/traces/` | ZIP |
| Network HAR | `tmp/har/` | JSON |
| Action Logs | `tmp/logs/` | JSONL |
| Screenshots | `tmp/screenshots/` | PNG |
| X11 Screenshots | `tmp/x11_screenshots/` | PNG |

### User Input Capture

User interactions are automatically captured at two levels:

**JS Level** - DOM events via injected script:
```json
{
  "source": "user",
  "level": "js",
  "action": "click",
  "x": 450,
  "y": 320,
  "element": {"tag": "button", "id": "login", "text": "Sign In"}
}
```

**X11 Level** - Raw input with screenshots:
```json
{
  "source": "user",
  "level": "x11",
  "action": "mouse_click",
  "pixel_x": 450,
  "pixel_y": 320,
  "normalized_x": 0.234375,
  "normalized_y": 0.296296,
  "screenshot": "tmp/x11_screenshots/20260204_123456_click_450_320.png"
}
```

### Viewing Traces

```bash
# Open Playwright trace viewer
npx playwright show-trace tmp/traces/my-session.zip
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VNC_PASSWORD` | `secret` | noVNC password |
| `RESOLUTION` | `1920x1080x24` | Display resolution |
| `RECORD_VIDEO` | `true` | Enable video recording |
| `RECORD_TRACE` | `true` | Enable Playwright tracing |
| `RECORD_HAR` | `true` | Enable HAR recording |
| `STEALTH_MODE` | `true` | Enable anti-detection |
| `HEADLESS` | `false` | Run headless (no display) |
| `INSTALL_UBLOCK` | `true` | Install [uBlock Origin](https://ublockorigin.com/) ad blocker |

### YAML Configuration

Create `config.yml`:

```yaml
browser:
  headless: false
  stealth_mode: true
  viewport_width: 1920
  viewport_height: 1080

recording:
  record_video: true
  record_trace: true
  record_har: true
  tmp_dir: tmp
  sessions_dir: sessions

tunnel:
  enable_tunnel: false
  tunnel_port: 6080

docker:
  vnc_password: secret
  resolution: 1920x1080x24
```

## Docker Services

### Browser Container

- **Port 6080**: noVNC web interface
- **Port 5900**: VNC protocol
- **Port 9222**: Chrome DevTools Protocol

### Video Recording

Uses Selenium's FFmpeg container for screen recording.

### Cloudflare Tunnel

Optional service for remote access. Enable with:

```bash
docker compose --profile tunnel up -d
```

## Examples

See the `examples/` directory:

- `basic_usage.py` - Simple automation examples
- `session_persistence.py` - Save and restore sessions
- `remote_access.py` - Cloudflare tunnel usage

## MCP Server

The package includes an MCP (Model Context Protocol) server that allows LLMs to control the browser.

### Installation

```bash
pip install -e .
```

### Configuration

Add to your Claude Desktop config (`~/.config/claude/claude_desktop_config.json` on Linux or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "novnc-automation": {
      "command": "novnc-mcp",
      "env": {
        "HEADLESS": "false",
        "STEALTH_MODE": "true"
      }
    }
  }
}
```

Or with uv:

```json
{
  "mcpServers": {
    "novnc-automation": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/novnc_automation_record", "novnc-mcp"]
    }
  }
}
```

### Available Tools

#### Docker Management

| Tool | Description |
|------|-------------|
| `docker_start` | Start Docker containers (browser, video, tunnel) |
| `docker_stop` | Stop all Docker containers |
| `docker_status` | Get status including tunnel URL, VNC URL, health |

#### Browser Control

| Tool | Description |
|------|-------------|
| `browser_start` | Start browser session (auto-connects to Docker via CDP if running) |
| `browser_stop` | Stop the browser and save session |
| `browser_goto` | Navigate to a URL |
| `browser_click` | Click an element by selector |
| `browser_fill` | Fill text into an input |
| `browser_type` | Type with key simulation |
| `browser_press` | Press a keyboard key |
| `browser_screenshot` | Take a screenshot (saved to `tmp/screenshots/`) |
| `browser_get_text` | Get element text content |
| `browser_evaluate` | Execute JavaScript |
| `browser_wait_for_selector` | Wait for element |
| `browser_get_url` | Get current URL |
| `browser_get_title` | Get page title |
| `natural_language_click` | Click using natural language (GUI-Actor AI) |

#### OmniParser (UI Element Detection)

| Tool | Description |
|------|-------------|
| `omniparser_analyze` | Detect UI elements, save annotated image + JSON |
| `omniparser_click` | Click an element by ID from analysis results |
| `omniparser_get_html` | Get HTML of element at bbox center |
| `omniparser_list_elements` | List all detected elements with details |

#### Session & Logs

| Tool | Description |
|------|-------------|
| `list_sessions` | List saved sessions |
| `list_screenshots` | List screenshots in tmp/ |
| `get_action_logs` | Get action logs |

### OmniParser for UI Element Detection

The MCP server integrates [OmniParser v2](https://huggingface.co/microsoft/OmniParser-v2.0) (Microsoft) for detecting and understanding UI elements:

- **YOLO** for icon/button detection
- **OCR** for text extraction
- **Florence-2** for element captioning

```bash
# Basic install uses HuggingFace Spaces API (no GPU needed)
pip install -e .

# For local inference (requires GPU)
pip install -e '.[omniparser-local]'
```

Example workflow:
1. `omniparser_analyze` - Detect all UI elements
2. Review the annotated image (numbered bounding boxes)
3. `omniparser_click` with element ID to interact
4. Or use `omniparser_get_html` to inspect element HTML

Output files are saved to `tmp/omniparser/`:
- `YYYYMMDD_HHMMSS_annotated.png` - Image with numbered bboxes
- `YYYYMMDD_HHMMSS_elements.json` - Element details (id, box, type, text, description)

**Note:** A future VLM query tool is planned for arbitrary UI questions via local vision-language model. Currently, Claude Code can analyze the annotated images directly.

### Natural Language Clicks with GUI-Actor

The MCP server integrates [GUI-Actor](https://huggingface.co/spaces/Mungert/GUI-Actor), a vision-language model that can understand natural language instructions to find click targets.

```bash
# Install GUI-Actor dependencies
pip install -e '.[gui-actor]'
```

Example usage via MCP:
```
User: Click the login button
LLM: [calls natural_language_click with instruction="Click the login button"]
```

The model takes a screenshot, analyzes it with AI, and clicks at the predicted location.

### Temporary Files

The MCP server saves files to the `tmp/` directory (gitignored):

| Directory | Contents |
|-----------|----------|
| `tmp/screenshots/` | Screenshots with timestamps and URL in filename |
| `tmp/omniparser/` | OmniParser annotated images and element JSON |
| `tmp/x11_screenshots/` | X11-level click screenshots with coordinates |
| `tmp/logs/` | Action logs in JSONL format |
| `tmp/repos/` | Cloned repositories (OmniParser, GUI-Actor) |

Screenshot filenames follow the pattern: `YYYYMMDD_HHMMSS_fff_flattened_url.png`

## Stealth Mode

The automation uses `playwright-stealth` with these anti-detection measures:

- `navigator.webdriver` set to `undefined`
- Realistic browser plugins and languages
- Removed "HeadlessChrome" from User-Agent
- WebGL vendor/renderer spoofing
- Chrome runtime patches

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/
```

## License

MIT
