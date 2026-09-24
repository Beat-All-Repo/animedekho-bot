"""Slash command handlers."""

import logging
import re

from pyrogram import Client, enums
from pyrogram.types import Message

from bot.keyboards import main_menu
from bot.auth import require_approved
import bot.logger

log = logging.getLogger(__name__)


@require_approved
async def cmd_start(client: Client, message: Message):
    user = message.from_user

    # Check for deep link parameters (file requests from library)
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("get_"):
        await _handle_file_request(client, message, args[1])
        return

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_bot_start(user.id, user.username or user.first_name)

    # Get channel invite link for the welcome message
    from bot.database import db
    invite_link = None
    if db:
        invite_link = await db.get_config("channel_invite_link")

    welcome_text = (
        "🎌 <b>AnimeDekho Bot</b>\n\n"
        "Stream Hindi dubbed anime!\n\n"
        "• 📺 <b>Series</b> — browse recent series\n"
        "• 📂 <b>Genres</b> — filter by genre\n\n"
        "Just type any anime name to search!"
    )

    markup = main_menu(invite_link=invite_link)

    await message.reply_text(
        welcome_text,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=markup,
    )


@require_approved
async def cmd_help(client: Client, message: Message):
    is_owner_user = message.from_user and message.from_user.id == settings.bot.owner_id
    owner_help = (
        "\n\n<b>Owner Commands:</b>\n"
        "/ai &lt;query&gt; — Chat with Autonomous AI Agent\n"
        "/setai — View & change AI model/provider\n"
        "/adduser &lt;id&gt; — Approve a user\n"
        "/removeuser &lt;id&gt; — Remove a user\n"
        "/users — List approved users\n"
        "/setchannellink &lt;url&gt; — Set channel invite link\n"
        "/delete — Delete a series or file"
    ) if is_owner_user else ""

    await message.reply_text(
        "📖 <b>Commands</b>\n\n"
        "/start — Main menu\n"
        "/search &lt;name&gt; — Search anime or movies\n"
        "/help — This message\n\n"
        "Just type any anime name in chat to search!"
        f"{owner_help}",
        parse_mode=enums.ParseMode.HTML,
    )


@require_approved
async def cmd_search(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "🔍 Usage: <code>/search &lt;anime name&gt;</code>\n"
            "Or simply type the anime name directly in chat!",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    from bot.handlers.messages import handle_text
    message.text = parts[1].strip()
    await handle_text(client, message)


async def _handle_file_request(client: Client, message: Message, param: str):
    """Handle deep link file requests from main channel.

    Format: get_<slug>_<quality>_<episode_key>
    Example: get_naruto-shippuden_720p_S1E01
    """
    from bot.library import library_manager
    from bot.database import db

    if not library_manager:
        await message.reply_text("⚠️ Library not initialized.")
        return

    # Parse: get_<slug>_<quality>_<ep_key>
    raw = param[4:]  # strip "get_"

    # Match episode key at the end (S\d+E\d+|movie|all)
    m = re.match(
        r"^(.+?)_(480p|720p|1080p|1080p\s*hq|1080p\s*hq\s*x265|4k|2160p|\d+p|auto)_(s\d+e\d+|movie|all)$",
        raw,
        re.IGNORECASE,
    )
    if not m:
        await message.reply_text("⚠️ Invalid file link format.")
        return

    series_slug = m.group(1)
    quality = m.group(2)
    episode_key = m.group(3)

    import html as htmlmod
    from utils.helpers import slug_to_title
    title = slug_to_title(series_slug)

    # Handle fetching ALL episodes
    if episode_key.lower() == "all":
        # Fetch all files for this series and quality
        query = {"series_slug": series_slug}
        if quality.lower() in ("4k", "2160p", "2160"):
            query["quality"] = {
                "$in": [
                    "4K", "4k", "2160p", "2160P", "2160",
                    "1080p HQ", "1080p HQ x265", "1080p 10-Bit", "1080p 10bit",
                    "1080p x265", "1080p HEVC",
                ]
            }
        else:
            query["quality"] = {"$regex": f"^{re.escape(quality)}$", "$options": "i"}

        cursor = db.files.find(query)
        all_files = await cursor.to_list(length=None)
        if not all_files:
            all_files = await db.files.find({"series_title": {"$regex": f"^{re.escape(title)}$", "$options": "i"}}).to_list(length=None)

        if not all_files:
            await message.reply_text("❌ No files found for this series.")
            return

        from bot.library import _ep_sort_key
        all_files.sort(key=lambda x: _ep_sort_key(x["episode_key"]))

        status_msg = await message.reply_text(f"📤 Sending {len(all_files)} episodes...")
        for f in all_files:
            ep_key = f["episode_key"]
            file_id = f["file_id"]
            q_label = f.get("quality", quality)
            caption = f"📺 {htmlmod.escape(title)} [{q_label}] — {ep_key}"
            try:
                await message.reply_video(video=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            except Exception:
                try:
                    await message.reply_document(document=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                except Exception as e:
                    log.error("Failed to send %s: %s", ep_key, e)

        await status_msg.delete()
        return

    # Normal single-file logic
    cached = await db.find_cached_file(series_slug, episode_key, quality)
    file_id = cached.get("file_id") if cached else await library_manager.get_file(series_slug, quality, episode_key)
    if not file_id:
        await message.reply_text("❌ File not found in library.")
        return

    # Send the file
    try:
        caption = f"📺 {htmlmod.escape(title)} [{quality}]"
        if episode_key.lower() != "movie":
            caption += f" — {episode_key}"

        await message.reply_video(
            video=file_id,
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
        )

        # Log download
        if db:
            user = message.from_user
            await db.log_download(
                user_id=user.id,
                series_slug=series_slug,
                episode=episode_key,
                quality=quality,
                file_id=file_id,
            )
    except Exception as e:
        log.error("Failed to send library file: %s", e)
        # Try as document fallback
        try:
            await message.reply_document(
                document=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e2:
            log.error("Document fallback also failed: %s", e2)
            await message.reply_text("❌ Could not send file.")
