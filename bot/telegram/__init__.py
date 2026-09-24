"""Telegram MTProto framework provider — powered by WZGram with Pyrogram fallback."""

from __future__ import annotations
import asyncio
import sys

# Ensure an event loop exists for pyrogram.sync wrap on Python 3.12+
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

try:
    import wzgram as tg
    from wzgram import Client, filters, enums, errors, idle
    import wzgram.types as types
    import wzgram.handlers as handlers
    import wzgram.raw as raw
    FRAMEWORK_NAME = "WZGram"
except ImportError:
    import pyrogram as tg
    from pyrogram import Client, filters, enums, errors, idle
    import pyrogram.types as types
    import pyrogram.handlers as handlers
    import pyrogram.raw as raw
    FRAMEWORK_NAME = "Pyrogram"

FRAMEWORK_VERSION = getattr(tg, "__version__", "unknown")

# Handlers
MessageHandler = handlers.MessageHandler
CallbackQueryHandler = handlers.CallbackQueryHandler

# Types
Message = types.Message
CallbackQuery = types.CallbackQuery
InlineKeyboardButton = types.InlineKeyboardButton
InlineKeyboardMarkup = types.InlineKeyboardMarkup
BotCommand = types.BotCommand
BotCommandScopeChat = types.BotCommandScopeChat
BotCommandScopeDefault = types.BotCommandScopeDefault
ChatPrivileges = types.ChatPrivileges
User = types.User

# Enums
ChatMemberStatus = enums.ChatMemberStatus

__all__ = [
    "Client",
    "filters",
    "enums",
    "errors",
    "idle",
    "types",
    "handlers",
    "raw",
    "MessageHandler",
    "CallbackQueryHandler",
    "Message",
    "CallbackQuery",
    "InlineKeyboardButton",
    "InlineKeyboardMarkup",
    "BotCommand",
    "BotCommandScopeChat",
    "BotCommandScopeDefault",
    "ChatPrivileges",
    "User",
    "ChatMemberStatus",
    "FRAMEWORK_NAME",
    "FRAMEWORK_VERSION",
]
