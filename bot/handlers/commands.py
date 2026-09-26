"""Slash command handlers."""

import logging
import re

from bot.telegram import Client, enums
from bot.telegram.types import Message

from bot.keyboards import main_menu
from bot.auth import require_approved
import bot.logger

log = logging.getLogger(__name__)


@require_approved
async def cmd_start(client: Client, message: Message):
    user = message.from_user
    user_id = user.id if user else 0

    from bot.database import db
    if db and user_id:
        if await db.is_banned(user_id):
            await message.reply_text("⛔ You are banned from using this bot.")
            return
        await db.track_bot_user(user_id, user.username or "", user.first_name or "")

    # Check for deep link parameters (file requests from library or channel join)
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1]
        if param.startswith("get_"):
            await _handle_file_request(client, message, param)
            return
        elif param.startswith("join_"):
            await _handle_channel_join_request(client, message, param[5:])
            return

    if bot.logger.bot_logger and user:
        await bot.logger.bot_logger.log_bot_start(user.id, user.username or user.first_name)

    # Get channel invite link for the welcome message
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
        "\n\n<b>Owner & Admin Commands:</b>\n"
        "/stats — View real-time VPS stats and net speed\n"
        "/health — System health & bot diagnostics\n"
        "/users — View total network users\n"
        "/broadcast — Broadcast text message\n"
        "/pbroadcast — Broadcast photo\n"
        "/dbroadcast — Broadcast video/doc\n"
        "/ban &lt;id&gt; — Ban a user\n"
        "/uban &lt;id&gt; — Unban a user\n"
        "/fsub — Manage Force Subscribe channel\n"
        "/fsub_mod — Toggle FSub 2-min timer link mode\n"
        "/dlt_time — Set file/video auto-delete timer\n"
        "/tutorial — Full system guide\n"
        "/ai &lt;query&gt; — Chat with Autonomous AI Agent\n"
        "/setai — View & change AI model/provider\n"
        "/addbot — Add child worker bot\n"
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


async def _handle_channel_join_request(client: Client, message: Message, series_slug: str):
    """Generate a 2-minute temporary invite link to the mapped series channel."""
    from bot.database import db
    import html as htmlmod
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    user = message.from_user
    user_id = user.id if user else 0

    from bot.fsub import check_fsub, create_timer_invite_link
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=f"join_{series_slug}")
    if not is_sub:
        await message.reply_text(f_text, parse_mode=enums.ParseMode.HTML, reply_markup=f_markup)
        return

    mapping = await db.get_channel_mapping(series_slug)
    if not mapping or not mapping.get("channel_id"):
        await message.reply_text("⚠️ No dedicated channel found for this series.")
        return

    channel_id = mapping["channel_id"]
    series_title = mapping.get("series_title", series_slug)

    # Generate 2-minute timer link (Anti-Copyright Protection)
    timer_link = await create_timer_invite_link(
        client,
        channel_id,
        expire_seconds=120,
        member_limit=1,
        name=f"Join {series_slug[:15]}",
    )
    if not timer_link:
        timer_link = mapping.get("invite_link")

    if not timer_link:
        await message.reply_text("⚠️ Could not generate channel invite link. Please try again later.")
        return

    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = [[InlineKeyboardButton("🚀 Join Series Channel", url=timer_link)]]
    sent_msg = await message.reply_text(
        f"📺 <b>Dedicated Series Channel:</b> {htmlmod.escape(series_title)}\n\n"
        f"⏳ <b>Temporary Invite Link:</b>\n"
        f"This invite link will automatically expire in <b>2 minutes</b>!\n\n"
        f"Click the button below to join:",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

    # Schedule deletion of this invite link notice after 2 minutes
    from bot.auto_delete import auto_delete_service
    await auto_delete_service.schedule_deletion(
        client=client,
        chat_id=message.chat.id,
        message_id=sent_msg.id,
        custom_seconds=120,
    )


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

    user = message.from_user
    user_id = user.id if user else 0

    # FSub verification (with timer link support)
    from bot.fsub import check_fsub
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=param)
    if not is_sub:
        await message.reply_text(f_text, parse_mode=enums.ParseMode.HTML, reply_markup=f_markup)
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
    bot_me = getattr(client, "me", None)
    b_name = bot_me.username if bot_me and bot_me.username else "bot"

    # Handle fetching ALL episodes
    if episode_key.lower() == "all":
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
        from bot.auto_delete import auto_delete_service

        for f in all_files:
            ep_key = f["episode_key"]
            file_id = f["file_id"]
            q_label = f.get("quality", quality)
            caption = f"📺 {htmlmod.escape(title)} [{q_label}] — {ep_key}"
            sent_msg = None
            try:
                sent_msg = await message.reply_video(video=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            except Exception:
                try:
                    sent_msg = await message.reply_document(document=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                except Exception as e:
                    log.error("Failed to send %s: %s", ep_key, e)

            if sent_msg:
                await auto_delete_service.schedule_deletion(
                    client=client,
                    chat_id=message.chat.id,
                    message_id=sent_msg.id,
                    get_file_link=f"https://t.me/{b_name}?start={param}",
                    file_title=title,
                )
            await asyncio.sleep(0.4)

        await status_msg.delete()
        return

    # Normal single-file logic
    cached = await db.find_cached_file(series_slug, episode_key, quality)
    file_id = cached.get("file_id") if cached else await library_manager.get_file(series_slug, quality, episode_key)
    if not file_id:
        await message.reply_text("❌ File not found in library.")
        return

    # Send the file
    sent_msg = None
    try:
        caption = f"📺 {htmlmod.escape(title)} [{quality}]"
        if episode_key.lower() != "movie":
            caption += f" — {episode_key}"

        try:
            sent_msg = await message.reply_video(
                video=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            sent_msg = await message.reply_document(
                document=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )

        # Log download
        if db and user:
            await db.log_download(
                user_id=user.id,
                series_slug=series_slug,
                episode=episode_key,
                quality=quality,
                file_id=file_id,
            )

        # Auto-delete scheduling
        if sent_msg:
            from bot.auto_delete import auto_delete_service
            await auto_delete_service.schedule_deletion(
                client=client,
                chat_id=message.chat.id,
                message_id=sent_msg.id,
                get_file_link=f"https://t.me/{b_name}?start={param}",
                file_title=title,
            )

    except Exception as e:
        log.error("Failed to send library file: %s", e)
        await message.reply_text("⚠️ Could not deliver file. Please try again.")
