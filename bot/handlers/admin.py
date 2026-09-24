"""Owner-only admin commands."""

import logging
from pyrogram import Client, enums
from pyrogram.types import Message

from bot.auth import require_owner, add_user, remove_user, get_users, is_owner
import bot.logger

log = logging.getLogger(__name__)


def _parse_args(message: Message) -> list[str]:
    """Parse arguments from message text (everything after the command)."""
    parts = message.text.split()
    return parts[1:] if len(parts) > 1 else []


@require_owner
async def cmd_adduser(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /adduser <telegram_id>")
        return
    uid = int(args[0])
    if is_owner(uid):
        await message.reply_text("👑 That's the owner — already has access.")
        return
    if await add_user(uid, added_by=message.from_user.id):
        await message.reply_text(f"✅ User <code>{uid}</code> added.", parse_mode=enums.ParseMode.HTML)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_user_added(uid)
    else:
        await message.reply_text("ℹ️ User already approved.")


@require_owner
async def cmd_removeuser(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /removeuser <telegram_id>")
        return
    uid = int(args[0])
    if is_owner(uid):
        await message.reply_text("👑 Can't remove the owner.")
        return
    if await remove_user(uid):
        await message.reply_text(f"✅ User <code>{uid}</code> removed.", parse_mode=enums.ParseMode.HTML)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_user_removed(uid)
    else:
        await message.reply_text("ℹ️ User not in the approved list.")


@require_owner
async def cmd_users(client: Client, message: Message):
    users = await get_users()
    if not users:
        await message.reply_text("📋 No approved users (only owner has access).")
        return
    lines = [f"  • <code>{uid}</code>" for uid in users]
    await message.reply_text(
        f"📋 <b>Approved Users ({len(users)}):</b>\n" + "\n".join(lines),
        parse_mode=enums.ParseMode.HTML,
    )



@require_owner
async def cmd_setchannellink(client: Client, message: Message):
    """Set the channel invite link for force subscribe button."""
    args = _parse_args(message)
    if not args:
        await message.reply_text(
            "Usage: /setchannellink <invite_link>\n\n"
            "Example: /setchannellink https://t.me/yourchannel"
        )
        return
    link = args[0].strip()
    if not link.startswith("https://"):
        await message.reply_text("⚠️ Please provide a valid HTTPS link.")
        return

    from bot.database import db
    if db:
        await db.set_config("channel_invite_link", link)
        await message.reply_text(
            f"✅ Channel invite link set:\n<code>{link}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("⚠️ Database not initialized yet.")


@require_owner
async def cmd_addbot(client: Client, message: Message):
    """Add a child worker bot to distribute download load."""
    args = _parse_args(message)
    if not args:
        await message.reply_text(
            "<b>Usage:</b> <code>/addbot &lt;bot_token&gt; [quality]</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/addbot 123456:ABC... 480p</code> (serves 480p)\n"
            "• <code>/addbot 123456:ABC... 720p</code> (serves 720p)\n"
            "• <code>/addbot 123456:ABC... 1080p</code> (serves 1080p)\n"
            "• <code>/addbot 123456:ABC... 4K</code> (serves 4K/HQ)\n"
            "• <code>/addbot 123456:ABC... all</code> (serves all qualities)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    token = args[0].strip()
    quality = args[1].strip().lower() if len(args) > 1 else "all"

    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager is not initialized.")
        return

    status_msg = await message.reply_text("⏳ Verifying token with Telegram...", parse_mode=enums.ParseMode.HTML)
    try:
        res = await child_bot_manager.add_bot(token, quality=quality)
        await status_msg.edit_text(
            f"✅ <b>Child Worker Bot Connected!</b>\n\n"
            f"🤖 <b>Bot:</b> @{res['username']} (<code>{res['bot_id']}</code>)\n"
            f"⚡ <b>Assigned Quality:</b> <code>{res['quality'].upper()}</code>\n"
            f"🟢 <b>Status:</b> Online & Serving\n\n"
            f"<i>Channel post buttons will now direct users to this bot for {res['quality'].upper()} requests.</i>\n"
            f"💡 <i>Tip: Run /refreshalbums to update all existing channel album buttons!</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Failed to add child bot:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_delbot(client: Client, message: Message):
    """Remove a child worker bot."""
    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: <code>/delbot &lt;@username or bot_id&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    identifier = args[0].strip()
    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager not initialized.")
        return

    success = await child_bot_manager.remove_bot(identifier)
    if success:
        await message.reply_text(f"🗑️ Child bot <code>{identifier}</code> stopped and removed from system.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"❌ Child bot <code>{identifier}</code> not found in database.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_bots(client: Client, message: Message):
    """List all connected child worker bots."""
    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager not initialized.")
        return

    bots = await child_bot_manager.get_all_bots()
    main_me = await client.get_me()

    text = f"👑 <b>Main Controller Bot:</b> @{main_me.username}\n\n"
    if not bots:
        text += (
            "🤖 <b>No Child Worker Bots Connected.</b>\n\n"
            "All download requests and links are handled directly by the Main Bot.\n\n"
            "To add worker bots for load balancing:\n"
            "<code>/addbot &lt;token&gt; 480p</code>\n"
            "<code>/addbot &lt;token&gt; 720p</code>\n"
            "<code>/addbot &lt;token&gt; 1080p</code>\n"
            "<code>/addbot &lt;token&gt; 4K</code>"
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return

    text += f"🤖 <b>Child Worker Bots ({len(bots)}):</b>\n\n"
    for i, b in enumerate(bots, 1):
        status_icon = "🟢 Online" if b.get("is_online") else "🔴 Offline"
        served = b.get("files_served", 0)
        q = b.get("quality", "all").upper()
        text += (
            f"<b>{i}. @{b.get('username', 'Unknown')}</b>\n"
            f"   • <b>ID:</b> <code>{b.get('bot_id')}</code>\n"
            f"   • <b>Target Quality:</b> <code>{q}</code>\n"
            f"   • <b>Status:</b> {status_icon}\n"
            f"   • <b>Files Delivered:</b> {served}\n\n"
        )

    text += "<i>Commands: /addbot, /delbot, /setbotquality, /refreshalbums</i>"
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_setbotquality(client: Client, message: Message):
    """Change the assigned quality tier for a child worker bot."""
    args = _parse_args(message)
    if len(args) < 2:
        await message.reply_text("Usage: <code>/setbotquality &lt;@username or bot_id&gt; &lt;480p|720p|1080p|4k|all&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    identifier = args[0].strip()
    quality = args[1].strip().lower()

    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager not initialized.")
        return

    success = await child_bot_manager.set_bot_quality(identifier, quality)
    if success:
        await message.reply_text(f"✅ Child bot <code>{identifier}</code> quality updated to <b>[{quality.upper()}]</b>.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"❌ Child bot <code>{identifier}</code> not found.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_refreshalbums(client: Client, message: Message):
    """Re-build buttons on all existing channel album posts with current child bot links."""
    from bot.library import library_manager
    if not library_manager or not library_manager.channel:
        await message.reply_text("⚠️ Main channel or library manager is not configured.")
        return

    status_msg = await message.reply_text("🔄 Updating channel album posts with latest child bot links...", parse_mode=enums.ParseMode.HTML)
    try:
        count = await library_manager.refresh_all_albums()
        await status_msg.edit_text(f"✅ Successfully refreshed <b>{count}</b> channel album post(s) with updated bot links!", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to refresh channel albums: {e}")


@require_owner
async def cmd_delete(client: Client, message: Message):
    """Interactive delete — shows all downloaded series as buttons."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not initialized.")
        return

    # Get all unique series from files collection
    pipeline = [
        {"$group": {
            "_id": "$series_slug",
            "title": {"$first": "$series_title"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"title": 1}},
    ]
    series_list = await db.files.aggregate(pipeline).to_list(length=100)

    if not series_list:
        await message.reply_text("📂 No downloaded files in the library.")
        return

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from utils.helpers import slug_to_title

    buttons = []
    for s in series_list:
        slug = s["_id"]
        title = s.get("title") or slug_to_title(slug)
        count = s["count"]
        buttons.append([InlineKeyboardButton(
            f"📺 {title} ({count} files)",
            callback_data=f"del:s:{slug[:40]}",
        )])

    await message.reply_text(
        "🗑️ <b>Delete Manager</b>\n\nSelect a series to manage:",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def delete_callback(client: Client, query):
    """Handle all delete-related callbacks (del:*)."""
    from bot.database import db
    from bot.library import library_manager
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from utils.helpers import slug_to_title
    from pyrogram import enums as pe

    if not db:
        await query.answer("DB not ready", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data.startswith("del:s:"):
        # Show episodes for this series
        series_slug = data[6:]
        cursor = db.files.find({"series_slug": series_slug})
        all_files = await cursor.to_list(length=None)

        if not all_files:
            await query.edit_message_text("⚠️ No files found for this series.")
            return

        title = all_files[0].get("series_title") or slug_to_title(series_slug)

        # Group by episode
        episodes: dict[str, list[str]] = {}
        for f in all_files:
            ep = f["episode_key"]
            q = f["quality"]
            episodes.setdefault(ep, []).append(q)

        # Sort episodes
        import re
        def _sort_key(k):
            m = re.match(r"S(\d+)E(\d+)", k, re.IGNORECASE)
            return (int(m.group(1)), int(m.group(2))) if m else (999, 0)

        sorted_eps = sorted(episodes.keys(), key=_sort_key)

        buttons = []
        for ep in sorted_eps:
            quals = ", ".join(sorted(episodes[ep]))
            label = f"🎬 {ep} [{quals}]" if ep.lower() == "movie" else f"▶️ {ep} [{quals}]"
            buttons.append([InlineKeyboardButton(
                label,
                callback_data=f"del:e:{series_slug[:30]}:{ep}",
            )])

        # Add "Delete ALL" button
        buttons.append([InlineKeyboardButton(
            f"🗑️ DELETE ENTIRE SERIES ({len(all_files)} files)",
            callback_data=f"del:all:{series_slug[:40]}",
        )])
        # Back button
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="del:back")])

        await query.edit_message_text(
            f"🗑️ <b>{title}</b>\n\n"
            f"📂 {len(sorted_eps)} episode(s) · {len(all_files)} file(s)\n\n"
            f"Tap an episode to delete it:",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:e:"):
        # Show confirmation for deleting a specific episode
        parts = data[6:].rsplit(":", 1)
        series_slug = parts[0]
        episode_key = parts[1]

        # Get qualities for this episode
        cursor = db.files.find({"series_slug": series_slug, "episode_key": episode_key})
        files = await cursor.to_list(length=None)
        title = files[0].get("series_title", series_slug) if files else series_slug
        quals = [f["quality"] for f in files]

        buttons = []
        # Delete specific quality
        for q in sorted(quals):
            buttons.append([InlineKeyboardButton(
                f"🗑️ Delete {episode_key} [{q}]",
                callback_data=f"del:x:{series_slug[:25]}:{episode_key}:{q}",
            )])
        # Delete all qualities for this episode
        if len(quals) > 1:
            buttons.append([InlineKeyboardButton(
                f"🗑️ Delete {episode_key} [ALL qualities]",
                callback_data=f"del:x:{series_slug[:25]}:{episode_key}:*",
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data=f"del:s:{series_slug[:40]}")])

        await query.edit_message_text(
            f"🗑️ <b>Delete {episode_key}</b>\n"
            f"📺 {slug_to_title(series_slug)}\n"
            f"📊 Qualities: {', '.join(sorted(quals))}\n\n"
            f"What do you want to delete?",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:x:"):
        # Execute deletion of specific episode+quality
        parts = data[6:].rsplit(":", 2)
        series_slug = parts[0]
        episode_key = parts[1]
        quality = parts[2]  # "*" means all qualities

        file_query = {"series_slug": series_slug, "episode_key": episode_key}
        if quality != "*":
            file_query["quality"] = quality

        deleted = await db.files.delete_many(file_query)
        await _refresh_album(db, library_manager, series_slug)

        await query.edit_message_text(
            f"✅ <b>Deleted!</b>\n"
            f"🗑️ {deleted.deleted_count} file(s) removed\n"
            f"📺 {episode_key} {'[' + quality + ']' if quality != '*' else '[all qualities]'}",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Back to series", callback_data=f"del:s:{series_slug[:40]}")],
                [InlineKeyboardButton("🏠 Delete menu", callback_data="del:back")],
            ]),
        )

    elif data.startswith("del:all:"):
        # Confirm delete entire series
        series_slug = data[8:]
        count = await db.files.count_documents({"series_slug": series_slug})
        title = slug_to_title(series_slug)

        buttons = [
            [InlineKeyboardButton(
                f"⚠️ YES, DELETE ALL {count} FILES",
                callback_data=f"del:confirm:{series_slug[:40]}",
            )],
            [InlineKeyboardButton("◀️ Cancel", callback_data=f"del:s:{series_slug[:40]}")],
        ]

        await query.edit_message_text(
            f"⚠️ <b>Are you sure?</b>\n\n"
            f"This will permanently delete:\n"
            f"📺 {title}\n"
            f"📁 {count} file(s)\n"
            f"📨 Album post from channel\n\n"
            f"<b>This cannot be undone!</b>",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:confirm:"):
        # Execute full series deletion
        series_slug = data[12:]
        deleted = await db.files.delete_many({"series_slug": series_slug})

        if library_manager:
            await library_manager.delete_album(series_slug)

        await db.downloads.delete_many({"series_slug": series_slug})

        await query.edit_message_text(
            f"✅ <b>Series deleted permanently!</b>\n"
            f"🗑️ {deleted.deleted_count} file(s) removed\n"
            f"📨 Album removed from channel",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Delete menu", callback_data="del:back")],
            ]),
        )

    elif data == "del:back":
        # Back to series list — re-run the list
        pipeline = [
            {"$group": {
                "_id": "$series_slug",
                "title": {"$first": "$series_title"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"title": 1}},
        ]
        series_list = await db.files.aggregate(pipeline).to_list(length=100)

        if not series_list:
            await query.edit_message_text("📂 Library is empty — nothing to delete.")
            return

        buttons = []
        for s in series_list:
            slug = s["_id"]
            title = s.get("title") or slug_to_title(slug)
            count = s["count"]
            buttons.append([InlineKeyboardButton(
                f"📺 {title} ({count} files)",
                callback_data=f"del:s:{slug[:40]}",
            )])

        await query.edit_message_text(
            "🗑️ <b>Delete Manager</b>\n\nSelect a series to manage:",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def _refresh_album(db, library_manager, series_slug: str):
    """After deletion, update or remove the album in main channel."""
    remaining = await db.files.count_documents({"series_slug": series_slug})
    if remaining == 0:
        if library_manager:
            await library_manager.delete_album(series_slug)
    else:
        if library_manager:
            sample = await db.files.find_one({"series_slug": series_slug})
            if sample:
                await library_manager.save_to_library(
                    series_slug=series_slug,
                    series_title=sample.get("series_title", series_slug),
                    quality=sample["quality"],
                    episode_key=sample["episode_key"],
                    file_id=sample["file_id"],
                    file_unique_id=sample["file_unique_id"],
                )


# ── Userbot & Channel Mapping Commands ──────────────────────────


@require_owner
async def cmd_login(client: Client, message: Message):
    """
    Login userbot session.
    Usage:
      /login - Start interactive phone login wizard
      /login <string_session> - Direct login with Pyrogram string session
    """
    from bot.userbot import userbot_manager
    if not userbot_manager:
        await message.reply_text("⚠️ Userbot Manager not initialized.")
        return

    args = _parse_args(message)
    if args:
        session_str = args[0].strip()
        try:
            await message.delete()
        except Exception:
            pass
        status_msg = await message.reply_text("🔄 Validating and connecting string session...")
        ok, text, _ = await userbot_manager.login_with_session(session_str, message.from_user.id)
        if ok:
            await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
        else:
            await status_msg.edit_text(f"❌ <b>Login Failed:</b> {text}", parse_mode=enums.ParseMode.HTML)
    else:
        prompt = await userbot_manager.start_interactive_login(message.from_user.id)
        await message.reply_text(prompt, parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_logout(client: Client, message: Message):
    """Logout userbot and clear session from database."""
    from bot.userbot import userbot_manager
    if not userbot_manager or not userbot_manager.is_active:
        await message.reply_text("ℹ️ Userbot is not currently logged in.")
        return

    await userbot_manager.logout()
    await message.reply_text("✅ <b>Userbot logged out</b> and session deleted from database.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_userbot(client: Client, message: Message):
    """Show current userbot session status."""
    from bot.userbot import userbot_manager
    from bot.database import db

    if not userbot_manager:
        await message.reply_text("⚠️ Userbot Manager not initialized.")
        return

    st = userbot_manager.get_status()
    is_active = st["is_active"]

    auto_chan = False
    album_mode = "channel"
    channel_count = 0
    if db:
        auto_chan = await db.get_config("auto_channel_creation", default=False)
        album_mode = await db.get_config("album_mode", default="channel")
        channels = await db.list_channel_mappings()
        channel_count = len(channels)

    if is_active:
        status_text = "🟢 <b>Connected</b>"
        user_info = (
            f"👤 <b>Name:</b> {st['name']}\n"
            f"🔗 <b>Username:</b> @{st['username'] or 'None'}\n"
            f"🆔 <b>User ID:</b> <code>{st['user_id']}</code>\n"
        )
    else:
        status_text = "🔴 <b>Disconnected</b>"
        user_info = "<i>Run /login to connect a userbot session.</i>\n"

    msg = (
        f"🤖 <b>Userbot Status</b>\n"
        f"⟐━━━━━━━━━━━━━━━━━⟐\n"
        f"⚡ <b>State:</b> {status_text}\n"
        f"{user_info}"
        f"📁 <b>Mapped Channels:</b> {channel_count}\n"
        f"⚙️ <b>Auto Channel Creation:</b> {'✅ Enabled' if auto_chan else '❌ Disabled'}\n"
        f"🖼️ <b>Album Mode:</b> <code>{album_mode}</code>\n"
        f"⟐━━━━━━━━━━━━━━━━━⟐\n"
        f"<b>Commands:</b>\n"
        f"• /login - Connect userbot\n"
        f"• /logout - Disconnect userbot\n"
        f"• /autochannel &lt;on|off&gt; - Toggle auto channel creation\n"
        f"• /albummode &lt;channel|both|direct&gt; - Set poster album mode\n"
        f"• /channels - List mapped channels\n"
        f"• /createchannel &lt;slug&gt; - Create channel for series\n"
        f"• /mapchannel &lt;slug&gt; &lt;channel_id&gt; [link] - Map channel"
    )
    await message.reply_text(msg, parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_cancel(client: Client, message: Message):
    """Cancel active userbot login wizard."""
    from bot.userbot import userbot_manager
    if userbot_manager and userbot_manager.is_in_login(message.from_user.id):
        await userbot_manager.cancel_login(message.from_user.id)
        await message.reply_text("❌ Login cancelled.")
    else:
        await message.reply_text("ℹ️ No active login wizard.")


@require_owner
async def cmd_autochannel(client: Client, message: Message):
    """Toggle auto-channel creation."""
    from bot.database import db
    args = _parse_args(message)
    if not args or args[0].lower() not in ("on", "off", "enable", "disable"):
        await message.reply_text("Usage: /autochannel <on|off>")
        return

    enabled = args[0].lower() in ("on", "enable")
    if db:
        await db.set_config("auto_channel_creation", enabled)
    await message.reply_text(
        f"✅ <b>Auto Channel Creation:</b> {'Enabled' if enabled else 'Disabled'}\n"
        f"When enabled, downloading a series will automatically create a dedicated channel.",
        parse_mode=enums.ParseMode.HTML
    )


@require_owner
async def cmd_albummode(client: Client, message: Message):
    """Configure poster album display mode."""
    from bot.database import db
    args = _parse_args(message)
    valid_modes = ("channel", "both", "direct")
    if not args or args[0].lower() not in valid_modes:
        await message.reply_text(
            "Usage: /albummode <channel|both|direct>\n\n"
            "• <code>channel</code>: Main channel poster has direct button to the dedicated series channel\n"
            "• <code>both</code>: Main channel poster has channel button + direct download buttons\n"
            "• <code>direct</code>: Main channel poster has only direct download buttons",
            parse_mode=enums.ParseMode.HTML
        )
        return

    mode = args[0].lower()
    if db:
        await db.set_config("album_mode", mode)
    await message.reply_text(
        f"✅ <b>Poster Album Mode set to:</b> <code>{mode}</code>",
        parse_mode=enums.ParseMode.HTML
    )


@require_owner
async def cmd_createchannel(client: Client, message: Message):
    """Manually create and map a dedicated channel for an anime series."""
    from bot.userbot import userbot_manager
    from api.client import api

    if not userbot_manager or not userbot_manager.is_active:
        await message.reply_text("❌ Userbot is not connected. Use /login first.")
        return

    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: /createchannel <series_slug or search query>\nExample: /createchannel solo-leveling-hindi")
        return

    query = " ".join(args).strip()
    wait_msg = await message.reply_text(f"🔍 Looking up anime series for: <code>{query}</code>...", parse_mode=enums.ParseMode.HTML)

    slug = query
    series_title = query
    poster_url = None

    try:
        series = await api.get_series(slug)
        if series:
            series_title = series.title
            poster_url = series.poster
    except Exception:
        try:
            results = await api.search(query)
            if results:
                match = results[0]
                slug = match.slug
                series_title = match.title
                poster_url = match.poster
        except Exception as se:
            log.warning("Search failed in createchannel: %s", se)

    await wait_msg.edit_text(f"🔨 Creating channel for <b>{series_title}</b>...", parse_mode=enums.ParseMode.HTML)

    try:
        mapping = await userbot_manager.create_anime_channel(
            series_title=series_title,
            series_slug=slug,
            poster_url=poster_url,
        )
        await wait_msg.edit_text(
            f"🎉 <b>Dedicated Channel Created & Mapped!</b>\n\n"
            f"📺 <b>Series:</b> {mapping.get('series_title', series_title)}\n"
            f"🆔 <b>Channel ID:</b> <code>{mapping['channel_id']}</code>\n"
            f"🔗 <b>Invite Link:</b> {mapping.get('invite_link')}\n"
            f"🏷️ <b>Slug:</b> <code>{slug}</code>",
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("cmd_createchannel failed")
        await wait_msg.edit_text(f"❌ <b>Channel creation failed:</b> {e}", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_mapchannel(client: Client, message: Message):
    """Manually map an existing Telegram channel to an anime series slug."""
    from bot.database import db
    args = _parse_args(message)
    if len(args) < 2:
        await message.reply_text(
            "Usage: /mapchannel <series_slug> <channel_id> [invite_link]\n\n"
            "Example: /mapchannel solo-leveling-hindi -100123456789 https://t.me/+AbCdEf"
        )
        return

    slug = args[0].strip()
    try:
        channel_id = int(args[1].strip())
    except ValueError:
        await message.reply_text("❌ Channel ID must be an integer (e.g. -100123456789).")
        return

    invite_link = args[2].strip() if len(args) > 2 else ""

    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    mapping = await db.set_channel_mapping(
        series_slug=slug,
        channel_id=channel_id,
        invite_link=invite_link,
        series_title=slug,
        auto_created=False,
        created_by=message.from_user.id,
    )

    await message.reply_text(
        f"✅ <b>Channel Mapped!</b>\n\n"
        f"🏷️ <b>Slug:</b> <code>{slug}</code>\n"
        f"🆔 <b>Channel ID:</b> <code>{channel_id}</code>\n"
        f"🔗 <b>Invite Link:</b> {invite_link or 'None'}",
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


@require_owner
async def cmd_unmapchannel(client: Client, message: Message):
    """Remove channel mapping for a series slug."""
    from bot.database import db
    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: /unmapchannel <series_slug>")
        return

    slug = args[0].strip()
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    deleted = await db.delete_channel_mapping(slug)
    if deleted:
        await message.reply_text(f"✅ Removed channel mapping for <code>{slug}</code>.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"ℹ️ No channel mapping found for <code>{slug}</code>.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_channels(client: Client, message: Message):
    """List all mapped channels."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    channels = await db.list_channel_mappings()
    if not channels:
        await message.reply_text("📂 <b>No mapped channels found.</b>\nUse /createchannel or /mapchannel to add channels.", parse_mode=enums.ParseMode.HTML)
        return

    lines = []
    for c in channels:
        title = c.get("series_title") or c.get("series_slug")
        cid = c.get("channel_id")
        link = c.get("invite_link")
        link_str = f"<a href='{link}'>Link</a>" if link else "No link"
        lines.append(f"• <b>{title}</b>\n  ID: <code>{cid}</code> | {link_str} | Slug: <code>{c.get('series_slug')}</code>")

    text = f"📋 <b>Mapped Series Channels ({len(channels)}):</b>\n\n" + "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
