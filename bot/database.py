"""MongoDB integration using motor (async driver)."""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)


class Database:
    def __init__(self, mongo_uri: str, db_name: str = "animedekho"):
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client[db_name]

        # Collections
        self.users = self.db["users"]
        self.library = self.db["library"]
        self.files = self.db["files"]
        self.downloads = self.db["downloads"]
        self.config = self.db["config"]
        self.ai_history = self.db["ai_history"]
        self.ai_facts = self.db["ai_facts"]

    async def init_indexes(self):
        """Create necessary indexes."""
        await self.users.create_index("user_id", unique=True)
        await self.library.create_index(
            [("series_slug", 1), ("quality", 1), ("part", 1)],
            unique=True,
        )
        await self.files.create_index("file_unique_id", unique=True)
        await self.files.create_index(
            [("series_slug", 1), ("quality", 1), ("episode_key", 1)],
            unique=True,
        )
        await self.downloads.create_index("user_id")
        await self.downloads.create_index("timestamp")
        await self.ai_history.create_index([("chat_id", 1), ("timestamp", 1)])
        await self.ai_facts.create_index([("chat_id", 1), ("key", 1)], unique=True)
        log.info("MongoDB indexes created")

    # ── User management ───────────────────────────────────────────

    async def add_user(self, user_id: int, username: str = "", added_by: int = 0) -> bool:
        """Add approved user. Returns True if newly added."""
        try:
            await self.users.insert_one({
                "user_id": user_id,
                "username": username,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "added_by": added_by,
            })
            return True
        except Exception:
            # Duplicate key = already exists
            return False

    async def remove_user(self, user_id: int) -> bool:
        """Remove user. Returns True if removed."""
        result = await self.users.delete_one({"user_id": user_id})
        return result.deleted_count > 0

    async def is_approved(self, user_id: int) -> bool:
        """Check if user is approved."""
        doc = await self.users.find_one({"user_id": user_id})
        return doc is not None

    async def get_users(self) -> list[int]:
        """Get all approved user IDs."""
        cursor = self.users.find({}, {"user_id": 1})
        return sorted([doc["user_id"] async for doc in cursor])

    # ── Config (channel invite link, etc.) ────────────────────────

    async def get_config(self, key: str, default=None):
        """Get a config value."""
        doc = await self.config.find_one({"_id": key})
        return doc["value"] if doc else default

    async def set_config(self, key: str, value):
        """Set a config value."""
        await self.config.update_one(
            {"_id": key},
            {"$set": {"value": value}},
            upsert=True,
        )

    # ── File cache (duplicate prevention) ────────────────────────

    async def find_cached_file(
        self,
        series_identifier: str,
        episode_key: str = "",
        quality: str = "",
    ) -> dict | None:
        """
        Flexible lookup for a cached anime file in DB.
        Matches series by slug, clean slug, or title (case-insensitive regex),
        normalizes episode keys (e.g. S1E1 == S01E01, movie),
        and matches quality (or any quality if quality='auto' or empty).
        Returns dict with file_id, quality, episode_key, series_title, series_slug or None.
        """
        import re

        clean_id = (series_identifier or "").strip()
        if not clean_id:
            return None

        # Build episode query condition if specified
        ep_condition = None
        if episode_key:
            ep_clean = episode_key.strip()
            if ep_clean.lower() in ("movie", "film"):
                ep_condition = {"$in": ["movie", "Movie", "MOVIE"]}
            else:
                m = re.search(r"S(\d+)E(\d+)", ep_clean, re.I)
                if m:
                    s_num, ep_num = int(m.group(1)), int(m.group(2))
                    variants = {
                        f"S{s_num:02d}E{ep_num:02d}",
                        f"S{s_num}E{ep_num}",
                        f"S{s_num:02d}E{ep_num}",
                        f"S{s_num}E{ep_num:02d}",
                        f"s{s_num:02d}e{ep_num:02d}",
                    }
                    ep_condition = {"$in": list(variants)}
                else:
                    ep_condition = {"$regex": f"^{re.escape(ep_clean)}$", "$options": "i"}

        # Build quality condition
        q_condition = None
        if quality and quality.lower() not in ("auto", "any", ""):
            q_clean = quality.strip()
            if q_clean.lower() in ("4k", "2160p", "2160"):
                q_condition = {
                    "$in": [
                        "4K", "4k", "2160p", "2160P", "2160",
                        "1080p HQ", "1080p HQ x265", "1080p 10-Bit", "1080p 10bit",
                        "1080p x265", "1080p HEVC", "4K (1080p HQ)",
                    ]
                }
            elif q_clean.lower() in ("1080p", "1080"):
                q_condition = {
                    "$in": [
                        "1080p", "1080P", "1080",
                        "1080p HQ", "1080p HQ x265", "1080p 10-Bit", "1080p 10bit",
                        "1080p x265", "1080p HEVC",
                    ]
                }
            else:
                q_condition = {"$regex": f"^{re.escape(q_clean)}$", "$options": "i"}

        # Build series matching criteria
        slug_candidate = re.sub(r'[^a-zA-Z0-9]+', '-', clean_id).strip('-').lower()
        core_title = re.sub(r'(?i)\s*(season\s*\d+|s\d+|hindi|dubbed|multi-audio|tamil|telugu).*$', '', clean_id).strip()
        core_slug = re.sub(r'[^a-zA-Z0-9]+', '-', core_title).strip('-').lower()

        series_clauses = [
            {"series_slug": clean_id},
            {"series_slug": slug_candidate},
            {"series_slug": {"$regex": f"^{re.escape(slug_candidate)}", "$options": "i"}},
            {"series_title": {"$regex": f"^{re.escape(clean_id)}$", "$options": "i"}},
        ]
        if core_title and core_title != clean_id:
            series_clauses.append({"series_title": {"$regex": f"^{re.escape(core_title)}", "$options": "i"}})
        if core_slug and core_slug != slug_candidate:
            series_clauses.append({"series_slug": {"$regex": f"^{re.escape(core_slug)}", "$options": "i"}})

        query: dict = {"$or": series_clauses}
        if ep_condition:
            query["episode_key"] = ep_condition
        if q_condition:
            query["quality"] = q_condition

        # 1. Try matching with exact criteria
        doc = await self.files.find_one(query)
        if doc:
            return doc

        # 2. Try direct series_slug match if not matched above
        direct_query = {"series_slug": clean_id}
        if ep_condition:
            direct_query["episode_key"] = ep_condition
        if q_condition:
            direct_query["quality"] = q_condition
        doc = await self.files.find_one(direct_query)
        if doc:
            return doc

        return None

    async def get_cached_file(
        self, series_slug: str, quality: str, episode_key: str
    ) -> str | None:
        """
        Check if this series+quality+episode was already downloaded.
        Returns file_id if cached, None otherwise.
        """
        cached = await self.find_cached_file(series_slug, episode_key, quality)
        return cached["file_id"] if cached else None

    async def save_file(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
    ):
        """Save a downloaded file reference for future cache lookups."""
        try:
            await self.files.update_one(
                {
                    "series_slug": series_slug,
                    "quality": quality,
                    "episode_key": episode_key,
                },
                {"$set": {
                    "series_title": series_title,
                    "file_id": file_id,
                    "file_unique_id": file_unique_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            log.warning("Failed to save file cache: %s", e)

    # ── Download logging ──────────────────────────────────────────

    async def log_download(
        self,
        user_id: int,
        series_slug: str,
        episode: str,
        quality: str,
        file_id: str,
    ):
        """Log a download to history."""
        await self.downloads.insert_one({
            "user_id": user_id,
            "series_slug": series_slug,
            "episode": episode,
            "quality": quality,
            "file_id": file_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── AI Persistent Memory & Conversation History ───────────────

    async def save_ai_message(self, chat_id: int, role: str, content: str):
        """Save a message to persistent AI conversation history for a chat."""
        if not content:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.ai_history.insert_one({
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "timestamp": now,
            })
            # Keep latest 50 messages per chat to keep database bounded
            count = await self.ai_history.count_documents({"chat_id": chat_id})
            if count > 50:
                oldest = await self.ai_history.find({"chat_id": chat_id}).sort("timestamp", 1).limit(count - 40).to_list(length=None)
                if oldest:
                    ids = [doc["_id"] for doc in oldest]
                    await self.ai_history.delete_many({"_id": {"$in": ids}})
        except Exception as e:
            log.warning("Failed to save AI message: %s", e)

    async def get_ai_history(self, chat_id: int, limit: int = 20) -> list[dict[str, str]]:
        """Retrieve recent conversation history in chronological order (oldest to newest)."""
        try:
            cursor = self.ai_history.find(
                {"chat_id": chat_id},
                {"_id": 0, "role": 1, "content": 1},
            ).sort("timestamp", -1).limit(limit)
            docs = await cursor.to_list(length=limit)
            docs.reverse()  # Oldest to newest
            return docs
        except Exception as e:
            log.warning("Failed to retrieve AI history: %s", e)
            return []

    async def clear_ai_history(self, chat_id: int | None = None):
        """Clear conversation history for a specific chat or all chats."""
        try:
            query = {"chat_id": chat_id} if chat_id is not None else {}
            await self.ai_history.delete_many(query)
        except Exception as e:
            log.warning("Failed to clear AI history: %s", e)

    async def save_ai_fact(self, chat_id: int, key: str, value: str):
        """Save or update a persistent long-term memory fact / preference."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.ai_facts.update_one(
                {"chat_id": chat_id, "key": key.strip().lower()},
                {"$set": {
                    "chat_id": chat_id,
                    "key": key.strip().lower(),
                    "value": value.strip(),
                    "updated_at": now,
                }},
                upsert=True,
            )
        except Exception as e:
            log.warning("Failed to save AI memory fact: %s", e)

    async def get_ai_facts(self, chat_id: int) -> list[dict]:
        """Retrieve all stored permanent memory facts for a chat."""
        try:
            cursor = self.ai_facts.find({"chat_id": chat_id}, {"_id": 0, "key": 1, "value": 1, "updated_at": 1})
            return await cursor.to_list(length=100)
        except Exception as e:
            log.warning("Failed to retrieve AI memory facts: %s", e)
            return []

    async def delete_ai_fact(self, chat_id: int, key: str) -> bool:
        """Delete a specific permanent memory fact."""
        try:
            res = await self.ai_facts.delete_one({"chat_id": chat_id, "key": key.strip().lower()})
            return res.deleted_count > 0
        except Exception as e:
            log.warning("Failed to delete AI memory fact: %s", e)
            return False

    def close(self):
        """Close the MongoDB connection."""
        self.client.close()


# Singleton — set during post_init
db: Database | None = None
