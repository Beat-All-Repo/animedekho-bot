"""Worker Bot & Shared Admin Handlers (/users, /ban, /uban, /broadcast, /fsub, /dlt_time, /stats, /tutorial)."""

from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Any

from bot.telegram import Client, enums, filters
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import settings
from bot.auth import is_owner
from bot.health import get_uptime, get_system_stats

log = logging.getLogger(__name__)

# Net I/O tracker for real-time DL/UL speed calculation
_last_net_time: float = time.time()
_last_net_bytes: tuple[int, int] = (0, 0)
try:
    import psutil
    _net = psutil.net_io_counters()
    _last_net_bytes = (_net.bytes_recv, _net.bytes_sent)
except Exception:
    pass


def get_network_speeds() -> tuple[str, str]:
    """Calculate current network download and upload speeds in human-readable format."""
    global _last_net_time, _last_net_bytes
    try:
        import psutil
        now = time.time()
        elapsed = max(0.1, now - _last_net_time)
        net = psutil.net_io_counters()
        recv_diff = max(0, net.bytes_recv - _last_net_bytes[0])
        sent_diff = max(0, net.bytes_sent - _last_net_bytes[1])
        _last_net_time = now
        _last_net_bytes = (net.bytes_recv, net.bytes_sent)

        dl_rate = recv_diff / elapsed
        ul_rate = sent_diff / elapsed

        def fmt_speed(rate_bytes: float) -> str:
            if rate_bytes >= 1024 * 1024:
                return f"{rate_bytes / (1024 * 1024):.2f} MB/s"
            elif rate_bytes >= 1024:
                return f"{rate_bytes / 1024:.1f} KB/s"
            else:
                return "0B/s"

        return fmt_speed(dl_rate), fmt_speed(ul_rate)
    except Exception:
        return "0B/s", "0B/s"


def format_stats_card() -> str:
    """Format the exact ASCII/Unicode VPS stats banner requested by user."""
    from bot.health import get_uptime, get_system_stats
    import psutil

    _, uptime_str = get_uptime()
    # Format uptime nicely as Xh Ym Zs
    uptime_fmt = uptime_str.replace(" ", "")

    sys_stats = get_system_stats()
    dl_str, ul_str = get_network_speeds()

    du = psutil.disk_usage("/")
    free_gb = round(du.free / (1024 ** 3), 2)
    total_disk_gb = round(du.total / (1024 ** 3), 2)

    vm = psutil.virtual_memory()
    used_mb = round(vm.used / (1024 ** 2), 2)
    total_ram_gb = round(vm.total / (1024 ** 3), 2)

    return (
        f"📊 <b>BOT STATS</b>   » <code>{total_disk_gb:.2f} GB</code>\n"
        "▬▬▬▬▬▬✘▬▬▬▬▬▬\n"
        f"╭ CPU » <code>{sys_stats['cpu_pct']:.1f}%</code> | UP » <code>{uptime_fmt}</code>\n"
        f"┊ RAM » <code>{sys_stats['ram_pct']:.2f}%</code> ({used_mb:.2f} MB/{total_ram_gb:.2f} GB)\n"
        f"┊ DISK » <code>{sys_stats['disk_pct']:.1f}%</code> | FREE <code>{free_gb:.2f} GB</code>\n"
        f"╰ DL » <code>{dl_str}</code> | UL » <code>{ul_str}</code>"
    )


# ── Commands ─────────────────────────────────────────────────────────

async def cmd_stats(client: Client, message: Message):
    """Display real-time VPS stats and bot performance."""
    user = message.from_user
    if user and not is_owner(user.id):
        from bot.auth import is_approved
        if not await is_approved(user.id):
            await message.reply_text("⛔ Access denied.")
            return

    card = format_stats_card()
    await message.reply_text(card, parse_mode=enums.ParseMode.HTML)


