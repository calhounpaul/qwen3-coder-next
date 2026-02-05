"""Configuration management via environment variables and YAML."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class BrowserConfig(BaseModel):
    """Browser-specific configuration."""

    headless: bool = False
    stealth_mode: bool = True
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_agent: str | None = None
    extra_args: list[str] = Field(default_factory=list)


class RecordingConfig(BaseModel):
    """Recording-related configuration."""

    record_video: bool = True
    record_trace: bool = True
    record_har: bool = True
    record_actions: bool = True
    recordings_dir: Path = Path("tmp")  # All ephemeral data goes in tmp/
    sessions_dir: Path = Path("tmp/sessions")


class TunnelConfig(BaseModel):
    """Cloudflare tunnel configuration."""

    enable_tunnel: bool = False
    tunnel_port: int = 6080


class DockerConfig(BaseModel):
    """Docker-related configuration."""

    vnc_password: str = "secret"
    resolution: str = "1920x1080x24"
    novnc_port: int = 6080
    vnc_port: int = 5900
    cdp_port: int = 9222


class MLServicesConfig(BaseModel):
    """ML services lifecycle configuration."""

    idle_timeout: int = 300  # seconds before auto-stop
    always_on: list[str] = Field(default_factory=list)  # services to never auto-stop


class Config(BaseModel):
    """Main configuration class."""

    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    tunnel: TunnelConfig = Field(default_factory=TunnelConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    ml_services: MLServicesConfig = Field(default_factory=MLServicesConfig)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            browser=BrowserConfig(
                headless=os.getenv("HEADLESS", "false").lower() == "true",
                stealth_mode=os.getenv("STEALTH_MODE", "true").lower() == "true",
                viewport_width=int(os.getenv("VIEWPORT_WIDTH", "1920")),
                viewport_height=int(os.getenv("VIEWPORT_HEIGHT", "1080")),
                user_agent=os.getenv("USER_AGENT"),
            ),
            recording=RecordingConfig(
                record_video=os.getenv("RECORD_VIDEO", "true").lower() == "true",
                record_trace=os.getenv("RECORD_TRACE", "true").lower() == "true",
                record_har=os.getenv("RECORD_HAR", "true").lower() == "true",
                record_actions=os.getenv("RECORD_ACTIONS", "true").lower() == "true",
                recordings_dir=Path(os.getenv("TMP_DIR", os.getenv("RECORDINGS_DIR", "tmp"))),
                sessions_dir=Path(os.getenv("SESSIONS_DIR", "tmp/sessions")),
            ),
            tunnel=TunnelConfig(
                enable_tunnel=os.getenv("ENABLE_TUNNEL", "false").lower() == "true",
                tunnel_port=int(os.getenv("TUNNEL_PORT", "6080")),
            ),
            docker=DockerConfig(
                vnc_password=os.getenv("VNC_PASSWORD", "secret"),
                resolution=os.getenv("RESOLUTION", "1920x1080x24"),
                novnc_port=int(os.getenv("NOVNC_PORT", "6080")),
                vnc_port=int(os.getenv("VNC_PORT", "5900")),
                cdp_port=int(os.getenv("CDP_PORT", "9222")),
            ),
        )

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Config":
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> "Config":
        """Load configuration from YAML file (if exists) merged with env vars.

        Environment variables take precedence over YAML values.
        """
        base_config: dict[str, Any] = {}

        # Load from YAML if path provided and exists
        if config_path:
            path = Path(config_path)
            if path.exists():
                with open(path) as f:
                    base_config = yaml.safe_load(f) or {}

        # Check for default config file
        default_path = Path("config.yml")
        if not config_path and default_path.exists():
            with open(default_path) as f:
                base_config = yaml.safe_load(f) or {}

        # Create config from YAML
        config = cls(**base_config) if base_config else cls()

        # Override with environment variables
        env_config = cls.from_env()

        # Merge - env vars take precedence for explicitly set values
        if os.getenv("HEADLESS"):
            config.browser.headless = env_config.browser.headless
        if os.getenv("STEALTH_MODE"):
            config.browser.stealth_mode = env_config.browser.stealth_mode
        if os.getenv("VIEWPORT_WIDTH"):
            config.browser.viewport_width = env_config.browser.viewport_width
        if os.getenv("VIEWPORT_HEIGHT"):
            config.browser.viewport_height = env_config.browser.viewport_height
        if os.getenv("USER_AGENT"):
            config.browser.user_agent = env_config.browser.user_agent
        if os.getenv("RECORD_VIDEO"):
            config.recording.record_video = env_config.recording.record_video
        if os.getenv("RECORD_TRACE"):
            config.recording.record_trace = env_config.recording.record_trace
        if os.getenv("RECORD_HAR"):
            config.recording.record_har = env_config.recording.record_har
        if os.getenv("RECORD_ACTIONS"):
            config.recording.record_actions = env_config.recording.record_actions
        if os.getenv("TMP_DIR") or os.getenv("RECORDINGS_DIR"):
            config.recording.recordings_dir = env_config.recording.recordings_dir
        if os.getenv("SESSIONS_DIR"):
            config.recording.sessions_dir = env_config.recording.sessions_dir
        if os.getenv("ENABLE_TUNNEL"):
            config.tunnel.enable_tunnel = env_config.tunnel.enable_tunnel
        if os.getenv("TUNNEL_PORT"):
            config.tunnel.tunnel_port = env_config.tunnel.tunnel_port
        if os.getenv("VNC_PASSWORD"):
            config.docker.vnc_password = env_config.docker.vnc_password
        if os.getenv("RESOLUTION"):
            config.docker.resolution = env_config.docker.resolution

        return config

    def to_yaml(self, path: Path | str) -> None:
        """Save configuration to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)
