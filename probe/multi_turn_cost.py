"""Run two turns and capture cost_usd from each agent_result."""
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        results = []
        done = asyncio.Event()
        turn_results = []

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                if m.get("type") == "agent_result":
                    print(f"[agent_result] cost_usd={m.get('cost_usd')!r} num_turns={m.get('num_turns')}")
                    turn_results.append(m)
                if m.get("type") == "turn_done":
                    print(f"[turn_done] turn={m.get('turn')}")
                    done.set()

        reader_task = asyncio.create_task(reader())

        await asyncio.sleep(0.3)
        # Reset to start fresh
        await ws.send(json.dumps({"type": "reset"}))
        await asyncio.sleep(0.5)

        # Turn 1
        await ws.send(json.dumps({"type": "user",
                                   "content": "create workspace/t1.py with def t1(): return 1"}))
        await asyncio.wait_for(done.wait(), timeout=240)
        done.clear()
        first_cost = turn_results[-1].get("cost_usd") if turn_results else None
        print(f"\nturn 1 cost_usd: {first_cost}\n")

        # Turn 2
        await ws.send(json.dumps({"type": "user",
                                   "content": "now also add t2() that returns 2 to t1.py"}))
        await asyncio.wait_for(done.wait(), timeout=240)
        second_cost = turn_results[-1].get("cost_usd") if turn_results else None
        print(f"\nturn 2 cost_usd: {second_cost}")

        if first_cost and second_cost:
            if abs(second_cost - first_cost) < first_cost * 0.5:
                interpretation = "PER-TURN (turn 2 cost stands alone)"
            elif second_cost > first_cost:
                interpretation = "CUMULATIVE (turn 2 includes turn 1)"
            else:
                interpretation = "UNCLEAR"
            print(f"\nInterpretation: {interpretation}")
            print(f"  turn1 alone: ${first_cost:.4f}")
            print(f"  turn2 raw:   ${second_cost:.4f}")
            print(f"  if cumulative: turn2 added ${second_cost - first_cost:.4f}")

asyncio.run(main())
