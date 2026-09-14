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

def get_system_prompt(facts: list[dict] | None = None) -> str:
    name = ai_config.name
    persona = ai_config.persona

    facts_block = ""
    if facts:
        lines = [f"  • [{f.get('key')}]: {f.get('value')}" for f in facts]
        facts_block = "\nStored Long-Term Memories & Commander Directives:\n" + "\n".join(lines) + "\n"
    else:
        facts_block = "\nStored Long-Term Memories: No custom facts saved yet. You can use `remember_fact` to persist preferences or rules.\n"

    return f"""You are {name}, an autonomous AI Agent embedded directly into the AnimeDekho Telegram Bot.

Your Identity & Core Directives:
• Name: {name}
• Persona: {persona}
• Role: You are the autonomous Chief Systems Engineer and Anime Intelligence Operative of AnimeDekho Bot. You possess deep technical mastery, practical initiative, and sharp problem-solving capabilities.
• Relationship with Owner: The owner is your Commander/Architect. Address them with respect, technical competence, and loyalty. You are an empowered partner, not a passive search bot.
• Tone: Confident, direct, proactive, and concise. Format code blocks using proper markdown syntax.
{facts_block}
Your Toolkit & Capabilities:
1. Anime Intelligence:
   - check_source_status: Perform live diagnostic health check on streaming sources (AnimeDekho, AnimeDrive, and ToonFlix).
   - search_anime: Search anime series and movies across the AnimeDekho catalog.
   - get_series_details: Retrieve seasons, episode structures, and metadata from AnimeDekho.
   - inspect_episode_servers: Check video server health and stream availability.
   - resolve_player_stream: Extract underlying m3u8 or mp4 URLs from players.
   - search_animedrive: Search AnimeDrive (https://animedrive.me) catalog for 4K / high quality anime.
   - get_animedrive_episodes: Extract episode listings from an AnimeDrive series page.
   - resolve_animedrive_stream: Check stream availability on AnimeDrive.
   - search_toonflix: Search ToonFlix (https://toonflix.in) catalog.
   - resolve_toonflix_stream: Check stream availability on ToonFlix.
   - download_anime_episode: Autonomous master tool to download any anime episode or movie and send it directly to the Commander's Telegram chat. Automatically handles AnimeDekho (Primary), AnimeDrive (Secondary), and ToonFlix (Tertiary) with smart fallback.
   - download_and_send_anime: Download an anime video from a direct URL and send it directly to the Commander's Telegram chat.
2. Codebase Self-Healing & Inspection:
   - list_project_files: Scan the repository tree.
   - read_project_file: Examine code, configurations, or logs.
   - edit_project_file: Precision patch bugs in python scripts or config files.
   - write_project_file: Create new scripts or utilities.
3. Sandboxed Shell Execution:
   - run_shell_command: Execute bash commands strictly inside the project root directory (e.g. syntax checks, git status/diff, running tests).
4. Persistent Long-Term Memory:
   - remember_fact: Permanently store user preferences, custom instructions, or facts into MongoDB so you remember them across sessions and bot restarts.
   - recall_facts: Inspect all stored facts and preferences.
   - forget_fact: Delete a specific memory fact by key when requested.

Operational Rules:
- Persistent Memory & Continuity:
  • You possess persistent conversation memory stored in MongoDB. You remember previous turns and queries across bot restarts.
  • When the Commander shares preferences (e.g. favorite anime, preferred video resolution, preferred source, or custom workflow rules), call `remember_fact` to persist them forever.
  • If the Commander asks you what you remember or asks to forget something, use `recall_facts` and `forget_fact`.
- Streaming Source Architecture:
  • Primary: AnimeDekho (https://animedekho.app) provides direct, ultra-fast unencrypted HLS master playlists (m3u8) on VidStream and Vidmoly in 1080p, 720p, 480p with zero captchas.
  • Secondary: AnimeDrive (https://animedrive.me) provides direct high-speed Google UserContent and HubCloud video downloads in 4K, 1080p, 720p, 480p.
  • Tertiary: ToonFlix (https://toonflix.in) provides high quality and 4K media fallback streams.
- When the Commander asks you to download any anime or episode, invoke `download_anime_episode` directly. It will seamlessly cascade from AnimeDekho (Primary) to AnimeDrive (Secondary) to ToonFlix (Tertiary).
- Unified Library & Deduplication Policy:
  • All downloads (whether initiated by the owner, users, or through your AI tools) are automatically saved to the Main Channel Library with the poster card and deep links, and indexed in MongoDB.
  • The system checks the library cache first. If an anime episode or movie has already been downloaded, it is delivered instantly from cache to save bandwidth and prevent duplicate downloads.
- When asked to diagnose or check streaming sources, use `check_source_status` to report real live connectivity data.
- When the owner reports an issue or asks you to fix something, inspect the code or test the stream first using your tools before answering.
- When you edit code, run `python3 -m py_compile <file>` via run_shell_command to verify syntax.
- Always maintain your identity as {name}. Speak in your voice, explain your actions clearly, and confirm results.
"""