async def cmd_users_count(client: Client, message: Message):
    """Check total bot user count across network."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not connected.")
        return

    total_users = await db.get_bot_users_count()
    banned_users = await db.get_banned_users_count()
    approved_users = len(await db.get_users())

    text = (
        "👥 <b>Bot Network User Analytics</b>\n\n"
        f"• <b>Total Registered Users:</b> <code>{total_users}</code>\n"
        f"• <b>Approved Whitelist Users:</b> <code>{approved_users}</code>\n"
        f"• <b>Banned Users:</b> <code>{banned_users}</code>\n\n"
        "<i>All users who interacted with main bot or child workers are indexed.</i>"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


async def cmd_ban(client: Client, message: Message):
    """Ban a user from accessing all bots in the network."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    target_id = None
    reason = "Violating terms"

    # Check reply
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            reason = parts[1]
    else:
        parts = message.text.split(maxsplit=2)
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target_id = int(parts[1])
            if len(parts) > 2:
                reason = parts[2]

    if not target_id:
        await message.reply_text("Usage: <code>/ban &lt;user_id&gt; [reason]</code> or reply to user message.", parse_mode=enums.ParseMode.HTML)
        return

    if is_owner(target_id):
        await message.reply_text("👑 You cannot ban the bot owner!")
        return

    success = await db.ban_user(target_id, reason=reason, banned_by=user.id if user else 0)
    if success:
        await message.reply_text(
            f"🚫 <b>User Banned</b>\n\n• <b>User ID:</b> <code>{target_id}</code>\n• <b>Reason:</b> {reason}",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("⚠️ Failed to ban user.")


async def cmd_unban(client: Client, message: Message):
    """Unban a user from the bot network."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target_id = int(parts[1])

    if not target_id:
        await message.reply_text("Usage: <code>/uban &lt;user_id&gt;</code> or <code>/unban &lt;user_id&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    success = await db.unban_user(target_id)
    if success:
        await message.reply_text(f"✅ User <code>{target_id}</code> has been unbanned.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"ℹ️ User <code>{target_id}</code> was not in the banned list.", parse_mode=enums.ParseMode.HTML)


async def cmd_broadcast(client: Client, message: Message):
    """Broadcast text message to all bot users."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    text_to_send = ""
    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        text_to_send = message.reply_to_message.text or message.reply_to_message.caption
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            text_to_send = parts[1]

    if not text_to_send:
        await message.reply_text("Usage: <code>/broadcast &lt;message&gt;</code> or reply to a text message with /broadcast", parse_mode=enums.ParseMode.HTML)
        return

    user_ids = await db.get_all_bot_user_ids()
    if not user_ids:
        await message.reply_text("⚠️ No users found in database to broadcast to.")
        return

    status_msg = await message.reply_text(f"📢 <b>Broadcasting to {len(user_ids)} users...</b>", parse_mode=enums.ParseMode.HTML)
    sent, failed, blocked = 0, 0, 0

    for i, uid in enumerate(user_ids, 1):
        try:
            await client.send_message(chat_id=uid, text=text_to_send, parse_mode=enums.ParseMode.HTML)
            sent += 1
        except Exception as e:
            err_str = str(e).lower()
            if "blocked" in err_str or "user_deactivated" in err_str:
                blocked += 1
            else:
                failed += 1

        if i % 30 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 <b>Broadcasting Progress:</b> {i}/{len(user_ids)}\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• Total Target: {len(user_ids)}\n"
        f"• Successfully Sent: {sent}\n"
        f"• Blocked / Deactivated: {blocked}\n"
        f"• Failed: {failed}",
        parse_mode=enums.ParseMode.HTML,
    )


