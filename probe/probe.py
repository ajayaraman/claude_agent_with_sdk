"""Probe the running backend's websocket — mimic exactly what the browser does."""
import asyncio
import json
import sys
import websockets

URI = "ws://127.0.0.1:8000/ws"

async def main():
    async with websockets.connect(URI) as ws:
        print("[connected]")
        # send a user message
        async def reader():
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except Exception:
                    print("RAW:", raw[:200])
                    continue
                t = m.get("type")
                if t in ("agent_text",):
                    print(f"  [{t}] {m.get('agent')}: {m.get('text','')[:200]}")
                elif t in ("tool_use",):
                    print(f"  [{t}] {m.get('agent')} -> {m.get('tool')} {str(m.get('input'))[:200]}")
                elif t in ("tool_result",):
                    print(f"  [{t}] {m.get('agent')}: {str(m.get('result_preview'))[:200]}")
                elif t == "delegate_start":
                    print(f"  [DELEGATE START] -> {m.get('subagent')}")
                elif t == "delegate_done":
                    print(f"  [DELEGATE DONE]  <- {m.get('subagent')}: {str(m.get('result_preview'))[:200]}")
                else:
                    print(f"  [{t}] {json.dumps({k:v for k,v in m.items() if k!='type'}, default=str)[:300]}")

        reader_task = asyncio.create_task(reader())
        await asyncio.sleep(0.5)  # let "ready" arrive
        msg = sys.argv[1] if len(sys.argv) > 1 else "Build a tiny Python module workspace/hello.py with a function greet(name) that returns f'hello, {name}!'."
        print(f"[send] {msg!r}")
        await ws.send(json.dumps({"type": "user", "content": msg}))
        try:
            await asyncio.wait_for(reader_task, timeout=120)
        except asyncio.TimeoutError:
            print("[timed out after 120s]")

asyncio.run(main())
