"""Userbot Manager — manages user session for creating and configuring per-anime channels."""

from __future__ import annotations
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from pyrogram import Client, errors
from pyrogram.types import ChatPrivileges, User

from config.settings import settings
from bot.database import db

log = logging.getLogger(__name__)


class UserbotManager:
    """
    Manages a Pyrogram user session (MTProto userbot).
    Allows creating dedicated anime channels, setting channel posters,
    promoting main and child bots as admins, and managing channel mappings.
    """

    def __init__(self, main_client: Client | None = None):
        self.main_client = main_client
        self.client: Client | None = None
        self.me: User | None = None
        self.is_active: bool = False
        self._login_states: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        """Initialize and start the userbot client from DB if session exists."""
        async with self._lock:
            if self.is_active and self.client and self.client.is_connected:
                return True

            if not db:
                log.warning("Database not initialized, cannot start userbot")
                return False

            session_data = await db.get_userbot_session()
            if not session_data or not session_data.get("session_string"):
                log.info("No userbot session configured")
                return False

            session_str = session_data["session_string"]

            try:
                self.client = Client(
                    name="animedekho_userbot",
                    api_id=settings.bot.api_id,
                    api_hash=settings.bot.api_hash,
                    session_string=session_str,
                    in_memory=True,
                )
                await self.client.start()
                self.me = await self.client.get_me()
                self.is_active = True
                log.info("Userbot started successfully as %s (@%s, ID: %d)",
                         self.me.first_name, self.me.username or "no_username", self.me.id)
                return True
            except Exception as e:
                log.error("Failed to start userbot with saved session: %s", e)
                self.is_active = False
                self.client = None
                self.me = None
                return False

    async def stop(self):
        """Gracefully stop the userbot client."""
        async with self._lock:
            if self.client:
                try:
                    if self.client.is_connected:
                        await self.client.stop()
                except Exception as e:
                    log.warning("Error stopping userbot client: %s", e)
                finally:
                    self.client = None
                    self.me = None
                    self.is_active = False
            log.info("Userbot stopped")

    def is_in_login(self, user_id: int) -> bool:
        """Check if a user is currently in an interactive login flow."""
        return user_id in self._login_states

    async def cancel_login(self, user_id: int) -> bool:
        """Cancel an ongoing interactive login for a user."""
        state = self._login_states.pop(user_id, None)
        if state:
            temp_client: Client | None = state.get("temp_client")
            if temp_client and temp_client.is_connected:
                try:
                    await temp_client.disconnect()
                except Exception:
                    pass
            return True
        return False

    async def login_with_session(self, session_string: str, user_id: int = 0) -> tuple[bool, str, dict]:
        """Direct login using an existing Pyrogram string session."""
        clean_session = session_string.strip()
        temp_client = Client(
            name="test_session_login",
            api_id=settings.bot.api_id,
            api_hash=settings.bot.api_hash,
            session_string=clean_session,
            in_memory=True,
        )
        try:
            await temp_client.start()
            me = await temp_client.get_me()
            await temp_client.stop()

            user_meta = {
                "id": me.id,
                "first_name": me.first_name,
                "last_name": me.last_name or "",
                "username": me.username or "",
                "phone_number": getattr(me, "phone_number", ""),
            }

            if db:
                await db.save_userbot_session(clean_session, user_meta)

            await self.stop()
            started = await self.start()
            if not started:
                return False, "Session validated but failed to start userbot daemon.", {}

            return True, (
                f"✅ <b>Userbot Connected!</b>\n\n"
                f"👤 <b>Name:</b> {me.first_name}\n"
                f"🔗 <b>Username:</b> @{me.username or 'None'}\n"
                f"🆔 <b>ID:</b> <code>{me.id}</code>"
            ), user_meta
        except Exception as e:
            log.warning("Direct session login failed: %s", e)
            return False, f"Invalid or expired string session: {e}", {}

    async def start_interactive_login(self, user_id: int) -> str:
        """Begin interactive step-by-step phone login."""
        await self.cancel_login(user_id)

        temp_client = Client(
            name=f"login_{user_id}",
            api_id=settings.bot.api_id,
            api_hash=settings.bot.api_hash,
            in_memory=True,
        )
        self._login_states[user_id] = {
            "step": "WAIT_PHONE",
            "temp_client": temp_client,
        }

        return (
            "🔐 <b>Userbot Login — Step 1/3</b>\n\n"
            "Please send your <b>Phone Number</b> including country code.\n"
            "Example: <code>+919876543210</code> or <code>+12025550143</code>\n\n"
            "<i>Send /cancel at any time to abort.</i>"
        )

    async def handle_login_step(self, user_id: int, text: str) -> tuple[str, bool]:
        """
        Handle text input from a user going through the login wizard.
        Returns: (reply_text, is_finished)
        """
        clean_text = text.strip()
        if clean_text.lower() in ("/cancel", "cancel", "/abort"):
            await self.cancel_login(user_id)
            return "❌ <b>Login cancelled.</b>", True

        state = self._login_states.get(user_id)
        if not state:
            return "⚠️ No active login process found. Type /login to start.", True

        step = state.get("step")
        temp_client: Client = state["temp_client"]

        # Step 1: Receiving Phone Number
        if step == "WAIT_PHONE":
            phone = re.sub(r"[\s\-\(\)]", "", clean_text)
            if not phone.startswith("+"):
                phone = "+" + phone

            if len(phone) < 8 or not phone[1:].isdigit():
                return (
                    "⚠️ <b>Invalid phone number format.</b>\n"
                    "Please provide a valid international phone number starting with <code>+</code>.\n"
                    "Example: <code>+919876543210</code>\n\n"
                    "<i>Type /cancel to abort.</i>",
                    False,
                )

            try:
                if not temp_client.is_connected:
                    await temp_client.connect()
                sent_code = await temp_client.send_code(phone)
                state["step"] = "WAIT_CODE"
                state["phone"] = phone
                state["phone_code_hash"] = sent_code.phone_code_hash
                return (
                    "📩 <b>Verification Code Sent — Step 2/3</b>\n\n"
                    f"A login code was sent to <code>{phone}</code> via Telegram.\n\n"
                    "Please enter the code here (e.g. <code>1 2 3 4 5</code> or <code>12345</code>):\n\n"
                    "<i>Tip: If Telegram blocks sending digits together, separate them with spaces: <code>1 2 3 4 5</code>.</i>\n"
                    "<i>Send /cancel to abort.</i>",
                    False,
                )
            except errors.FloodWait as fw:
                await self.cancel_login(user_id)
                return f"⏳ <b>FloodWait Error:</b> Please wait {fw.value} seconds before trying again.", True
            except errors.PhoneNumberInvalid:
                return "❌ <b>Invalid Phone Number:</b> Please re-check the number and try again.", False
            except Exception as e:
                log.exception("send_code failed")
                await self.cancel_login(user_id)
                return f"❌ <b>Failed to send code:</b> {e}", True

        # Step 2: Receiving OTP Code
        elif step == "WAIT_CODE":
            code = re.sub(r"[\s\-]", "", clean_text)
            if not code.isdigit():
                return "⚠️ Please enter only numbers for the verification code (e.g. <code>1 2 3 4 5</code>).", False

            phone = state["phone"]
            phone_code_hash = state["phone_code_hash"]

            try:
                await temp_client.sign_in(phone, phone_code_hash, code)
                # Successful sign in without 2FA
                return await self._finalize_login(user_id, temp_client)
            except errors.SessionPasswordNeeded:
                state["step"] = "WAIT_PASSWORD"
                return (
                    "🔑 <b>Two-Step Verification (2FA) — Step 3/3</b>\n\n"
                    "Your Telegram account has Two-Step Verification (Cloud Password) enabled.\n\n"
                    "Please enter your 2FA password:\n\n"
                    "<i>Your password message will be immediately deleted for privacy. Send /cancel to abort.</i>",
                    False,
                )
            except errors.PhoneCodeInvalid:
                return "❌ <b>Incorrect code:</b> The verification code entered is invalid. Please try again.", False
            except errors.PhoneCodeExpired:
                await self.cancel_login(user_id)
                return "⌛ <b>Code Expired:</b> The confirmation code has expired. Please run /login again.", True
            except Exception as e:
                log.exception("sign_in failed")
                await self.cancel_login(user_id)
                return f"❌ <b>Sign in failed:</b> {e}", True

        # Step 3: Receiving 2FA Cloud Password
        elif step == "WAIT_PASSWORD":
            password = clean_text
            try:
                await temp_client.check_password(password)
                return await self._finalize_login(user_id, temp_client)
            except errors.PasswordHashInvalid:
                return "❌ <b>Incorrect password:</b> Please enter your 2FA cloud password again.", False
            except Exception as e:
                log.exception("check_password failed")
                await self.cancel_login(user_id)
                return f"❌ <b>2FA Verification failed:</b> {e}", True

        return "⚠️ Unknown login state. Run /login to restart.", True

    async def _finalize_login(self, user_id: int, temp_client: Client) -> tuple[str, bool]:
        """Finalize login, export session string, save to DB, and start userbot daemon."""
        try:
            session_str = await temp_client.export_session_string()
            me = await temp_client.get_me()
            await temp_client.disconnect()

            user_meta = {
                "id": me.id,
                "first_name": me.first_name,
                "last_name": me.last_name or "",
                "username": me.username or "",
                "phone_number": getattr(me, "phone_number", ""),
            }

            if db:
                await db.save_userbot_session(session_str, user_meta)

            self._login_states.pop(user_id, None)

            await self.stop()
            started = await self.start()

            status_note = "Userbot daemon is active!" if started else "Session saved, but userbot daemon failed to start."

            return (
                f"🎉 <b>Login Successful!</b>\n\n"
                f"👤 <b>Account:</b> {me.first_name}\n"
                f"🔗 <b>Username:</b> @{me.username or 'None'}\n"
                f"🆔 <b>User ID:</b> <code>{me.id}</code>\n\n"
                f"✨ {status_note}\n"
                f"Per-anime channel creation and mapping are now ready to use."
            ), True
        except Exception as e:
            log.exception("Failed to finalize login")
            self._login_states.pop(user_id, None)
            return f"❌ <b>Error finalizing session:</b> {e}", True

    async def logout(self) -> bool:
        """Logout userbot and clear saved session."""
        await self.stop()
        if db:
            await db.delete_userbot_session()
        return True

    def get_status(self) -> dict[str, Any]:
        """Return status dictionary of current userbot."""
        return {
            "is_active": self.is_active,
            "user_id": self.me.id if self.me else None,
            "name": self.me.first_name if self.me else None,
            "username": self.me.username if self.me else None,
        }

    async def check_health(self) -> dict[str, Any]:
        """Check userbot health and measure round-trip ping latency."""
        import time
        res = {
            "is_active": self.is_active,
            "is_connected": False,
            "ping_ms": None,
            "user_id": None,
            "username": None,
            "first_name": None,
            "error": None,
        }
        if not self.is_active or not self.client:
            res["error"] = "Userbot not logged in or inactive"
            return res

        if not self.client.is_connected:
            res["error"] = "Userbot client disconnected"
            return res

        try:
            start_t = time.perf_counter()
            me = await self.client.get_me()
            latency = (time.perf_counter() - start_t) * 1000
            res["is_connected"] = True
            res["ping_ms"] = round(latency, 1)
            res["user_id"] = me.id
            res["username"] = me.username
            res["first_name"] = me.first_name
        except Exception as e:
            res["error"] = str(e)[:100]

        return res

    async def create_anime_channel(
        self,
        series_title: str,
        series_slug: str,
        poster_url: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a dedicated Telegram channel for an anime series.
        - Sets title & description
        - Downloads and sets poster as channel photo
        - Adds & promotes Main Bot and Child Worker Bots as administrators
        - Creates a permanent invite link
        - Stores the mapping in MongoDB
        """
        if not db:
            raise RuntimeError("Database not available")

        # Check existing mapping first
        existing = await db.get_channel_mapping(series_slug)
        if existing and existing.get("channel_id"):
            log.info("Series %s already mapped to channel %s", series_slug, existing["channel_id"])
            return existing

        if not self.is_active or not self.client or not self.client.is_connected:
            raise RuntimeError("Userbot is not active or logged in. Run /login to connect a user session.")

        channel_title = f"{series_title[:120]}"
        channel_desc = description or (
            f"Official archive for {series_title}.\n"
            f"All episodes in multi-quality (1080p, 720p, 480p).\n"
            f"Powered by AnimeDekho Bot"
        )

        log.info("Creating channel for series %s ('%s')...", series_slug, channel_title)
        chat = await self.client.create_channel(title=channel_title, description=channel_desc)
        channel_id = chat.id
        log.info("Channel created with ID: %d", channel_id)

        # Set channel photo if poster_url provided
        if poster_url:
            poster_path = None
            try:
                from bot.library import _download_poster
                poster_path = await _download_poster(poster_url)
                if poster_path and os.path.exists(poster_path):
                    await self.client.set_chat_photo(channel_id, photo=poster_path)
                    log.info("Set channel photo for channel %d", channel_id)
            except Exception as pe:
                log.warning("Failed to set channel photo for channel %d: %s", channel_id, pe)
            finally:
                if poster_path and os.path.exists(poster_path):
                    try:
                        os.remove(poster_path)
                    except Exception:
                        pass

        # Privileges to grant bots
        bot_privileges = ChatPrivileges(
            can_manage_chat=True,
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_change_info=True,
            can_pin_messages=True,
        )

        # 1. Promote Main Bot
        if self.main_client:
            try:
                main_me = await self.main_client.get_me()
                if main_me:
                    target = main_me.username or main_me.id
                    try:
                        await self.client.add_chat_members(channel_id, target)
                    except Exception:
                        pass
                    try:
                        await self.client.promote_chat_member(channel_id, target, privileges=bot_privileges)
                        log.info("Promoted main bot @%s as admin in %d", target, channel_id)
                    except Exception as me_err:
                        log.warning("Could not promote main bot @%s: %s", target, me_err)
            except Exception as ge:
                log.warning("Failed getting main bot info: %s", ge)

        # 2. Promote Child Worker Bots
        try:
            from bot.child_bots import child_bot_manager
            if child_bot_manager:
                active_children = await child_bot_manager.get_active_bots()
                for cb in active_children:
                    cb_target = cb.get("username") or cb.get("bot_id")
                    if cb_target:
                        try:
                            await self.client.add_chat_members(channel_id, cb_target)
                        except Exception:
                            pass
                        try:
                            await self.client.promote_chat_member(channel_id, cb_target, privileges=bot_privileges)
                            log.info("Promoted child bot %s in channel %d", cb_target, channel_id)
                        except Exception as cbe:
                            log.warning("Failed to promote child bot %s: %s", cb_target, cbe)
        except Exception as ce:
            log.warning("Failed promoting child bots: %s", ce)

        # 3. Create permanent invite link
        invite_link = ""
        try:
            inv = await self.client.create_chat_invite_link(
                channel_id,
                name=f"{series_title[:25]} Archive"
            )
            invite_link = inv.invite_link
        except Exception as ie:
            log.warning("create_chat_invite_link failed, trying export: %s", ie)
            try:
                invite_link = await self.client.export_chat_invite_link(channel_id)
            except Exception as ee:
                log.warning("export_chat_invite_link also failed: %s", ee)

        # Fallback invite link if private
        if not invite_link:
            invite_link = f"https://t.me/c/{abs(channel_id) % (10**10)}/1"

        # 4. Save mapping in database
        mapping = await db.set_channel_mapping(
            series_slug=series_slug,
            channel_id=channel_id,
            invite_link=invite_link,
            series_title=series_title,
            poster_url=poster_url or "",
            auto_created=True,
            created_by=self.me.id if self.me else 0,
        )
        log.info("Successfully created and mapped channel for %s -> ID: %d, Link: %s",
                 series_slug, channel_id, invite_link)
        return mapping


# Global singleton
userbot_manager: UserbotManager | None = None
