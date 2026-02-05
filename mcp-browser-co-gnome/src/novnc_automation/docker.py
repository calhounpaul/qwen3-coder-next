"""Docker Compose orchestration for the noVNC automation suite."""

import asyncio
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from novnc_automation.tunnel import TunnelManager


@dataclass
class PortConfig:
    """Port configuration for Docker services."""

    novnc: int = 6080
    vnc: int = 5900
    cdp: int = 9222  # Chrome DevTools Protocol


@dataclass
class DockerStatus:
    """Status of Docker Compose services."""

    browser_running: bool = False
    browser_healthy: bool = False
    tunnel_running: bool = False
    video_running: bool = False
    tunnel_url: str | None = None
    novnc_url: str = "http://localhost:6080"
    vnc_password: str = "secret"
    ports: PortConfig | None = None


class DockerOrchestrator:
    """Manages Docker Compose services for the automation suite."""

    def __init__(
        self,
        compose_dir: str | Path | None = None,
        ports: PortConfig | None = None,
    ):
        """Initialize the orchestrator.

        Args:
            compose_dir: Directory containing docker-compose.yml.
                        Defaults to the package root.
            ports: Custom port configuration. Defaults to standard ports.
        """
        if compose_dir is None:
            # Default to the repo root (parent of src/)
            compose_dir = Path(__file__).parent.parent.parent
        self.compose_dir = Path(compose_dir)
        self.ports = ports or PortConfig()
        self._tunnel_url: str | None = None

    def _run_compose(
        self,
        *args: str,
        check: bool = True,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a docker compose command."""
        import os

        cmd = ["docker", "compose", *args]

        # Merge custom env with current environment
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        return subprocess.run(
            cmd,
            cwd=self.compose_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            env=run_env,
        )

    def start(
        self,
        with_tunnel: bool = True,
        with_video: bool = True,
        vnc_password: str | None = None,
    ) -> DockerStatus:
        """Start Docker Compose services.

        Args:
            with_tunnel: Include the Cloudflare tunnel service
            with_video: Include the video recording service
            vnc_password: Custom VNC password (defaults to 'secret')

        Returns:
            DockerStatus with service states and tunnel URL if applicable
        """
        # Build the command
        args = ["up", "-d"]
        if with_tunnel:
            args = ["--profile", "tunnel"] + args

        # If not wanting video, specify only the services we want
        if not with_video:
            args.extend(["browser"])
            if with_tunnel:
                args.append("cloudflared")

        # Set port configuration via environment variables
        env = {
            "NOVNC_PORT": str(self.ports.novnc),
            "VNC_PORT": str(self.ports.vnc),
            "CDP_PORT": str(self.ports.cdp),
        }
        if vnc_password:
            env["VNC_PASSWORD"] = vnc_password

        # Start services
        self._run_compose(*args, timeout=300.0, env=env)

        # Wait for browser to be healthy
        self._wait_for_healthy("automation-browser", timeout=60.0)

        # Get tunnel URL if tunnel is enabled
        tunnel_url = None
        if with_tunnel:
            tunnel_url = TunnelManager.get_tunnel_url_from_docker_logs(timeout=30.0)
            self._tunnel_url = tunnel_url

        return self.status()

    def stop(self) -> None:
        """Stop all Docker Compose services."""
        self._run_compose("--profile", "tunnel", "down", check=False)
        self._tunnel_url = None

    def status(self) -> DockerStatus:
        """Get current status of all services."""
        status = DockerStatus(
            novnc_url=f"http://localhost:{self.ports.novnc}",
            ports=self.ports,
        )

        # Check container states
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name, container_status = parts[0], parts[1]

                if name == "automation-browser":
                    status.browser_running = "Up" in container_status
                    status.browser_healthy = "(healthy)" in container_status
                elif name == "automation-tunnel":
                    status.tunnel_running = "Up" in container_status
                elif name == "automation-video":
                    status.video_running = "Up" in container_status
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass

        # Get tunnel URL if tunnel is running
        if status.tunnel_running:
            if self._tunnel_url:
                status.tunnel_url = self._tunnel_url
            else:
                status.tunnel_url = TunnelManager.get_tunnel_url_from_docker_logs(timeout=5.0)
                self._tunnel_url = status.tunnel_url

        # Get VNC password from environment or compose file
        status.vnc_password = self._get_vnc_password()

        return status

    def _wait_for_healthy(self, container_name: str, timeout: float = 60.0) -> bool:
        """Wait for a container to be healthy."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_name],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                if result.stdout.strip() == "healthy":
                    return True
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                pass

            time.sleep(2.0)

        return False

    def _get_vnc_password(self) -> str:
        """Get the VNC password from the running container or default."""
        try:
            result = subprocess.run(
                ["docker", "exec", "automation-browser", "printenv", "VNC_PASSWORD"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass

        return "secret"

    def get_tunnel_url(self, timeout: float = 30.0) -> str | None:
        """Get the tunnel URL, waiting if necessary."""
        if self._tunnel_url:
            return self._tunnel_url

        url = TunnelManager.get_tunnel_url_from_docker_logs(timeout=timeout)
        self._tunnel_url = url
        return url

    async def start_async(
        self,
        with_tunnel: bool = True,
        with_video: bool = True,
        vnc_password: str | None = None,
    ) -> DockerStatus:
        """Async version of start()."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.start(with_tunnel, with_video, vnc_password)
        )

    async def stop_async(self) -> None:
        """Async version of stop()."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.stop)

    def __enter__(self) -> "DockerOrchestrator":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    async def __aenter__(self) -> "DockerOrchestrator":
        await self.start_async()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop_async()


def quick_start(
    with_tunnel: bool = True,
    with_video: bool = True,
    ports: PortConfig | None = None,
    vnc_password: str | None = None,
) -> DockerStatus:
    """Quick start function to launch the automation environment.

    Args:
        with_tunnel: Include the Cloudflare tunnel for remote access
        with_video: Include video recording
        ports: Custom port configuration (novnc, vnc, cdp)
        vnc_password: Custom VNC password (defaults to 'secret')

    Returns:
        DockerStatus with all service info including tunnel URL

    Example:
        >>> from novnc_automation.docker import quick_start, PortConfig
        >>> status = quick_start()
        >>> print(f"Tunnel URL: {status.tunnel_url}")
        >>> print(f"VNC Password: {status.vnc_password}")

        # With custom ports
        >>> status = quick_start(ports=PortConfig(novnc=7080, vnc=5901))
    """
    orchestrator = DockerOrchestrator(ports=ports)
    return orchestrator.start(with_tunnel=with_tunnel, with_video=with_video, vnc_password=vnc_password)


def quick_stop() -> None:
    """Quick stop function to shut down the automation environment."""
    orchestrator = DockerOrchestrator()
    orchestrator.stop()
