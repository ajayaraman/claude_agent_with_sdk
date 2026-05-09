# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A five-agent software-engineering pipeline built on the **Claude Agent SDK**. Agent behavior is defined declaratively as markdown files in a Claude project (`claude_project/.claude/`). A FastAPI + WebSocket backend pipes browser input into a long-lived SDK session, and a single-file frontend shows the resulting activity in real time.

## Running the project

```bash
# Prerequisites (one-time)
npm install -g @anthropic-ai/claude-code   # Python SDK shells out to this binary
export ANTHROPIC_API_KEY=sk-ant-...        # or run `claude` to log in interactively

# Start the server (creates .venv, installs deps, starts uvicorn with --reload)
./run.sh
```

Open <http://127.0.0.1:8000>.

**Test the agent setup without the backend:**

```bash
cd claude_project
claude
> /orchestrate build a fizzbuzz function with edge cases
```

**Run the WebSocket probe against a running server:**

```bash
source .venv/bin/activate
python probe/probe.py "Build workspace/hello.py with greet(name)"
```

## Architecture

### The three layers

```
claude_project/.claude/   ← agent definitions (the real system)
backend/                  ← FastAPI + SDK wiring
frontend/index.html       ← browser UI (~350 lines vanilla JS)
```

### Agent definitions (`claude_project/.claude/`)

- **`agents/{architect,coder,test-writer,reviewer,critic}.md`** — each file's YAML frontmatter (`name`, `description`, `tools`, `model`) defines one subagent; the body is its system prompt. The orchestrator's `Task` tool reads `description` to decide which subagent to invoke.
- **`commands/orchestrate.md`** — slash command that sets up the orchestrator's pipeline. First user turn is sent as `/orchestrate <task>`; follow-ups go as plain text.
- **`settings.json`** — tool allow-list for the project (Read, Write, Edit, Glob, Grep, limited Bash, Task).

### Backend (`backend/`)

| File | Role |
|------|------|
| `main.py` | FastAPI routes (`/`, `/files`, `/file`, `/ws`). One WebSocket = one SDK session. Routes inbound WS messages (user text, interrupt, clear, reset) to `session.py`. |
| `session.py` | All SDK wiring. `make_options()` builds `ClaudeAgentOptions`; `make_hooks()` returns `(pre_tool, post_tool)` closures that track the subagent delegation stack and emit structured events. `run()` is the turn loop. |
| `formatters.py` | Pure formatting helpers. `format_tool_input/result` produce one-line summaries for the UI. To support a new tool, add an entry to `INPUT_FORMATTERS` or `RESULT_FORMATTERS`. |

### Two critical SDK details

1. **`setting_sources=["project"]`** in `ClaudeAgentOptions` — without this the SDK ignores `.claude/` and the slash command + subagents are invisible.
2. **Hooks fire inside subagents** — the parent stream only surfaces the orchestrator's messages and `Task` tool results. `pre_tool`/`post_tool` hooks see every nested tool call. `SessionState.stack` tracks the current delegation depth so events are tagged with the right subagent name.

### WebSocket event protocol

Events emitted by the backend to the browser:

| `type` | Meaning |
|--------|---------|
| `ready` | Session socket accepted |
| `session_started` / `session_ended` | SDK client lifecycle |
| `turn_start` / `turn_done` / `turn_cancelled` / `turn_error` | Per-turn lifecycle |
| `delegate_start` / `delegate_done` | Subagent Task invocation start/end |
| `tool_use` / `tool_result` | Any non-delegation tool call |
| `agent_text` / `agent_thinking` | Text/thinking blocks from an assistant message |
| `agent_result` | Final `ResultMessage` (includes cost, duration, num_turns) |
| `queued` | User message received while a turn is in flight |
| `interrupted` | `client.interrupt()` succeeded |
| `workspace_cleared` | Workspace wipe complete |
| `session_reset` | New `ClaudeSDKClient` session started |

### Probe scripts (`probe/`)

Standalone scripts for testing without a browser. `probe.py` mimics the browser WebSocket connection. `cost_check*.py` and `multi_turn_cost.py` measure token usage; `e2e_*.py` run end-to-end scenarios.

## Customization points

- **Change models per-agent**: edit `model: inherit` in any `.claude/agents/*.md` file.
- **Change the pipeline order or add subagents**: edit `.claude/commands/orchestrate.md` and drop a new `.md` in `.claude/agents/`.
- **Change the orchestrator persona**: edit `ORCHESTRATOR_PERSONA` in `session.py`.
- **Token-level streaming**: set `include_partial_messages=True` in `make_options()`.
- **Subagent keyword inference** (for SDK versions that omit `subagent_type`): edit `SUBAGENT_KEYWORDS` in `session.py`.
- **Truncation limits for the UI**: all constants are at the top of `formatters.py`.
