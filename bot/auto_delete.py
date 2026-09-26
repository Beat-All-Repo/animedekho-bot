"""Persistent Auto-Delete System for Videos & Files — resilient across bot restarts."""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)

DELETED_NOTICE_TEXT = (
    "<u><b>Pʀᴇᴠɪᴏᴜs Mᴇssᴀɢᴇ ᴡᴀs Dᴇʟᴇᴛᴇᴅ</b></u> 🗑️\n"
    "<blockquote><b>Iғ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ɢᴇᴛ ᴛʜᴇ ғɪʟᴇ(s) ᴀɢᴀɪɴ, ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ ɢᴇᴛ ғɪʟᴇ ᴀɢᴀɪɴ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴇʟsᴇ ᴄʟᴏsᴇ ᴛʜɪs ᴍᴇssᴀɢᴇ ʙʏ ᴄʟɪᴄᴋ ᴏɴ ᴄʟᴏsᴇ.</b></blockquote>"
)


class AutoDeleteService:
    """Manages scheduled message deletions backed by MongoDB for survival across restarts."""

    def __init__(self, main_client: Client | None = None):
        self.main_client = main_client
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self, main_client: Client | None = None):
        if main_client:
            self.main_client = main_client
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._worker_loop())
            log.info("AutoDeleteService: Background deletion worker started.")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("AutoDeleteService: Stopped.")

    async def schedule_deletion(
        self,
        client: Client,
        chat_id: int,
        message_id: int,
        get_file_link: str = "",
        file_title: str = "",
        custom_seconds: int | None = None,
    ):
        """Schedule a message for automatic deletion after configured delay."""
        from bot.database import db
        if not db:
            return

        if custom_seconds is not None:
            delay = custom_seconds
        else:
            delay = await db.get_dlt_time()

        if delay <= 0:
            return  # Auto-delete is disabled

        delete_at = time.time() + delay
        bot_id = getattr(client, "me", None)
        bot_id_int = bot_id.id if bot_id else 0

        await db.add_auto_delete_job(
            chat_id=chat_id,
            message_id=message_id,
            bot_id=bot_id_int,
            delete_at=delete_at,
            get_file_link=get_file_link,
            file_title=file_title,
        )
        log.debug(
            "AutoDeleteService: Scheduled msg %d in chat %d for deletion in %ds (at %s)",
            message_id, chat_id, delay, datetime.fromtimestamp(delete_at, tz=timezone.utc).strftime("%H:%M:%S")
        )

    async def _worker_loop(self):
        """Periodically check and process expired delete jobs."""
        from bot.database import db
        while self._running:
            try:
                if db:
                    now = time.time()
                    expired_jobs = await db.get_pending_auto_delete_jobs(before_ts=now)
                    if expired_jobs:
                        log.info("AutoDeleteService: Processing %d expired deletion job(s)", len(expired_jobs))
                        for job in expired_jobs:
                            await self._process_single_job(job)
            except Exception as e:
                log.warning("AutoDeleteService error in worker loop: %s", e)

            await asyncio.sleep(5)

    async def _process_single_job(self, job: dict[str, Any]):
        """Execute deletion of a single message and send post-delete recovery notice."""
        from bot.database import db
        chat_id = job.get("chat_id")
        message_id = job.get("message_id")
        bot_id = job.get("bot_id", 0)
        get_file_link = job.get("get_file_link", "")

        if not chat_id or not message_id:
            if db:
                await db.remove_auto_delete_job(chat_id, message_id)
            return

        # Resolve which client to use for deletion
        client = None
        if self.main_client and getattr(self.main_client, "me", None) and self.main_client.me.id == bot_id:
            client = self.main_client
        else:
            from bot.child_bots import child_bot_manager
            if child_bot_manager and bot_id in child_bot_manager.active_clients:
                client = child_bot_manager.active_clients[bot_id]

        if not client:
            client = self.main_client

        if not client:
            log.warning("AutoDeleteService: No client available to delete msg %d in chat %d", message_id, chat_id)
            return

        # 1. Delete the media message
        try:
            await client.delete_messages(chat_id=chat_id, message_ids=message_id)
            log.info("AutoDeleteService: Deleted msg %d in chat %d", message_id, chat_id)
        except Exception as de:
            log.debug("AutoDeleteService: delete_messages failed (already deleted or error): %s", de)

        # 2. Send post-delete notice message if link exists
        if get_file_link:
            try:
                buttons = [
                    [
                        InlineKeyboardButton("GET FILE AGAIN! ↗", url=get_file_link),
                        InlineKeyboardButton("CLOSE", callback_data="close_dlt_notice"),
                    ]
                ]
                await client.send_message(
                    chat_id=chat_id,
                    text=DELETED_NOTICE_TEXT,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    disable_web_page_preview=True,
                )
            except Exception as se:
                log.debug("AutoDeleteService: Failed sending delete notification notice: %s", se)

        # 3. Clean up job from DB
        if db:
            await db.remove_auto_delete_job(chat_id, message_id)


# Singleton instance
auto_delete_service = AutoDeleteService()


async def handle_close_dlt_notice(client: Client, query: CallbackQuery):
    """Callback query handler to dismiss the auto-delete notification."""
    try:
        await query.message.delete()
    except Exception:
        pass
    try:
        await query.answer("Closed")
    except Exception:
        pass
