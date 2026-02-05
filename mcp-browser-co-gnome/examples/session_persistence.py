#!/usr/bin/env python3
"""Session persistence example - save and restore browser state."""

import asyncio

from novnc_automation import AutomationBrowser, SessionManager


async def create_session():
    """Create a session with some state."""
    print("Creating session with logged-in state...")

    async with AutomationBrowser(session_id="persistent-session") as browser:
        # Navigate and interact
        await browser.goto("https://httpbin.org/cookies/set/session_id/abc123")
        await browser.wait_for_load_state("networkidle")

        # Set some cookies via the test endpoint
        await browser.goto("https://httpbin.org/cookies/set/user/john_doe")
        await browser.wait_for_load_state("networkidle")

        # Verify cookies are set
        await browser.goto("https://httpbin.org/cookies")
        cookies_text = await browser.get_text("pre")
        print(f"Cookies set: {cookies_text}")

        # Set localStorage via JavaScript
        await browser.evaluate("""
            localStorage.setItem('app_settings', JSON.stringify({
                theme: 'dark',
                language: 'en',
                notifications: true
            }));
        """)

        # Navigate to final URL
        await browser.goto("https://example.com")

        # Take screenshot
        await browser.screenshot("session_created")

        print("Session saved!")


async def restore_session():
    """Restore a previously saved session."""
    print("\nRestoring session...")

    async with AutomationBrowser(session_id="restored-session") as browser:
        # Start with restored session
        await browser.start(restore_session="persistent-session")

        print(f"Restored to URL: {browser.page.url}")

        # Verify cookies are still present
        await browser.goto("https://httpbin.org/cookies")
        cookies_text = await browser.get_text("pre")
        print(f"Restored cookies: {cookies_text}")

        # Verify localStorage
        settings = await browser.evaluate("localStorage.getItem('app_settings')")
        print(f"Restored localStorage: {settings}")

        await browser.screenshot("session_restored")


async def list_sessions():
    """List all saved sessions."""
    print("\nListing all sessions...")

    session_manager = SessionManager()
    sessions = await session_manager.list_sessions()

    if not sessions:
        print("No sessions found.")
        return

    for session in sessions:
        print(f"\nSession: {session.session_id}")
        print(f"  Created: {session.created_at}")
        print(f"  Updated: {session.updated_at}")
        print(f"  URL: {session.url}")
        print(f"  Storage state: {session.storage_state_path}")


async def delete_session(session_id: str):
    """Delete a session."""
    print(f"\nDeleting session: {session_id}")

    session_manager = SessionManager()
    deleted = await session_manager.delete_session(session_id)

    if deleted:
        print("Session deleted successfully.")
    else:
        print("Session not found.")


async def main():
    """Run session persistence demo."""
    # Create a session
    await create_session()

    # List sessions
    await list_sessions()

    # Restore the session
    await restore_session()

    # List sessions again
    await list_sessions()

    # Optionally delete (uncomment to test)
    # await delete_session("persistent-session")
    # await delete_session("restored-session")


if __name__ == "__main__":
    asyncio.run(main())