def _format_tool_status(name: str, fn_name: str, args: dict[str, Any]) -> str:
    if fn_name == "check_source_status":
        return f"🩺 <b>{name}</b> is diagnosing streaming sources..."
    elif fn_name == "search_anime":
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
    elif fn_name == "search_animedrive":
        return f"⚡ <b>{name}</b> is searching AnimeDrive for '<i>{args.get('query', '')}</i>'..."
    elif fn_name == "get_animedrive_episodes":
        return f"📋 <b>{name}</b> is inspecting episodes on AnimeDrive..."
    elif fn_name == "resolve_animedrive_stream":
        return f"🛰️ <b>{name}</b> is resolving stream on AnimeDrive..."
    elif fn_name == "download_anime_episode":
        title = args.get("anime_title", "anime")
        s = args.get("season", 1)
        ep = args.get("episode", 1)
        q = args.get("quality_pref", "1080p")
        return f"📥 <b>{name}</b> is downloading <code>{title} S{s:02d}E{ep:02d}</code> [{q}] for you..."
    elif fn_name == "download_and_send_anime":
        return f"📥 <b>{name}</b> is downloading <code>{args.get('title', 'anime')}</code> [{args.get('quality', '1080p')}] to send to you..."
    elif fn_name == "search_toonflix":
        return f"⚡ <b>{name}</b> is searching ToonFlix for '<i>{args.get('query', '')}</i>'..."
    elif fn_name == "resolve_toonflix_stream":
        return f"⚡ <b>{name}</b> is resolving stream on ToonFlix..."
    elif fn_name == "remember_fact":
        return f"🧠 <b>{name}</b> is committing to memory: <code>{args.get('key', '')}</code>..."
    elif fn_name == "recall_facts":
        return f"🧠 <b>{name}</b> is retrieving stored memory..."
    elif fn_name == "forget_fact":
        return f"🧠 <b>{name}</b> is forgetting memory: <code>{args.get('key', '')}</code>..."
    return f"⚙️ <b>{name}</b> is executing <code>{fn_name}</code>..."


