#!/usr/bin/env python3
"""Remote access example using Cloudflare tunnels."""

import asyncio
import subprocess
import sys

from novnc_automation import AutomationBrowser, TunnelManager


async def local_tunnel_example():
    """Start a local tunnel to access the browser remotely.

    This example runs cloudflared locally (not in Docker).
    Requires cloudflared to be installed.
    """
    print("Starting local Cloudflare tunnel...")
    print("(Requires cloudflared CLI to be installed)")

    try:
        async with TunnelManager() as tunnel:
            print(f"\n✓ Tunnel started!")
            print(f"  Remote URL: {tunnel.url}")
            print(f"\nOpen this URL in any browser to view the noVNC interface.")
            print("Press Ctrl+C to stop.\n")

            # Keep tunnel running
            while True:
                await asyncio.sleep(1)

    except FileNotFoundError:
        print("Error: cloudflared not installed.")
        print("Install it from: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation")
        return
    except KeyboardInterrupt:
        print("\nTunnel stopped.")


async def docker_tunnel_example():
    """Get tunnel URL from Docker Compose cloudflared container.

    This assumes you've started the services with:
        docker compose --profile tunnel up -d
    """
    print("Getting tunnel URL from Docker container...")

    url = await TunnelManager.get_tunnel_url_from_docker_logs_async(
        container_name="automation-tunnel",
        timeout=30.0,
    )

    if url:
        print(f"\n✓ Tunnel URL: {url}")
        print("\nOpen this URL to access noVNC remotely.")
    else:
        print("\n✗ Could not get tunnel URL.")
        print("Make sure the tunnel container is running:")
        print("  docker compose --profile tunnel up -d")


async def automation_with_remote_view():
    """Run automation while providing remote viewing access."""
    print("Starting automation with remote viewing...")

    # Start tunnel in background
    tunnel = TunnelManager()

    try:
        # Start tunnel
        url = await tunnel.start(timeout=30.0)
        print(f"\n✓ View browser at: {url}")
        print("(Share this URL to allow remote viewing)\n")

        # Run automation
        async with AutomationBrowser(session_id="remote-demo") as browser:
            print("Navigating to example.com...")
            await browser.goto("https://example.com")
            await asyncio.sleep(2)  # Pause for remote viewer

            print("Taking screenshot...")
            await browser.screenshot("remote_demo")

            print("\nAutomation complete!")
            print(f"Continue viewing at: {url}")

            # Keep tunnel open for a bit
            print("Tunnel will stay open for 30 seconds...")
            await asyncio.sleep(30)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await tunnel.stop()
        print("Tunnel closed.")


def show_docker_compose_tunnel_instructions():
    """Print instructions for using tunnel with Docker Compose."""
    print("""
=== Using Cloudflare Tunnel with Docker Compose ===

1. Start all services including the tunnel:

   docker compose --profile tunnel up -d

2. Get the tunnel URL from logs:

   docker logs automation-tunnel 2>&1 | grep trycloudflare.com

   Or use the Python helper:

   from novnc_automation import TunnelManager
   url = TunnelManager.get_tunnel_url_from_docker_logs()
   print(url)

3. Open the URL in your browser to access noVNC remotely.

4. Stop all services:

   docker compose --profile tunnel down

Note: The tunnel profile is optional. Without it, you can still
access noVNC locally at http://localhost:6080
""")


async def main():
    """Run remote access demos."""
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "local":
            await local_tunnel_example()
        elif command == "docker":
            await docker_tunnel_example()
        elif command == "demo":
            await automation_with_remote_view()
        elif command == "help":
            show_docker_compose_tunnel_instructions()
        else:
            print(f"Unknown command: {command}")
            print("Available: local, docker, demo, help")
    else:
        print("Remote Access Examples")
        print("=" * 40)
        print("\nUsage:")
        print("  python remote_access.py local   - Start local tunnel")
        print("  python remote_access.py docker  - Get Docker tunnel URL")
        print("  python remote_access.py demo    - Run automation with tunnel")
        print("  python remote_access.py help    - Show Docker Compose instructions")
        print("\n")
        show_docker_compose_tunnel_instructions()


if __name__ == "__main__":
    asyncio.run(main())
