"""Telegram raw functions & types re-exporter from WZGram / Pyrogram."""

try:
    from wzgram.raw import *
    import wzgram.raw as raw_module
except ImportError:
    from pyrogram.raw import *
    import pyrogram.raw as raw_module
