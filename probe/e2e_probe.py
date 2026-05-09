"""Drive the page in a real Chromium browser and capture everything."""
import asyncio
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # ---- capture every signal ----
        page.on("console", lambda m: print(f"[console.{m.type}] {m.text}"))
        page.on("pageerror", lambda e: print(f"[PAGEERROR] {e}"))
        page.on("requestfailed", lambda r: print(f"[REQFAIL] {r.url} :: {r.failure}"))

        def _trim(p): return (p if isinstance(p, str) else str(p))[:200]
        def on_ws(ws):
            print(f"[WS OPEN ] {ws.url}")
            ws.on("framesent",     lambda payload: print(f"[WS  out ] {_trim(payload)}"))
            ws.on("framereceived", lambda payload: print(f"[WS  in  ] {_trim(payload)}"))
            ws.on("close",         lambda: print(f"[WS CLOSE] {ws.url}"))
            ws.on("socketerror",   lambda err: print(f"[WS ERR  ] {err}"))
        page.on("websocket", on_ws)

        print(f"[load] {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=10_000)

        # let connection settle
        await page.wait_for_timeout(1500)

        # ---- read what the page thinks its state is ----
        conn_text = await page.locator('#conn-pill').inner_text()
        print(f"[pill] '{conn_text}'")

        ws_state = await page.evaluate("typeof ws === 'undefined' ? 'no-var' : ws?.readyState")
        print(f"[ws.readyState in page] {ws_state}  (0=connecting 1=open 2=closing 3=closed)")
        ws_url = await page.evaluate("typeof wsUrl === 'function' ? wsUrl() : '(no fn)'")
        print(f"[wsUrl()] {ws_url}")

        # ---- click send with a small task ----
        if conn_text.lower().startswith("connected"):
            print("[ui] typing task and clicking Send")
            await page.locator("#input").fill("write workspace/hi.py with a function hi() that returns 'hi!'")
            await page.locator("#send-btn").click()
            await page.wait_for_timeout(8000)
            chat = await page.locator("#chat-log").inner_text()
            feed = await page.locator("#feed").inner_text()
            print(f"[chat-log after 8s]\n{chat[:500]}")
            print(f"[feed after 8s]\n{feed[:1500]}")
        else:
            print("[ui] not connected — skipping click")

        await browser.close()

asyncio.run(main())
