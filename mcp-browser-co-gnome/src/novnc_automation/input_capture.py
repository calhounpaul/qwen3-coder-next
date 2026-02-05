"""Input capture at both JS and X11 levels.

JS Level: Injected script captures DOM events (clicks, inputs, keyboard)
X11 Level: pynput captures raw mouse/keyboard events with screenshots
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import aiofiles

# JS script to inject via addInitScript for capturing user interactions
USER_INPUT_CAPTURE_SCRIPT = """
(function() {
    // Avoid re-injection
    if (window.__inputCaptureInstalled) return;
    window.__inputCaptureInstalled = true;

    const getSelector = (el) => {
        if (!el || el === document.body) return 'body';
        if (el.id) return '#' + el.id;
        if (el.className && typeof el.className === 'string') {
            const classes = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
            if (classes) return el.tagName.toLowerCase() + '.' + classes;
        }
        // Try to build a unique path
        let path = el.tagName.toLowerCase();
        if (el.parentElement) {
            const siblings = Array.from(el.parentElement.children).filter(c => c.tagName === el.tagName);
            if (siblings.length > 1) {
                path += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
            }
        }
        return path;
    };

    const getElementInfo = (el) => {
        if (!el) return {};
        return {
            tag: el.tagName?.toLowerCase(),
            id: el.id || null,
            className: el.className || null,
            selector: getSelector(el),
            text: (el.innerText || el.textContent || '').slice(0, 100),
            type: el.type || null,
            name: el.name || null,
            href: el.href || null,
            value: el.type === 'password' ? '[REDACTED]' : (el.value?.slice(0, 50) || null),
        };
    };

    const emit = (eventType, data) => {
        console.log('__USER_INPUT__:' + JSON.stringify({
            type: eventType,
            timestamp: new Date().toISOString(),
            url: window.location.href,
            ...data
        }));
    };

    // Click events
    document.addEventListener('click', (e) => {
        emit('click', {
            x: e.clientX,
            y: e.clientY,
            pageX: e.pageX,
            pageY: e.pageY,
            button: e.button,
            element: getElementInfo(e.target),
        });
    }, true);

    // Input events (debounced)
    let inputTimeout = null;
    document.addEventListener('input', (e) => {
        clearTimeout(inputTimeout);
        inputTimeout = setTimeout(() => {
            emit('input', {
                element: getElementInfo(e.target),
                inputType: e.inputType,
            });
        }, 300);
    }, true);

    // Change events (for selects, checkboxes)
    document.addEventListener('change', (e) => {
        emit('change', {
            element: getElementInfo(e.target),
            checked: e.target.checked,
        });
    }, true);

    // Focus events
    document.addEventListener('focus', (e) => {
        emit('focus', {
            element: getElementInfo(e.target),
        });
    }, true);

    // Submit events
    document.addEventListener('submit', (e) => {
        emit('submit', {
            element: getElementInfo(e.target),
            formAction: e.target.action,
        });
    }, true);

    // Keyboard events (special keys only to avoid logging passwords)
    document.addEventListener('keydown', (e) => {
        const specialKeys = ['Enter', 'Escape', 'Tab', 'Backspace', 'Delete',
                           'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
                           'Home', 'End', 'PageUp', 'PageDown', 'F1', 'F2', 'F3',
                           'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'];
        if (specialKeys.includes(e.key) || e.ctrlKey || e.altKey || e.metaKey) {
            emit('keydown', {
                key: e.key,
                code: e.code,
                ctrlKey: e.ctrlKey,
                altKey: e.altKey,
                shiftKey: e.shiftKey,
                metaKey: e.metaKey,
                element: getElementInfo(e.target),
            });
        }
    }, true);

    // Scroll events (debounced)
    let scrollTimeout = null;
    window.addEventListener('scroll', (e) => {
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(() => {
            emit('scroll', {
                scrollX: window.scrollX,
                scrollY: window.scrollY,
            });
        }, 200);
    }, true);

    // Navigation events
    window.addEventListener('beforeunload', (e) => {
        emit('beforeunload', {
            url: window.location.href,
        });
    });

    // Visibility change
    document.addEventListener('visibilitychange', (e) => {
        emit('visibilitychange', {
            hidden: document.hidden,
        });
    });

    console.log('__USER_INPUT__:' + JSON.stringify({
        type: 'capture_installed',
        timestamp: new Date().toISOString(),
        url: window.location.href,
    }));
})();
"""


class JSInputCapture:
    """Captures user input at the JavaScript/DOM level via Playwright."""

    def __init__(self, log_callback: Callable[[dict], Any] | None = None):
        self.log_callback = log_callback
        self._installed = False

    async def install(self, context) -> None:
        """Install the input capture script on a browser context."""
        if self._installed:
            return

        # Add init script to run on every page
        await context.add_init_script(USER_INPUT_CAPTURE_SCRIPT)
        self._installed = True

    async def attach_to_page(self, page) -> None:
        """Attach console listener to capture events from a page."""
        async def handle_console(msg):
            text = msg.text
            if text.startswith('__USER_INPUT__:'):
                try:
                    data = json.loads(text[15:])  # Skip prefix
                    data['source'] = 'user'
                    data['level'] = 'js'
                    if self.log_callback:
                        await self.log_callback(data)
                except json.JSONDecodeError:
                    pass

        page.on('console', handle_console)

    @staticmethod
    def get_init_script() -> str:
        """Get the JS script for manual injection."""
        return USER_INPUT_CAPTURE_SCRIPT


class X11InputCapture:
    """Captures user input at the X11 level using pynput.

    This runs inside the Docker container where X11/Xvfb is available.
    Records mouse clicks, keyboard events, and takes screenshots.
    """

    def __init__(
        self,
        screenshot_dir: Path | str,
        log_callback: Callable[[dict], Any] | None = None,
        display: str = ":99",
        resolution: tuple[int, int] = (1920, 1080),
    ):
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.log_callback = log_callback
        self.display = display
        self.resolution = resolution
        self._running = False
        self._mouse_listener = None
        self._keyboard_listener = None

    def _get_normalized_coords(self, x: int, y: int) -> tuple[float, float]:
        """Convert pixel coords to normalized 0-1 range."""
        width, height = self.resolution
        return (x / width, y / height)

    def _take_screenshot(self, event_type: str, x: int | None = None, y: int | None = None) -> Path | None:
        """Take a screenshot and return the path."""
        try:
            import os
            os.environ['DISPLAY'] = self.display

            from PIL import ImageGrab

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{timestamp}_{event_type}"
            if x is not None and y is not None:
                filename += f"_{x}_{y}"
            filename += ".png"

            screenshot_path = self.screenshot_dir / filename

            # Capture the screen
            img = ImageGrab.grab(xdisplay=self.display)
            img.save(screenshot_path)

            return screenshot_path
        except Exception as e:
            print(f"Screenshot failed: {e}")
            return None

    def _log_event(self, event_data: dict) -> None:
        """Log an event with all required fields."""
        if self.log_callback:
            # Run callback - handle both sync and async
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.log_callback(event_data))
                else:
                    loop.run_until_complete(self.log_callback(event_data))
            except RuntimeError:
                # No event loop, just call sync if possible
                result = self.log_callback(event_data)
                if asyncio.iscoroutine(result):
                    asyncio.run(result)

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        """Handle mouse click events."""
        if not pressed:  # Only log on press, not release
            return

        norm_x, norm_y = self._get_normalized_coords(x, y)
        screenshot_path = self._take_screenshot("click", x, y)

        event_data = {
            "type": "mouse_click",
            "source": "user",
            "level": "x11",
            "timestamp": datetime.now().isoformat(),
            "pixel_x": x,
            "pixel_y": y,
            "normalized_x": round(norm_x, 6),
            "normalized_y": round(norm_y, 6),
            "button": str(button),
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "resolution": {"width": self.resolution[0], "height": self.resolution[1]},
        }
        self._log_event(event_data)

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        """Handle scroll events."""
        norm_x, norm_y = self._get_normalized_coords(x, y)

        event_data = {
            "type": "mouse_scroll",
            "source": "user",
            "level": "x11",
            "timestamp": datetime.now().isoformat(),
            "pixel_x": x,
            "pixel_y": y,
            "normalized_x": round(norm_x, 6),
            "normalized_y": round(norm_y, 6),
            "scroll_dx": dx,
            "scroll_dy": dy,
        }
        self._log_event(event_data)

    def _on_key_press(self, key) -> None:
        """Handle key press events."""
        try:
            key_str = key.char if hasattr(key, 'char') and key.char else str(key)
        except AttributeError:
            key_str = str(key)

        # Don't log individual characters to avoid capturing passwords
        # Only log special keys
        special_prefixes = ['Key.', 'ctrl', 'alt', 'shift', 'cmd', 'super']
        if not any(key_str.startswith(p) or p in key_str.lower() for p in special_prefixes):
            if len(key_str) == 1:  # Single character, skip
                return

        event_data = {
            "type": "key_press",
            "source": "user",
            "level": "x11",
            "timestamp": datetime.now().isoformat(),
            "key": key_str,
        }
        self._log_event(event_data)

    def start(self) -> None:
        """Start capturing X11 input events."""
        if self._running:
            return

        try:
            from pynput import mouse, keyboard
            import os
            os.environ['DISPLAY'] = self.display

            self._mouse_listener = mouse.Listener(
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
            )

            self._mouse_listener.start()
            self._keyboard_listener.start()
            self._running = True

            print(f"X11 input capture started on {self.display}")
        except ImportError as e:
            print(f"pynput not available: {e}")
        except Exception as e:
            print(f"Failed to start X11 capture: {e}")

    def stop(self) -> None:
        """Stop capturing input events."""
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


async def write_log_entry(log_file: Path, entry: dict) -> None:
    """Write a log entry to a JSONL file."""
    async with aiofiles.open(log_file, "a") as f:
        await f.write(json.dumps(entry) + "\n")
