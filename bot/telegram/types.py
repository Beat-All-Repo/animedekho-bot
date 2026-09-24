"""Telegram types re-exporter from WZGram / Pyrogram."""

try:
    from wzgram.types import *
except ImportError:
    from pyrogram.types import *
