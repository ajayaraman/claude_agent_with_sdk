"""FastAPI server.

This file is the I/O edge — HTTP routes for the frontend + workspace, and a
WebSocket endpoint that pipes a browser conversation into a long-lived
Claude Agent SDK session.

Behavior of the multi-agent pipeline is defined declaratively in
../claude_project/.claude/ and is wired up in session.py. See that file
to customize models, persona, hooks, etc.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse

import formatters
import session

ROOT = Path(__file__).resolve().parent.parent
PROJECT_DIR = ROOT / "claude_project"
WORKSPACE = PROJECT_DIR / "workspace"
FRONTEND = ROOT / "frontend"

WORKSPACE.mkdir(parents=True, exist_ok=True)
formatters.configure(PROJECT_DIR)

app = FastAPI(title="Claude Agent SDK — Multi-Agent Orchestrator")


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    # no-store: small page, frequent JS edits — never serve a stale bundle.
    return FileResponse(
        FRONTEND / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/files")
async def list_files() -> dict:
    files = []
    if WORKSPACE.exists():
        for p in sorted(WORKSPACE.rglob("*")):
            if p.is_file() and p.name != ".gitkeep":
                files.append({
                    "path": str(p.relative_to(WORKSPACE)),
                    "size": p.stat().st_size,
                })
    return {"files": files, "workspace": str(WORKSPACE)}


@app.get("/file", response_class=PlainTextResponse)
async def read_file(path: str) -> str:
    target = (WORKSPACE / path).resolve()
    if not str(target).startswith(str(WORKSPACE.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return target.read_text(errors="replace")


# ---------------------------------------------------------------------------
# WebSocket endpoint — one session per connection
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    inbox: asyncio.Queue[dict] = asyncio.Queue()
    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        async with send_lock:
            try:
                await websocket.send_json(payload)
            except Exception:
                pass

    await emit({
        "type": "ready",
        "project_dir": str(PROJECT_DIR),
        "workspace": str(WORKSPACE),
    })

    session_task = asyncio.create_task(session.run(PROJECT_DIR, inbox, emit))

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await emit({"type": "error", "error": "invalid json"})
                continue
            session_task = await _route(msg, inbox, session_task, emit)
    except WebSocketDisconnect:
        session_task.cancel()


async def _route(msg: dict, inbox: asyncio.Queue, session_task: asyncio.Task, emit) -> asyncio.Task:
    """Inbound websocket message → enqueue or session-lifecycle action.
    Returns the (possibly replaced) session task."""
    mtype = msg.get("type")

    if mtype == "user":
        content = (msg.get("content") or "").strip()
        if content:
            await inbox.put({"kind": "user", "content": content})
            await emit({"type": "queued", "content": content, "queue_size": inbox.qsize()})

    elif mtype == "interrupt":
        await inbox.put({"kind": "interrupt"})

    elif mtype == "clear":
        _wipe_workspace()
        await emit({"type": "workspace_cleared"})

    elif mtype == "reset":
        session_task.cancel()
        try:
            await session_task
        except (asyncio.CancelledError, Exception):
            pass
        while not inbox.empty():
            inbox.get_nowait()
        session_task = asyncio.create_task(session.run(PROJECT_DIR, inbox, emit))
        await emit({"type": "session_reset"})

    return session_task


def _wipe_workspace() -> None:
    """Called only when the frontend sends {"type": "clear"}.
    Logs each file removed so accidental triggers are obvious in the server log."""
    if not WORKSPACE.exists():
        return
    removed: list[str] = []
    for p in WORKSPACE.iterdir():
        if p.name == ".gitkeep":
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        removed.append(p.name)
    if removed:
        print(f"[main] _wipe_workspace removed {len(removed)} entries: {removed}", flush=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