async def cmd_pbroadcast(client: Client, message: Message):
    """Broadcast photo to all bot users (reply to a photo)."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply_text("Usage: Reply to a photo with <code>/pbroadcast [optional caption]</code>", parse_mode=enums.ParseMode.HTML)
        return

    from bot.database import db
    user_ids = await db.get_all_bot_user_ids()
    if not user_ids:
        await message.reply_text("⚠️ No users found.")
        return

    photo_id = message.reply_to_message.photo.file_id
    caption = message.reply_to_message.caption or ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        caption = parts[1]

    status_msg = await message.reply_text(f"🖼 <b>Broadcasting photo to {len(user_ids)} users...</b>", parse_mode=enums.ParseMode.HTML)
    sent, failed, blocked = 0, 0, 0

    for i, uid in enumerate(user_ids, 1):
        try:
            await client.send_photo(chat_id=uid, photo=photo_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            sent += 1
        except Exception as e:
            if "blocked" in str(e).lower():
                blocked += 1
            else:
                failed += 1

        if i % 30 == 0:
            try:
                await status_msg.edit_text(
                    f"🖼 <b>Photo Broadcast:</b> {i}/{len(user_ids)}\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Photo Broadcast Completed!</b>\n\n• Sent: {sent}\n• Blocked: {blocked}\n• Failed: {failed}",
        parse_mode=enums.ParseMode.HTML,
    )


async def cmd_dbroadcast(client: Client, message: Message):
    """Broadcast document/video to all bot users (reply to a doc or video)."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    rep = message.reply_to_message
    if not rep or (not rep.document and not rep.video):
        await message.reply_text("Usage: Reply to a video or document with <code>/dbroadcast [optional caption]</code>", parse_mode=enums.ParseMode.HTML)
        return

    from bot.database import db
    user_ids = await db.get_all_bot_user_ids()
    if not user_ids:
        await message.reply_text("⚠️ No users found.")
        return

    caption = rep.caption or ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        caption = parts[1]

    status_msg = await message.reply_text(f"📁 <b>Broadcasting file to {len(user_ids)} users...</b>", parse_mode=enums.ParseMode.HTML)
    sent, failed, blocked = 0, 0, 0

    for i, uid in enumerate(user_ids, 1):
        try:
            if rep.video:
                await client.send_video(chat_id=uid, video=rep.video.file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            else:
                await client.send_document(chat_id=uid, document=rep.document.file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            sent += 1
        except Exception as e:
            if "blocked" in str(e).lower():
                blocked += 1
            else:
                failed += 1

        if i % 30 == 0:
            try:
                await status_msg.edit_text(
                    f"📁 <b>File Broadcast:</b> {i}/{len(user_ids)}\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>File Broadcast Completed!</b>\n\n• Sent: {sent}\n• Blocked: {blocked}\n• Failed: {failed}",
        parse_mode=enums.ParseMode.HTML,
    )


async def cmd_fsub(client: Client, message: Message):
    """Manage Force Subscribe channel."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        arg = parts[1].strip()
        if arg.lower() in ("off", "disable", "none", "0"):
            await db.set_fsub_channel(None)
            await message.reply_text("✅ Force Subscribe (FSub) has been disabled.")
            return
        else:
            # Set channel
            chan_val = int(arg) if arg.lstrip("-").isdigit() else arg
            await db.set_fsub_channel(chan_val)
            await message.reply_text(f"✅ FSub channel updated to: <code>{chan_val}</code>", parse_mode=enums.ParseMode.HTML)
            return

    # View status
    current_chan = await db.get_fsub_channel()
    fsub_mod = await db.get_fsub_mod()

    text = (
        "📢 <b>Force Subscribe (FSub) System</b>\n\n"
        f"• <b>Target Channel:</b> <code>{current_chan or 'Disabled'}</code>\n"
        f"• <b>Timer Link Mode (/fsub_mod):</b> <code>{'ON (2 min expiring links)' if fsub_mod else 'OFF (Standard links)'}</code>\n\n"
        "<b>Commands:</b>\n"
        "• <code>/fsub &lt;channel_id_or_username&gt;</code> — Set FSub channel\n"
        "• <code>/fsub off</code> — Disable FSub check\n"
        "• <code>/fsub_mod on</code> — Enable 2-minute expiring timer links\n"
        "• <code>/fsub_mod off</code> — Use standard permanent links"
    )
    buttons = [
        [
            InlineKeyboardButton(f"Timer Mode: {'ON ✅' if fsub_mod else 'OFF ❌'}", callback_data="toggle_fsub_mod"),
        ]
    ]
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cmd_fsub_mod(client: Client, message: Message):
    """Toggle FSub Timer Link Mode (ON: 2-min auto-expiring links; OFF: normal links)."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        mode_str = parts[1].strip().lower()
        if mode_str in ("on", "true", "1", "yes", "enable"):
            await db.set_fsub_mod(True)
            await message.reply_text("🔒 <b>FSub Timer Mode: ON</b>\n\nAll FSub invite links will now be generated as <b>2-minute auto-expiring links</b> to prevent link sharing and copyright strikes.", parse_mode=enums.ParseMode.HTML)
            return
        elif mode_str in ("off", "false", "0", "no", "disable"):
            await db.set_fsub_mod(False)
            await message.reply_text("🔓 <b>FSub Timer Mode: OFF</b>\n\nFSub will use standard normal channel links.", parse_mode=enums.ParseMode.HTML)
            return

    # Toggle if no param
    current = await db.get_fsub_mod()
    new_state = not current
    await db.set_fsub_mod(new_state)
    state_str = "ON (2-min expiring links)" if new_state else "OFF (Normal links)"
    await message.reply_text(f"⚙️ <b>FSub Mode Toggled:</b> <code>{state_str}</code>", parse_mode=enums.ParseMode.HTML)


async def cmd_dlt_time(client: Client, message: Message):
    """Set or inspect auto-delete time for sent files and videos."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        raw_val = parts[1].strip().lower()
        seconds = 0
        if raw_val in ("off", "disable", "none", "0"):
            seconds = 0
        elif raw_val.endswith("m"):
            seconds = int(raw_val[:-1]) * 60
        elif raw_val.endswith("h"):
            seconds = int(raw_val[:-1]) * 3600
        elif raw_val.endswith("s"):
            seconds = int(raw_val[:-1])
        elif raw_val.isdigit():
            seconds = int(raw_val)
        else:
            await message.reply_text("⚠️ Invalid time. Examples: <code>/dlt_time 10m</code>, <code>/dlt_time 600</code>, <code>/dlt_time off</code>", parse_mode=enums.ParseMode.HTML)
            return

        await db.set_dlt_time(seconds)
        if seconds == 0:
            await message.reply_text("❌ <b>Auto-Delete Disabled.</b> Sent files will not be deleted automatically.", parse_mode=enums.ParseMode.HTML)
        else:
            mins = seconds / 60
            await message.reply_text(
                f"⏱ <b>Auto-Delete Time Updated:</b> <code>{seconds} seconds</code> ({mins:.1f} mins)\n\n"
                "Files/videos delivered to users will be automatically deleted after this duration, and replaced with a 'Get File Again' message. Pending deletions persist across bot restarts.",
                parse_mode=enums.ParseMode.HTML,
            )
        return

    # Display current settings and quick preset buttons
    current_sec = await db.get_dlt_time()
    pending_count = await db.get_auto_delete_jobs_count()
    status_str = f"{current_sec}s ({current_sec / 60:.1f} mins)" if current_sec > 0 else "Disabled (OFF)"

    text = (
        "⏱ <b>Auto-Delete System Settings</b>\n\n"
        f"• <b>Current Deletion Timer:</b> <code>{status_str}</code>\n"
        f"• <b>Pending Auto-Delete Jobs:</b> <code>{pending_count}</code>\n\n"
        "User-delivered videos/files will automatically be deleted after this timer expires, and replaced with recovery notice.\n"
        "<i>Select a preset below or use <code>/dlt_time &lt;seconds&gt;</code>:</i>"
    )
    buttons = [
        [
            InlineKeyboardButton("1 Min", callback_data="dlt:60"),
            InlineKeyboardButton("2 Min", callback_data="dlt:120"),
            InlineKeyboardButton("5 Min", callback_data="dlt:300"),
        ],
        [
            InlineKeyboardButton("10 Min (Default)", callback_data="dlt:600"),
            InlineKeyboardButton("30 Min", callback_data="dlt:1800"),
            InlineKeyboardButton("1 Hour", callback_data="dlt:3600"),
        ],
        [
            InlineKeyboardButton("❌ Disable Auto-Delete", callback_data="dlt:0"),
        ]
    ]
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cmd_tutorial(client: Client, message: Message):
    """Display comprehensive system tutorial explaining Main Bot & Child Workers."""
    tutorial_text = (
        "📚 <b>AnimeDekho Bot System Tutorial</b>\n\n"
        "<b>1. Multi-Bot Worker Fleet (Load Balancing)</b>\n"
        "• Add child bots using <code>/addbot &lt;token&gt; [quality]</code> on Main Bot.\n"
        "• Each worker can serve specific quality tiers (1080p, 720p, 480p) or 'all'.\n"
        "• Deep links on channel album cards automatically route user downloads to worker bots.\n\n"
        "<b>2. Timer Links (Anti-Copyright Protection)</b>\n"
        "• No permanent invite links are leaked to users.\n"
        "• When users click channel links, the bot generates a <b>2-minute timer link</b> that automatically expires.\n"
        "• <code>/fsub_mod on</code>: Forces all FSub links to also be temporary 2-minute links.\n\n"
        "<b>3. Persistent Auto-Delete System</b>\n"
        "• Set auto-delete duration via <code>/dlt_time</code> (default: 10 minutes).\n"
        "• Once a file is delivered, it is automatically purged after the set time.\n"
        "• Bot displays a clean 'Previous Message was Deleted' card with a <i>[Get File Again]</i> button.\n"
        "• <b>Survives Restarts:</b> All pending deletions are stored in MongoDB and resume on boot.\n\n"
        "<b>4. Worker Bot Admin Commands</b>\n"
        "• <code>/users</code> — View total bot user count\n"
        "• <code>/broadcast</code>, <code>/pbroadcast</code>, <code>/dbroadcast</code> — Broadcast announcements\n"
        "• <code>/ban &lt;id&gt;</code> & <code>/uban &lt;id&gt;</code> — Manage user access\n"
        "• <code>/status</code> & <code>/stats</code> — Check worker health & VPS resources"
    )
    await message.reply_text(tutorial_text, parse_mode=enums.ParseMode.HTML)


async def dlt_time_callback(client: Client, query: CallbackQuery):
    """Handle preset auto-delete time selection buttons."""
    user = query.from_user
    if user and not is_owner(user.id):
        await query.answer("⛔ Owner only", show_alert=True)
        return

    from bot.database import db
    data = query.data
    sec_str = data.split(":", 1)[1]
    sec = int(sec_str)

    await db.set_dlt_time(sec)
    await query.answer(f"Updated auto-delete to {sec}s")

    status_str = f"{sec}s ({sec / 60:.1f} mins)" if sec > 0 else "Disabled (OFF)"
    await query.message.edit_text(
        f"✅ <b>Auto-Delete Time Updated:</b> <code>{status_str}</code>\n\n"
        "Delivered files will automatically be deleted and replaced with recovery notice.",
        parse_mode=enums.ParseMode.HTML,
    )


async def toggle_fsub_mod_callback(client: Client, query: CallbackQuery):
    """Handle FSub timer mode toggle button."""
    user = query.from_user
    if user and not is_owner(user.id):
        await query.answer("⛔ Owner only", show_alert=True)
        return

    from bot.database import db
    cur = await db.get_fsub_mod()
    new_st = not cur
    await db.set_fsub_mod(new_st)

    await query.answer(f"FSub Timer Mode: {'ON' if new_st else 'OFF'}")
    await cmd_fsub(client, query.message)
