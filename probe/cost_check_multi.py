"""Browser-driven multi-turn cost test — verify the pill matches the SDK's cumulative."""
import asyncio, json
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"[PAGEERROR] {e}"))

        backend_costs = []
        def on_ws(ws):
            def on_frame(payload):
                if not isinstance(payload, str): return
                try: m = json.loads(payload)
                except: return
                if m.get("type") == "agent_result" and isinstance(m.get("cost_usd"), (int, float)):
                    backend_costs.append(m["cost_usd"])
                    print(f"[backend agent_result] cost_usd={m['cost_usd']:.6f}")
            ws.on("framereceived", on_frame)
        page.on("websocket", on_ws)

        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_function("ws && ws.readyState === 1", timeout=10_000)

        async def run_turn(prompt, label):
            await page.locator("#input").fill(prompt)
            await page.locator("#send-btn").click()
            await page.wait_for_function(
                "document.querySelector('#status-meta')?.textContent?.includes('running')",
                timeout=20_000)
            await page.wait_for_function(
                "document.querySelector('#status-meta')?.textContent?.includes('after turn')",
                timeout=240_000)
            await page.wait_for_timeout(800)
            pill = await page.locator("#cost-pill").inner_text()
            page_total = await page.evaluate("totalCost")
            print(f"[ui after {label}] pill={pill!r}  totalCost={page_total!r}")
            return pill, page_total

        await run_turn("create workspace/m1.py with def m1(): return 'one'", "turn 1")
        await run_turn("now also add m2() returning 'two' to that file", "turn 2")

        last_backend = backend_costs[-1] if backend_costs else None
        page_total = await page.evaluate("totalCost")
        print(f"\n== final pill matches SDK cumulative? "
              f"page={page_total:.6f} sdk_last={last_backend:.6f} "
              f"{'OK ✓' if abs((page_total or 0) - (last_backend or 0)) < 1e-6 else 'MISMATCH'}")

        await browser.close()

asyncio.run(main())
