#!/usr/bin/env python3
"""Basic browser automation example."""

import asyncio

from novnc_automation import AutomationBrowser


async def main():
    """Demonstrate basic browser automation."""
    # Create a browser with a named session
    async with AutomationBrowser(session_id="basic-example") as browser:
        # Navigate to a website
        await browser.goto("https://example.com")
        print(f"Navigated to: {browser.page.url}")

        # Get page title
        title = await browser.page.title()
        print(f"Page title: {title}")

        # Take a screenshot
        screenshot_path = await browser.screenshot("homepage")
        print(f"Screenshot saved to: {screenshot_path}")

        # Get text content
        heading = await browser.get_text("h1")
        print(f"Heading: {heading}")

        # Click a link
        await browser.click("a")
        await browser.wait_for_load_state("networkidle")
        print(f"After click, URL: {browser.page.url}")

        # Session is automatically saved on exit


async def form_example():
    """Demonstrate form interaction."""
    async with AutomationBrowser(session_id="form-example") as browser:
        # Navigate to a form page (using httpbin for demo)
        await browser.goto("https://httpbin.org/forms/post")

        # Fill form fields
        await browser.fill('input[name="custname"]', "John Doe")
        await browser.fill('input[name="custtel"]', "555-1234")
        await browser.fill('input[name="custemail"]', "john@example.com")

        # Select options
        await browser.click('input[name="size"][value="medium"]')
        await browser.check('input[name="topping"][value="cheese"]')

        # Fill textarea
        await browser.fill('textarea[name="comments"]', "Please deliver quickly!")

        # Take screenshot before submit
        await browser.screenshot("form_filled")
        print("Form filled and screenshot taken")

        # Note: Not submitting to avoid leaving example site


async def javascript_example():
    """Demonstrate JavaScript evaluation."""
    async with AutomationBrowser(session_id="js-example") as browser:
        await browser.goto("https://example.com")

        # Evaluate JavaScript
        user_agent = await browser.evaluate("navigator.userAgent")
        print(f"User Agent: {user_agent}")

        # Get viewport size
        viewport = await browser.evaluate(
            "({ width: window.innerWidth, height: window.innerHeight })"
        )
        print(f"Viewport: {viewport}")

        # Check for automation detection
        webdriver = await browser.evaluate("navigator.webdriver")
        print(f"navigator.webdriver: {webdriver}")


if __name__ == "__main__":
    print("=== Basic Usage Example ===")
    asyncio.run(main())

    print("\n=== Form Example ===")
    asyncio.run(form_example())

    print("\n=== JavaScript Example ===")
    asyncio.run(javascript_example())
