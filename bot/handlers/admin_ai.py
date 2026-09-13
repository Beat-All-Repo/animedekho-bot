"""Owner AI Agent handlers — configuration and chat interaction."""

from __future__ import annotations
import logging

from pyrogram import Client, enums
from pyrogram.types import Message

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
            f"<b>Commands:</b>\n"
            f"• <code>/setai key &lt;API_KEY&gt;</code> — Set API key\n"
            f"• <code>/setai url &lt;BASE_URL&gt;</code> — Set base URL\n"
            f"• <code>/setai model &lt;MODEL&gt;</code> — Set model name\n"
            f"• <code>/setai on</code> | <code>/setai off</code> — Enable/disable AI\n"
            f"• <code>/setai clear</code> — Clear chat history\n"
            f"• <code>/setai test</code> — Test API connection\n\n"
            f"<b>How to use AI:</b>\n"
            f"Send <code>/ai &lt;instruction or question&gt;</code>\n"
            f"<i>e.g. /ai check the downloader logs and find out why Naruto episode 1 failed</i>"
        )
        await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)
        return

    subcmd = parts[1].lower()

    if subcmd in ("on", "enable"):
        await ai_config.set_enabled(True)
        await message.reply_text("✅ AI Agent has been <b>enabled</b>.", parse_mode=enums.ParseMode.HTML)
        return

    if subcmd in ("off", "disable"):
        await ai_config.set_enabled(False)
        await message.reply_text("🔴 AI Agent has been <b>disabled</b>.", parse_mode=enums.ParseMode.HTML)
        return

    if subcmd in ("clear", "reset"):
        ai_agent.clear_history()
        await message.reply_text("🧹 AI conversation history cleared.")
        return

    if subcmd == "test":
        status_msg = await message.reply_text("🧪 Testing AI connection...")
        reply = await ai_agent.chat("Say 'AI Agent is connected and operational!' and list your active tools.")
        await status_msg.edit_text(reply, parse_mode=enums.ParseMode.HTML)
        return

    if len(parts) < 3:
        await message.reply_text(f"⚠️ Missing value. Usage: <code>/setai {subcmd} &lt;value&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    val = parts[2].strip()

    if subcmd in ("key", "api_key"):
        await ai_config.set_api_key(val)
        masked = f"{val[:4]}...{val[-4:]}" if len(val) > 8 else "***"
        await message.reply_text(f"✅ AI API key updated to <code>{masked}</code>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("url", "base_url"):
        await ai_config.set_base_url(val)
        await message.reply_text(f"✅ AI Base URL updated to <code>{ai_config.base_url}</code>", parse_mode=enums.ParseMode.HTML)

    elif subcmd in ("model", "name"):
        await ai_config.set_model(val)
        await message.reply_text(f"✅ AI Model updated to <code>{ai_config.model}</code>", parse_mode=enums.ParseMode.HTML)

    else:
        await message.reply_text(f"⚠️ Unknown setting '<code>{esc(subcmd)}</code>'. Type <code>/setai</code> for help.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_ai(client: Client, message: Message):
    """Direct conversation with the autonomous AI Agent."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "🤖 <b>AI Agent</b>\n\n"
            "Usage: <code>/ai &lt;your request or question&gt;</code>\n\n"
            "<i>Examples:</i>\n"
            "• <code>/ai search Solo Leveling and inspect season 1 episodes</code>\n"
            "• <code>/ai inspect bot/downloader.py and check if there are any bugs</code>\n"
            "• <code>/ai run git status</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    prompt = parts[1].strip()
    status_msg = await message.reply_text("🤖 <i>Thinking & executing tools...</i>", parse_mode=enums.ParseMode.HTML)

    try:
        reply = await ai_agent.chat(prompt)
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
        await status_msg.edit_text(f"❌ <b>AI Error:</b> {esc(str(e))}", parse_mode=enums.ParseMode.HTML)
