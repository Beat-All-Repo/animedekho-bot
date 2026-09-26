"""Force Subscribe (FSub) & Timer Invite Link System (Anti-Copyright Protection)."""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.telegram.enums import ChatMemberStatus

from config.settings import settings
from bot.auth import is_owner

log = logging.getLogger(__name__)


async def create_timer_invite_link(
    client: Client,
    channel_id: int | str,
    expire_seconds: int = 120,
    member_limit: int = 1,
    name: str = "Timer Link",
) -> str | None:
    """
    Generate a Telegram-enforced temporary invite link that expires after expire_seconds.
    Because expire_date is set in Telegram servers, the link automatically invalidates
    even if the bot goes offline or restarts.
    """
    try:
        exp_dt = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
        invite = await client.create_chat_invite_link(
            chat_id=channel_id,
            expire_date=exp_dt,
            member_limit=member_limit,
            name=name[:32],
        )
        log.info("Created timer invite link for %s (expires in %ds)", channel_id, expire_seconds)
        return invite.invite_link
    except Exception as e:
        log.warning("Failed to create timer invite link for %s: %s", channel_id, e)
        # Try userbot if client lacks permissions
        from bot.userbot import userbot_manager
        if userbot_manager and userbot_manager.is_active and userbot_manager.client:
            try:
                exp_dt = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
                u_inv = await userbot_manager.client.create_chat_invite_link(
                    chat_id=channel_id,
                    expire_date=exp_dt,
                    member_limit=member_limit,
                    name=name[:32],
                )
                log.info("Userbot created timer invite link for %s", channel_id)
                return u_inv.invite_link
            except Exception as ue:
                log.warning("Userbot also failed creating timer invite link: %s", ue)
        return None


async def check_fsub(
    client: Client,
    user_id: int,
    retry_param: str = "",
) -> tuple[bool, str | None, InlineKeyboardMarkup | None]:
    """
    Check if a user is subscribed to the FSub channel.
    Returns: (is_subscribed, alert_text, reply_markup)
    """
    if is_owner(user_id):
        return True, None, None

    from bot.database import db
    if not db:
        return True, None, None

    # Determine FSub channel
    fsub_chan = await db.get_fsub_channel()
    if not fsub_chan:
        return True, None, None

    # Check member status
    is_member = False
    try:
        member = await client.get_chat_member(fsub_chan, user_id)
        if member and member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            is_member = True
    except Exception as ce:
        log.debug("FSub member check for %d failed: %s", user_id, ce)
        # If check fails due to bot not being admin in channel or unknown error, don't lock user out
        is_member = False

    if is_member:
        return True, None, None

    # User is not a member — construct prompt
    fsub_mod = await db.get_fsub_mod()
    invite_url = None

    if fsub_mod:
        # Timer link mode is ON — generate a 2-minute expiring link
        invite_url = await create_timer_invite_link(client, fsub_chan, expire_seconds=120, name="FSub 2m Link")

    if not invite_url:
        # Normal link mode or fallback
        if isinstance(fsub_chan, str) and not fsub_chan.startswith("-"):
            invite_url = f"https://t.me/{fsub_chan.lstrip('@')}"
        else:
            invite_url = await db.get_config("channel_invite_link")

    bot_username = getattr(client, "me", None)
    b_user = bot_username.username if bot_username and bot_username.username else "bot"
    retry_url = f"https://t.me/{b_user}?start={retry_param}" if retry_param else f"https://t.me/{b_user}?start=start"

    buttons = []
    if invite_url:
        buttons.append([InlineKeyboardButton("📢 Join Channel", url=invite_url)])
    buttons.append([InlineKeyboardButton("🔄 Try Again", url=retry_url)])

    timer_note = "\n\n⚠️ <i>Note: This invite link is temporary and will expire in <b>2 minutes</b>!</i>" if fsub_mod else ""
    text = (
        "📢 <b>Please Join Our Channel to Continue!</b>\n\n"
        "You must be a member of our channel to download files or view content."
        f"{timer_note}\n\n"
        "After joining, click the <b>Try Again</b> button below!"
    )

    return False, text, InlineKeyboardMarkup(buttons)
