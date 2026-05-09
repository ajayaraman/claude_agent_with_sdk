# Multi-Agent Orchestrator — Claude Project + FastAPI Shim

A small project that demonstrates running a five-agent software-engineering
team using the **Claude Agent SDK's native multi-agent framework** —
subagents and slash commands defined as **markdown files in a Claude
project**, with a thin FastAPI websocket shim so a browser UI can drive it
and observe everything that happens, including activity inside subagents.

```
              chat (left)            activity (center)            workspace (right)
   ┌──────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────┐
   │ user: build calculator   │  │ turn 1 start            │  │  workspace/         │
   │ assistant: …             │  │ → architect (running)   │  │  ├─ calculator.py   │
   │ (queued) also negatives  │  │   prompt: ...           │  │  └─ test_calc.py    │
   │                          │  │ → coder (done)          │  │                     │
   └──────────────────────────┘  │   tool: Write file.py   │  └─────────────────────┘
                                 │   tool: Write …          
                                 │ → test-writer (done)     
                                 └─────────────────────────┘
```

## The Claude project comes first

Everything that defines agent behavior lives under `claude_project/.claude/`:

```
claude_project/
├── .claude/
│   ├── agents/                  ← five subagent role files
│   │   ├── architect.md
│   │   ├── coder.md
│   │   ├── test-writer.md
│   │   ├── reviewer.md
│   │   └── critic.md
│   ├── commands/
│   │   └── orchestrate.md       ← slash command that drives the pipeline
│   └── settings.json            ← tool allow-list
└── workspace/                   ← agents write code here
```

You could `cd claude_project && claude` and then type `/orchestrate build a
roman numeral parser` directly in the Claude Code CLI — no backend needed.
The backend just plumbs that same setup into a websocket.

### How a subagent file looks

```markdown
---
name: coder
description: Implements a plan by writing code files into ./workspace/.
tools: Read, Write, Edit, Glob, Grep, Bash
model: inherit
---

You are a Coder. ...
```

The `description` field is what the parent agent reads when it decides
whether to delegate via the `Task` tool. The `tools` list is the subagent's
tool whitelist (omit to inherit all). `model: inherit` reuses the parent's
model.

### How the orchestrator slash command looks

`.claude/commands/orchestrate.md` is a normal Claude Code slash command:

```markdown
---
description: Run the multi-agent SWE pipeline.
argument-hint: <task description>
---

You are the orchestrator. The user's task: $ARGUMENTS
1. Task(subagent_type="architect", ...)
2. Task(subagent_type="coder", ...)
... etc.
```

When the user types `/orchestrate build X`, Claude expands this template
with `$ARGUMENTS = "build X"` and the model takes it from there.

## The backend (FastAPI shim)

`backend/main.py` is one file. The interesting bits:

```python
options = ClaudeAgentOptions(
    cwd=str(PROJECT_DIR),
    permission_mode="acceptEdits",
    setting_sources=["project"],     # <- loads .claude/ from cwd
    system_prompt={                  # <- preset+append keeps Claude Code's
        "type": "preset",            #    coding-agent prompt and stacks our
        "preset": "claude_code",     #    orchestrator persona on top of it
        "append": "You are the lead orchestrator…",
    },
    hooks={
        "PreToolUse":  [HookMatcher(matcher=None, hooks=[pre_tool])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool])],
    },
)

async with ClaudeSDKClient(options=options) as client:
    await client.query(f"/orchestrate {user_task}")     # first turn
    async for msg in client.receive_response():
        ...                                              # stream to browser
    # ...subsequent turns are plain follow-ups in the same session
```

Two things make this work:

1. **`setting_sources=["project"]`** — without this the SDK ignores the
   project's `.claude/` and the slash command + subagents are invisible.

2. **Hooks** — they fire for *every* tool call the SDK runs, including
   tools called *inside* a subagent. The parent stream only contains the
   orchestrator's own messages and Task tool results; for the rich nested
   view ("Coder used Write to create calculator.py") we need hooks. We
   maintain a small stack: when a `Task` tool starts we push the
   `subagent_type`; when it ends we pop. Other tool events are tagged with
   the current top-of-stack so the UI can nest them under the right
   delegation.

3. **`ClaudeSDKClient`** (not the one-shot `query()`) — keeps the
   conversation alive after the pipeline finishes, so the user can ask
   follow-ups like *"also support negative numbers"* and the orchestrator
   decides whether to re-delegate.

## The frontend

Single HTML file, three columns:

| pane          | shows                                                                |
|---------------|----------------------------------------------------------------------|
| **left**      | chat history + textarea. Send button always works — if a turn is in flight your message is queued and shown as `(queued)`. |
| **center**    | activity timeline. Top-level events are the orchestrator's. When the orchestrator delegates via `Task`, a colored collapsible block opens; everything that subagent does (its `Write`, `Read`, etc. as captured by hooks) appears nested inside it; the block closes with the subagent's return value. The five agent pills at the top light up when active. |
| **right**     | live workspace tree (`./workspace/`). Polled every 2.5s. Click any file to view its current contents. |

Top bar: project path, running cost meter, total model turns, ws status.

Buttons: **Send**, **Interrupt** (cancel the in-flight turn via
`client.interrupt()`), **New session** (drop the SDK session and start
fresh), **Clear workspace**.

## Setup

```bash
# one-time
npm install -g @anthropic-ai/claude-code   # the Python SDK shells out to this
export ANTHROPIC_API_KEY=sk-ant-...         # or `claude` to log in

# every time
./run.sh
```

Open <http://127.0.0.1:8000>.

Try it: type something like

> Build a Python module `roman.py` that parses Roman numeral strings into integers.

Watch the architect pill light up, then coder writes `workspace/roman.py`,
then test-writer adds `workspace/test_roman.py`, the reviewer flags one
thing, the critic delivers a verdict. Then in chat, follow up with:

> Now also support lowercase numerals.

The orchestrator decides which subagent to re-invoke (likely coder + maybe
test-writer) and you'll see another delegation block open.

## Testing the project without the backend

You don't have to run the backend to validate the agent setup. Open the
project directly in the Claude CLI:

```bash
cd claude_project
claude
> /orchestrate build a fizzbuzz function with edge cases
```

If that works, the backend will work too — it's running the exact same
configuration via the SDK.

## File map

```
claude_sdk/
├── claude_project/             ← the Claude project (this is the system)
│   ├── .claude/
│   │   ├── agents/{architect,coder,test-writer,reviewer,critic}.md
│   │   ├── commands/orchestrate.md
│   │   └── settings.json
│   └── workspace/              ← agents write code here
├── backend/
│   ├── main.py                 ← FastAPI + ClaudeSDKClient + hooks
│   └── requirements.txt
├── frontend/
│   └── index.html              ← three-column UI, ~350 lines of vanilla JS
├── run.sh
└── README.md
```

## What to embellish from here

- **Granular streaming** — set `include_partial_messages=True` on
  `ClaudeAgentOptions` to get token-level streaming inside each text block.
- **Per-agent models** — change `model: inherit` to `sonnet`/`opus`/`haiku`
  in any agent .md file (e.g. Critic on Opus, Reviewer on Haiku).
- **Real critic loop** — make the orchestrator re-invoke the coder if the
  critic says FAIL. Just edit `.claude/commands/orchestrate.md`.
- **More subagents** — drop another `.md` in `.claude/agents/`, add it to
  the orchestrator's prompt, and you've got a sixth team member.
- **Session persistence** — wrap each session as a folder of saved events
  for replay.
