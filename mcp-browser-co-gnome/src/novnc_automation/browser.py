"""Main browser automation class with Playwright control."""

import uuid
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

try:
    from playwright_stealth import Stealth

    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from novnc_automation.config import Config
from novnc_automation.recording import ActionLogger, RecordingManager
from novnc_automation.session import SessionManager


class AutomationBrowser:
    """High-level browser automation with recording and session support.

    Usage:
        async with AutomationBrowser(session_id="my-session") as browser:
            await browser.goto("https://example.com")
            await browser.click("#login")
            await browser.fill("#username", "user@example.com")
            await browser.screenshot("after_login")

        # Restore a session
        async with AutomationBrowser() as browser:
            await browser.start(restore_session="my-session")
    """

    # Stealth browser args
    STEALTH_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--window-position=0,0",
        "--ignore-certificate-errors",
        "--ignore-certificate-errors-spki-list",
    ]

    def __init__(
        self,
        session_id: str | None = None,
        config: Config | None = None,
        headless: bool | None = None,
        stealth: bool | None = None,
        cdp_endpoint: str | None = None,
    ):
        """Initialize AutomationBrowser.

        Args:
            session_id: Unique session identifier (auto-generated if not provided)
            config: Configuration object
            headless: Override headless setting
            stealth: Override stealth mode setting
            cdp_endpoint: CDP endpoint URL to connect to existing browser (e.g., "http://localhost:9222")
        """
        self.session_id = session_id or str(uuid.uuid4())
        self.config = config or Config.load()
        self.cdp_endpoint = cdp_endpoint

        # Allow overrides
        if headless is not None:
            self.config.browser.headless = headless
        if stealth is not None:
            self.config.browser.stealth_mode = stealth

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        self._session_manager = SessionManager(self.config)
        self._recording_manager = RecordingManager(self.session_id, self.config)

        self._restore_session_id: str | None = None
        self._using_cdp = False

    @property
    def page(self) -> Page:
        """Get the current page."""
        if not self._page:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Get the browser context."""
        if not self._context:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._context

    @property
    def browser(self) -> Browser:
        """Get the browser instance."""
        if not self._browser:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._browser

    async def start(
        self,
        restore_session: str | None = None,
        url: str | None = None,
    ) -> "AutomationBrowser":
        """Start the browser.

        Args:
            restore_session: Session ID to restore
            url: Initial URL to navigate to

        Returns:
            Self for chaining
        """
        self._restore_session_id = restore_session

        # Start Playwright
        self._playwright = await async_playwright().start()

        if self.cdp_endpoint:
            # Connect to existing browser via CDP
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_endpoint)
            self._using_cdp = True

            # Get existing contexts or create new one
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                pages = self._context.pages
                if pages:
                    self._page = pages[0]
                else:
                    self._page = await self._context.new_page()
            else:
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()

            # Start action logging
            await self._recording_manager.start_action_log()

            # Navigate to URL if specified
            target_url = url
            if target_url:
                await self.goto(target_url)

            return self

        # Build browser args
        args = list(self.config.browser.extra_args)
        if self.config.browser.stealth_mode:
            args.extend(self.STEALTH_ARGS)

        # Launch browser
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.browser.headless,
            args=args,
        )

        # Create context with optional session restore
        context_options: dict[str, Any] = {
            "viewport": {
                "width": self.config.browser.viewport_width,
                "height": self.config.browser.viewport_height,
            },
        }

        # Add user agent if specified
        if self.config.browser.user_agent:
            context_options["user_agent"] = self.config.browser.user_agent

        # Add HAR recording if enabled
        har_options = self._recording_manager.get_har_options()
        if har_options:
            context_options.update(har_options)

        # Restore session storage state if requested
        restore_url: str | None = None
        if restore_session:
            storage_path = self._session_manager.get_storage_state_path(restore_session)
            if storage_path:
                context_options["storage_state"] = str(storage_path)

            # Get URL from session state
            session_state = await self._session_manager.load_session_state(restore_session)
            if session_state and session_state.url:
                restore_url = session_state.url

        self._context = await self._browser.new_context(**context_options)

        # Start tracing
        await self._recording_manager.start_tracing(self._context)

        # Start action logging
        await self._recording_manager.start_action_log()

        # Create page
        self._page = await self._context.new_page()

        # Apply stealth patches (only if playwright-stealth is installed)
        if self.config.browser.stealth_mode and HAS_STEALTH:
            await Stealth().apply_stealth_async(self._context)

        # Navigate to URL
        target_url = url or restore_url
        if target_url:
            await self.goto(target_url)

        return self

    async def stop(self, save_session: bool = True) -> None:
        """Stop the browser and save session.

        Args:
            save_session: Whether to save session state
        """
        if save_session and self._context and self._page and not self._using_cdp:
            current_url = self._page.url if self._page.url != "about:blank" else None
            await self._session_manager.save_session(
                self.session_id,
                self._context,
                url=current_url,
            )

            # Update session with recording paths
            recording_paths = self._recording_manager.get_recording_paths()
            await self._session_manager.update_session_metadata(
                self.session_id,
                trace_path=str(recording_paths.get("trace")) if recording_paths.get("trace") else None,
                har_path=str(recording_paths.get("har")) if recording_paths.get("har") else None,
                action_log_path=str(recording_paths.get("actions")) if recording_paths.get("actions") else None,
            )

        # Stop tracing (only if not using CDP)
        if self._context and not self._using_cdp:
            await self._recording_manager.stop_tracing(self._context)

        # Close browser (don't close shared browser when using CDP)
        if not self._using_cdp:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()

        if self._playwright:
            await self._playwright.stop()

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._using_cdp = False

    async def __aenter__(self) -> "AutomationBrowser":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop(save_session=exc_type is None)

    # Navigation methods

    async def goto(
        self,
        url: str,
        wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
    ) -> None:
        """Navigate to a URL.

        Args:
            url: URL to navigate to
            wait_until: When to consider navigation complete
        """
        async with ActionLogger(self._recording_manager, "goto", url=url):
            await self.page.goto(url, wait_until=wait_until)

    async def reload(self) -> None:
        """Reload the current page."""
        async with ActionLogger(self._recording_manager, "reload", url=self.page.url):
            await self.page.reload()

    async def go_back(self) -> None:
        """Go back in history."""
        async with ActionLogger(self._recording_manager, "go_back"):
            await self.page.go_back()

    async def go_forward(self) -> None:
        """Go forward in history."""
        async with ActionLogger(self._recording_manager, "go_forward"):
            await self.page.go_forward()

    # Interaction methods

    async def click(
        self,
        selector: str,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
        timeout: float | None = None,
    ) -> None:
        """Click an element.

        Args:
            selector: Element selector
            button: Mouse button
            click_count: Number of clicks
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "click", selector=selector):
            await self.page.click(
                selector,
                button=button,
                click_count=click_count,
                timeout=timeout,
            )

    async def fill(
        self,
        selector: str,
        value: str,
        timeout: float | None = None,
    ) -> None:
        """Fill an input field.

        Args:
            selector: Input selector
            value: Value to fill
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "fill", selector=selector, value=value):
            await self.page.fill(selector, value, timeout=timeout)

    async def type(
        self,
        selector: str,
        text: str,
        delay: float = 0,
        timeout: float | None = None,
    ) -> None:
        """Type text into an element (with key-by-key simulation).

        Args:
            selector: Element selector
            text: Text to type
            delay: Delay between keystrokes in milliseconds
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "type", selector=selector, value=text):
            await self.page.type(selector, text, delay=delay, timeout=timeout)

    async def press(
        self,
        selector: str,
        key: str,
        timeout: float | None = None,
    ) -> None:
        """Press a key on an element.

        Args:
            selector: Element selector
            key: Key to press (e.g., 'Enter', 'Tab')
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "press", selector=selector, value=key):
            await self.page.press(selector, key, timeout=timeout)

    async def select_option(
        self,
        selector: str,
        value: str | list[str] | None = None,
        label: str | list[str] | None = None,
        index: int | list[int] | None = None,
        timeout: float | None = None,
    ) -> list[str]:
        """Select option(s) in a <select> element.

        Args:
            selector: Select element selector
            value: Value(s) to select
            label: Label(s) to select
            index: Index(es) to select
            timeout: Timeout in milliseconds

        Returns:
            List of selected values
        """
        async with ActionLogger(
            self._recording_manager,
            "select_option",
            selector=selector,
            value=str(value or label or index),
        ):
            return await self.page.select_option(
                selector,
                value=value,
                label=label,
                index=index,
                timeout=timeout,
            )

    async def check(self, selector: str, timeout: float | None = None) -> None:
        """Check a checkbox.

        Args:
            selector: Checkbox selector
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "check", selector=selector):
            await self.page.check(selector, timeout=timeout)

    async def uncheck(self, selector: str, timeout: float | None = None) -> None:
        """Uncheck a checkbox.

        Args:
            selector: Checkbox selector
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "uncheck", selector=selector):
            await self.page.uncheck(selector, timeout=timeout)

    async def hover(self, selector: str, timeout: float | None = None) -> None:
        """Hover over an element.

        Args:
            selector: Element selector
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "hover", selector=selector):
            await self.page.hover(selector, timeout=timeout)

    # Waiting methods

    async def wait_for_selector(
        self,
        selector: str,
        state: Literal["attached", "detached", "visible", "hidden"] = "visible",
        timeout: float | None = None,
    ) -> None:
        """Wait for an element to reach a state.

        Args:
            selector: Element selector
            state: State to wait for
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(
            self._recording_manager,
            "wait_for_selector",
            selector=selector,
            value=state,
        ):
            await self.page.wait_for_selector(selector, state=state, timeout=timeout)

    async def wait_for_load_state(
        self,
        state: Literal["domcontentloaded", "load", "networkidle"] = "load",
        timeout: float | None = None,
    ) -> None:
        """Wait for page load state.

        Args:
            state: Load state to wait for
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "wait_for_load_state", value=state):
            await self.page.wait_for_load_state(state, timeout=timeout)

    async def wait_for_url(
        self,
        url: str,
        timeout: float | None = None,
    ) -> None:
        """Wait for URL to match pattern.

        Args:
            url: URL pattern (string, regex, or predicate)
            timeout: Timeout in milliseconds
        """
        async with ActionLogger(self._recording_manager, "wait_for_url", url=url):
            await self.page.wait_for_url(url, timeout=timeout)

    # Content extraction

    async def get_text(self, selector: str, timeout: float | None = None) -> str:
        """Get text content of an element.

        Args:
            selector: Element selector
            timeout: Timeout in milliseconds

        Returns:
            Text content
        """
        async with ActionLogger(self._recording_manager, "get_text", selector=selector):
            return await self.page.text_content(selector, timeout=timeout) or ""

    async def get_attribute(
        self,
        selector: str,
        name: str,
        timeout: float | None = None,
    ) -> str | None:
        """Get attribute value of an element.

        Args:
            selector: Element selector
            name: Attribute name
            timeout: Timeout in milliseconds

        Returns:
            Attribute value or None
        """
        async with ActionLogger(
            self._recording_manager,
            "get_attribute",
            selector=selector,
            value=name,
        ):
            return await self.page.get_attribute(selector, name, timeout=timeout)

    async def get_inner_html(self, selector: str, timeout: float | None = None) -> str:
        """Get inner HTML of an element.

        Args:
            selector: Element selector
            timeout: Timeout in milliseconds

        Returns:
            Inner HTML
        """
        async with ActionLogger(self._recording_manager, "get_inner_html", selector=selector):
            return await self.page.inner_html(selector, timeout=timeout)

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        """Evaluate JavaScript in the page.

        Args:
            expression: JavaScript expression
            arg: Argument to pass to the expression

        Returns:
            Result of evaluation
        """
        async with ActionLogger(self._recording_manager, "evaluate", value=expression[:100]):
            return await self.page.evaluate(expression, arg)

    # Screenshots

    async def screenshot(
        self,
        name: str | None = None,
        full_page: bool = False,
        selector: str | None = None,
    ) -> Path:
        """Take a screenshot.

        Args:
            name: Screenshot name (defaults to timestamp)
            full_page: Capture full page
            selector: Element selector to screenshot

        Returns:
            Path to screenshot file
        """
        import time

        name = name or f"screenshot_{int(time.time() * 1000)}"
        screenshots_dir = self.config.recording.recordings_dir / "screenshots" / self.session_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        path = screenshots_dir / f"{name}.png"

        async with ActionLogger(
            self._recording_manager,
            "screenshot",
            selector=selector,
            screenshot_path=str(path),
        ):
            if selector:
                element = await self.page.query_selector(selector)
                if element:
                    await element.screenshot(path=str(path))
                else:
                    raise ValueError(f"Element not found: {selector}")
            else:
                await self.page.screenshot(path=str(path), full_page=full_page)

        return path

    # Session management

    async def save_session(self, session_id: str | None = None) -> None:
        """Manually save the current session.

        Args:
            session_id: Override session ID
        """
        sid = session_id or self.session_id
        current_url = self.page.url if self.page.url != "about:blank" else None

        await self._session_manager.save_session(
            sid,
            self.context,
            url=current_url,
        )

    async def list_sessions(self):
        """List all saved sessions."""
        return await self._session_manager.list_sessions()

    # Recording access

    @property
    def recording_manager(self) -> RecordingManager:
        """Get the recording manager."""
        return self._recording_manager

    @property
    def session_manager(self) -> SessionManager:
        """Get the session manager."""
        return self._session_manager
