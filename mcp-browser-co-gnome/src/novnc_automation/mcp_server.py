"""MCP Server for browser automation with ML inference via Docker containers.

ML services (OmniParser, GUI-Actor) run in separate Docker containers and are
accessed via HTTP APIs. Tools are only exposed when their service is healthy.
"""

import asyncio
import base64
import json
import re
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiofiles
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from PIL import Image

from novnc_automation.browser import AutomationBrowser
from novnc_automation.docker import DockerOrchestrator
from novnc_automation.input_capture import JSInputCapture, X11InputCapture
from novnc_automation.ml_services import MLServiceManager, ServiceName, get_ml_manager

# Directories for ephemeral data (all under tmp/)
PROJECT_ROOT = Path(__file__).parent.parent.parent
TMP_DIR = PROJECT_ROOT / "tmp"
SCREENSHOTS_DIR = TMP_DIR / "screenshots"
X11_SCREENSHOTS_DIR = TMP_DIR / "x11_screenshots"
LOGS_DIR = TMP_DIR / "logs"
VIDEOS_DIR = TMP_DIR / "videos"
TRACES_DIR = TMP_DIR / "traces"
HAR_DIR = TMP_DIR / "har"
REPOS_DIR = TMP_DIR / "repos"
OMNIPARSER_DIR = TMP_DIR / "omniparser"

# Ensure all tmp directories exist
for dir_path in [SCREENSHOTS_DIR, X11_SCREENSHOTS_DIR, LOGS_DIR, VIDEOS_DIR, TRACES_DIR, HAR_DIR, REPOS_DIR, OMNIPARSER_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# ML Service URLs (Docker containers)
OMNIPARSER_URL = "http://localhost:8010"
GUI_ACTOR_URL = "http://localhost:8001"
VLM_URL = "http://localhost:8004"

# Global instances
_browser: AutomationBrowser | None = None
_session_id: str | None = None
_docker: DockerOrchestrator | None = None
_js_capture: JSInputCapture | None = None
_x11_capture: X11InputCapture | None = None
_ml_manager: MLServiceManager | None = None

# Cached OmniParser results
_omniparser_result: dict | None = None


def _get_ml_manager() -> MLServiceManager:
    """Get or create the ML service manager."""
    global _ml_manager
    if _ml_manager is None:
        _ml_manager = get_ml_manager()
    return _ml_manager


def _flatten_url(url: str, max_length: int = 50) -> str:
    """Flatten a URL into a safe filename component."""
    try:
        parsed = urlparse(url)
        flat = f"{parsed.netloc}{parsed.path}"
        flat = re.sub(r"[^\w\-.]", "_", flat)
        flat = re.sub(r"_+", "_", flat)
        if len(flat) > max_length:
            flat = flat[:max_length]
        return flat.strip("_")
    except Exception:
        return "unknown"


def _get_screenshot_path(url: str | None = None) -> Path:
    """Generate a timestamped screenshot path with URL in filename."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    url_part = _flatten_url(url) if url else "no_url"
    filename = f"{timestamp}_{url_part}.png"
    return SCREENSHOTS_DIR / filename


async def _log_action(action: str, details: dict[str, Any], source: str = "api") -> None:
    """Log an action to the action log file."""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": _session_id,
        "source": source,
        "action": action,
        **details,
    }
    log_file = LOGS_DIR / f"actions_{datetime.now().strftime('%Y%m%d')}.jsonl"
    async with aiofiles.open(log_file, "a") as f:
        await f.write(json.dumps(log_entry) + "\n")


async def _log_user_input(event_data: dict) -> None:
    """Log a user input event (from JS or X11 capture)."""
    log_entry = {
        "timestamp": event_data.get("timestamp", datetime.now().isoformat()),
        "session_id": _session_id,
        "source": "user",
        "level": event_data.get("level", "unknown"),
        "action": event_data.get("type", "unknown"),
        **{k: v for k, v in event_data.items() if k not in ("type", "timestamp", "source", "level")},
    }
    log_file = LOGS_DIR / f"actions_{datetime.now().strftime('%Y%m%d')}.jsonl"
    async with aiofiles.open(log_file, "a") as f:
        await f.write(json.dumps(log_entry) + "\n")


async def _check_ml_service_health(url: str, timeout: float = 2.0) -> bool:
    """Check if an ML service is healthy and responding."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/health")
            if resp.status_code == 200:
                data = resp.json()
                # Service is available if it responds with healthy status
                # Models may load lazily on first request
                return data.get("status") == "healthy"
    except Exception:
        pass
    return False


