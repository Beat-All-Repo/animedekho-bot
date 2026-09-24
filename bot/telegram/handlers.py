"""Telegram handlers re-exporter from WZGram / Pyrogram."""

try:
    from wzgram.handlers import *
except ImportError:
    from pyrogram.handlers import *
