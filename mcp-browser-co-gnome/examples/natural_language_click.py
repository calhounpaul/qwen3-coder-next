#!/usr/bin/env python3
"""Natural language click example using GUI-Actor.

Requires: pip install -e '.[gui-actor]'
"""

import asyncio
from io import BytesIO

from PIL import Image

from novnc_automation import AutomationBrowser


async def main():
    """Demonstrate natural language click functionality."""
    print("Starting browser...")

    async with AutomationBrowser(session_id="nl-click-demo") as browser:
        # Navigate to a page
        await browser.goto("https://example.com")
        print(f"Navigated to: {browser.page.url}")

        # Take a screenshot
        screenshot_bytes = await browser.page.screenshot()
        image = Image.open(BytesIO(screenshot_bytes))
        print(f"Screenshot taken: {image.size}")

        # Load GUI-Actor model
        print("\nLoading GUI-Actor model (this may take a while on first run)...")
        try:
            from novnc_automation.gui_actor import get_gui_actor_model

            model = get_gui_actor_model()
            model.load()  # Explicit load
            print("Model loaded!")

            # Predict click location for a natural language instruction
            instruction = "Click the 'More information...' link"
            print(f"\nInstruction: {instruction}")

            x, y, metadata = model.predict_click_absolute(image, instruction)
            print(f"Predicted click location: ({x}, {y})")
            print(f"Confidence: {metadata.get('topk_values', 'N/A')}")

            # Click at the predicted location
            await browser.page.mouse.click(x, y)
            print("Clicked!")

            # Wait for navigation
            await browser.wait_for_load_state("networkidle")
            print(f"After click, URL: {browser.page.url}")

            # Take another screenshot
            await browser.screenshot("after_nl_click")

        except ImportError as e:
            print(f"\nGUI-Actor not available: {e}")
            print("Install with: pip install -e '.[gui-actor]'")
            return


async def demo_multiple_clicks():
    """Demonstrate multiple natural language clicks."""
    print("\n=== Multiple Clicks Demo ===")

    async with AutomationBrowser(session_id="nl-multi-click") as browser:
        await browser.goto("https://www.google.com")
        print("Navigated to Google")

        try:
            from novnc_automation.gui_actor import get_gui_actor_model

            model = get_gui_actor_model()

            # Define a sequence of natural language instructions
            instructions = [
                "Click the search box",
                # After typing, you might say:
                # "Click the Google Search button",
            ]

            for instruction in instructions:
                # Take fresh screenshot
                screenshot_bytes = await browser.page.screenshot()
                image = Image.open(BytesIO(screenshot_bytes))

                print(f"\nInstruction: {instruction}")
                x, y, metadata = model.predict_click_absolute(image, instruction)
                print(f"Clicking at: ({x}, {y})")

                await browser.page.mouse.click(x, y)
                await asyncio.sleep(0.5)  # Brief pause

            # Type in search box
            await browser.page.keyboard.type("GUI automation with AI")
            await browser.screenshot("search_typed")
            print("\nTyped search query")

        except ImportError:
            print("GUI-Actor not available")


if __name__ == "__main__":
    print("=== Natural Language Click Demo ===\n")
    asyncio.run(main())

    # Uncomment to run multi-click demo
    # asyncio.run(demo_multiple_clicks())
