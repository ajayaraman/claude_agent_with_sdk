"""One ClaudeSDKClient session per WebSocket connection.

Architecture:
  • SessionState   — the small mutable thing shared across hooks + turn loop
  • make_hooks     — builds (pre_tool, post_tool) closures over state + emit
  • make_options   — assembles ClaudeAgentOptions; tweak this to customize
                     the model, working directory, system prompt, etc.
  • run            — main entrypoint: drains the inbox forever, one turn
                     per user message, first message wrapped as /orchestrate
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import formatters as fmt

Emit = Callable[[dict], Awaitable[None]]


# ---------------------------------------------------------------------------
# Tunables — change these to customize behavior
# ---------------------------------------------------------------------------

# In claude-agent-sdk 0.1.x, the subagent-delegation tool may be exposed
# under either name (older docs say Task; current SDK uses Agent).
DELEGATION_TOOLS = {"Task", "Agent"}

# SystemMessage subtypes that fire on every nested tool call inside a
# subagent — pure noise for humans, dropped before reaching the UI.
NOISY_SYSTEM_SUBTYPES = {"task_started", "task_progress", "task_notification"}

# Stacked on top of Claude Code's default coding-agent system prompt.
ORCHESTRATOR_PERSONA = (
    "You are the lead Orchestrator of a small SWE team. You have "
    "five subagents available via the Task tool: architect, coder, "
    "test-writer, reviewer, critic. When the user gives a coding "
    "task you should usually run them in that order. On follow-up "
    "turns, decide which subagent (if any) to invoke."
)

# Order matters — match more specific keywords before generic ones.
SUBAGENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("test-writer", ("test-writer", "test_writer", "pytest", "write tests", "test file")),
    ("architect",   ("architect", "implementation plan", "produce a plan", "plan ")),
    ("reviewer",    ("reviewer", "code review", "review the code", "review workspace")),
    ("critic",      ("critic", "verdict", "pass or fail", "final assess")),
    ("coder",       ("coder", "implement the plan", "implement ", "write the code", "write code")),
]


def infer_subagent(tool_input: dict) -> str | None:
    """The Agent tool's visible input often omits subagent_type. Sniff
    description + prompt and map to one of our five subagent names."""
    blob = (
        (tool_input.get("description") or "") + " "
        + (tool_input.get("prompt") or "")[:800]
    ).lower()
    for name, keywords in SUBAGENT_KEYWORDS:
        if any(k in blob for k in keywords):
            return name
    return None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """Shared between hooks (which tag events with the active subagent)
    and the turn loop (which numbers turns)."""
    stack: list[str] = field(default_factory=list)
    task_use_ids: dict[str, str] = field(default_factory=dict)
    turn: int = 0

    def current_scope(self) -> str:
        return self.stack[-1] if self.stack else "orchestrator"

    def enter_subagent(self, name: str, tool_use_id: str | None) -> None:
        if tool_use_id:
            self.task_use_ids[tool_use_id] = name
        self.stack.append(name)

    def exit_subagent(self, tool_use_id: str | None) -> str:
        name = self.task_use_ids.pop(tool_use_id, None) if tool_use_id else None
        if not name and self.stack:
            name = self.stack[-1]
        if self.stack:
            self.stack.pop()
        return name or "?"


# ---------------------------------------------------------------------------
# Hooks — fire for every tool use, including those nested inside subagents
# ---------------------------------------------------------------------------

def make_hooks(state: SessionState, emit: Emit):
    """Return (pre_tool, post_tool) closures bound to this session's state."""

    async def pre_tool(input_data: dict, tool_use_id: str | None, _ctx) -> dict:
        tool = input_data.get("tool_name", "?")
        inp = input_data.get("tool_input", {}) or {}

        if tool in DELEGATION_TOOLS:
            subagent = (
                inp.get("subagent_type") or infer_subagent(inp) or "subagent"
            )
            state.enter_subagent(subagent, tool_use_id)
            await emit({
                "type": "delegate_start",
                "parent": "orchestrator" if len(state.stack) == 1 else state.stack[-2],
                "subagent": subagent,
                "tool_use_id": tool_use_id,
                "description": inp.get("description", ""),
                "prompt": fmt.truncate(inp.get("prompt", ""), fmt.PROMPT_PREVIEW_TRUNCATE),
            })
        else:
            await emit({
                "type": "tool_use",
                "agent": state.current_scope(),
                "tool": tool,
                "summary": fmt.format_tool_input(tool, inp),
                "input": fmt.truncate(inp),
                "tool_use_id": tool_use_id,
            })
        return {}

    async def post_tool(input_data: dict, tool_use_id: str | None, _ctx) -> dict:
        tool = input_data.get("tool_name", "?")
        response = input_data.get("tool_response", "")

        if tool in DELEGATION_TOOLS:
            subagent = state.exit_subagent(tool_use_id)
            await emit({
                "type": "delegate_done",
                "subagent": subagent,
                "tool_use_id": tool_use_id,
                "text": fmt.extract_subagent_text(response),
            })
        else:
            await emit({
                "type": "tool_result",
                "agent": state.current_scope(),
                "tool": tool,
                "tool_use_id": tool_use_id,
                "summary": fmt.format_tool_result(tool, response),
                "is_error": fmt.is_error_response(response),
            })
        return {}

    return pre_tool, post_tool


