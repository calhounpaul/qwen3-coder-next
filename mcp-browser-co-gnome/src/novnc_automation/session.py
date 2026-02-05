"""Session management for browser state persistence."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

from novnc_automation.config import Config
from novnc_automation.models.session_state import SessionState

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext


class SessionManager:
    """Manages browser session state persistence."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        self.sessions_dir = self.config.recording.sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        """Get the directory for a specific session."""
        return self.sessions_dir / session_id

    def _state_file(self, session_id: str) -> Path:
        """Get the state file path for a session."""
        return self._session_dir(session_id) / "session_state.json"

    def _storage_state_file(self, session_id: str) -> Path:
        """Get the storage state file path for a session."""
        return self._session_dir(session_id) / "storage_state.json"

    async def save_session(
        self,
        session_id: str,
        context: "BrowserContext",
        url: str | None = None,
        metadata: dict | None = None,
    ) -> SessionState:
        """Save browser session state.

        Args:
            session_id: Unique identifier for the session
            context: Playwright browser context
            url: Current URL (optional)
            metadata: Additional metadata to store

        Returns:
            SessionState object representing the saved state
        """
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Save Playwright storage state (cookies, localStorage)
        storage_state_path = self._storage_state_file(session_id)
        await context.storage_state(path=str(storage_state_path))

        # Create or update session state
        state = SessionState(
            session_id=session_id,
            url=url,
            storage_state_path=str(storage_state_path),
            metadata=metadata or {},
        )

        # Check if session already exists to preserve created_at
        existing_state = await self.load_session_state(session_id)
        if existing_state:
            state.created_at = existing_state.created_at

        state.touch()

        # Save session state
        async with aiofiles.open(self._state_file(session_id), "w") as f:
            await f.write(state.model_dump_json(indent=2))

        return state

    async def load_session_state(self, session_id: str) -> SessionState | None:
        """Load session state metadata.

        Args:
            session_id: Session identifier to load

        Returns:
            SessionState if found, None otherwise
        """
        state_file = self._state_file(session_id)
        if not state_file.exists():
            return None

        async with aiofiles.open(state_file) as f:
            content = await f.read()
            return SessionState.model_validate_json(content)

    async def get_storage_state(self, session_id: str) -> dict | None:
        """Get the Playwright storage state for a session.

        Args:
            session_id: Session identifier

        Returns:
            Storage state dict if found, None otherwise
        """
        storage_file = self._storage_state_file(session_id)
        if not storage_file.exists():
            return None

        async with aiofiles.open(storage_file) as f:
            content = await f.read()
            return json.loads(content)

    def get_storage_state_path(self, session_id: str) -> Path | None:
        """Get path to storage state file if it exists.

        Args:
            session_id: Session identifier

        Returns:
            Path to storage state file if exists, None otherwise
        """
        path = self._storage_state_file(session_id)
        return path if path.exists() else None

    async def list_sessions(self) -> list[SessionState]:
        """List all saved sessions.

        Returns:
            List of SessionState objects for all saved sessions
        """
        sessions = []
        if not self.sessions_dir.exists():
            return sessions

        for session_dir in self.sessions_dir.iterdir():
            if session_dir.is_dir():
                state = await self.load_session_state(session_dir.name)
                if state:
                    sessions.append(state)

        # Sort by updated_at descending
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    async def delete_session(self, session_id: str) -> bool:
        """Delete a saved session.

        Args:
            session_id: Session identifier to delete

        Returns:
            True if session was deleted, False if not found
        """
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return False

        import shutil

        shutil.rmtree(session_dir)
        return True

    async def update_session_metadata(
        self,
        session_id: str,
        **kwargs,
    ) -> SessionState | None:
        """Update session metadata fields.

        Args:
            session_id: Session identifier
            **kwargs: Fields to update (url, trace_path, har_path, etc.)

        Returns:
            Updated SessionState if found, None otherwise
        """
        state = await self.load_session_state(session_id)
        if not state:
            return None

        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)

        state.touch()

        async with aiofiles.open(self._state_file(session_id), "w") as f:
            await f.write(state.model_dump_json(indent=2))

        return state
