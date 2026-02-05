"""Cloudflare tunnel management for remote access."""

import asyncio
import re
import subprocess
from typing import Callable

from novnc_automation.config import Config


class TunnelManager:
    """Manages Cloudflare quick tunnels for remote browser access."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        self._process: asyncio.subprocess.Process | None = None
        self._url: str | None = None
        self._started = False

    @property
    def url(self) -> str | None:
        """Get the tunnel URL if available."""
        return self._url

    @property
    def is_running(self) -> bool:
        """Check if tunnel is running."""
        return self._started and self._process is not None

    async def start(
        self,
        port: int | None = None,
        on_url: Callable[[str], None] | None = None,
        timeout: float = 30.0,
    ) -> str:
        """Start a Cloudflare quick tunnel.

        Args:
            port: Port to tunnel (defaults to config tunnel_port)
            on_url: Callback when URL is available
            timeout: Timeout waiting for URL in seconds

        Returns:
            The public tunnel URL

        Raises:
            RuntimeError: If tunnel fails to start or URL not captured
            TimeoutError: If URL not captured within timeout
        """
        if self._started:
            if self._url:
                return self._url
            raise RuntimeError("Tunnel already started but URL not available")

        port = port or self.config.tunnel.tunnel_port

        # Start cloudflared process
        self._process = await asyncio.create_subprocess_exec(
            "cloudflared",
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://localhost:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._started = True

        # Wait for URL in output
        url_pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

        async def read_output():
            while True:
                if self._process is None or self._process.stderr is None:
                    break

                line = await self._process.stderr.readline()
                if not line:
                    break

                line_str = line.decode("utf-8", errors="ignore")

                match = url_pattern.search(line_str)
                if match:
                    self._url = match.group(0)
                    if on_url:
                        on_url(self._url)
                    return self._url

            return None

        try:
            url = await asyncio.wait_for(read_output(), timeout=timeout)
            if url:
                return url
            raise RuntimeError("Cloudflared exited without providing URL")
        except asyncio.TimeoutError:
            await self.stop()
            raise TimeoutError(f"Tunnel URL not captured within {timeout}s")

    async def stop(self) -> None:
        """Stop the tunnel."""
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        self._process = None
        self._started = False
        self._url = None

    async def __aenter__(self) -> "TunnelManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    @staticmethod
    def get_tunnel_url_from_docker_logs(
        container_name: str = "automation-tunnel",
        timeout: float = 30.0,
    ) -> str | None:
        """Extract tunnel URL from Docker container logs.

        This is useful when running cloudflared in Docker Compose.

        Args:
            container_name: Name of the cloudflared container
            timeout: Timeout in seconds

        Returns:
            Tunnel URL if found, None otherwise
        """
        import time

        url_pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ["docker", "logs", container_name],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )

                output = result.stdout + result.stderr
                match = url_pattern.search(output)
                if match:
                    return match.group(0)

            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                pass

            time.sleep(1.0)

        return None

    @staticmethod
    async def get_tunnel_url_from_docker_logs_async(
        container_name: str = "automation-tunnel",
        timeout: float = 30.0,
    ) -> str | None:
        """Async version of get_tunnel_url_from_docker_logs."""
        url_pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "logs",
                    container_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                output = stdout.decode() + stderr.decode()

                match = url_pattern.search(output)
                if match:
                    return match.group(0)

            except (asyncio.TimeoutError, Exception):
                pass

            await asyncio.sleep(1.0)

        return None
