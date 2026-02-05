#!/usr/bin/env python3
"""X11 Input Capture Service.

Runs inside the Docker container to capture mouse/keyboard events
and take screenshots at the X11 level.

Events are written to /app/recordings/actions/x11_events.jsonl
Screenshots are saved to /app/recordings/x11_screenshots/
"""

import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

# Set display before importing pynput
os.environ.setdefault("DISPLAY", ":99")

from pynput import mouse, keyboard

# Configuration
DISPLAY = os.environ.get("DISPLAY", ":99")
RESOLUTION = os.environ.get("RESOLUTION", "1920x1080x24")
SCREENSHOT_DIR = Path("/app/tmp/x11_screenshots")
LOG_FILE = Path("/app/tmp/logs/x11_events.jsonl")

# Parse resolution
res_parts = RESOLUTION.split("x")
WIDTH = int(res_parts[0])
HEIGHT = int(res_parts[1])

# Ensure directories exist
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Thread-safe logging
log_lock = Lock()


def log_event(event_data: dict) -> None:
    """Write an event to the log file."""
    with log_lock:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(event_data) + "\n")
        # Also print for debugging
        print(f"[X11] {event_data.get('action')}: {event_data}", flush=True)


def take_screenshot(event_type: str, x: int | None = None, y: int | None = None) -> str | None:
    """Take a screenshot and return the path."""
    try:
        from PIL import ImageGrab

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"{timestamp}_{event_type}"
        if x is not None and y is not None:
            filename += f"_{x}_{y}"
        filename += ".png"

        screenshot_path = SCREENSHOT_DIR / filename

        # Capture using PIL with X11
        img = ImageGrab.grab(xdisplay=DISPLAY)
        img.save(screenshot_path)

        return str(screenshot_path)
    except Exception as e:
        print(f"Screenshot failed: {e}", flush=True)
        return None


def get_normalized_coords(x: int, y: int) -> tuple[float, float]:
    """Convert pixel coords to normalized 0-1 range."""
    return (round(x / WIDTH, 6), round(y / HEIGHT, 6))


def on_click(x: int, y: int, button, pressed: bool) -> None:
    """Handle mouse click events."""
    if not pressed:  # Only log on press
        return

    norm_x, norm_y = get_normalized_coords(x, y)
    screenshot_path = take_screenshot("click", x, y)

    log_event({
        "timestamp": datetime.now().isoformat(),
        "source": "user",
        "level": "x11",
        "action": "mouse_click",
        "pixel_x": x,
        "pixel_y": y,
        "normalized_x": norm_x,
        "normalized_y": norm_y,
        "button": str(button),
        "screenshot": screenshot_path,
        "resolution": {"width": WIDTH, "height": HEIGHT},
    })


def on_scroll(x: int, y: int, dx: int, dy: int) -> None:
    """Handle scroll events."""
    norm_x, norm_y = get_normalized_coords(x, y)

    log_event({
        "timestamp": datetime.now().isoformat(),
        "source": "user",
        "level": "x11",
        "action": "mouse_scroll",
        "pixel_x": x,
        "pixel_y": y,
        "normalized_x": norm_x,
        "normalized_y": norm_y,
        "scroll_dx": dx,
        "scroll_dy": dy,
    })


def on_key_press(key) -> None:
    """Handle key press events."""
    try:
        key_str = key.char if hasattr(key, "char") and key.char else str(key)
    except AttributeError:
        key_str = str(key)

    # Only log special keys to avoid capturing passwords
    is_special = key_str.startswith("Key.") or len(key_str) > 1

    if is_special:
        log_event({
            "timestamp": datetime.now().isoformat(),
            "source": "user",
            "level": "x11",
            "action": "key_press",
            "key": key_str,
        })


def main():
    print(f"Starting X11 input capture on {DISPLAY}", flush=True)
    print(f"Resolution: {WIDTH}x{HEIGHT}", flush=True)
    print(f"Screenshots: {SCREENSHOT_DIR}", flush=True)
    print(f"Log file: {LOG_FILE}", flush=True)

    # Log service start
    log_event({
        "timestamp": datetime.now().isoformat(),
        "source": "system",
        "level": "x11",
        "action": "capture_started",
        "display": DISPLAY,
        "resolution": {"width": WIDTH, "height": HEIGHT},
    })

    # Create listeners
    mouse_listener = mouse.Listener(
        on_click=on_click,
        on_scroll=on_scroll,
    )
    keyboard_listener = keyboard.Listener(
        on_press=on_key_press,
    )

    # Handle graceful shutdown
    def shutdown(signum, frame):
        print("Shutting down X11 input capture...", flush=True)
        mouse_listener.stop()
        keyboard_listener.stop()
        log_event({
            "timestamp": datetime.now().isoformat(),
            "source": "system",
            "level": "x11",
            "action": "capture_stopped",
        })
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Start listeners
    mouse_listener.start()
    keyboard_listener.start()

    print("X11 input capture running. Press Ctrl+C to stop.", flush=True)

    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
