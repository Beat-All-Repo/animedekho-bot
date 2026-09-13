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

SYSTEM_PROMPT = """You are the autonomous AI Agent of the AnimeDekho Telegram Bot.
You assist the bot owner in managing, monitoring, debugging, and self-healing the bot codebase, as well as searching and discovering anime.

Your capabilities:
1. Anime catalog discovery: search series, fetch details, inspect episode stream URLs, and examine server health.
2. Codebase inspection & self-healing: list project files, read code, edit code to fix bugs, and create new files.
3. Sandboxed bash execution: run tests, check syntax, run git commands, inspect logs within the project root directory.

Guidelines:
- When asked to inspect an issue, examine the relevant code or logs first using your tools.
- When fixing code, be precise and verify syntax after editing.
- Never try to escape the project directory. All actions must remain within the repository root.
- Keep responses concise, clear, and informative. Format code snippets in markdown.
"""


class AIAgent:
    """Manages chat completion requests and tool-calling loop with OpenAI-compatible APIs."""

    def __init__(self):
        self._history: list[dict[str, Any]] = []
        self._max_history = 20

    def clear_history(self):
        self._history.clear()

    async def chat(self, user_prompt: str) -> str:
        """Run an autonomous agent turn with tool-calling loop."""
        if not ai_config.enabled:
            return "⚠️ AI Agent is currently disabled. Use <code>/setai on</code> to enable it."

        api_key = ai_config.api_key
        if not api_key:
            return (
                "⚠️ <b>AI API Key not set!</b>\n\n"
                "Please configure your API key using:\n"
                "<code>/setai key &lt;YOUR_API_KEY&gt;</code>\n\n"
                "You can also set the model and base URL:\n"
                "• <code>/setai model &lt;model_name&gt;</code>\n"
                "• <code>/setai url &lt;base_url&gt;</code>"
            )

        base_url = ai_config.base_url
        model = ai_config.model
        endpoint = f"{base_url}/chat/completions"

        # Build messages list
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_prompt})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Multi-step tool execution loop (max 8 iterations)
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
                    return "⚠️ AI returned an empty response."

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
                    return final_text or "Done."

                # Append assistant's message containing tool_calls
                messages.append(message)

                # Execute all requested tool calls in parallel or sequence
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")

                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {}

                    log.info("Agent iteration %d: executing tool '%s'", iteration, fn_name)
                    tool_result = await execute_tool_call(fn_name, args)

                    # Append tool result message
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": fn_name,
                        "content": tool_result,
                    })

            return "⚠️ Agent exceeded maximum tool call iterations."


ai_agent = AIAgent()