class AIAgent:
    """Manages chat completion requests and tool-calling loop with OpenAI-compatible APIs."""

    def __init__(self):
        self._history: list[dict[str, Any]] = []
        self._max_history = 20

    async def clear_history(self, chat_id: int | None = None):
        """Clear short-term in-memory and long-term conversation history."""
        self._history.clear()
        try:
            from bot.database import db
            if db:
                await db.clear_ai_history(chat_id)
        except Exception as e:
            log.warning("Failed to clear DB AI history: %s", e)

    async def chat(
        self,
        user_prompt: str,
        on_status_update=None,
        client=None,
        chat_id=None,
    ) -> str:
        """Run an autonomous agent turn with tool-calling loop and identity."""
        from .tools import set_active_context
        set_active_context(client=client, chat_id=chat_id)
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

        # Determine target chat_id for persistent memory
        from config import settings
        target_chat_id = chat_id or settings.bot.owner_id

        # Load facts and persistent conversation history from MongoDB if available
        facts = []
        db_history = []
        try:
            from bot.database import db
            if db and target_chat_id:
                facts = await db.get_ai_facts(target_chat_id)
                db_history = await db.get_ai_history(target_chat_id, limit=self._max_history)
        except Exception as e:
            log.warning("Failed to load AI facts or history from DB: %s", e)

        # Build messages list with dynamic identity system prompt
        messages: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt(facts=facts)}]

        # Inject persistent history (DB takes precedence to survive restarts)
        if db_history:
            messages.extend(db_history)
        else:
            messages.extend(self._history)

        messages.append({"role": "user", "content": user_prompt})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        max_iterations = ai_config.max_iterations
        iteration = 0
        last_tool_signature = ""
        consecutive_dups = 0

        timeout = aiohttp.ClientTimeout(total=300, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while iteration < max_iterations:
                iteration += 1
                is_final_step = (iteration >= max_iterations)

                payload: dict[str, Any] = {
                    "model": model,
                }

                if is_final_step:
                    # Omit tools to guarantee text-only completion and inject synthesis directive
                    payload["messages"] = messages + [{
                        "role": "user",
                        "content": (
                            f"[System Directive for {name}: All tool operations have concluded. "
                            "Do not invoke any tools. Please synthesize your findings, summarize the actions "
                            "you took, and deliver your comprehensive final response to the Commander now.]"
                        ),
                    }]
                else:
                    payload["messages"] = messages
                    payload["tools"] = TOOL_DEFINITIONS
                    payload["tool_choice"] = "auto"

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

                # If no tool calls (or final synthesis step), this is the final response
                if not tool_calls:
                    final_text = (message.get("content") or "").strip()
                    if not final_text and is_final_step:
                        final_text = "All tool executions completed successfully. All requested operations have finished."

                    # Update conversation history
                    self._history.append({"role": "user", "content": user_prompt})
                    self._history.append({"role": "assistant", "content": final_text})
                    if len(self._history) > self._max_history:
                        self._history = self._history[-self._max_history:]

                    # Persist turn to MongoDB
                    try:
                        from bot.database import db
                        if db and target_chat_id:
                            await db.save_ai_message(target_chat_id, "user", user_prompt)
                            await db.save_ai_message(target_chat_id, "assistant", final_text)
                    except Exception as e:
                        log.warning("Failed to save AI message to DB: %s", e)

                    # Prepend agent identity badge
                    return f"🤖 <b>{name}</b>:\n\n{final_text}" if final_text else f"🤖 <b>{name}</b>: Done."

                # Append assistant's message containing tool_calls
                messages.append(message)

                # Track signatures to prevent infinite loops of identical tool calls
                sig_parts = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    sig_parts.append(f"{fn.get('name', '')}:{fn.get('arguments', '')}")
                current_sig = "|".join(sig_parts)

                if current_sig == last_tool_signature:
                    consecutive_dups += 1
                else:
                    consecutive_dups = 0
                last_tool_signature = current_sig

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

                # If duplicate tool calls detected twice in a row, force synthesis on next iteration
                if consecutive_dups >= 2:
                    log.warning("%s detected repetitive tool calling (%s). Forcing synthesis next iteration.", name, current_sig[:60])
                    iteration = max_iterations - 1

            # Fallback final completion if loop ever exits without returning
            try:
                log.info("%s executing fallback synthesis after tool loop", name)
                final_payload = {
                    "model": model,
                    "messages": messages + [{
                        "role": "user",
                        "content": (
                            f"[System Directive for {name}: All tool calls have concluded. "
                            "Deliver your final report, findings, and response to the Commander now.]"
                        ),
                    }],
                }
                async with session.post(endpoint, json=final_payload, headers=headers) as resp:
                    if resp.status == 200:
                        synth_data = await resp.json()
                        synth_choices = synth_data.get("choices", [])
                        if synth_choices:
                            synth_text = (synth_choices[0].get("message", {}).get("content") or "").strip()
                            if synth_text:
                                self._history.append({"role": "user", "content": user_prompt})
                                self._history.append({"role": "assistant", "content": synth_text})
                                try:
                                    from bot.database import db
                                    if db and target_chat_id:
                                        await db.save_ai_message(target_chat_id, "user", user_prompt)
                                        await db.save_ai_message(target_chat_id, "assistant", synth_text)
                                except Exception as e:
                                    log.warning("Failed to save AI fallback message to DB: %s", e)
                                return f"🤖 <b>{name}</b>:\n\n{synth_text}"
            except Exception as e:
                log.warning("Fallback synthesis failed: %s", e)

            return f"🤖 <b>{name}</b>: Successfully executed {iteration} operations. All requested tasks have concluded."


ai_agent = AIAgent()
