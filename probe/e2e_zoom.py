"""Zoom into the rendered feed and snap separate close-ups of:
   - the critic's delegation block (most important readability test)
   - one tool row
   - the orchestrator's final markdown summary
"""
import asyncio
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900},
                                        device_scale_factor=2)
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_function("ws && ws.readyState === 1", timeout=10_000)
        await page.locator("#input").fill(
            "create workspace/hi.py with a function hi() that returns the string 'hi!'"
        )
        await page.locator("#send-btn").click()

        # Wait for the critic's agent_answer panel to render — that's the
        # last thing to appear before the orchestrator wraps up.
        await page.wait_for_selector(
            '.delegation[data-subagent="critic"] .agent-answer',
            timeout=240_000,
        )
        await page.wait_for_timeout(1500)  # let the final summary render too

        # Snap individual elements
        await page.locator('.delegation[data-subagent="critic"]').first.screenshot(
            path="/tmp/ui_critic.png"
        )
        print("/tmp/ui_critic.png saved")

        await page.locator('.delegation[data-subagent="reviewer"]').first.screenshot(
            path="/tmp/ui_reviewer.png"
        )
        print("/tmp/ui_reviewer.png saved")

        rows = page.locator('.tool-row')
        if await rows.count() > 0:
            await rows.first.screenshot(path="/tmp/ui_tool_row.png")
            print("/tmp/ui_tool_row.png saved")

        # Dump the inner HTML of the critic's answer to confirm markdown structure
        html = await page.locator('.delegation[data-subagent="critic"] .agent-answer').first.inner_html()
        print("\n--- critic .agent-answer inner HTML ---")
        print(html[:2000])

        await browser.close()

asyncio.run(main())
