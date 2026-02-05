"""ML Service Manager for on-demand service lifecycle management.

Manages OmniParser, GUI-Actor, and VLM services with:
- On-demand startup when tools are called
- Automatic shutdown after idle timeout
- Mutual exclusion (only one heavy ML service at a time on GPU 1)
"""

import asyncio
import os
import subprocess
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable

import httpx


class ServiceName(Enum):
    """ML service identifiers."""
    OMNIPARSER = "omniparser"
    GUI_ACTOR = "gui-actor"
    VLM = "vlm"


# Service configurations
SERVICE_CONFIG = {
    ServiceName.OMNIPARSER: {
        "port": 8010,
        "health_endpoint": "/health",
        "health_key": "status",
        "health_value": "healthy",
        "startup_timeout": 300,  # 5 minutes
        "compose_service": "omniparser",
        "compose_profile": "ml",
    },
    ServiceName.GUI_ACTOR: {
        "port": 8001,
        "health_endpoint": "/health",
        "health_key": "status",
        "health_value": "healthy",
        "startup_timeout": 360,  # 6 minutes
        "compose_service": "gui-actor",
        "compose_profile": "ml",
    },
    ServiceName.VLM: {
        "port": 8004,
        "health_endpoint": "/health",
        "health_key": "status",
        "health_value": "ok",
        "startup_timeout": 180,  # 3 minutes
        "compose_service": "vlm",
        "compose_profile": "vlm",
    },
}


