"""Child Bot Manager — manages worker bots for load balancing & multi-quality deep linking."""

from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone
import html as htmlmod

from bot.telegram import Client, filters, enums
from bot.telegram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import settings

log = logging.getLogger(__name__)


class ChildBotManager:
    """Manages dynamic Pyrogram Client instances for child/worker bots."""

    def __init__(self, main_client: Client | None = None):
        self.main_client = main_client
        self.active_clients: dict[int, Client] = {}   # bot_id -> Pyrogram Client
        self.bot_info_cache: dict[int, dict] = {}     # bot_id -> metadata doc
        self._round_robin_indices: dict[str, int] = {} # quality -> current index
        self._running = False

    async def start(self):
        """Load and start all active child bots from the database on bot boot."""
        from bot.database import db
        if not db:
            log.warning("ChildBotManager: Database not ready, skipping child bot startup.")
            return

        bot_docs = await db.get_child_bots(active_only=True)
        log.info("ChildBotManager: Found %d registered child bot(s)", len(bot_docs))

        started_count = 0
        for doc in bot_docs:
            success = await self._start_single_bot(doc)
            if success:
                started_count += 1

        self._running = True
        log.info("ChildBotManager: Successfully initialized %d/%d child bot(s)", started_count, len(bot_docs))

    async def stop(self):
        """Gracefully stop all child bot client instances on shutdown."""
        self._running = False
        for bot_id, client in list(self.active_clients.items()):
            try:
                log.info("Stopping child bot ID %d...", bot_id)
                await client.stop()
            except Exception as e:
                log.warning("Error stopping child bot %d: %s", bot_id, e)
        self.active_clients.clear()
        self.bot_info_cache.clear()
        log.info("ChildBotManager: All child bots stopped.")

    async def add_bot(self, token: str, quality: str = "all") -> dict:
        """Validate token with Telegram, register in DB, and start the child bot."""
        from bot.database import db
        if not db:
            raise RuntimeError("Database not available.")

        token = token.strip()
        quality = quality.strip().lower()

        # Step 1: Validate token by testing get_me()
        temp_client = Client(
            name=f"temp_val_{int(datetime.now().timestamp())}",
            api_id=settings.bot.api_id,
            api_hash=settings.bot.api_hash,
            bot_token=token,
            in_memory=True,
        )
        try:
            await temp_client.start()
            me = await temp_client.get_me()
            await temp_client.stop()
        except Exception as e:
            raise ValueError(f"Invalid Telegram bot token or connection failure: {e}")

        bot_id = me.id
        username = me.username or f"bot_{bot_id}"
        first_name = me.first_name or "AnimeDekho Worker"

        # Step 2: Save to MongoDB
        await db.add_child_bot(
            token=token,
            username=username,
            bot_id=bot_id,
            quality=quality,
            first_name=first_name,
        )

        doc = await db.get_child_bot(str(bot_id))
        if not doc:
            doc = {
                "token": token,
                "username": username,
                "bot_id": bot_id,
                "first_name": first_name,
                "quality": quality,
                "is_active": True,
                "files_served": 0,
            }

        # Step 3: Launch live client
        await self._start_single_bot(doc)

        return {
            "bot_id": bot_id,
            "username": username,
            "first_name": first_name,
            "quality": quality,
        }

    async def remove_bot(self, identifier: str) -> bool:
        """Stop and remove a child bot by username, bot_id, or token prefix."""
        from bot.database import db
        if not db:
            return False

        doc = await db.get_child_bot(identifier)
        if not doc:
            return False

        bot_id = doc["bot_id"]
        if bot_id in self.active_clients:
            try:
                client = self.active_clients.pop(bot_id)
                await client.stop()
            except Exception as e:
                log.warning("Error stopping removed child bot %d: %s", bot_id, e)

        self.bot_info_cache.pop(bot_id, None)
        return await db.remove_child_bot(identifier)

    async def set_bot_quality(self, identifier: str, quality: str) -> bool:
        """Update assigned quality tier for an existing child bot."""
        from bot.database import db
        if not db:
            return False

        q_clean = quality.strip().lower()
        success = await db.update_child_bot(identifier, {"quality": q_clean})
        if success:
            doc = await db.get_child_bot(identifier)
            if doc and doc["bot_id"] in self.bot_info_cache:
                self.bot_info_cache[doc["bot_id"]]["quality"] = q_clean
        return success

    async def get_all_bots(self) -> list[dict]:
        """List all child bots with live status and metadata."""
        from bot.database import db
        if not db:
            return []

        docs = await db.get_child_bots()
        for d in docs:
            b_id = d.get("bot_id")
            d["is_online"] = b_id in self.active_clients
        return docs

    def get_bot_for_quality(self, quality: str) -> str | None:
        """
        Get the child bot username assigned to this quality tier.
        Supports round-robin load balancing when multiple bots are assigned to the same quality.
        Returns username without '@' or None (falls back to main bot).
        """
        q_norm = quality.strip().lower()
        is_4k = q_norm in ("4k", "2160p", "2160")

        # Find matching candidates
        matching_bots: list[dict] = []
        fallback_all_bots: list[dict] = []

        for b_id, doc in self.bot_info_cache.items():
            if b_id not in self.active_clients:
                continue
            bot_q = doc.get("quality", "all").lower()

            if is_4k and bot_q in ("4k", "2160p", "2160", "uhd"):
                matching_bots.append(doc)
            elif bot_q == q_norm or (q_norm.replace("p", "") == bot_q.replace("p", "")):
                matching_bots.append(doc)
            elif bot_q in ("all", "any"):
                fallback_all_bots.append(doc)

        pool = matching_bots if matching_bots else fallback_all_bots
        if not pool:
            return None

        # Round-robin
        idx = self._round_robin_indices.get(q_norm, 0) % len(pool)
        self._round_robin_indices[q_norm] = idx + 1
        return pool[idx].get("username")

    async def _start_single_bot(self, doc: dict) -> bool:
        """Launch a single child bot Pyrogram Client and attach message handlers."""
        bot_id = doc["bot_id"]
        token = doc["token"]
        username = doc["username"]
        quality = doc.get("quality", "all")

        # Stop existing client if any
        if bot_id in self.active_clients:
            try:
                await self.active_clients[bot_id].stop()
            except Exception:
                pass

        client = Client(
            name=f"child_bot_{bot_id}",
            api_id=settings.bot.api_id,
            api_hash=settings.bot.api_hash,
            bot_token=token,
            in_memory=True,
        )

        # ── Register Message Handlers for Child Bot ────────────────
        @client.on_message(filters.command("start") & filters.private)
        async def _child_start(c: Client, m: Message):
            args = m.text.split(maxsplit=1)
            if len(args) > 1 and args[1].startswith("get_"):
                await self._handle_child_file_request(c, m, args[1], doc)
                return

            main_user = ""
            if self.main_client and hasattr(self.main_client, "me") and self.main_client.me:
                main_user = f"@{self.main_client.me.username}"
            else:
                from bot.library import library_manager
                if library_manager and library_manager.bot_username:
                    main_user = f"@{library_manager.bot_username}"

            welcome = (
                f"🤖 <b>AnimeDekho Worker Bot</b> (@{username})\n\n"
                f"⚡ <b>Assigned Tier:</b> <code>{quality.upper()}</code>\n"
                f"📥 I deliver requested anime episodes and movies directly to you with maximum download speed.\n\n"
                f"🔍 <b>To search and browse all anime, use our Main Bot:</b>\n"
                f"👉 {main_user or 'Main Controller Bot'}"
            )
            buttons = []
            if main_user:
                buttons.append([InlineKeyboardButton("🚀 Go to Main Bot", url=f"https://t.me/{main_user.lstrip('@')}")])
            await m.reply_text(welcome, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

        @client.on_message(filters.command("help") & filters.private)
        async def _child_help(c: Client, m: Message):
            main_user = ""
            if self.main_client and hasattr(self.main_client, "me") and self.main_client.me:
                main_user = f"@{self.main_client.me.username}"

            await m.reply_text(
                f"📖 <b>Worker Bot Help</b>\n\n"
                f"This bot is an automated file delivery worker for {main_user}.\n"
                f"Click on any episode or quality button in our channel or main bot to download.",
                parse_mode=enums.ParseMode.HTML,
            )

        try:
            await client.start()
            self.active_clients[bot_id] = client
            self.bot_info_cache[bot_id] = doc
            log.info("Child Bot @%s (ID: %d) successfully started for tier [%s]", username, bot_id, quality)
            return True
        except Exception as e:
            log.error("Failed to start child bot @%s (%d): %s", username, bot_id, e)
            return False

    async def _handle_child_file_request(
        self, client: Client, message: Message, param: str, bot_doc: dict
    ):
        """Deliver requested anime episodes / movies through this child worker bot."""
        from bot.database import db
        user = message.from_user
        user_id = user.id if user else 0

        # Force Subscribe verification (if main channel is configured)
        if settings.bot.main_channel:
            from bot.forcesub import check_subscription
            from bot.auth import is_owner
            if not is_owner(user_id):
                is_sub = await check_subscription(client, user_id, settings.bot.main_channel)
                if not is_sub:
                    invite_link = None
                    if db:
                        invite_link = await db.get_config("channel_invite_link")
                    text = "📢 <b>Please join our Channel to download this file!</b>\n\nAfter joining, click the link again."
                    markup = None
                    if invite_link:
                        markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=invite_link)]])
                    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
                    return

        # Parse deep link: get_<slug>_<quality>_<episode_key>
        raw = param[4:]  # strip 'get_'
        m = re.match(
            r"^(.+?)_(480p|720p|1080p|1080p\s*hq|1080p\s*hq\s*x265|4k|2160p|auto)_(s\d+e\d+|movie|all)$",
            raw,
            re.IGNORECASE,
        )
        if not m:
            await message.reply_text("⚠️ Invalid or expired download link.")
            return

        series_slug = m.group(1)
        quality = m.group(2)
        episode_key = m.group(3)

        from utils.helpers import slug_to_title
        title = slug_to_title(series_slug)

        # Batch download / Get All request
        if episode_key.lower() == "all":
            # Find all files matching this series and quality
            query: dict = {"series_slug": series_slug}
            is_4k = quality.lower() in ("4k", "2160p", "2160")
            if is_4k:
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
                # Flexible fallback if exact series_slug didn't match
                all_files = await db.files.find({"series_title": {"$regex": f"^{re.escape(title)}$", "$options": "i"}}).to_list(length=None)

            if not all_files:
                await message.reply_text("❌ No downloaded files found in the library for this quality tier.")
                return

            from bot.library import _ep_sort_key
            all_files.sort(key=lambda x: _ep_sort_key(x.get("episode_key", "")))

            status_msg = await message.reply_text(f"⚡ <b>Worker @{bot_doc['username']}:</b> Delivering {len(all_files)} episode(s)...", parse_mode=enums.ParseMode.HTML)
            delivered = 0

            for f in all_files:
                ep_key = f.get("episode_key", "")
                q_label = f.get("quality", quality)
                caption = f"📦 <b>{htmlmod.escape(title)}</b> [{q_label}] — {ep_key}\n<i>⚡ Delivered via @{bot_doc['username']}</i>"

                sent = await self._send_single_file(client, message.chat.id, f, caption)
                if sent:
                    delivered += 1
                await asyncio.sleep(0.4)

            await status_msg.edit_text(f"✅ <b>Delivered {delivered}/{len(all_files)} episode(s)</b> via @{bot_doc['username']}!", parse_mode=enums.ParseMode.HTML)
            await db.increment_child_bot_stats(bot_doc["bot_id"])
            return

        # Single episode or movie
        cached_file = await db.find_cached_file(series_slug, episode_key, quality)
        if not cached_file:
            # Fallback to direct query
            cached_file = await db.files.find_one({
                "series_slug": series_slug,
                "episode_key": episode_key,
            })

        if not cached_file:
            await message.reply_text("❌ This episode is not yet available in the library.")
            return

        q_label = cached_file.get("quality", quality)
        disp_ep = f" — {episode_key}" if episode_key.lower() != "movie" else " 🎬 Movie"
        caption = f"📦 <b>{htmlmod.escape(title)}</b> [{q_label}]{disp_ep}\n<i>⚡ Delivered via @{bot_doc['username']}</i>"

        sent = await self._send_single_file(client, message.chat.id, cached_file, caption)
        if sent:
            await db.increment_child_bot_stats(bot_doc["bot_id"])
        else:
            await message.reply_text("⚠️ Could not deliver file. Please try again or download via our main bot.")

    async def _send_single_file(
        self, child_client: Client, chat_id: int, file_doc: dict, caption: str
    ) -> bool:
        """
        Deliver a single file to user with fallback cascade:
        1. Copy from storage/main channel (instant & works across all bots).
        2. Send document/video using file_id via child client.
        3. Fallback to main client sending/copying directly.
        """
        storage_cid = file_doc.get("storage_channel_id")
        storage_mid = file_doc.get("storage_message_id")
        file_id = file_doc.get("file_id")

        # 1. Try copy_message from storage/dump channel
        if storage_cid and storage_mid:
            try:
                await child_client.copy_message(
                    chat_id=chat_id,
                    from_chat_id=storage_cid,
                    message_id=storage_mid,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
                return True
            except Exception as e:
                log.debug("copy_message from storage channel failed: %s", e)

        # 2. Try sending directly with file_id
        if file_id:
            try:
                await child_client.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
                return True
            except Exception:
                try:
                    await child_client.send_document(
                        chat_id=chat_id,
                        document=file_id,
                        caption=caption,
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return True
                except Exception as e2:
                    log.debug("Child bot direct send file_id failed: %s", e2)

        # 3. Fallback: Main Bot client delivers on behalf of child bot
        if self.main_client and file_id:
            try:
                await self.main_client.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
                return True
            except Exception as e3:
                log.warning("Main client fallback send failed: %s", e3)

        return False

    async def check_bots_health(self) -> list[dict]:
        """
        Check health and connection latency of all registered child worker bots.
        Returns list of status dicts with bot info, ping latency, and online/offline state.
        """
        import time
        from bot.database import db
        if not db:
            return []
        try:
            bot_docs = await db.get_child_bots()
        except Exception as e:
            log.warning("Failed to fetch child bots for health check: %s", e)
            return []

        results = []
        for doc in bot_docs:
            bot_id = doc.get("bot_id")
            username = doc.get("username", "")
            quality = doc.get("quality", "all")
            files_served = doc.get("files_served", 0)
            client = self.active_clients.get(bot_id)

            status = {
                "bot_id": bot_id,
                "username": username,
                "quality": quality,
                "files_served": files_served,
                "is_active_config": doc.get("is_active", True),
                "is_connected": False,
                "ping_ms": None,
                "first_name": username,
                "error": None,
            }

            if not client:
                status["error"] = "Client not running"
            elif not client.is_connected:
                status["error"] = "Client disconnected"
            else:
                try:
                    start_t = time.perf_counter()
                    me = await client.get_me()
                    latency = (time.perf_counter() - start_t) * 1000
                    status["is_connected"] = True
                    status["ping_ms"] = round(latency, 1)
                    status["first_name"] = me.first_name or username
                except Exception as e:
                    status["error"] = str(e)[:100]

            results.append(status)
        return results


# Singleton instance
child_bot_manager: ChildBotManager | None = None