# ---------------------------------------------------------------------------
# SDK options — change THIS function to customize the agent
# ---------------------------------------------------------------------------

def make_options(project_dir: Path, hooks_pair) -> ClaudeAgentOptions:
    pre, post = hooks_pair
    return ClaudeAgentOptions(
        cwd=str(project_dir),
        permission_mode="acceptEdits",
        # Loads .claude/{agents,commands,settings.json} from cwd. Without
        # this the slash command and subagents are invisible to the SDK.
        setting_sources=["project"],
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": ORCHESTRATOR_PERSONA,
        },
        hooks={
            "PreToolUse":  [HookMatcher(matcher=None, hooks=[pre])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[post])],
        },
    )


# ---------------------------------------------------------------------------
# Stream translation — SDK message → UI events
# ---------------------------------------------------------------------------

async def stream_message(message, state: SessionState, emit: Emit) -> None:
    """Tool uses and tool results are skipped here — hooks already emit
    them with nested-subagent visibility that the parent stream lacks."""
    scope = state.current_scope()

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                if (block.text or "").strip():
                    await emit({"type": "agent_text", "agent": scope, "text": block.text})
            elif isinstance(block, ThinkingBlock):
                if (block.thinking or "").strip():
                    await emit({"type": "agent_thinking", "agent": scope, "text": block.thinking})

    elif isinstance(message, UserMessage):
        # ToolResultBlocks are already covered by the post_tool hook.
        return

    elif isinstance(message, ResultMessage):
        await emit({
            "type": "agent_result",
            "agent": scope,
            "cost_usd":    getattr(message, "total_cost_usd", None),
            "duration_ms": getattr(message, "duration_ms", None),
            "num_turns":   getattr(message, "num_turns", None),
        })

    elif isinstance(message, SystemMessage):
        subtype = getattr(message, "subtype", "?")
        if subtype not in NOISY_SYSTEM_SUBTYPES:
            await emit({"type": "system", "subtype": subtype})


# ---------------------------------------------------------------------------
# Turn loop
# ---------------------------------------------------------------------------

async def _run_turn(client, state: SessionState, emit: Emit, user_text: str) -> None:
    # First turn → wrap as the slash command. Subsequent turns continue
    # the same conversation as plain follow-ups.
    to_send = f"/orchestrate {user_text}" if state.turn == 1 else user_text

    await emit({
        "type": "turn_start",
        "turn": state.turn,
        "user_text": user_text,
        "sent_as": to_send if state.turn == 1 else None,
    })

    try:
        await client.query(to_send)
        async for message in client.receive_response():
            await stream_message(message, state, emit)
    except asyncio.CancelledError:
        await emit({"type": "turn_cancelled", "turn": state.turn})
        raise
    except Exception as e:  # noqa: BLE001
        await emit({"type": "turn_error", "turn": state.turn, "error": repr(e)})
        return

    await emit({"type": "turn_done", "turn": state.turn})


async def run(project_dir: Path, inbox: asyncio.Queue, emit: Emit) -> None:
    """Long-lived session. Drains `inbox` until cancelled or disconnected."""
    state = SessionState()
    options = make_options(project_dir, make_hooks(state, emit))

    try:
        async with ClaudeSDKClient(options=options) as client:
            await emit({"type": "session_started"})

            while True:
                msg = await inbox.get()
                kind = msg.get("kind")

                if kind == "interrupt":
                    try:
                        await client.interrupt()
                        await emit({"type": "interrupted"})
                    except Exception as e:  # noqa: BLE001
                        await emit({"type": "error", "error": f"interrupt failed: {e!r}"})
                    continue

                if kind != "user":
                    continue

                state.turn += 1
                await _run_turn(client, state, emit, msg["content"])

    except asyncio.CancelledError:
        await emit({"type": "session_ended", "reason": "cancelled"})
        raise
    except Exception as e:  # noqa: BLE001
        await emit({"type": "session_error", "error": repr(e)})