class MLServiceManager:
    """Manages ML service lifecycle with on-demand startup and idle timeout."""

    def __init__(
        self,
        compose_dir: Path | None = None,
        idle_timeout: int = 300,
        always_on: list[str] | None = None,
        on_status_change: Callable[[ServiceName, str], None] | None = None,
    ):
        """Initialize the ML service manager.

        Args:
            compose_dir: Directory containing docker-compose.yml
            idle_timeout: Seconds before idle services are stopped (default: 300)
            always_on: Service names that should never be auto-stopped
            on_status_change: Callback for status changes (for logging)
        """
        if compose_dir is None:
            compose_dir = Path(__file__).parent.parent.parent
        self.compose_dir = compose_dir

        # Load config from environment
        self.idle_timeout = int(os.getenv("ML_IDLE_TIMEOUT", str(idle_timeout)))
        always_on_env = os.getenv("ML_ALWAYS_ON", "")
        self.always_on = set(always_on or [])
        if always_on_env:
            self.always_on.update(s.strip() for s in always_on_env.split(",") if s.strip())

        self.on_status_change = on_status_change

        # State tracking
        self._lock = asyncio.Lock()
        self._last_used: dict[ServiceName, datetime] = {}
        self._active_service: ServiceName | None = None
        self._monitor_task: asyncio.Task | None = None
        self._shutting_down = False

    async def start(self) -> None:
        """Start the background idle monitor."""
        if self._monitor_task is None or self._monitor_task.done():
            self._shutting_down = False
            self._monitor_task = asyncio.create_task(self._idle_monitor())

    async def stop(self) -> None:
        """Stop the background idle monitor and all services."""
        self._shutting_down = True
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def ensure_service(self, service: ServiceName) -> bool:
        """Ensure a service is running and healthy.

        If another heavy ML service is running, it will be stopped first.

        Args:
            service: The service to ensure is running

        Returns:
            True if service is healthy, False if startup failed
        """
        async with self._lock:
            config = SERVICE_CONFIG[service]

            # ALWAYS stop any other running ML services (they share GPU 1)
            # Check ALL services, not just _active_service, in case services
            # were started externally (e.g., via local-cc.sh --ml flag)
            for other_service in ServiceName:
                if other_service != service:
                    if await self._check_health(other_service):
                        self._log_status(other_service, "stopping")
                        await self._stop_service(other_service)

            # Check if already healthy (after stopping others)
            if await self._check_health(service):
                self._last_used[service] = datetime.now()
                self._active_service = service
                return True

            self._active_service = None

            # Start the requested service
            self._log_status(service, "starting")
            success = await self._start_service(service)
            if success:
                # Wait for health
                healthy = await self._wait_for_health(service, config["startup_timeout"])
                if healthy:
                    self._last_used[service] = datetime.now()
                    self._active_service = service
                    self._log_status(service, "ready")
                    return True
                else:
                    self._log_status(service, "startup_timeout")
                    return False
            else:
                self._log_status(service, "start_failed")
                return False

    async def is_healthy(self, service: ServiceName) -> bool:
        """Check if a service is healthy without starting it."""
        return await self._check_health(service)

    async def get_status(self) -> dict[str, any]:
        """Get status of all ML services."""
        status = {}
        for service in ServiceName:
            healthy = await self._check_health(service)
            last_used = self._last_used.get(service)
            status[service.value] = {
                "healthy": healthy,
                "last_used": last_used.isoformat() if last_used else None,
                "active": self._active_service == service,
                "always_on": service.value in self.always_on,
            }
        return status

    async def _check_health(self, service: ServiceName) -> bool:
        """Check if a service is healthy."""
        config = SERVICE_CONFIG[service]
        url = f"http://localhost:{config['port']}{config['health_endpoint']}"

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get(config["health_key"]) == config["health_value"]
        except Exception:
            pass
        return False

    async def _wait_for_health(self, service: ServiceName, timeout: int) -> bool:
        """Wait for a service to become healthy."""
        config = SERVICE_CONFIG[service]
        start_time = datetime.now()
        poll_interval = 2  # seconds

        while (datetime.now() - start_time).total_seconds() < timeout:
            if await self._check_health(service):
                return True
            await asyncio.sleep(poll_interval)

        return False

    async def _start_service(self, service: ServiceName) -> bool:
        """Start a service via docker compose."""
        config = SERVICE_CONFIG[service]
        compose_service = config["compose_service"]
        compose_profile = config["compose_profile"]

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose",
                "--profile", compose_profile,
                "up", "-d", compose_service,
                cwd=self.compose_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            return False

    async def _stop_service(self, service: ServiceName) -> bool:
        """Stop a service via docker compose."""
        config = SERVICE_CONFIG[service]
        compose_service = config["compose_service"]
        compose_profile = config["compose_profile"]

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose",
                "--profile", compose_profile,
                "stop", compose_service,
                cwd=self.compose_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            return False

    async def _idle_monitor(self) -> None:
        """Background task to stop idle services."""
        check_interval = 30  # seconds

        while not self._shutting_down:
            try:
                await asyncio.sleep(check_interval)

                async with self._lock:
                    now = datetime.now()
                    timeout_delta = timedelta(seconds=self.idle_timeout)

                    for service, last_used in list(self._last_used.items()):
                        # Skip always-on services
                        if service.value in self.always_on:
                            continue

                        # Check if service is idle
                        if now - last_used > timeout_delta:
                            if await self._check_health(service):
                                self._log_status(service, "idle_stopping")
                                await self._stop_service(service)
                                if self._active_service == service:
                                    self._active_service = None
                                del self._last_used[service]

            except asyncio.CancelledError:
                break
            except Exception:
                # Log error but keep running
                pass

    def _log_status(self, service: ServiceName, status: str) -> None:
        """Log a status change."""
        if self.on_status_change:
            self.on_status_change(service, status)


# Global instance (lazy initialization)
_manager: MLServiceManager | None = None


def get_ml_manager() -> MLServiceManager:
    """Get or create the global ML service manager."""
    global _manager
    if _manager is None:
        _manager = MLServiceManager()
    return _manager


async def init_ml_manager(
    compose_dir: Path | None = None,
    idle_timeout: int = 300,
    always_on: list[str] | None = None,
) -> MLServiceManager:
    """Initialize and start the global ML service manager."""
    global _manager
    _manager = MLServiceManager(
        compose_dir=compose_dir,
        idle_timeout=idle_timeout,
        always_on=always_on,
    )
    await _manager.start()
    return _manager


async def shutdown_ml_manager() -> None:
    """Shutdown the global ML service manager."""
    global _manager
    if _manager:
        await _manager.stop()
        _manager = None
