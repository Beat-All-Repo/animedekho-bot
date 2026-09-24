from bot.telegram import Client, filters, MessageHandler, CallbackQueryHandler

from .commands import cmd_start, cmd_help, cmd_search
from .callbacks import callback_router
from .messages import handle_text
from .admin import (
    cmd_adduser, cmd_removeuser, cmd_users,
    cmd_setchannellink,
    cmd_delete, delete_callback,
    cmd_addbot, cmd_delbot, cmd_bots, cmd_setbotquality, cmd_refreshalbums,
    cmd_login, cmd_logout, cmd_userbot, cmd_cancel, cmd_autochannel,
    cmd_albummode, cmd_createchannel, cmd_mapchannel, cmd_unmapchannel, cmd_channels,
    cmd_health, cmd_logs, cmd_errors, cmd_clearerrors, health_callback,
)
from .admin_ai import cmd_setai, cmd_ai

__all__ = [
    "cmd_start", "cmd_help", "cmd_search", "callback_router", "handle_text",
    "cmd_adduser", "cmd_removeuser", "cmd_users",
    "cmd_setchannellink",
    "cmd_delete",
    "cmd_setai", "cmd_ai",
    "cmd_addbot", "cmd_delbot", "cmd_bots", "cmd_setbotquality", "cmd_refreshalbums",
    "cmd_login", "cmd_logout", "cmd_userbot", "cmd_cancel", "cmd_autochannel",
    "cmd_albummode", "cmd_createchannel", "cmd_mapchannel", "cmd_unmapchannel", "cmd_channels",
    "cmd_health", "cmd_logs", "cmd_errors", "cmd_clearerrors", "health_callback",
    "register_handlers",
]


def register_handlers(app: Client):
    """Register all handlers on the Pyrogram Client."""
    # Commands
    app.add_handler(MessageHandler(cmd_start, filters.command("start") & filters.private))
    app.add_handler(MessageHandler(cmd_help, filters.command("help") & filters.private))
    app.add_handler(MessageHandler(cmd_search, filters.command("search") & filters.private))

    # Owner AI commands
    app.add_handler(MessageHandler(cmd_setai, filters.command("setai") & filters.private))
    app.add_handler(MessageHandler(cmd_ai, filters.command("ai") & filters.private))

    # Admin commands (owner-only, checked inside each handler)
    app.add_handler(MessageHandler(cmd_adduser, filters.command("adduser") & filters.private))
    app.add_handler(MessageHandler(cmd_removeuser, filters.command("removeuser") & filters.private))
    app.add_handler(MessageHandler(cmd_users, filters.command("users") & filters.private))
    app.add_handler(MessageHandler(cmd_setchannellink, filters.command("setchannellink") & filters.private))
    app.add_handler(MessageHandler(cmd_delete, filters.command("delete") & filters.private))
    app.add_handler(MessageHandler(cmd_addbot, filters.command("addbot") & filters.private))
    app.add_handler(MessageHandler(cmd_delbot, filters.command("delbot") & filters.private))
    app.add_handler(MessageHandler(cmd_bots, filters.command("bots") & filters.private))
    app.add_handler(MessageHandler(cmd_setbotquality, filters.command("setbotquality") & filters.private))
    app.add_handler(MessageHandler(cmd_refreshalbums, filters.command("refreshalbums") & filters.private))

    # Userbot & Channel mapping commands
    app.add_handler(MessageHandler(cmd_login, filters.command("login") & filters.private))
    app.add_handler(MessageHandler(cmd_logout, filters.command("logout") & filters.private))
    app.add_handler(MessageHandler(cmd_userbot, filters.command("userbot") & filters.private))
    app.add_handler(MessageHandler(cmd_cancel, filters.command("cancel") & filters.private))
    app.add_handler(MessageHandler(cmd_autochannel, filters.command("autochannel") & filters.private))
    app.add_handler(MessageHandler(cmd_albummode, filters.command("albummode") & filters.private))
    app.add_handler(MessageHandler(cmd_createchannel, filters.command("createchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_mapchannel, filters.command("mapchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_unmapchannel, filters.command("unmapchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_channels, filters.command("channels") & filters.private))

    # Health, Diagnostics & System Monitoring
    app.add_handler(MessageHandler(cmd_health, filters.command(["health", "status"]) & filters.private))
    app.add_handler(MessageHandler(cmd_logs, filters.command("logs") & filters.private))
    app.add_handler(MessageHandler(cmd_errors, filters.command("errors") & filters.private))
    app.add_handler(MessageHandler(cmd_clearerrors, filters.command("clearerrors") & filters.private))

    # Health callbacks
    app.add_handler(CallbackQueryHandler(health_callback, filters.regex(r"^health:")))

    # Delete callbacks (owner-only, before general router)
    app.add_handler(CallbackQueryHandler(delete_callback, filters.regex(r"^del:")))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Text messages (search) — must be last to avoid catching commands
    # Note: filters.regex matches non-command text (doesn't start with /)
    app.add_handler(MessageHandler(handle_text, filters.text & filters.private & filters.regex(r"^[^/]")))
