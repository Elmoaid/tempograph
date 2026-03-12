"""Minimal SWE-bench agent: issue → bash commands → patch.

Adapted from the mini-swe-agent pattern. Uses Claude to read an issue,
explore the repo via shell commands, and produce a git diff patch.

The agent loop:
1. LLM sees issue + repo context → generates a bash command
2. Command runs in the repo directory → output captured
3. LLM sees output → generates next command OR submits patch
4. Repeat until patch or max steps reached
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SYSTEM_PROMPT = """\
You are a software engineer fixing a bug in a Python repository.
You have access to bash commands in the repo directory.

RULES:
- Respond with EXACTLY ONE of:
  a) A bash command wrapped in ```bash ... ``` to explore or edit the repo
  b) The string SUBMIT when you are done and ready to submit your changes
- Do NOT explain your reasoning — just output the command or SUBMIT
- Use standard tools: grep, find, cat, sed, python3, git diff, etc.
- Make minimal, targeted fixes. Do not refactor unrelated code.
- When done editing, run the relevant tests to verify your fix.
- When satisfied, respond with SUBMIT

You will see the output of each command and can issue more commands.
"""

MAX_STEPS = 15
COMMAND_TIMEOUT = 30  # seconds per command


async def run_agent(
    repo_path: Path,
    issue_text: str,
    *,
    context: str = "",
    model: str = "claude-haiku-4-5-20251001",
    max_steps: int = MAX_STEPS,
) -> dict:
    """Run the agent loop on a single SWE-bench instance.

    Returns dict with:
        patch: str — the git diff (empty if failed)
        steps: int — number of steps taken
        trajectory: list[dict] — full conversation history
    """
    try:
        import anthropic
    except ImportError:
        print("Install anthropic: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.AsyncAnthropic()

    # Build initial user message
    user_parts = []
    if context:
        user_parts.append(f"## Repository Structure (from tempograph)\n\n{context}\n")
    user_parts.append(f"## Issue to Fix\n\n{issue_text}\n")
    user_parts.append("Explore the repo and fix this issue. Start by finding the relevant code.")

    messages = [{"role": "user", "content": "\n".join(user_parts)}]
    trajectory = []

    for step in range(max_steps):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            assistant_text = response.content[0].text.strip()
        except Exception as e:
            print(f"    API error at step {step}: {e}", file=sys.stderr)
            break

        trajectory.append({"role": "assistant", "content": assistant_text, "step": step})

        # Check for SUBMIT
        if "SUBMIT" in assistant_text and "```" not in assistant_text:
            break

        # Extract bash command
        command = _extract_command(assistant_text)
        if not command:
            # No command found — ask LLM to try again
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({"role": "user", "content": "Please respond with a bash command in ```bash ... ``` or SUBMIT."})
            continue

        # Execute command
        output = _run_command(command, repo_path)
        trajectory.append({"role": "command", "command": command, "output": output[:3000], "step": step})

        # Feed output back to LLM
        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": f"```\n{output[:3000]}\n```"})

    # Collect the patch
    patch = _get_patch(repo_path)

    return {
        "patch": patch,
        "steps": len([t for t in trajectory if t["role"] == "assistant"]),
        "trajectory": trajectory,
    }


def _extract_command(text: str) -> str | None:
    """Extract a bash command from ```bash ... ``` blocks."""
    import re
    match = re.search(r'```(?:bash|sh)?\s*\n(.+?)```', text, re.DOTALL)
    if match:
        cmd = match.group(1).strip()
        # Safety: block destructive commands
        dangerous = ["rm -rf /", "rm -rf ~", ":(){ :|:& };:", "mkfs", "dd if="]
        if any(d in cmd for d in dangerous):
            return None
        return cmd
    return None


def _run_command(command: str, cwd: Path) -> str:
    """Execute a shell command in the repo directory."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out)"
    except Exception as e:
        return f"(error: {e})"


def _get_patch(repo_path: Path) -> str:
    """Get the git diff of all changes made in the repo."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return ""
