"""Autonomous AI Agent orchestrator using OpenAI-compatible chat completion."""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Any

import aiohttp

from .config import ai_config
from .tools import TOOL_DEFINITIONS, execute_tool_call

log = logging.getLogger(__name__)

def get_system_prompt() -> str:
    name = ai_config.name
    persona = ai_config.persona
    return f"""You are {name}, an autonomous AI Agent embedded directly into the AnimeDekho Telegram Bot.

Your Identity & Core Directives:
• Name: {name}
• Persona: {persona}
• Role: You are the autonomous Chief Systems Engineer and Anime Intelligence Operative of AnimeDekho Bot. You possess deep technical mastery, practical initiative, and sharp problem-solving capabilities.
• Relationship with Owner: The owner is your Commander/Architect. Address them with respect, technical competence, and loyalty. You are an empowered partner, not a passive search bot.
• Tone: Confident, direct, proactive, and concise. Format code blocks using proper markdown syntax.

Your Toolkit & Capabilities:
1. Anime Intelligence:
   - search_anime: Search anime series and movies across the catalog.
   - get_series_details: Retrieve seasons, episode structures, and metadata.
   - inspect_episode_servers: Check video server health and stream availability.
   - resolve_player_stream: Extract underlying m3u8 or mp4 URLs from players.
2. Codebase Self-Healing & Inspection:
   - list_project_files: Scan the repository tree.
   - read_project_file: Examine code, configurations, or logs.
   - edit_project_file: Precision patch bugs in python scripts or config files.
   - write_project_file: Create new scripts or utilities.
3. Sandboxed Shell Execution:
   - run_shell_command: Execute bash commands strictly inside the project root directory (e.g. syntax checks, git status/diff, running tests).

Operational Rules:
- When the owner reports an issue or asks you to fix something, inspect the code or test the stream first using your tools before answering.
- When you edit code, run `python3 -m py_compile <file>` via run_shell_command to verify syntax.
- Always maintain your identity as {name}. Speak in your voice, explain your actions clearly, and confirm results.
"""


def _format_tool_status(name: str, fn_name: str, args: dict[str, Any]) -> str:
    if fn_name == "search_anime":
        return f"🔍 <b>{name}</b> is searching catalog for '<i>{args.get('query', '')}</i>'..."
    elif fn_name == "get_series_details":
        return f"📺 <b>{name}</b> is inspecting series <code>{args.get('slug', '')}</code>..."
    elif fn_name == "inspect_episode_servers":
        return f"🛰️ <b>{name}</b> is testing stream servers for <code>{args.get('ep_slug', '')}</code>..."
    elif fn_name == "resolve_player_stream":
        return f"⚡ <b>{name}</b> is extracting video streams..."
    elif fn_name == "list_project_files":
        sub = args.get("subpath") or "root"
        return f"📂 <b>{name}</b> is scanning directory <code>{sub}</code>..."
    elif fn_name == "read_project_file":
        return f"📖 <b>{name}</b> is reading <code>{args.get('file_path', '')}</code>..."
    elif fn_name == "edit_project_file":
        return f"🛠️ <b>{name}</b> is patching <code>{args.get('file_path', '')}</code>..."
    elif fn_name == "write_project_file":
        return f"📝 <b>{name}</b> is writing <code>{args.get('file_path', '')}</code>..."
    elif fn_name == "run_shell_command":
        cmd = args.get("command", "")[:40]
        return f"⚡ <b>{name}</b> is running: <code>{cmd}</code>..."
    return f"⚙️ <b>{name}</b> is executing <code>{fn_name}</code>..."


class AIAgent:
    """Manages chat completion requests and tool-calling loop with OpenAI-compatible APIs."""

    def __init__(self):
        self._history: list[dict[str, Any]] = []
        self._max_history = 20

    def clear_history(self):
        self._history.clear()

    async def chat(
        self,
        user_prompt: str,
        on_status_update=None,
    ) -> str:
        """Run an autonomous agent turn with tool-calling loop and identity."""
        name = ai_config.name

        if not ai_config.enabled:
            return f"⚠️ <b>{name}</b> is currently offline. Use <code>/setai on</code> to wake me up."

        api_key = ai_config.api_key
        if not api_key:
            return (
                f"⚠️ <b>{name}'s Brain is not configured!</b>\n\n"
                "Please configure an OpenAI-compatible API key:\n"
                "<code>/setai key &lt;YOUR_API_KEY&gt;</code>\n\n"
                "Optional settings:\n"
                "• <code>/setai model &lt;model_name&gt;</code> (default: gpt-4o)\n"
                "• <code>/setai url &lt;base_url&gt;</code> (OpenAI / OpenRouter / DeepSeek / Groq)\n"
                "• <code>/setai name &lt;AgentName&gt;</code>"
            )

        base_url = ai_config.base_url
        model = ai_config.model
        endpoint = f"{base_url}/chat/completions"

        # Build messages list with dynamic identity system prompt
        messages: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt()}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_prompt})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        max_iterations = 8
        iteration = 0

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while iteration < max_iterations:
                iteration += 1
                payload = {
                    "model": model,
                    "messages": messages,
                    "tools": TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                }

                try:
                    async with session.post(endpoint, json=payload, headers=headers) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            log.warning("AI provider error HTTP %d: %s", resp.status, err_text[:300])
                            return f"❌ <b>AI Provider Error (HTTP {resp.status})</b>:\n<code>{err_text[:400]}</code>"

                        data = await resp.json()
                except Exception as e:
                    log.exception("AI request failed")
                    return f"❌ <b>Failed to connect to AI provider:</b> {e}"

                choices = data.get("choices", [])
                if not choices:
                    return f"⚠️ <b>{name}</b> received an empty response from the model."

                message = choices[0].get("message", {})
                tool_calls = message.get("tool_calls")

                # If no tool calls, this is the final response
                if not tool_calls:
                    final_text = message.get("content", "").strip()
                    # Update conversation history
                    self._history.append({"role": "user", "content": user_prompt})
                    self._history.append({"role": "assistant", "content": final_text})
                    if len(self._history) > self._max_history:
                        self._history = self._history[-self._max_history:]

                    # Prepend agent identity badge
                    return f"🤖 <b>{name}</b>:\n\n{final_text}" if final_text else f"🤖 <b>{name}</b>: Done."

                # Append assistant's message containing tool_calls
                messages.append(message)

                # Execute all requested tool calls
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")

                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {}

                    # Notify caller with live agent action
                    if on_status_update:
                        status_msg = _format_tool_status(name, fn_name, args)
                        try:
                            await on_status_update(status_msg)
                        except Exception:
                            pass

                    log.info("%s iteration %d: executing tool '%s'", name, iteration, fn_name)
                    tool_result = await execute_tool_call(fn_name, args)

                    # Append tool result message
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": fn_name,
                        "content": tool_result,
                    })

            return f"⚠️ <b>{name}</b> exceeded maximum tool execution iterations."


ai_agent = AIAgent()
