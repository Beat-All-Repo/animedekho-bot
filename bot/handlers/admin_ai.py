"""Owner AI Agent handlers — configuration and chat interaction."""

from __future__ import annotations
import logging

from bot.telegram import Client, enums
from bot.telegram.types import Message

from bot.ai import ai_config, ai_agent
from bot.auth import require_owner
from utils.helpers import esc

log = logging.getLogger(__name__)


@require_owner
async def cmd_setai(client: Client, message: Message):
    """Owner command to view or update AI settings dynamically."""
    text = message.text.strip()
    parts = text.split(maxsplit=2)

    if len(parts) == 1:
        # Show status
        help_text = (
            f"{ai_config.get_status_summary()}\n"
            f"<b>Agent Configuration Commands:</b>\n"
            f"• <code>/setai name &lt;Name&gt;</code> — Set agent identity name\n"
            f"• <code>/setai persona &lt;Description&gt;</code> — Set personality/vibe\n"
            f"• <code>/setai key &lt;API_KEY&gt;</code> — Set API key\n"
            f"• <code>/setai url &lt;BASE_URL&gt;</code> — Set base URL (OpenAI/Groq/OpenRouter/DeepSeek)\n"
            f"• <code>/setai model &lt;MODEL&gt;</code> — Set model name\n"
            f"• <code>/setai limit &lt;NUMBER&gt;</code> — Set max tool iterations (default: 20)\n"
            f"• <code>/setai on</code> | <code>/setai off</code> — Bring agent online/offline\n"
            f"• <code>/setai memory</code> — View stored long-term memory & conversation stats\n"
            f"• <code>/setai forget &lt;key&gt;</code> — Delete a specific permanent fact\n"
            f"• <code>/setai clear</code> — Clear conversation history\n"
            f"• <code>/setai test</code> — Ping agent & verify tool readiness\n\n"
            f"<b>Interact with your Agent:</b>\n"
            f"Send <code>/ai &lt;instruction or question&gt;</code>\n"
            f"<i>e.g. /ai check why a download failed and inspect bot/downloader.py</i>"
        )
        await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)
        return

    subcmd = parts[1].lower()

    if subcmd in ("on", "enable"):
        await ai_config.set_enabled(True)
        await message.reply_text(f"✅ <b>{ai_config.name}</b> is now <b>Online</b>.", parse_mode=enums.ParseMode.HTML)
        return

    if subcmd in ("off", "disable"):
        await ai_config.set_enabled(False)
        await message.reply_text(f"🔴 <b>{ai_config.name}</b> is now <b>Offline</b>.", parse_mode=enums.ParseMode.HTML)
        return

    if subcmd in ("clear", "reset"):
        await ai_agent.clear_history(message.chat.id)
        await message.reply_text(f"🧹 <b>{ai_config.name}</b>'s conversation memory has been cleared.", parse_mode=enums.ParseMode.HTML)
        return

    if subcmd in ("memory", "facts"):
        from bot.database import db
        if not db:
            await message.reply_text("⚠️ Database is not connected.", parse_mode=enums.ParseMode.HTML)
            return
        facts = await db.get_ai_facts(message.chat.id)
        history = await db.get_ai_history(message.chat.id, limit=50)

        lines = [f"🧠 <b>{ai_config.name}'s Memory Status:</b>\n"]
        lines.append(f"• Active conversation turns in DB: <b>{len(history)}</b>")
        if facts:
            lines.append(f"\n<b>Permanent Facts ({len(facts)}):</b>")
            for f in facts:
                lines.append(f"• <code>{esc(f.get('key', ''))}</code>: {esc(f.get('value', ''))}")
        else:
            lines.append("\n<i>No permanent facts stored yet.</i>\nTell the AI e.g. '<i>remember that I prefer 1080p</i>' or use <code>/ai remember...</code>")
        await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
        return

    if subcmd == "test":
        status_msg = await message.reply_text(f"🧪 Testing <b>{ai_config.name}</b>'s cognitive connection...", parse_mode=enums.ParseMode.HTML)
        reply = await ai_agent.chat("Introduce yourself briefly, state your role and your ready status.")
        await status_msg.edit_text(reply, parse_mode=enums.ParseMode.HTML)
        return

    if len(parts) < 3:
        await message.reply_text(f"⚠️ Missing value. Usage: <code>/setai {subcmd} &lt;value&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    val = parts[2].strip()

    if subcmd in ("name", "agent_name"):
        await ai_config.set_name(val)
        await message.reply_text(f"✅ Agent identity updated to <b>{ai_config.name}</b>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("persona", "personality"):
        await ai_config.set_persona(val)
        await message.reply_text(f"✅ Agent persona updated to:\n<i>{ai_config.persona}</i>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("key", "api_key"):
        await ai_config.set_api_key(val)
        masked = f"{val[:4]}...{val[-4:]}" if len(val) > 8 else "***"
        await message.reply_text(f"✅ API key updated to <code>{masked}</code>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("url", "base_url"):
        await ai_config.set_base_url(val)
        await message.reply_text(f"✅ Base URL updated to <code>{ai_config.base_url}</code>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("model", "model_name"):
        await ai_config.set_model(val)
        await message.reply_text(f"✅ Model updated to <code>{ai_config.model}</code>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("limit", "iterations", "max_iterations"):
        try:
            val_int = int(val)
            await ai_config.set_max_iterations(val_int)
            await message.reply_text(f"✅ Tool execution limit updated to <b>{ai_config.max_iterations}</b> iterations.", parse_mode=enums.ParseMode.HTML)
        except ValueError:
            await message.reply_text("⚠️ Please provide a valid integer (e.g. <code>/setai limit 20</code>).", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("forget", "delete_fact", "forget_fact"):
        from bot.database import db
        if not db:
            await message.reply_text("⚠️ Database is not connected.", parse_mode=enums.ParseMode.HTML)
            return
        deleted = await db.delete_ai_fact(message.chat.id, val)
        if deleted:
            await message.reply_text(f"✅ Memory fact <code>{esc(val)}</code> has been forgotten.", parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(f"⚠️ No memory fact found with key <code>{esc(val)}</code>.", parse_mode=enums.ParseMode.HTML)

    else:
        await message.reply_text(f"⚠️ Unknown setting '<code>{esc(subcmd)}</code>'. Type <code>/setai</code> for help.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_ai(client: Client, message: Message):
    """Direct conversation with the autonomous AI Agent."""
    parts = message.text.split(maxsplit=1)
    name = ai_config.name
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            f"🤖 <b>{name} — Autonomous Agent</b>\n\n"
            f"Usage: <code>/ai &lt;your instruction or question&gt;</code>\n\n"
            f"<i>Examples:</i>\n"
            f"• <code>/ai search Solo Leveling and inspect season 1 episodes</code>\n"
            f"• <code>/ai inspect bot/downloader.py and check if there are any bugs</code>\n"
            f"• <code>/ai test video streams for yowayowa-sensei-1x1</code>\n"
            f"• <code>/ai run git status</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    prompt = parts[1].strip()
    status_msg = await message.reply_text(f"🤖 <i>{name} is thinking & analyzing...</i>", parse_mode=enums.ParseMode.HTML)

    import time
    last_edit = [0.0]

    async def _on_status_update(status_text: str):
        now = time.time()
        # Throttle edits to at least 1.0 second apart to avoid Telegram 429 FloodWait
        if now - last_edit[0] >= 1.0:
            last_edit[0] = now
            try:
                await status_msg.edit_text(status_text, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

    try:
        reply = await ai_agent.chat(prompt, on_status_update=_on_status_update, client=client, chat_id=message.chat.id)
        # Split message if it exceeds Telegram 4096 character limit
        if len(reply) > 4000:
            chunks = [reply[i:i + 3900] for i in range(0, len(reply), 3900)]
            await status_msg.edit_text(chunks[0], parse_mode=enums.ParseMode.HTML)
            for chunk in chunks[1:]:
                await message.reply_text(chunk, parse_mode=enums.ParseMode.HTML)
        else:
            await status_msg.edit_text(reply, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        log.exception("Error in /ai handler")
        await status_msg.edit_text(f"❌ <b>{name} Error:</b> {esc(str(e))}", parse_mode=enums.ParseMode.HTML)
