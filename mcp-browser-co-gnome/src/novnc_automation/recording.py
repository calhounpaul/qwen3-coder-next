"""Recording management for video, trace, HAR, and action logs."""

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

from novnc_automation.config import Config
from novnc_automation.models.session_state import ActionEntry, ActionLog

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


# Global action log directory - timestamped logs that persist across sessions
GLOBAL_ACTION_LOG_DIR = Path("tmp/data")


def get_global_action_log_path() -> Path:
    """Get the path for today's global action log file."""
    GLOBAL_ACTION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    return GLOBAL_ACTION_LOG_DIR / f"actions_{timestamp}.jsonl"


async def log_global_action(
    action: str,
    selector: str | None = None,
    value: str | None = None,
    url: str | None = None,
    session_id: str | None = None,
    duration_ms: float | None = None,
    success: bool = True,
    error: str | None = None,
    **metadata,
) -> None:
    """Log an action to the global timestamped action log.

    This logs all browser actions (clicks, fills, keypresses, etc.) to a
    daily JSONL file in tmp/data/ for debugging and replay purposes.

    Args:
        action: Action type (e.g., 'click', 'fill', 'goto', 'keypress')
        selector: CSS/XPath selector if applicable
        value: Value used (e.g., text filled, key pressed)
        url: URL if applicable
        session_id: Session ID for correlation
        duration_ms: Action duration in milliseconds
        success: Whether action succeeded
        error: Error message if failed
        **metadata: Additional metadata
    """
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "selector": selector,
        "value": value,
        "url": url,
        "session_id": session_id,
        "duration_ms": duration_ms,
        "success": success,
        "error": error,
        **metadata,
    }
    # Remove None values for cleaner logs
    entry = {k: v for k, v in entry.items() if v is not None}

    log_path = get_global_action_log_path()
    async with aiofiles.open(log_path, "a") as f:
        await f.write(json.dumps(entry) + "\n")


