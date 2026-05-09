"""Run a small task and check what cost-related events arrive + what the pill shows."""
import asyncio, json, time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        cost_events = []
        all_events = []

        def on_ws(ws):
            def on_frame(payload):
                if not isinstance(payload, str): return
                try:
                    m = json.loads(payload)
                except Exception:
                    return
                all_events.append(m)
                if m.get("type") == "agent_result":
                    cost_events.append(m)
                    print(f"[agent_result] cost_usd={m.get('cost_usd')!r} duration={m.get('duration_ms')} turns={m.get('num_turns')}")
                elif m.get("type") in ("turn_done", "turn_error", "session_started", "ready"):
                    print(f"[{m.get('type')}] {m}")
            ws.on("framereceived", on_frame)

        page.on("websocket", on_ws)
        page.on("pageerror", lambda e: print(f"[PAGEERROR] {e}"))

        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_function("ws && ws.readyState === 1", timeout=10_000)

        await page.locator("#input").fill("create workspace/echo.py with a function echo(x) returning x")
        await page.locator("#send-btn").click()

        # Wait for turn_start first (so we don't pick up the initial "idle"),
        # then for "after turn" which means turn_done has fired.
        await page.wait_for_function(
            "document.querySelector('#status-meta')?.textContent?.includes('running')",
            timeout=20_000
        )
        await page.wait_for_function(
            "document.querySelector('#status-meta')?.textContent?.includes('after turn')",
            timeout=240_000
        )
        await page.wait_for_timeout(1000)

        # Inspect the actual displayed pill text
        pill_text = await page.locator("#cost-pill").inner_text()
        page_total = await page.evaluate("typeof totalCost === 'undefined' ? 'undefined' : totalCost")
        page_turns = await page.locator("#turns-pill").inner_text()

        print()
        print(f"== pill text:    {pill_text!r}")
        print(f"== window.totalCost: {page_total!r}")
        print(f"== turns pill:   {page_turns!r}")
        print(f"== sum of cost_usd values: ${sum((e.get('cost_usd') or 0) for e in cost_events):.6f}")
        print(f"== agent_result events: {len(cost_events)}")
        for i, e in enumerate(cost_events):
            print(f"   [{i}] cost_usd={e.get('cost_usd')!r}")

        # also dump any other event types that might carry cost info
        types = {}
        for e in all_events:
            types[e.get("type")] = types.get(e.get("type"), 0) + 1
        print(f"== event-type counts: {dict(sorted(types.items(), key=lambda kv: -kv[1]))}")

        await browser.close()

asyncio.run(main())
