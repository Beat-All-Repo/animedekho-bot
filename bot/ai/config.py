"""Dynamic AI configuration manager with MongoDB persistence."""

from __future__ import annotations
import logging
from dataclasses import dataclass
from config.settings import settings

log = logging.getLogger(__name__)


@dataclass
class AIState:
    api_key: str
    base_url: str
    model: str
    enabled: bool
    name: str = "Kage"
    persona: str = (
        "Autonomous Senior Systems Engineer and Anime Intelligence Operative for AnimeDekho. "
        "Sharp, loyal, proactive, technically precise, and self-reliant."
    )
    max_iterations: int = 20


class AIConfigManager:
    """Manages AI runtime settings with MongoDB persistence and env fallback."""

    def __init__(self):
        self._state = AIState(
            api_key=settings.ai.api_key,
            base_url=settings.ai.base_url.rstrip("/"),
            model=settings.ai.model,
            enabled=settings.ai.enabled,
        )
        self._initialized = False

    async def load(self):
        """Load persisted settings from MongoDB."""
        from bot.database import db
        if not db:
            return

        try:
            saved_key = await db.get_config("ai_api_key")
            saved_url = await db.get_config("ai_base_url")
            saved_model = await db.get_config("ai_model")
            saved_enabled = await db.get_config("ai_enabled")
            saved_name = await db.get_config("ai_name")
            saved_persona = await db.get_config("ai_persona")
            saved_iterations = await db.get_config("ai_max_iterations")

            if saved_key is not None:
                self._state.api_key = saved_key
            if saved_url is not None:
                self._state.base_url = saved_url.rstrip("/")
            if saved_model is not None:
                self._state.model = saved_model
            if saved_enabled is not None:
                self._state.enabled = bool(saved_enabled)
            if saved_name is not None:
                self._state.name = str(saved_name)
            if saved_persona is not None:
                self._state.persona = str(saved_persona)
            if saved_iterations is not None:
                try:
                    self._state.max_iterations = int(saved_iterations)
                except Exception:
                    pass

            self._initialized = True
            log.info("AI config loaded: name=%s model=%s base_url=%s enabled=%s max_iterations=%d",
                     self._state.name, self._state.model, self._state.base_url, self._state.enabled, self._state.max_iterations)
        except Exception as e:
            log.warning("Failed to load AI config from database: %s", e)

    @property
    def name(self) -> str:
        return self._state.name

    @property
    def persona(self) -> str:
        return self._state.persona

    @property
    def api_key(self) -> str:
        return self._state.api_key

    @property
    def base_url(self) -> str:
        return self._state.base_url

    @property
    def model(self) -> str:
        return self._state.model

    @property
    def enabled(self) -> bool:
        return self._state.enabled

    async def set_name(self, name: str):
        self._state.name = name.strip()
        from bot.database import db
        if db:
            await db.set_config("ai_name", self._state.name)

    async def set_persona(self, persona: str):
        self._state.persona = persona.strip()
        from bot.database import db
        if db:
            await db.set_config("ai_persona", self._state.persona)

    async def set_api_key(self, key: str):
        self._state.api_key = key.strip()
        from bot.database import db
        if db:
            await db.set_config("ai_api_key", self._state.api_key)

    async def set_base_url(self, url: str):
        url = url.strip().rstrip("/")
        if not url.endswith("/v1") and not "/chat/completions" in url:
            # Standard OpenAI compat base
            if not url.endswith("/v1"):
                url = f"{url}/v1"
        self._state.base_url = url
        from bot.database import db
        if db:
            await db.set_config("ai_base_url", self._state.base_url)

    async def set_model(self, model_name: str):
        self._state.model = model_name.strip()
        from bot.database import db
        if db:
            await db.set_config("ai_model", self._state.model)

    async def set_enabled(self, enabled: bool):
        self._state.enabled = enabled
        from bot.database import db
        if db:
            await db.set_config("ai_enabled", self._state.enabled)

    @property
    def max_iterations(self) -> int:
        return self._state.max_iterations

    async def set_max_iterations(self, limit: int):
        self._state.max_iterations = max(5, min(int(limit), 50))
        from bot.database import db
        if db:
            await db.set_config("ai_max_iterations", self._state.max_iterations)

    def get_status_summary(self) -> str:
        masked_key = (
            f"{self._state.api_key[:4]}...{self._state.api_key[-4:]}"
            if len(self._state.api_key) > 8
            else ("Set" if self._state.api_key else "❌ NOT SET")
        )
        status_icon = "🟢 Online" if self._state.enabled else "🔴 Offline"
        return (
            f"🤖 <b>Agent Profile: {self._state.name}</b>\n\n"
            f"• <b>Status:</b> {status_icon}\n"
            f"• <b>Persona:</b> <i>{self._state.persona}</i>\n"
            f"• <b>Model:</b> <code>{self._state.model}</code>\n"
            f"• <b>Base URL:</b> <code>{self._state.base_url}</code>\n"
            f"• <b>API Key:</b> <code>{masked_key}</code>\n"
            f"• <b>Tool Iteration Limit:</b> <code>{self._state.max_iterations}</code>\n"
        )


ai_config = AIConfigManager()