class RecordingManager:
    """Manages recording of browser sessions (video, trace, HAR, actions)."""

    def __init__(self, session_id: str, config: Config | None = None):
        self.session_id = session_id
        self.config = config or Config.load()
        self.recordings_dir = self.config.recording.recordings_dir

        # Ensure directories exist
        self._traces_dir.mkdir(parents=True, exist_ok=True)
        self._har_dir.mkdir(parents=True, exist_ok=True)
        self._actions_dir.mkdir(parents=True, exist_ok=True)
        self._videos_dir.mkdir(parents=True, exist_ok=True)

        self._action_log: ActionLog | None = None
        self._tracing_started = False
        self._har_path: Path | None = None

    @property
    def _traces_dir(self) -> Path:
        return self.recordings_dir / "traces"

    @property
    def _har_dir(self) -> Path:
        return self.recordings_dir / "har"

    @property
    def _actions_dir(self) -> Path:
        return self.recordings_dir / "actions"

    @property
    def _videos_dir(self) -> Path:
        return self.recordings_dir / "videos"

    @property
    def trace_path(self) -> Path:
        """Path to the trace file for this session."""
        return self._traces_dir / f"{self.session_id}.zip"

    @property
    def har_path(self) -> Path:
        """Path to the HAR file for this session."""
        return self._har_dir / f"{self.session_id}.har"

    @property
    def actions_path(self) -> Path:
        """Path to the action log file for this session."""
        return self._actions_dir / f"{self.session_id}.jsonl"

    @property
    def video_path(self) -> Path:
        """Path to the video file for this session."""
        return self._videos_dir / f"{self.session_id}.mp4"

    async def start_tracing(self, context: "BrowserContext") -> None:
        """Start Playwright tracing.

        Args:
            context: Playwright browser context
        """
        if not self.config.recording.record_trace:
            return

        if self._tracing_started:
            return

        await context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True,
        )
        self._tracing_started = True

    async def stop_tracing(self, context: "BrowserContext") -> Path | None:
        """Stop Playwright tracing and save trace file.

        Args:
            context: Playwright browser context

        Returns:
            Path to trace file if tracing was active
        """
        if not self._tracing_started:
            return None

        await context.tracing.stop(path=str(self.trace_path))
        self._tracing_started = False
        return self.trace_path

    async def start_har_recording(self, page: "Page") -> None:
        """Start HAR recording for a page.

        Note: HAR recording is configured when creating the context,
        this method is for documentation purposes.

        Args:
            page: Playwright page
        """
        # HAR recording is set up via context.new_page() with record_har_path
        # This method exists for API consistency
        self._har_path = self.har_path

    def get_har_options(self) -> dict | None:
        """Get HAR recording options for context creation.

        Returns:
            Dict with HAR options if recording enabled, None otherwise
        """
        if not self.config.recording.record_har:
            return None

        return {
            "record_har_path": str(self.har_path),
            "record_har_content": "embed",
        }

    async def start_action_log(self) -> None:
        """Initialize the action log for this session."""
        if not self.config.recording.record_actions:
            return

        self._action_log = ActionLog(session_id=self.session_id)

    async def log_action(
        self,
        action: str,
        selector: str | None = None,
        value: str | None = None,
        url: str | None = None,
        screenshot_path: str | None = None,
        duration_ms: float | None = None,
        success: bool = True,
        error: str | None = None,
        **metadata,
    ) -> None:
        """Log a browser action.

        Actions are always logged to the global timestamped log (tmp/data/).
        If session action logging is enabled, also logs to per-session file.

        Args:
            action: Action type (e.g., 'click', 'fill', 'goto')
            selector: CSS/XPath selector if applicable
            value: Value used (e.g., text filled)
            url: URL if applicable
            screenshot_path: Path to screenshot if taken
            duration_ms: Action duration in milliseconds
            success: Whether action succeeded
            error: Error message if failed
            **metadata: Additional metadata
        """
        # Always log to global timestamped action log (tmp/data/)
        await log_global_action(
            action=action,
            selector=selector,
            value=value,
            url=url,
            session_id=self.session_id,
            duration_ms=duration_ms,
            success=success,
            error=error,
            screenshot_path=screenshot_path,
            **metadata,
        )

        # Also log to per-session file if enabled
        if self._action_log:
            entry = ActionEntry(
                action=action,
                selector=selector,
                value=value,
                url=url,
                screenshot_path=screenshot_path,
                duration_ms=duration_ms,
                success=success,
                error=error,
                metadata=metadata,
            )
            self._action_log.add_action(entry)

            # Append to session-specific JSONL file
            async with aiofiles.open(self.actions_path, "a") as f:
                await f.write(entry.model_dump_json() + "\n")

    async def get_action_log(self) -> ActionLog | None:
        """Get the current action log.

        Returns:
            ActionLog for this session
        """
        return self._action_log

    async def load_action_log(self) -> ActionLog | None:
        """Load action log from file.

        Returns:
            ActionLog if file exists, None otherwise
        """
        if not self.actions_path.exists():
            return None

        action_log = ActionLog(session_id=self.session_id)

        async with aiofiles.open(self.actions_path) as f:
            async for line in f:
                line = line.strip()
                if line:
                    entry = ActionEntry.model_validate_json(line)
                    action_log.add_action(entry)

        return action_log

    def get_recording_paths(self) -> dict[str, Path | None]:
        """Get all recording file paths.

        Returns:
            Dict mapping recording type to path (None if not exists)
        """
        return {
            "trace": self.trace_path if self.trace_path.exists() else None,
            "har": self.har_path if self.har_path.exists() else None,
            "actions": self.actions_path if self.actions_path.exists() else None,
            "video": self.video_path if self.video_path.exists() else None,
        }


class ActionLogger:
    """Context manager for timing and logging actions."""

    def __init__(
        self,
        recording_manager: RecordingManager,
        action: str,
        selector: str | None = None,
        value: str | None = None,
        url: str | None = None,
        **metadata,
    ):
        self.recording_manager = recording_manager
        self.action = action
        self.selector = selector
        self.value = value
        self.url = url
        self.metadata = metadata
        self._start_time: datetime | None = None
        self._success = True
        self._error: str | None = None

    async def __aenter__(self) -> "ActionLogger":
        self._start_time = datetime.utcnow()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        duration_ms = None
        if self._start_time:
            duration_ms = (datetime.utcnow() - self._start_time).total_seconds() * 1000

        if exc_val:
            self._success = False
            self._error = str(exc_val)

        await self.recording_manager.log_action(
            action=self.action,
            selector=self.selector,
            value=self.value,
            url=self.url,
            duration_ms=duration_ms,
            success=self._success,
            error=self._error,
            **self.metadata,
        )

    def set_error(self, error: str) -> None:
        """Mark this action as failed with an error message."""
        self._success = False
        self._error = error