async def _omniparser_healthy() -> bool:
    """Check if OmniParser service is healthy and ready."""
    return await _check_ml_service_health(OMNIPARSER_URL)


async def _gui_actor_healthy() -> bool:
    """Check if GUI-Actor service is healthy and ready."""
    return await _check_ml_service_health(GUI_ACTOR_URL)


async def _vlm_healthy() -> bool:
    """Check if VLM service is healthy and ready."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{VLM_URL}/health")
            if resp.status_code == 200:
                data = resp.json()
                # llama-server returns {"status": "ok"} when ready
                return data.get("status") == "ok"
    except Exception:
        pass
    return False


# Create MCP server
server = Server("novnc-automation")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools. ML tools are always shown (services start on-demand)."""
    tools = [
        Tool(
            name="docker_start",
            description="Start the Docker containers (browser, video, tunnel). Must be called before browser_start when running in Docker mode.",
            inputSchema={
                "type": "object",
                "properties": {
                    "with_tunnel": {
                        "type": "boolean",
                        "description": "Include Cloudflare tunnel for remote access. Default: true.",
                    },
                    "with_video": {
                        "type": "boolean",
                        "description": "Include video recording service. Default: true.",
                    },
                    "vnc_password": {
                        "type": "string",
                        "description": "Custom VNC password. Default: 'secret'.",
                    },
                },
            },
        ),
        Tool(
            name="docker_stop",
            description="Stop all Docker containers (browser, video, tunnel).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="docker_status",
            description="Get status of Docker containers including tunnel URL, VNC URL, and service health.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="browser_start",
            description="Start the browser automation session. Must be called before other browser tools.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Optional session ID for persistence. Auto-generated if not provided.",
                    },
                    "restore_session": {
                        "type": "string",
                        "description": "Optional session ID to restore from (cookies, localStorage).",
                    },
                    "headless": {
                        "type": "boolean",
                        "description": "Run in headless mode (no visible browser). Default: false.",
                    },
                    "stealth": {
                        "type": "boolean",
                        "description": "Enable stealth mode for anti-detection. Default: true.",
                    },
                },
            },
        ),
        Tool(
            name="browser_stop",
            description="Stop the browser and save the session state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "save_session": {
                        "type": "boolean",
                        "description": "Whether to save session state. Default: true.",
                    },
                },
            },
        ),
        Tool(
            name="browser_goto",
            description="Navigate to a URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to."},
                    "wait_until": {
                        "type": "string",
                        "enum": ["commit", "domcontentloaded", "load", "networkidle"],
                        "description": "When to consider navigation complete. Default: load.",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="browser_click",
            description="Click an element by CSS selector.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element to click."},
                    "timeout": {"type": "number", "description": "Timeout in milliseconds."},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_fill",
            description="Fill text into an input field.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input field."},
                    "value": {"type": "string", "description": "Text to fill into the field."},
                    "timeout": {"type": "number", "description": "Timeout in milliseconds."},
                },
                "required": ["selector", "value"],
            },
        ),
        Tool(
            name="browser_type",
            description="Type text into an element with key-by-key simulation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element."},
                    "text": {"type": "string", "description": "Text to type."},
                    "delay": {"type": "number", "description": "Delay between keystrokes in milliseconds."},
                },
                "required": ["selector", "text"],
            },
        ),
        Tool(
            name="browser_press",
            description="Press a keyboard key on an element.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element."},
                    "key": {"type": "string", "description": "Key to press (e.g., 'Enter', 'Tab', 'Escape')."},
                },
                "required": ["selector", "key"],
            },
        ),
        Tool(
            name="browser_screenshot",
            description="Take a screenshot of the current page. Saves to tmp/screenshots with timestamp and URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "description": "Capture full scrollable page. Default: false."},
                    "selector": {"type": "string", "description": "Optional CSS selector to screenshot specific element."},
                },
            },
        ),
        Tool(
            name="browser_get_text",
            description="Get text content of an element.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element."},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_evaluate",
            description="Execute JavaScript in the browser and return the result.",
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript code to execute."},
                },
                "required": ["script"],
            },
        ),
        Tool(
            name="browser_wait_for_selector",
            description="Wait for an element to appear on the page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to wait for."},
                    "state": {
                        "type": "string",
                        "enum": ["attached", "detached", "visible", "hidden"],
                        "description": "State to wait for. Default: visible.",
                    },
                    "timeout": {"type": "number", "description": "Timeout in milliseconds."},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="browser_get_url",
            description="Get the current page URL.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="browser_get_title",
            description="Get the current page title.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_sessions",
            description="List all saved browser sessions.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_screenshots",
            description="List all screenshots in the tmp/screenshots directory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of screenshots to list. Default: 20."},
                },
            },
        ),
        Tool(
            name="get_action_logs",
            description="Get recent action logs from both API calls and user interactions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of log entries. Default: 50."},
                    "date": {"type": "string", "description": "Date in YYYYMMDD format. Default: today."},
                    "source": {
                        "type": "string",
                        "enum": ["user", "api"],
                        "description": "Filter by source: 'user' for user interactions, 'api' for MCP tool calls. Omit for all.",
                    },
                },
            },
        ),
    ]

    # ML tools are always available (services start on-demand)
    # OmniParser tools
    tools.extend([
        Tool(
            name="omniparser_analyze",
            description="Analyze the current browser screenshot with OmniParser v2 to detect UI elements. Returns an annotated image with numbered bounding boxes and a JSON file with element descriptions. Use this to understand what's on screen before clicking.",
            inputSchema={
                "type": "object",
                "properties": {
                    "use_local": {
                        "type": "boolean",
                        "description": "Use local models instead of API (requires GPU). Default: false.",
                    },
                },
            },
        ),
        Tool(
            name="omniparser_click",
            description="Click at the center of a UI element detected by omniparser_analyze. Must call omniparser_analyze first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "The ID number of the element to click (from omniparser_analyze results).",
                    },
                },
                "required": ["element_id"],
            },
        ),
        Tool(
            name="omniparser_get_html",
            description="Get the HTML of the DOM element at the center of a bounding box detected by omniparser_analyze. Useful for inspecting element structure without interacting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "The ID number of the element (from omniparser_analyze results).",
                    },
                    "outer_html": {
                        "type": "boolean",
                        "description": "Return outerHTML instead of innerHTML. Default: true.",
                    },
                },
                "required": ["element_id"],
            },
        ),
        Tool(
            name="omniparser_list_elements",
            description="List all elements from the most recent omniparser_analyze call with their IDs, types, text, and descriptions.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ])

    # GUI-Actor tool
    tools.append(
        Tool(
            name="natural_language_click",
            description="Click on a UI element described in natural language using GUI-Actor AI model. Takes a screenshot and uses vision AI to find the element.",
            inputSchema={
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Natural language description of what to click (e.g., 'Click the search button', 'Click the login link').",
                    },
                },
                "required": ["instruction"],
            },
        )
    )

    # VLM tool
    tools.append(
        Tool(
            name="vlm_chat",
            description="Chat with local Qwen3-VL vision model. Use for image analysis like "
                       "'describe this image' or 'which element should I click?'.\n\n"
                       "Simple usage: provide `prompt` and `image_path` parameters.\n\n"
                       "Advanced usage: provide `messages` array for multi-turn conversations. Example:\n"
                       '[{"role": "user", "content": [{"type": "image_path", "image_path": "/path/to/img.png"}, '
                       '{"type": "text", "text": "Describe this"}]}]',
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text prompt to send with the image (simple mode).",
                    },
                    "image_path": {
                        "type": "string",
                        "description": "Path to image file to analyze (simple mode).",
                    },
                    "messages": {
                        "type": "array",
                        "description": "Full conversation array for multi-turn chat (advanced mode). "
                                      "Overrides prompt/image_path if provided.",
                        "items": {"type": "object"},
                    },
                    "max_tokens": {"type": "integer", "description": "Max response tokens (default: 512)"},
                },
            },
        )
    )

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    global _browser, _session_id, _docker, _omniparser_result

    try:
        if name == "docker_start":
            if _docker is not None:
                status = _docker.status()
                if status.browser_running:
                    return [TextContent(
                        type="text",
                        text=f"Docker already running.\nTunnel URL: {status.tunnel_url}\nnoVNC URL: {status.novnc_url}",
                    )]

            _docker = DockerOrchestrator()
            with_tunnel = arguments.get("with_tunnel", True)
            with_video = arguments.get("with_video", True)
            vnc_password = arguments.get("vnc_password")

            status = await _docker.start_async(
                with_tunnel=with_tunnel,
                with_video=with_video,
                vnc_password=vnc_password,
            )

            await _log_action("docker_start", {
                "with_tunnel": with_tunnel,
                "with_video": with_video,
                "tunnel_url": status.tunnel_url,
            })

            lines = ["Docker containers started successfully!"]
            if status.tunnel_url:
                lines.append(f"Tunnel URL: {status.tunnel_url}")
            lines.append(f"noVNC URL: {status.novnc_url}")
            lines.append(f"VNC Password: {status.vnc_password}")
            lines.append(f"Browser healthy: {status.browser_healthy}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "docker_stop":
            if _docker is None:
                _docker = DockerOrchestrator()
            await _docker.stop_async()
            _docker = None

            await _log_action("docker_stop", {})

            return [TextContent(type="text", text="Docker containers stopped.")]

        elif name == "docker_status":
            if _docker is None:
                _docker = DockerOrchestrator()
            status = _docker.status()

            # Also check ML services
            omniparser_ready, gui_actor_ready, vlm_ready = await asyncio.gather(
                _omniparser_healthy(),
                _gui_actor_healthy(),
                _vlm_healthy(),
            )

            lines = ["Docker Status:"]
            lines.append(f"  Browser running: {status.browser_running}")
            lines.append(f"  Browser healthy: {status.browser_healthy}")
            lines.append(f"  Tunnel running: {status.tunnel_running}")
            lines.append(f"  Video running: {status.video_running}")
            lines.append(f"  OmniParser ready: {omniparser_ready}")
            lines.append(f"  GUI-Actor ready: {gui_actor_ready}")
            lines.append(f"  VLM ready: {vlm_ready}")
            if status.tunnel_url:
                lines.append(f"  Tunnel URL: {status.tunnel_url}")
            lines.append(f"  noVNC URL: {status.novnc_url}")
            lines.append(f"  VNC Password: {status.vnc_password}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "browser_start":
            global _js_capture, _x11_capture

            if _browser is not None:
                return [TextContent(type="text", text="Browser already running. Stop it first.")]

            session_id = arguments.get("session_id") or str(uuid.uuid4())[:8]
            restore_session = arguments.get("restore_session")
            headless = arguments.get("headless", False)
            stealth = arguments.get("stealth", True)

            # Check if Docker browser is running and connect via CDP
            cdp_endpoint = None
            if _docker is None:
                _docker = DockerOrchestrator()
            docker_status = _docker.status()
            if docker_status.browser_healthy:
                cdp_endpoint = "http://localhost:9222"

            _session_id = session_id
            _browser = AutomationBrowser(
                session_id=session_id,
                headless=headless,
                stealth=stealth,
                cdp_endpoint=cdp_endpoint,
            )
            await _browser.start(restore_session=restore_session)

            # Install JS-level input capture
            _js_capture = JSInputCapture(log_callback=_log_user_input)
            try:
                await _js_capture.install(_browser.context)
                await _js_capture.attach_to_page(_browser.page)
                _browser.context.on("page", lambda page: asyncio.create_task(_js_capture.attach_to_page(page)))
            except Exception as e:
                print(f"JS input capture setup failed: {e}")

            x11_capture_started = docker_status.browser_healthy

            await _log_action("browser_start", {
                "session_id": session_id,
                "restore_session": restore_session,
                "headless": headless,
                "stealth": stealth,
                "cdp_endpoint": cdp_endpoint,
                "js_capture": True,
                "x11_capture": x11_capture_started,
            })

            msg = f"Browser started with session ID: {session_id}"
            if cdp_endpoint:
                msg += " (connected to Docker browser via CDP)"
            msg += " (user input capture enabled)"
            if restore_session:
                msg += f" (restored from: {restore_session})"
            return [TextContent(type="text", text=msg)]

        elif name == "browser_stop":
            if _browser is None:
                return [TextContent(type="text", text="No browser running.")]

            save_session = arguments.get("save_session", True)
            await _browser.stop(save_session=save_session)

            await _log_action("browser_stop", {"save_session": save_session})

            _browser = None
            msg = f"Browser stopped. Session {'saved' if save_session else 'not saved'}."
            return [TextContent(type="text", text=msg)]

        elif name == "browser_goto":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            url = arguments["url"]
            wait_until = arguments.get("wait_until", "load")
            await _browser.goto(url, wait_until=wait_until)

            await _log_action("goto", {"url": url, "wait_until": wait_until})

            return [TextContent(type="text", text=f"Navigated to: {url}")]

        elif name == "browser_click":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            selector = arguments["selector"]
            timeout = arguments.get("timeout")
            await _browser.click(selector, timeout=timeout)

            await _log_action("click", {"selector": selector})

            return [TextContent(type="text", text=f"Clicked: {selector}")]

        elif name == "browser_fill":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            selector = arguments["selector"]
            value = arguments["value"]
            timeout = arguments.get("timeout")
            await _browser.fill(selector, value, timeout=timeout)

            await _log_action("fill", {"selector": selector, "value_length": len(value)})

            return [TextContent(type="text", text=f"Filled {selector} with {len(value)} characters")]

        elif name == "browser_type":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            selector = arguments["selector"]
            text = arguments["text"]
            delay = arguments.get("delay", 0)
            await _browser.type(selector, text, delay=delay)

            await _log_action("type", {"selector": selector, "text_length": len(text)})

            return [TextContent(type="text", text=f"Typed {len(text)} characters into {selector}")]

        elif name == "browser_press":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            selector = arguments["selector"]
            key = arguments["key"]
            await _browser.press(selector, key)

            await _log_action("press", {"selector": selector, "key": key})

            return [TextContent(type="text", text=f"Pressed {key} on {selector}")]

        elif name == "browser_screenshot":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            full_page = arguments.get("full_page", False)
            selector = arguments.get("selector")

            current_url = _browser.page.url
            screenshot_path = _get_screenshot_path(current_url)

            if selector:
                element = await _browser.page.query_selector(selector)
                if element:
                    await element.screenshot(path=str(screenshot_path))
                else:
                    return [TextContent(type="text", text=f"Element not found: {selector}")]
            else:
                await _browser.page.screenshot(path=str(screenshot_path), full_page=full_page)

            await _log_action("screenshot", {
                "path": str(screenshot_path),
                "url": current_url,
                "full_page": full_page,
                "selector": selector,
            })

            return [TextContent(
                type="text",
                text=f"Screenshot saved: {screenshot_path.name}\nFull path: {screenshot_path}",
            )]

        elif name == "browser_get_text":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            selector = arguments["selector"]
            text = await _browser.get_text(selector)
            return [TextContent(type="text", text=text)]

        elif name == "browser_evaluate":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            script = arguments["script"]
            result = await _browser.evaluate(script)

            await _log_action("evaluate", {"script": script[:100]})

            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "browser_wait_for_selector":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            selector = arguments["selector"]
            state = arguments.get("state", "visible")
            timeout = arguments.get("timeout")
            await _browser.wait_for_selector(selector, state=state, timeout=timeout)

            return [TextContent(type="text", text=f"Element {selector} is now {state}")]

        elif name == "browser_get_url":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            return [TextContent(type="text", text=_browser.page.url)]

        elif name == "browser_get_title":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            title = await _browser.page.title()
            return [TextContent(type="text", text=title)]

        elif name == "natural_language_click":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            # Ensure GUI-Actor service is running
            manager = _get_ml_manager()
            if not await manager.ensure_service(ServiceName.GUI_ACTOR):
                return [TextContent(type="text", text="Failed to start GUI-Actor service. Check logs: docker logs automation-gui-actor")]

            instruction = arguments["instruction"]

            # Take screenshot
            screenshot_bytes = await _browser.page.screenshot()

            # Call GUI-Actor API
            async with httpx.AsyncClient(timeout=60.0) as client:
                files = {"file": ("screenshot.png", screenshot_bytes, "image/png")}
                data = {"instruction": instruction}
                resp = await client.post(f"{GUI_ACTOR_URL}/predict", files=files, data=data)
                resp.raise_for_status()
                result = resp.json()

            x = result["x_pixel"]
            y = result["y_pixel"]

            # Click at the predicted coordinates
            await _browser.page.mouse.click(x, y)

            # Save screenshot with click location marked
            current_url = _browser.page.url
            screenshot_path = _get_screenshot_path(current_url)

            image = Image.open(BytesIO(screenshot_bytes))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(image)
            radius = 10
            draw.ellipse(
                [(x - radius, y - radius), (x + radius, y + radius)],
                outline=(255, 0, 0),
                width=3,
            )
            image.save(screenshot_path)

            await _log_action("natural_language_click", {
                "instruction": instruction,
                "x": x,
                "y": y,
                "screenshot": str(screenshot_path),
                "confidence": result.get("topk_values", [None])[0] if result.get("topk_values") else None,
            })

            return [TextContent(
                type="text",
                text=f"Clicked at ({x}, {y}) for: '{instruction}'\n"
                f"Screenshot with marker: {screenshot_path.name}\n"
                f"Confidence scores: {result.get('topk_values', 'N/A')}\n"
                f"Processing time: {result.get('processing_time_ms', 'N/A')}ms",
            )]

        elif name == "list_sessions":
            if _browser is None:
                from novnc_automation.session import SessionManager
                sm = SessionManager()
            else:
                sm = _browser.session_manager

            sessions = await sm.list_sessions()
            if not sessions:
                return [TextContent(type="text", text="No saved sessions found.")]

            lines = ["Saved sessions:"]
            for s in sessions:
                lines.append(f"  - {s.session_id} (updated: {s.updated_at}, url: {s.url})")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "list_screenshots":
            limit = arguments.get("limit", 20)
            screenshots = sorted(SCREENSHOTS_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            screenshots = screenshots[:limit]

            if not screenshots:
                return [TextContent(type="text", text="No screenshots found.")]

            lines = [f"Screenshots in {SCREENSHOTS_DIR}:"]
            for s in screenshots:
                lines.append(f"  - {s.name}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "get_action_logs":
            limit = arguments.get("limit", 50)
            date = arguments.get("date") or datetime.now().strftime("%Y%m%d")
            source_filter = arguments.get("source")

            all_entries = []

            log_file = LOGS_DIR / f"actions_{date}.jsonl"
            if log_file.exists():
                async with aiofiles.open(log_file) as f:
                    content = await f.read()
                for line in content.strip().split("\n"):
                    if line:
                        try:
                            all_entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            x11_log_file = LOGS_DIR / "x11_events.jsonl"
            if x11_log_file.exists():
                async with aiofiles.open(x11_log_file) as f:
                    content = await f.read()
                for line in content.strip().split("\n"):
                    if line:
                        try:
                            all_entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            if not all_entries:
                return [TextContent(type="text", text=f"No logs found for date: {date}")]

            if source_filter:
                all_entries = [e for e in all_entries if e.get("source") == source_filter]

            all_entries.sort(key=lambda x: x.get("timestamp", ""))
            all_entries = all_entries[-limit:]

            return [TextContent(type="text", text=json.dumps(all_entries, indent=2))]

        # OmniParser tools
        elif name == "omniparser_analyze":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            # Ensure OmniParser service is running
            manager = _get_ml_manager()
            if not await manager.ensure_service(ServiceName.OMNIPARSER):
                return [TextContent(type="text", text="Failed to start OmniParser service. Check logs: docker logs automation-omniparser")]

            # Take screenshot
            screenshot_bytes = await _browser.page.screenshot()

            # Call OmniParser API
            async with httpx.AsyncClient(timeout=120.0) as client:
                files = {"file": ("screenshot.png", screenshot_bytes, "image/png")}
                resp = await client.post(f"{OMNIPARSER_URL}/analyze", files=files)
                resp.raise_for_status()
                result = resp.json()

            _omniparser_result = result

            # Save annotated image
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            image_path = OMNIPARSER_DIR / f"{timestamp}_annotated.png"
            json_path = OMNIPARSER_DIR / f"{timestamp}_elements.json"

            # Decode and save annotated image
            annotated_bytes = base64.b64decode(result["annotated_image_base64"])
            with open(image_path, "wb") as f:
                f.write(annotated_bytes)

            # Save elements JSON
            with open(json_path, "w") as f:
                json.dump({
                    "timestamp": timestamp,
                    "image_width": result["image_width"],
                    "image_height": result["image_height"],
                    "elements": result["elements"],
                }, f, indent=2)

            await _log_action("omniparser_analyze", {
                "element_count": result["element_count"],
                "image_path": str(image_path),
                "json_path": str(json_path),
                "processing_time_ms": result["processing_time_ms"],
            })

            lines = [
                "OmniParser analysis complete!",
                f"Detected {result['element_count']} UI elements.",
                f"Processing time: {result['processing_time_ms']}ms",
                "",
                f"Annotated image: {image_path}",
                f"Elements JSON: {json_path}",
                "",
                "Elements summary:",
            ]
            for el in result["elements"]:
                label = el.get("text") or el.get("description") or "(no label)"
                if len(label) > 50:
                    label = label[:47] + "..."
                lines.append(f"  [{el['id']}] {el['type']}: {label} @ {el['center_pixel']}")

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "omniparser_click":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            if _omniparser_result is None:
                return [TextContent(type="text", text="No OmniParser results. Call omniparser_analyze first.")]

            element_id = arguments["element_id"]
            element = None
            for el in _omniparser_result["elements"]:
                if el["id"] == element_id:
                    element = el
                    break

            if element is None:
                valid_ids = [el["id"] for el in _omniparser_result["elements"]]
                return [TextContent(
                    type="text",
                    text=f"Element ID {element_id} not found. Valid IDs: {valid_ids}",
                )]

            cx, cy = element["center_pixel"]
            await _browser.page.mouse.click(cx, cy)

            await _log_action("omniparser_click", {
                "element_id": element_id,
                "x": cx,
                "y": cy,
                "element_type": element["type"],
                "element_text": element.get("text"),
            })

            label = element.get("text") or element.get("description") or f"element {element_id}"
            return [TextContent(
                type="text",
                text=f"Clicked at ({cx}, {cy}) - {element['type']}: {label}",
            )]

        elif name == "omniparser_get_html":
            if _browser is None:
                return [TextContent(type="text", text="Browser not running. Call browser_start first.")]

            if _omniparser_result is None:
                return [TextContent(type="text", text="No OmniParser results. Call omniparser_analyze first.")]

            element_id = arguments["element_id"]
            outer_html = arguments.get("outer_html", True)

            element = None
            for el in _omniparser_result["elements"]:
                if el["id"] == element_id:
                    element = el
                    break

            if element is None:
                valid_ids = [el["id"] for el in _omniparser_result["elements"]]
                return [TextContent(
                    type="text",
                    text=f"Element ID {element_id} not found. Valid IDs: {valid_ids}",
                )]

            cx, cy = element["center_pixel"]

            html_type = "outerHTML" if outer_html else "innerHTML"
            js_code = f"""
                (() => {{
                    const el = document.elementFromPoint({cx}, {cy});
                    if (!el) return null;
                    return {{
                        tagName: el.tagName.toLowerCase(),
                        id: el.id || null,
                        className: el.className || null,
                        html: el.{html_type},
                    }};
                }})()
            """
            result = await _browser.page.evaluate(js_code)

            if result is None:
                return [TextContent(
                    type="text",
                    text=f"No DOM element found at ({cx}, {cy})",
                )]

            await _log_action("omniparser_get_html", {
                "element_id": element_id,
                "x": cx,
                "y": cy,
                "tag": result["tagName"],
            })

            lines = [
                f"Element at ({cx}, {cy}):",
                f"Tag: {result['tagName']}",
            ]
            if result["id"]:
                lines.append(f"ID: {result['id']}")
            if result["className"]:
                lines.append(f"Class: {result['className']}")
            lines.append("")
            lines.append(f"HTML ({html_type}):")
            lines.append(result["html"])

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "omniparser_list_elements":
            if _omniparser_result is None:
                return [TextContent(type="text", text="No OmniParser results. Call omniparser_analyze first.")]

            lines = [
                f"OmniParser elements ({len(_omniparser_result['elements'])} total):",
                f"Image size: {_omniparser_result['image_width']}x{_omniparser_result['image_height']}",
                "",
            ]

            for el in _omniparser_result["elements"]:
                lines.append(f"[{el['id']}] {el['type']}")
                lines.append(f"    Box: {el['box_2d']}")
                lines.append(f"    Center: {el['center_pixel']}")
                if el.get("text"):
                    lines.append(f"    Text: {el['text']}")
                if el.get("description"):
                    lines.append(f"    Description: {el['description']}")
                lines.append(f"    Confidence: {el['confidence']:.2f}")
                lines.append("")

            return [TextContent(type="text", text="\n".join(lines))]

        # VLM chat tool
        elif name == "vlm_chat":
            # Ensure VLM service is running
            manager = _get_ml_manager()
            if not await manager.ensure_service(ServiceName.VLM):
                return [TextContent(type="text", text="Failed to start VLM service. Check logs: docker logs automation-vlm")]

            max_tokens = arguments.get("max_tokens", 512)

            # Support simple mode (prompt + image_path) or advanced mode (messages array)
            if "messages" in arguments:
                messages = arguments["messages"]
            elif "prompt" in arguments:
                # Simple mode: build messages from prompt and optional image_path
                prompt = arguments["prompt"]
                image_path = arguments.get("image_path")
                if image_path:
                    messages = [{
                        "role": "user",
                        "content": [
                            {"type": "image_path", "image_path": image_path},
                            {"type": "text", "text": prompt},
                        ]
                    }]
                else:
                    messages = [{"role": "user", "content": prompt}]
            else:
                return [TextContent(type="text", text="Error: provide either 'prompt' (simple mode) or 'messages' (advanced mode)")]

            # Convert messages to OpenAI chat format, encoding image paths as base64
            api_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content")

                if isinstance(content, str):
                    # Simple text message
                    api_messages.append({"role": role, "content": content})
                elif isinstance(content, list):
                    # Multi-part message with images
                    parts = []
                    for item in content:
                        item_type = item.get("type")
                        if item_type == "text":
                            parts.append({"type": "text", "text": item.get("text", "")})
                        elif item_type == "image_path":
                            image_path = item.get("image_path")
                            if image_path and Path(image_path).exists():
                                # Read and encode image as base64
                                async with aiofiles.open(image_path, "rb") as f:
                                    image_bytes = await f.read()
                                b64_data = base64.b64encode(image_bytes).decode("utf-8")
                                # Detect mime type from extension
                                ext = Path(image_path).suffix.lower()
                                mime_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
                                mime_type = mime_types.get(ext, "image/png")
                                parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                                })
                            else:
                                parts.append({"type": "text", "text": f"[Image not found: {image_path}]"})
                    api_messages.append({"role": role, "content": parts})
                else:
                    api_messages.append({"role": role, "content": str(content)})

            # Call VLM via OpenAI-compatible API
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{VLM_URL}/v1/chat/completions",
                    json={
                        "model": "Qwen3-VL-4B",
                        "messages": api_messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                        "cache_prompt": False,  # Don't cache KV state between calls
                    },
                )
                resp.raise_for_status()
                result = resp.json()

            # Extract response
            response_text = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})

            await _log_action("vlm_chat", {
                "message_count": len(messages),
                "max_tokens": max_tokens,
                "response_length": len(response_text),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            })

            return [TextContent(
                type="text",
                text=f"{response_text}\n\n---\nTokens: {usage.get('prompt_tokens', '?')} prompt, {usage.get('completion_tokens', '?')} completion",
            )]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        await _log_action("error", {"tool": name, "error": str(e), "arguments": arguments})
        return [TextContent(type="text", text=f"Error: {e}")]


async def run_server():
    """Run the MCP server."""
    # Start the ML service manager's idle monitor
    ml_manager = _get_ml_manager()
    await ml_manager.start()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        # Clean up the ML service manager
        await ml_manager.stop()


def main():
    """Main entry point."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
