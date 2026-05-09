"""Run a small end-to-end task, then dump the rendered UI text and raw events
so we can see exactly what the human sees vs. the underlying data."""
import asyncio, json, sys, time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000"
TASK = "create workspace/hi.py with a function hi() returning the string 'hi!'"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await ctx.new_page()

        events = []
        def on_ws(ws):
            ws.on("framereceived", lambda payload: events.append(payload if isinstance(payload, str) else str(payload)))
        page.on("websocket", on_ws)
        page.on("pageerror", lambda e: print(f"[PAGEERROR] {e}"))

        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_function("ws && ws.readyState === 1", timeout=10_000)
        print("[ws] connected")

        await page.locator("#input").fill(TASK)
        await page.locator("#send-btn").click()

        # Wait until pipeline finishes
        deadline = time.time() + 200
        last_count = 0
        while time.time() < deadline:
            done = await page.evaluate("document.querySelector('#status-meta')?.textContent || ''")
            n = len(events)
            if "idle (after turn" in done:
                print(f"[done] status={done} after {n} ws frames")
                break
            if n != last_count:
                print(f"  ... {n} ws frames so far ({done})")
                last_count = n
            await page.wait_for_timeout(2000)
        else:
            print("[!] timed out")

        # Save a screenshot for the user
        await page.screenshot(path="/tmp/ui_state.png", full_page=True)
        print("[screenshot] /tmp/ui_state.png")

        # Dump rendered feed text
        feed_text = await page.locator("#feed").inner_text()
        print("\n=========== RENDERED FEED (what human sees) ===========")
        print(feed_text)

        # Dump the raw delegate_done events so we can see what was inside
        print("\n=========== RAW delegate_done events ===========")
        for raw in events:
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") == "delegate_done":
                print(f"-- {m.get('subagent')} --")
                print((m.get("result_preview") or "")[:1500])
                print()

        # Dump tool_use events to see how inputs render
        print("\n=========== RAW tool_use events (first 3) ===========")
        seen = 0
        for raw in events:
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") == "tool_use":
                print(f"-- {m.get('agent')} → {m.get('tool')} --")
                print(json.dumps(m.get("input"), indent=2)[:800])
                print()
                seen += 1
                if seen >= 3: break

        # Dump orchestrator agent_text events (these are the user-facing summaries)
        print("\n=========== RAW agent_text from orchestrator ===========")
        for raw in events:
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") == "agent_text" and m.get("agent") == "orchestrator":
                print(m.get("text", "")[:400])
                print("---")

        await browser.close()

asyncio.run(main())
