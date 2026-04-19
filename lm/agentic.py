"""agentic — run a coding agent in unattended mode."""

import json
import os
import signal
import subprocess
import sys


def run_agent(prompt: str, agent: str | None = None, verbose: bool = False) -> str | None:
    """Run a coding agent and return the result.
    
    Args:
        prompt: The prompt to send to the agent
        agent: Agent to use (claude, codex, opencode). Defaults to SHORTCUTS_CODING_AGENT env var.
        verbose: Print intermediate tool usage to stderr
    
    Returns:
        The final result from the agent, or None on error
    """
    if agent is None:
        agent = os.environ.get("SHORTCUTS_CODING_AGENT", "claude")

    if agent == "claude":
        return _run_claude(prompt, verbose)
    elif agent == "codex":
        return _run_codex(prompt)
    elif agent == "opencode":
        return _run_opencode(prompt)
    
    print(f"error: unsupported agent '{agent}'", file=sys.stderr)
    print("Supported: claude, codex, opencode. Set SHORTCUTS_CODING_AGENT.", file=sys.stderr)
    return None


def _format_tool_args(tool_input: dict) -> str:
    """Format tool_use input as key=value pairs."""
    parts = []
    for key, value in list(tool_input.items())[:3]:
        display = str(value)[:60]
        if len(str(value)) > 60:
            display += "…"
        parts.append(f"{key}={display}")
    return ", ".join(parts)


def _run_claude(prompt: str, verbose: bool = False) -> str | None:
    """Run Claude CLI with stream-json output, parsing events."""
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose" if verbose else "--quiet",
        "--dangerously-skip-permissions",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
    )

    def forward_signal(signum: int, _frame: object) -> None:
        if proc.poll() is None:
            proc.send_signal(signum)

    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGTERM, forward_signal)

    final_result = None

    for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use" and verbose:
                name = block.get("name", "unknown")
                print(f"▶ {name}", file=sys.stderr)

        elif event_type == "assistant":
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "tool_use" and verbose:
                    name = block.get("name", "unknown")
                    args_str = _format_tool_args(block.get("input", {}))
                    print(f"  {name}({args_str})", file=sys.stderr)

        elif event_type == "result":
            subtype = event.get("subtype", "")
            if subtype == "success":
                duration = event.get("duration_ms", 0)
                turns = event.get("num_turns", 0)
                cost = event.get("total_cost_usd", 0)
                final_result = event.get("result", "")
                if verbose:
                    secs = duration / 1000
                    print(f"✓ Done ({turns} turns, {secs:.1f}s, ${cost:.2f})", file=sys.stderr)
            elif subtype == "error":
                error_msg = event.get("error", "unknown error")
                print(f"error: {error_msg}", file=sys.stderr)

    proc.wait()

    if proc.returncode != 0 and final_result is None:
        return None

    return final_result


def _run_codex(prompt: str) -> str | None:
    """Exec into codex."""
    try:
        os.execvp("codex", ["codex", "exec", prompt, "--dangerously-bypass-approvals-and-sandbox"])
    except FileNotFoundError:
        print("error: codex not found", file=sys.stderr)
    return None


def _run_opencode(prompt: str) -> str | None:
    """Run opencode in unattended mode."""
    try:
        os.execvp("opencode", ["opencode", "run", prompt])
    except FileNotFoundError:
        print("error: opencode not found", file=sys.stderr)
    return None