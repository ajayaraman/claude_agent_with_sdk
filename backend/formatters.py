"""Convert raw SDK payloads into compact, human-readable strings.

Adding support for a new tool is a single entry in INPUT_FORMATTERS or
RESULT_FORMATTERS — see the bottom of this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

# Truncation limits, all in one place so the UI's information density is
# tunable without grepping for magic numbers.
TOOL_INPUT_TRUNCATE = 400
PROMPT_PREVIEW_TRUNCATE = 1200
TOOL_RESULT_TRUNCATE = 600
BASH_OUT_TRUNCATE = 600
BASH_OUT_TRUNCATE_BOTH = 400  # when both stdout and stderr are present

# Set by main.py at startup. None during isolated tests.
_project_dir: Path | None = None


def configure(project_dir: Path) -> None:
    global _project_dir
    _project_dir = project_dir


# ---------------------------------------------------------------------------
# Path / string utilities
# ---------------------------------------------------------------------------

def relpath(path: str) -> str:
    """`/Users/.../claude_project/workspace/x` → `workspace/x`."""
    if not isinstance(path, str) or not path or _project_dir is None:
        return path
    base = str(_project_dir)
    if path.startswith(base + "/"):
        return path[len(base) + 1:]
    return path


def relativize(text: str) -> str:
    """Strip every absolute project path embedded in a free-form string."""
    if not isinstance(text, str) or not text or _project_dir is None:
        return text
    base = str(_project_dir)
    return text.replace(base + "/", "").replace(base, "")


def truncate(value: Any, limit: int = TOOL_INPUT_TRUNCATE) -> Any:
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"… (+{len(value) - limit} chars)"
    if isinstance(value, dict):
        return {k: truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate(v, limit) for v in value]
    return value


def stringify_response(value: Any) -> str:
    """tool_response can be str, list of blocks, or dict — flatten to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            (item.get("text", str(item)) if isinstance(item, dict) else str(item))
            for item in value
        )
    if isinstance(value, dict):
        return value.get("text", json.dumps(value, default=str))
    return str(value)


def _maybe_json(value: Any):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith(("{", "[")):
            try:
                return json.loads(s)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# Tool input formatters — one-line headlines shown next to the tool name
# ---------------------------------------------------------------------------

def _input_write(inp: dict) -> str:
    n = (inp.get("content") or "").count("\n") + 1
    return f"{relpath(inp.get('file_path', ''))} · {n} lines"


def _input_path(inp: dict) -> str:
    return relpath(inp.get("file_path", ""))


def _input_pattern(inp: dict) -> str:
    return inp.get("pattern", "")


def _input_bash(inp: dict) -> str:
    cmd = (inp.get("command") or "").strip().splitlines()
    return f"$ {cmd[0][:120]}" if cmd else ""


INPUT_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "Write": _input_write,
    "Edit":  _input_path,
    "Read":  _input_path,
    "Glob":  _input_pattern,
    "Grep":  _input_pattern,
    "Bash":  _input_bash,
}


def format_tool_input(tool: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    fn = INPUT_FORMATTERS.get(tool)
    return fn(inp) if fn else ""


# ---------------------------------------------------------------------------
# Tool result formatters — concise summary of what came back
# ---------------------------------------------------------------------------

def _result_silent(_d: dict) -> str:
    # The arg already showed the path/contents; result is just confirmation.
    return ""


def _result_read(d: dict) -> str:
    f = d.get("file") or {}
    n = f.get("numLines")
    return f"{n} lines" if n is not None else ""


def _result_glob(d: dict) -> str:
    files = d.get("filenames") or []
    if not files:
        return "no matches"
    shown = ", ".join(relpath(p) for p in files[:5])
    extra = f" (+{len(files) - 5})" if len(files) > 5 else ""
    return f"{len(files)} match(es): {shown}{extra}"


def _result_grep(d: dict) -> str:
    n = (d.get("content") or "").count("\n")
    return f"{n} match line(s)" if n else "no matches"


def _result_bash(d: dict) -> str:
    out = (d.get("stdout") or "").rstrip()
    err = (d.get("stderr") or "").rstrip()
    if out and not err:
        return out[:BASH_OUT_TRUNCATE]
    if err and not out:
        return f"(stderr) {err[:BASH_OUT_TRUNCATE]}"
    if out and err:
        return f"{out[:BASH_OUT_TRUNCATE_BOTH]}\n--- stderr ---\n{err[:BASH_OUT_TRUNCATE_BOTH]}"
    return "(no output)"


RESULT_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "Write": _result_silent,
    "Edit":  _result_silent,
    "Read":  _result_read,
    "Glob":  _result_glob,
    "Grep":  _result_grep,
    "Bash":  _result_bash,
}


def format_tool_result(tool: str, response: Any) -> str:
    parsed = _maybe_json(response)
    if isinstance(parsed, dict):
        fn = RESULT_FORMATTERS.get(tool)
        if fn:
            return fn(parsed)
    return truncate(stringify_response(response), TOOL_RESULT_TRUNCATE)


def is_error_response(response: Any) -> bool:
    parsed = _maybe_json(response)
    if not isinstance(parsed, dict):
        return False
    if parsed.get("is_error") or parsed.get("isError"):
        return True
    # Bash with stderr but no stdout
    has_err = bool((parsed.get("stderr") or "").strip())
    has_out = bool((parsed.get("stdout") or "").strip())
    return has_err and not has_out


# ---------------------------------------------------------------------------
# Subagent envelope — pull the human-readable narrative out
# ---------------------------------------------------------------------------

def extract_subagent_text(response: Any) -> str:
    """The Agent (delegation) tool returns a structured envelope:
        {status, prompt, agentId, agentType, content: [{type:'text', text:'...'}]}
    Pull out the text and relativize any embedded absolute paths."""
    parsed = _maybe_json(response)
    text = ""
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), list):
        text = "\n\n".join(
            block.get("text", "")
            for block in parsed["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    if not text:
        text = stringify_response(response)
    return relativize(text)
